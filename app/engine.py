"""Component 2: thin Stockfish wrapper (UCI via python-chess).

All scores are exposed from WHITE's point of view in centipawns; forced mates
are reported separately in `mate` (+N = White mates in N, -N = Black mates in N)
and mapped to +/-MATE_CP in `cp` so they can be compared numerically.
"""
from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass, field
from typing import Optional

import chess
import chess.engine

MATE_CP = 10_000
_FALLBACK_PATHS = ["/usr/games/stockfish", "/usr/bin/stockfish",
                   "/usr/local/bin/stockfish", "/opt/stockfish/stockfish"]


class EngineError(Exception):
    pass


def find_stockfish() -> str:
    """STOCKFISH_PATH env var, then PATH, then common install locations."""
    candidates = [os.environ.get("STOCKFISH_PATH"), shutil.which("stockfish"),
                  *_FALLBACK_PATHS]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise EngineError("Stockfish binary not found. Set STOCKFISH_PATH.")


@dataclass
class Line:
    """One candidate line (a MultiPV entry)."""
    move_uci: str
    move_san: str
    cp: int                      # White POV, mate mapped to +/-MATE_CP(-n)
    mate: Optional[int]          # White POV; None if no forced mate
    pv_san: list[str] = field(default_factory=list)
    depth: int = 0


@dataclass
class Analysis:
    fen: str
    turn: str                    # "white" | "black" (side to move)
    lines: list[Line]            # best first
    is_game_over: bool = False

    @property
    def best(self) -> Optional[Line]:
        return self.lines[0] if self.lines else None


def _score_to_cp_mate(score: chess.engine.PovScore) -> tuple[int, Optional[int]]:
    w = score.white()
    m = w.mate()
    if m is not None:
        # closer mates rank higher in magnitude
        return (MATE_CP - abs(m)) * (1 if m > 0 else -1), m
    return w.score(), None


def _pv_to_san(board: chess.Board, pv: list[chess.Move], max_len: int = 8) -> list[str]:
    b, out = board.copy(), []
    for mv in pv[:max_len]:
        out.append(b.san(mv))
        b.push(mv)
    return out


class StockfishEngine:
    """Reusable engine process. Thread-safe (calls are serialized by a lock)."""

    def __init__(self, path: Optional[str] = None, threads: int = 1,
                 hash_mb: int = 64):
        self.path = path or find_stockfish()
        try:
            self._eng = chess.engine.SimpleEngine.popen_uci(self.path)
        except Exception as e:
            raise EngineError(f"Could not start Stockfish: {e}") from e
        self._eng.configure({"Threads": threads, "Hash": hash_mb})
        self._lock = threading.Lock()

    def analyse(self, board: chess.Board, depth: int = 14,
                multipv: int = 3) -> Analysis:
        """Analyse `board`; returns up to `multipv` lines, best first."""
        turn = "white" if board.turn == chess.WHITE else "black"
        if board.is_game_over():
            return Analysis(board.fen(), turn, [], is_game_over=True)
        multipv = max(1, min(multipv, board.legal_moves.count()))
        with self._lock:
            infos = self._eng.analyse(board, chess.engine.Limit(depth=depth),
                                      multipv=multipv)
        lines = []
        for info in infos:
            pv = info.get("pv") or []
            if not pv:
                continue
            cp, mate = _score_to_cp_mate(info["score"])
            lines.append(Line(pv[0].uci(), board.san(pv[0]), cp, mate,
                              _pv_to_san(board, pv), info.get("depth", 0)))
        lines.sort(key=lambda l: l.cp if board.turn == chess.WHITE else -l.cp,
                   reverse=True)
        return Analysis(board.fen(), turn, lines)

    def evaluate_move(self, board: chess.Board, move: chess.Move,
                      depth: int = 14) -> Line:
        """Evaluate the position after `move` (White POV). Used to score played moves."""
        b = board.copy()
        b.push(move)
        if b.is_checkmate():
            sign = 1 if board.turn == chess.WHITE else -1   # mover delivered mate
            return Line(move.uci(), board.san(move), sign * MATE_CP, sign * 1,
                        [board.san(move)], 0)
        if b.is_game_over():                      # stalemate / insufficient / etc.
            return Line(move.uci(), board.san(move), 0, None, [board.san(move)], 0)
        a = self.analyse(b, depth=depth, multipv=1)
        best = a.best
        return Line(move.uci(), board.san(move), best.cp, best.mate,
                    [board.san(move)] + best.pv_san, best.depth)

    def close(self) -> None:
        try:
            self._eng.quit()
        except Exception:
            pass

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
