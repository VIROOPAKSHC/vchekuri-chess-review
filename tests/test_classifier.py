import chess
import pytest
from app import classifier as c
from app.engine import StockfishEngine, find_stockfish, EngineError


# ---------- pure tests (no engine needed) ----------
def test_win_prob_properties():
    assert c.win_prob(0) == pytest.approx(0.5)
    assert c.win_prob(300) > 0.7 and c.win_prob(-300) < 0.3
    assert c.win_prob(100) - c.win_prob(0) > c.win_prob(1100) - c.win_prob(1000)  # diminishing
    assert c.win_prob(9999) <= 1.0 and c.win_prob(-9999) >= 0.0


def kw(**o):
    d = dict(is_best=False, gap=0.0, sacrifice=False, wp_after=0.5, quiet=False,
             legal_moves=30, recapture=False)
    d.update(o)
    return d


@pytest.mark.parametrize("loss,exp", [(0.0, "Good"), (0.04, "Good"), (0.08, "Medium"),
                                       (0.15, "Bad"), (0.40, "Bad")])
def test_loss_thresholds(loss, exp):
    assert c.classify(loss, **kw())[0] == exp


def test_sublabels():
    assert c.classify(0.30, **kw())[1] == "Blunder"
    assert c.classify(0.15, **kw())[1] == "Mistake"
    assert c.classify(0.0, **kw(is_best=True))[1] == "Best"


def test_great_needs_best_and_gap():
    assert c.classify(0, **kw(is_best=True, gap=0.2))[0] == "Great"
    assert c.classify(0, **kw(is_best=True, gap=0.05))[0] == "Good"
    assert c.classify(0.01, **kw(is_best=False, gap=0.5))[0] == "Good"


def test_great_not_for_recapture_or_forced_move():
    assert c.classify(0, **kw(is_best=True, gap=0.5, recapture=True))[0] == "Good"
    assert c.classify(0, **kw(is_best=True, gap=0.5, legal_moves=1))[0] == "Good"


def test_genius_strict_rules():
    base = kw(is_best=True, gap=0.3, sacrifice=True, wp_after=0.7)
    assert c.classify(0, **base)[0] == "Genius"
    assert c.classify(0, **{**base, "sacrifice": False})[0] == "Great"     # no sac
    assert c.classify(0, **{**base, "gap": 0.05})[0] == "Good"             # sac not needed
    assert c.classify(0, **{**base, "wp_after": 0.2})[0] == "Great"        # sac leaves you losing
    assert c.classify(0, **{**base, "is_best": False})[0] == "Good"        # not engine's best
    q = kw(is_best=True, gap=0.5, quiet=True, wp_after=0.5)
    assert c.classify(0, **q)[0] == "Genius"
    assert c.classify(0, **{**q, "quiet": False})[0] == "Great"


def test_settled_delta_even_exchange_is_zero():
    b = chess.Board("3r2k1/8/8/3q4/8/8/3Q4/3K4 w - - 0 1")
    pv = [chess.Move.from_uci("d2d5"), chess.Move.from_uci("d8d5")]   # QxQ RxQ: even trade
    assert c.settled_delta(b, pv, chess.WHITE) == 0


def test_settled_delta_real_sacrifice():
    b = chess.Board("3r2k1/8/8/3p4/8/8/3Q4/3K4 w - - 0 1")
    pv = [chess.Move.from_uci("d2d5"), chess.Move.from_uci("d8d5")]   # Qxd5 Rxd5: queen for pawn
    assert c.settled_delta(b, pv, chess.WHITE) == -8
    assert c.settled_delta(b, pv[:1], chess.WHITE) == 1               # pawn grab only


def test_settled_delta_black_pov_and_empty():
    b = chess.Board("3r2k1/8/8/3p4/8/8/3Q4/3K4 w - - 0 1")
    pv = [chess.Move.from_uci("d2d5"), chess.Move.from_uci("d8d5")]
    assert c.settled_delta(b, pv, chess.BLACK) == 8                  # mirror image
    assert c.settled_delta(b, [], chess.WHITE) == 0


