"""Component 5: FastAPI app (JSON API + single-page UI)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import chesscom
from .engine import EngineError, find_stockfish
from .jobs import JobManager, QueueFull
from .pipeline import PGNError

STATIC = Path(__file__).parent / "static"
MIN_DEPTH, MAX_DEPTH = 6, int(os.environ.get("MAX_DEPTH", 18))
DEFAULT_DEPTH = int(os.environ.get("DEFAULT_DEPTH", 12))


class AnalyzeRequest(BaseModel):
    pgn: str = Field(..., max_length=250_000)
    user_color: Optional[Literal["white", "black"]] = None
    depth: Optional[int] = None


def _make_engine():
    from .engine import StockfishEngine
    from .stockfish_install import ensure_stockfish
    return StockfishEngine(path=ensure_stockfish(), threads=int(os.environ.get("STOCKFISH_THREADS", 1)),
                           hash_mb=int(os.environ.get("STOCKFISH_HASH", 64)))


def create_app(jobs: Optional[JobManager] = None) -> FastAPI:
    manager = jobs or JobManager(_make_engine,
                                 max_seconds=float(os.environ.get("MAX_SECONDS", 180)),
                                 max_active=int(os.environ.get("MAX_QUEUE", 10)))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        manager.close()

    app = FastAPI(title="Chess Review", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    def health():
        try:
            find_stockfish()
            sf = True
        except EngineError:
            sf = False
        return {"status": "ok" if sf else "degraded", "stockfish": sf,
                "default_depth": DEFAULT_DEPTH}

    @app.get("/api/chess-com/games")
    def chesscom_games(username: str = Query(..., min_length=1, max_length=60),
                       limit: int = Query(10, ge=1, le=50),
                       year: Optional[int] = Query(None, ge=2007, le=2100),
                       month: Optional[int] = Query(None, ge=1, le=12)):
        try:
            if year is not None and month is not None:
                games = chesscom.fetch_month_games(username, year, month)[:limit]
            else:
                games = chesscom.fetch_recent_games(username, limit=limit)
        except chesscom.InvalidInput as e:
            raise HTTPException(400, str(e))
        except chesscom.UserNotFound as e:
            raise HTTPException(404, str(e))
        except chesscom.ChessComError as e:
            raise HTTPException(502, str(e))
        return {"username": username.strip(), "count": len(games),
                "games": [g.to_dict() for g in games]}

    @app.post("/api/analyze", status_code=202)
    def analyze(req: AnalyzeRequest):
        depth = max(MIN_DEPTH, min(req.depth or DEFAULT_DEPTH, MAX_DEPTH))
        try:
            job = manager.submit(req.pgn, req.user_color, depth)
        except PGNError as e:
            raise HTTPException(400, str(e))
        except QueueFull as e:
            raise HTTPException(429, str(e))
        return job.public(include_result=True)

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = manager.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown or expired job")
        return job.public()

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.state.jobs = manager
    return app


app = create_app()
