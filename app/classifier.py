"""Component 3: rate each move (Bad / Medium / Good / Great / Genius) and explain it.

Method (chess.com-style "expected points"):
  1. Convert Stockfish centipawns -> win probability for the side that moved.
  2. loss = win%(best move) - win%(played move).
  3. Map loss to a label; promote best moves to Great / Genius under strict rules.
All thresholds live in T so they are easy to tune.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

import chess

from .engine import Analysis, Line, StockfishEngine, MATE_CP

T = {
    "good_max": 0.05,        # loss <= 5%  -> Good
    "medium_max": 0.12,      # loss <= 12% -> Medium, above -> Bad
    "blunder": 0.25,         # loss >= 25% -> "Blunder" sub-label (still Bad)
    "excellent": 0.02,       # sub-label only
    "great_gap": 0.12,       # best move beats every alternative by >=12% win prob
    "genius_sac_gap": 0.08,  # sacrifice must beat alternatives by >=8%
    "genius_quiet_gap": 0.40,# quiet only-move: beats alternatives by >=40%
    "genius_min_wp_after": 0.45,   # a sacrifice that leaves you losing isn't genius
    "sac_material": 2,       # net material (pawn units) given up to count as sacrifice
}
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5,
          chess.QUEEN: 9, chess.KING: 0}
LABELS = ["Bad", "Medium", "Good", "Great", "Genius"]


# ---------- pure helpers ----------
def win_prob(cp: int) -> float:
    """Lichess' logistic fit: centipawns (side POV) -> win probability in [0,1]."""
    return 0.5 + 0.5 * (2 / (1 + math.exp(-0.00368208 * max(-3000, min(3000, cp)))) - 1)


