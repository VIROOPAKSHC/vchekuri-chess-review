import chess
import pytest
from app.engine import StockfishEngine, find_stockfish, EngineError, MATE_CP

try:
    find_stockfish()
    HAVE_SF = True
except EngineError:
    HAVE_SF = False
pytestmark = pytest.mark.skipif(not HAVE_SF, reason="stockfish not installed")


@pytest.fixture(scope="module")
def eng():
    with StockfishEngine() as e:
        yield e


def test_start_position_is_roughly_equal(eng):
    a = eng.analyse(chess.Board(), depth=12)
    assert a.turn == "white" and len(a.lines) == 3
    assert abs(a.best.cp) < 80 and a.best.mate is None


def test_finds_mate_in_one_white(eng):
    b = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")   # Ra8#
    a = eng.analyse(b, depth=10, multipv=1)
    assert a.best.move_san == "Ra8#" and a.best.mate == 1 and a.best.cp > 9000


def test_mate_for_black_is_negative_white_pov(eng):
    b = chess.Board("r5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1")   # ...Ra1#
    a = eng.analyse(b, depth=10, multipv=1)
    assert a.best.move_san == "Ra1#" and a.best.mate == -1 and a.best.cp < -9000


def test_wins_hanging_queen(eng):
    b = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    a = eng.analyse(b, depth=12, multipv=1)
    assert a.best.move_san == "Nxh4"
    assert a.best.cp > 500


def test_side_to_move_sorting_for_black(eng):
    b = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2")  # ...Qh4#
    a = eng.analyse(b, depth=10, multipv=3)
    assert a.best.move_san == "Qh4#"
    cps = [l.cp for l in a.lines]
    assert cps == sorted(cps)          # for Black, best (most negative) first


def test_game_over_position(eng):
    b = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    a = eng.analyse(b)
    assert a.is_game_over and a.best is None


def test_multipv_capped_by_legal_moves(eng):
    b = chess.Board("7k/8/6K1/8/8/8/8/6R1 b - - 0 1")          # few legal moves; multipv must be capped
    a = eng.analyse(b, depth=8, multipv=5)
    assert 1 <= len(a.lines) <= b.legal_moves.count()


def test_evaluate_move_blunder_vs_best(eng):
    b = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    good = eng.evaluate_move(b, chess.Move.from_uci("f3h4"), depth=12)
    bad = eng.evaluate_move(b, chess.Move.from_uci("a2a3"), depth=12)
    assert good.cp > bad.cp + 300
    assert good.pv_san[0] == "Nxh4"


def test_evaluate_move_checkmate_played(eng):
    b = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    ev = eng.evaluate_move(b, chess.Move.from_uci("a1a8"))
    assert ev.cp >= MATE_CP - 1 and ev.mate and ev.mate > 0