@pytest.mark.parametrize("n,txt", [(1, "a pawn"), (3, "a minor piece"), (9, "a queen"),
                                    (10, "a queen and a pawn"), (6, "a rook and a pawn"),
                                    (18, "2 queens")])
def test_material_phrase(n, txt):
    assert c._material_phrase(n) == txt


# ---------- engine integration ----------
try:
    find_stockfish(); HAVE_SF = True
except EngineError:
    HAVE_SF = False
sf = pytest.mark.skipif(not HAVE_SF, reason="stockfish not installed")


@pytest.fixture(scope="module")
def eng():
    with StockfishEngine() as e:
        yield e


def board_from(sans):
    b = chess.Board()
    for s in sans.split():
        b.push_san(s)
    return b


@sf
def test_blunder_allowing_mate(eng):
    b = board_from("e4 e5 Bc4 Nc6 Qh5")
    r = c.review_move(eng, b, b.parse_san("Nf6"), depth=12)          # ...Nf6?? Qxf7#
    assert r.label == "Bad" and r.sub_label == "Blunder"
    assert "mate in 1" in r.explanation and "Qxf7#" in r.explanation
    assert r.color == "black" and r.win_after < 0.01


@sf
def test_missed_queen_win(eng):
    b = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    r = c.review_move(eng, b, b.parse_san("a3"), depth=12)
    assert r.label == "Bad" and r.best_san == "Nxh4"
    assert "queen" in r.explanation and "Nxh4" in r.explanation


@sf
def test_finds_mate_gets_great_or_better(eng):
    b = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    r = c.review_move(eng, b, b.parse_san("Qxf7#"), depth=12)
    assert r.label in ("Great", "Genius") and r.is_best
    assert "mate" in r.explanation


@sf
def test_quiet_good_move_in_opening(eng):
    b = board_from("e4 e5")
    r = c.review_move(eng, b, b.parse_san("Nf3"), depth=12)
    assert r.label in ("Good", "Great") and r.loss < 0.03


@sf
def test_inaccuracy_or_worse_for_weird_opening_move(eng):
    b = board_from("e4 e5 Nf3 Nc6")
    r = c.review_move(eng, b, b.parse_san("h4"), depth=12)
    assert r.label in ("Medium", "Bad", "Good") and r.loss >= 0.0
    assert r.to_dict()["san"] == "h4"


@sf
def test_illegal_move_rejected(eng):
    with pytest.raises(ValueError):
        c.review_move(eng, chess.Board(), chess.Move.from_uci("e2e5"))


@sf
def test_board_not_mutated(eng):
    b = board_from("e4 e5")
    fen = b.fen()
    c.review_move(eng, b, b.parse_san("Nf3"), depth=10)
    assert b.fen() == fen and len(b.move_stack) == 2


# ---------- strict-Genius regression tests on famous games ----------
OPERA = ("e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5 Nxb5 cxb5 "
         "Bxb5+ Nbd7 O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6 Bxd7+ Nxd7")
LEGAL = "e4 e5 Nf3 d6 Bc4 Bg4 Nc3 g6"


@sf
def test_opera_game_queen_sacrifice_is_genius(eng):
    b = board_from(OPERA)
    r = c.review_move(eng, b, b.parse_san("Qb8+"), depth=16)
    assert r.label == "Genius" and r.sub_label == "Brilliant sacrifice"
    assert "queen" in r.explanation and "Rd8#" in r.explanation


@sf
def test_forced_reply_to_sacrifice_is_not_genius(eng):
    b = board_from(OPERA + " Qb8+")
    r = c.review_move(eng, b, b.parse_san("Nxb8"), depth=14)
    assert r.label != "Genius"


@sf
def test_legal_mate_nxe5_is_not_genius_but_edge_described(eng):
    b = board_from(LEGAL)
    r = c.review_move(eng, b, b.parse_san("Nxe5"), depth=16)
    assert r.label != "Genius"                 # engine PV shows Black declines the sac
    assert "balanced" not in r.explanation


@sf
def test_ordinary_recapture_never_genius_or_great(eng):
    b = board_from("e4 d5 exd5")
    r = c.review_move(eng, b, b.parse_san("Qxd5"), depth=14)
    assert r.label == "Good"