def material(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for pt, v in VALUES.items():
        total += v * (len(board.pieces(pt, color)) - len(board.pieces(pt, not color)))
    return total


def settled_delta(board: chess.Board, pv: list[chess.Move], mover: chess.Color) -> int:
    """Net material change (mover POV) at the first quiet point of the line.

    Stops after one of the mover's moves when the reply isn't a capture, so plain
    exchanges (Nxe5 Nxe5 Nxe5) don't look like sacrifices.
    """
    if not pv:
        return 0
    b = board.copy()
    base = material(b, mover)
    caps, states = [], []
    for mv in pv:
        caps.append(b.is_capture(mv))
        b.push(mv)
        states.append(material(b, mover) - base)
    for i in range(0, len(pv), 2):                   # mover's plies: 0, 2, 4...
        if i + 1 >= len(pv) or not caps[i + 1]:
            return states[i]
    return states[-1]


def _is_recapture(board: chess.Board, move: chess.Move) -> bool:
    if not board.move_stack or not board.is_capture(move):
        return False
    last = board.peek()
    if last.to_square != move.to_square:
        return False
    b = board.copy()
    b.pop()
    return b.is_capture(last)


def fmt_eval(cp: int, mate: Optional[int]) -> str:
    if mate is not None:
        return f"M{abs(mate)}" if mate > 0 else f"-M{abs(mate)}"
    return f"{cp / 100:+.2f}"


def _material_phrase(n: int) -> str:
    """Pawn-unit material -> words, e.g. 9 -> 'a queen', 10 -> 'a queen and a pawn'."""
    names = [(9, "a queen", "{} queens"), (5, "a rook", "{} rooks"),
             (3, "a minor piece", "{} minor pieces"), (1, "a pawn", "{} pawns")]
    parts, rem = [], n
    for val, one, many in names:
        k, rem = divmod(rem, val)
        if k:
            parts.append(one if k == 1 else many.format(k))
        if len(parts) == 2:
            break
    return " and ".join(parts) if parts else "material"


def _moves_from_san(board: chess.Board, sans: list[str]) -> list[chess.Move]:
    b, out = board.copy(), []
    for s in sans:
        try:
            mv = b.parse_san(s)
        except ValueError:
            break
        out.append(mv)
        b.push(mv)
    return out


# ---------- classification ----------
def classify(loss: float, *, is_best: bool, gap: float, sacrifice: bool,
             wp_after: float, quiet: bool, legal_moves: int,
             recapture: bool) -> tuple[str, str]:
    """Return (label, sub_label). Pure function of numbers -> easy to test."""
    if is_best and legal_moves > 1 and not recapture:
        if sacrifice and gap >= T["genius_sac_gap"] and wp_after >= T["genius_min_wp_after"]:
            return "Genius", "Brilliant sacrifice"
        if quiet and gap >= T["genius_quiet_gap"] and wp_after >= 0.45:
            return "Genius", "Brilliant only-move"
        if gap >= T["great_gap"]:
            return "Great", "Critical move"
    if loss <= T["good_max"]:
        if is_best:
            return "Good", "Best"
        return "Good", "Excellent" if loss <= T["excellent"] else "Good"
    if loss <= T["medium_max"]:
        return "Medium", "Inaccuracy"
    return "Bad", "Blunder" if loss >= T["blunder"] else "Mistake"


@dataclass
class MoveReview:
    ply: int                     # 1-based half-move index
    move_number: int
    color: str                   # who moved
    san: str
    uci: str
    fen_before: str
    fen_after: str
    label: str
    sub_label: str
    eval_before: str             # best-play eval, White POV ("+0.35", "M3")
    eval_after: str              # eval after the played move
    cp_before: int
    cp_after: int
    win_before: float            # mover's win probability under best play
    win_after: float
    loss: float
    best_san: str
    best_uci: str
    best_pv: list[str]
    is_best: bool
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


def _explain(board, move, played: Line, best: Line, label, sub, *, is_best, only_move,
             sacrifice, wp_before, wp_after, mover) -> str:
    san = played.move_san
    sgn = 1 if mover == chess.WHITE else -1
    played_pv = _moves_from_san(board, played.pv_san)
    best_pv = _moves_from_san(board, best.pv_san)
    d_played = settled_delta(board, played_pv, mover)
    d_best = settled_delta(board, best_pv, mover)
    line_txt = " ".join(best.pv_san[:4])

    if is_best:
        if label == "Genius" and sacrifice:
            return (f"Brilliant! {san} gives up {_material_phrase(-d_played)} but the engine's "
                    f"line ({line_txt}) shows full compensation, and no other move comes close.")
        if label == "Genius":
            return (f"Brilliant only-move: {san} is a quiet move that is far stronger "
                    f"than every alternative ({line_txt}).")
        if best.mate is not None and best.mate * sgn > 0:
            return f"{san} is the best move and leads to a forced mate in {abs(best.mate)}."
        if best.mate is not None:
            return f"{san} is the best defence, though the position is already lost (mate in {abs(best.mate)})."
        if d_best >= 2:
            return f"{san} wins {_material_phrase(d_best)} by force ({line_txt})."
        if label == "Great":
            return f"{san} is a critical move: the alternatives all give away a lot ({line_txt})."
        if wp_after >= 0.75:
            return f"{san} is the best move and keeps your winning advantage."
        if wp_after >= 0.58:
            return f"{san} is the engine's top choice and keeps a clear edge."
        if wp_after <= 0.25:
            return f"{san} is the most resilient defence in a difficult position."
        if wp_after <= 0.42:
            return f"{san} is the engine's top choice and holds a slightly worse position."
        return f"{san} is the engine's top choice and keeps the position balanced."

    # not the best move
    best_hint = f" Better was {best.move_san} ({line_txt})."
    if label == "Good":
        return f"{san} is a solid move; the engine slightly prefers {best.move_san}."
    if played.mate is not None and played.mate * sgn < 0:
        reply = played.pv_san[1] if len(played.pv_san) > 1 else "a reply"
        return (f"{san} allows a forced mate in {abs(played.mate)} for the opponent "
                f"(starting with {reply}).{best_hint}")
    if d_played <= -2 and d_played < d_best:
        reply = played.pv_san[1] if len(played.pv_san) > 1 else "the reply"
        return f"{san} loses {_material_phrase(-d_played)} after {reply}.{best_hint}"
    if best.mate is not None and best.mate * sgn > 0:
        return f"{san} misses a forced mate in {abs(best.mate)} with {best.move_san}."
    if d_best - d_played >= 2:
        return (f"{san} misses a chance to win {_material_phrase(d_best - d_played)} "
                f"with {best.move_san}.")
    if wp_before >= 0.70 and wp_after < 0.60:
        return f"{san} throws away a winning advantage.{best_hint}"
    if wp_before >= 0.40 and wp_after <= 0.25:
        return f"{san} turns a balanced position into a losing one.{best_hint}"
    if label == "Medium":
        return f"{san} is slightly inaccurate and gives up some of your edge.{best_hint}"
    return f"{san} is a significant mistake.{best_hint}"


def review_move(engine: StockfishEngine, board: chess.Board, move: chess.Move,
                depth: int = 14, multipv: int = 3, ply: Optional[int] = None,
                analysis: Optional[Analysis] = None) -> MoveReview:
    """Rate `move` played on `board` (board is NOT modified)."""
    if move not in board.legal_moves:
        raise ValueError(f"Illegal move {move.uci()} in {board.fen()}")
    mover = board.turn
    analysis = analysis or engine.analyse(board, depth=depth, multipv=multipv)
    best = analysis.best
    played = next((l for l in analysis.lines if l.move_uci == move.uci()), None) \
        or engine.evaluate_move(board, move, depth=depth)

    def wp(line: Line) -> float:
        w = win_prob(line.cp)
        return w if mover == chess.WHITE else 1 - w

    wp_best, wp_played = wp(best), wp(played)
    loss = max(0.0, wp_best - wp_played)
    is_best = move.uci() == best.move_uci
    others = [wp(l) for l in analysis.lines if l.move_uci != move.uci()]
    gap = (wp_best - max(others)) if others else 0.0

    played_pv = _moves_from_san(board, played.pv_san)
    sacrifice = settled_delta(board, played_pv, mover) <= -T["sac_material"]
    quiet = (not board.is_capture(move)) and (not board.gives_check(move)) \
        and (not board.is_check())
    legal = board.legal_moves.count()

    label, sub = classify(loss, is_best=is_best, gap=gap, sacrifice=sacrifice,
                          wp_after=wp_played, quiet=quiet, legal_moves=legal,
                          recapture=_is_recapture(board, move))
    san = board.san(move)
    after = board.copy()
    after.push(move)
    expl = _explain(board, move, played, best, label, sub, is_best=is_best,
                    only_move=gap >= T["great_gap"], sacrifice=sacrifice,
                    wp_before=wp_best, wp_after=wp_played, mover=mover)
    return MoveReview(
        ply=ply if ply is not None else len(board.move_stack) + 1,
        move_number=board.fullmove_number,
        color="white" if mover == chess.WHITE else "black",
        san=san, uci=move.uci(), fen_before=board.fen(), fen_after=after.fen(),
        label=label, sub_label=sub,
        eval_before=fmt_eval(best.cp, best.mate), eval_after=fmt_eval(played.cp, played.mate),
        cp_before=best.cp, cp_after=played.cp,
        win_before=round(wp_best, 4), win_after=round(wp_played, 4), loss=round(loss, 4),
        best_san=best.move_san, best_uci=best.move_uci, best_pv=best.pv_san,
        is_best=is_best, explanation=expl)
