"""Component 4: full-game review pipeline (PGN in -> per-move reviews + summary out)."""
from __future__ import annotations

import io
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

import chess
import chess.pgn

from .classifier import LABELS, MoveReview, review_move, T
from .engine import StockfishEngine, MATE_CP

MAX_PLIES = 400
MAX_PGN_CHARS = 200_000
_STANDARD_VARIANTS = {"standard", "chess", "from position"}


class PGNError(ValueError):
    """PGN problem with a user-presentable message."""


# ---------------- PGN parsing / validation ----------------
def parse_pgn(text: str) -> chess.pgn.Game:
    """Parse and validate exactly one standard-chess game."""
    text = (text or "").strip()
    if not text:
        raise PGNError("The PGN is empty.")
    if len(text) > MAX_PGN_CHARS:
        raise PGNError("The PGN is too large (limit 200 KB).")
    stream = io.StringIO(text)
    try:
        game = chess.pgn.read_game(stream)
    except Exception as e:                        # python-chess is lenient, but be safe
        raise PGNError(f"Could not parse the PGN: {e}") from e
    if game is None:
        raise PGNError("No game found in the PGN.")
    if game.errors:
        raise PGNError(f"The PGN contains an invalid or illegal move: {game.errors[0]}")
    if chess.pgn.read_game(stream) is not None:
        raise PGNError("The upload contains more than one game. Please upload one game at a time.")
    variant = game.headers.get("Variant", "Standard").strip().lower()
    if variant not in _STANDARD_VARIANTS:
        raise PGNError(f"Only standard chess is supported (got variant '{game.headers['Variant']}').")
    n = sum(1 for _ in game.mainline_moves())
    if n == 0:
        raise PGNError("No chess moves were found. Is this a valid PGN?")
    if n > MAX_PLIES:
        raise PGNError(f"The game is too long ({n} half-moves; limit {MAX_PLIES}).")
    return game


def user_color_for(headers: dict, username: str) -> Optional[str]:
    u = username.strip().lower()
    if headers.get("White", "").lower() == u:
        return "white"
    if headers.get("Black", "").lower() == u:
        return "black"
    return None


# ---------------- scoring helpers ----------------
def move_accuracy(win_before: float, win_after: float) -> float:
    """Per-move accuracy 0-100 (Lichess' formula on win-probability drop)."""
    drop = max(0.0, win_before - win_after) * 100
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * drop) - 3.1669))


@dataclass
class SideSummary:
    color: str
    moves: int
    accuracy: Optional[float]                  # mean per-move accuracy, None if no moves
    avg_win_loss: Optional[float]              # mean win-probability lost per move (0-1)
    counts: dict[str, int] = field(default_factory=dict)


def summarize(moves: list[MoveReview]) -> dict[str, SideSummary]:
    out = {}
    for color in ("white", "black"):
        mine = [m for m in moves if m.color == color]
        counts = {l: 0 for l in LABELS}
        for m in mine:
            counts[m.label] += 1
        acc = (sum(move_accuracy(m.win_before, m.win_after) for m in mine) / len(mine)) if mine else None
        loss = (sum(m.loss for m in mine) / len(mine)) if mine else None
        out[color] = SideSummary(color, len(mine),
                                 round(acc, 1) if acc is not None else None,
                                 round(loss, 4) if loss is not None else None, counts)
    return out


@dataclass
class GameReview:
    headers: dict
    user_color: Optional[str]
    moves: list[MoveReview]
    summary: dict[str, SideSummary]
    eval_series: list[float]          # pawns, White POV, clipped to +/-10; index 0 = start
    worst_moves: list[int]            # ply numbers of the biggest mistakes (Bad), worst first
    highlights: list[int]             # ply numbers of Genius / Great moves, in game order
    total_plies: int
    analyzed_plies: int
    truncated: bool
    depth: int
    elapsed_s: float

    def to_dict(self) -> dict:
        return asdict(self)


def _clip_pawns(cp: int) -> float:
    return round(max(-1000, min(1000, cp)) / 100, 2)


# ---------------- main entry points ----------------
def analyze_game(engine: StockfishEngine, pgn_text: str, *, depth: int = 14,
                 multipv: int = 3, user_color: Optional[str] = None,
                 max_seconds: Optional[float] = 120,
                 progress: Optional[Callable[[int, int], None]] = None) -> GameReview:
    """Review every move of a single PGN game.

    max_seconds: wall-clock budget; if exceeded the review stops early and
    `truncated` is True (the moves analysed so far are still returned).
    progress(done, total) is called after each analysed ply.
    """
    if user_color not in (None, "white", "black"):
        raise ValueError("user_color must be 'white', 'black' or None")
    game = parse_pgn(pgn_text)
    board = game.board()                     # honours [FEN] start positions
    moves = list(game.mainline_moves())
    total = len(moves)

    t0 = time.monotonic()
    reviews: list[MoveReview] = []
    truncated = False
    for i, mv in enumerate(moves, 1):
        if max_seconds is not None and time.monotonic() - t0 >= max_seconds:
            truncated = True
            break
        r = review_move(engine, board, mv, depth=depth, multipv=multipv, ply=i)
        reviews.append(r)
        board.push(mv)
        if progress:
            progress(i, total)

    series = [_clip_pawns(reviews[0].cp_before)] if reviews else []
    series += [_clip_pawns(r.cp_after) for r in reviews]
    worst = sorted((r for r in reviews if r.label == "Bad"), key=lambda r: r.loss, reverse=True)[:5]
    return GameReview(
        headers=dict(game.headers), user_color=user_color, moves=reviews,
        summary=summarize(reviews), eval_series=series,
        worst_moves=[r.ply for r in worst],
        highlights=[r.ply for r in reviews if r.label in ("Genius", "Great")],
        total_plies=total, analyzed_plies=len(reviews), truncated=truncated,
        depth=depth, elapsed_s=round(time.monotonic() - t0, 2))


def analyze_chesscom_game(engine: StockfishEngine, game, **kw) -> GameReview:
    """Convenience wrapper for app.chesscom.Game objects."""
    kw.setdefault("user_color", game.user_color)
    return analyze_game(engine, game.pgn, **kw)
