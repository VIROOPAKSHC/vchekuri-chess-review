"""Background analysis jobs: one shared Stockfish, one worker, progress + result cache."""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from .engine import StockfishEngine
from .pipeline import analyze_game, parse_pgn


class QueueFull(Exception):
    pass


@dataclass
class Job:
    id: str
    status: str = "queued"            # queued | running | done | error
    done: int = 0
    total: int = 0
    result: Optional[dict] = None
    error: Optional[str] = None
    cached: bool = False
    created: float = field(default_factory=time.time)

    def public(self, include_result: bool = True) -> dict:
        d = {"id": self.id, "status": self.status, "cached": self.cached,
             "progress": {"done": self.done, "total": self.total}}
        if self.error:
            d["error"] = self.error
        if include_result and self.status == "done":
            d["result"] = self.result
        return d


class JobManager:
    def __init__(self, engine_factory: Callable[[], StockfishEngine] = StockfishEngine, *,
                 max_active: int = 10, max_jobs: int = 200, cache_size: int = 64,
                 max_seconds: float = 180):
        self._factory = engine_factory
        self._engine: Optional[StockfishEngine] = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._cache: "OrderedDict[str, dict]" = OrderedDict()
        self._lock = threading.Lock()
        self.max_active, self.max_jobs, self.cache_size = max_active, max_jobs, cache_size
        self.max_seconds = max_seconds

    # -- public API --
    def submit(self, pgn: str, user_color: Optional[str], depth: int) -> Job:
        game = parse_pgn(pgn)                          # raises PGNError synchronously
        total = sum(1 for _ in game.mainline_moves())
        key = hashlib.sha256(f"{pgn.strip()}|{user_color}|{depth}".encode()).hexdigest()
        with self._lock:
            job = Job(id=uuid.uuid4().hex[:12], total=total)
            if key in self._cache:
                self._cache.move_to_end(key)
                job.status, job.result, job.done, job.cached = "done", self._cache[key], total, True
                self._remember(job)
                return job
            active = sum(1 for j in self._jobs.values() if j.status in ("queued", "running"))
            if active >= self.max_active:
                raise QueueFull("The server is busy. Please try again in a minute.")
            self._remember(job)
        self._pool.submit(self._run, job, pgn, user_color, depth, key)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._engine:
            self._engine.close()

    # -- internals --
    def _remember(self, job: Job) -> None:
        self._jobs[job.id] = job
        while len(self._jobs) > self.max_jobs:
            self._jobs.popitem(last=False)

    def _run(self, job: Job, pgn, user_color, depth, key) -> None:
        job.status = "running"
        try:
            if self._engine is None:
                self._engine = self._factory()

            def progress(done, total):
                job.done, job.total = done, total

            review = analyze_game(self._engine, pgn, depth=depth, user_color=user_color,
                                  max_seconds=self.max_seconds, progress=progress)
            job.result = review.to_dict()
            job.done = review.analyzed_plies
            job.status = "done"
            with self._lock:
                self._cache[key] = job.result
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        except Exception as e:                         # engine crash, bad state, etc.
            if self._engine is not None:
                try:
                    self._engine.close()
                finally:
                    self._engine = None                # recreate on the next job
            job.status, job.error = "error", f"Analysis failed: {e}"
