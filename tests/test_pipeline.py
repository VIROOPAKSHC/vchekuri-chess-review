import pytest
from app import pipeline as p
from app.classifier import MoveReview, LABELS
from app.engine import StockfishEngine, find_stockfish, EngineError

# ---- the user's real chess.com game (112 plies, black wins by Qaa5#) ----
USER_PGN = '''[Event "Play vs Coach"]
[Site "Chess.com"]
[Date "2026.10.01"]
[White "Coach-Vishy"]
[Black "blackmoonbear25"]
[Result "0-1"]
[ECO "C44"]
[ECOUrl "https://www.chess.com/openings/Kings-Pawn-Opening-Kings-Knight-Variation-2...Nc6"]
[Termination "blackmoonbear25 won by checkmate"]

1. e4 Nc6 2. Nf3 e5 3. d4 Nxd4 4. Nxd4 exd4 5. Qxd4 b6 6. Nc3 Nf6 7. Qe5+ Be7 8. Nd5 Nxd5 9. exd5 O-O 10. Qc3 Bg5 11. Be3 Bxe3 12. Qxe3 Re8 13. Be2 Rxe3 14. fxe3 Qh4+ 15. g3 Qe4 16. Kf2 Qxc2 17. Rad1 Qxb2 18. Rhf1 Qf6+ 19. Kg2 Qc3 20. Bb5 Qxe3 21. Rf5 c6 22. dxc6 Qe4+ 23. Rf3 Qb4 24. cxd7 Qb2+ 25. Kh3 Bxd7+ 26. Bxd7 Qxa2 27. Bc6 Rc8 28. Be4 Qa4 29. Bxh7+ Kxh7 30. Rfd3 Qc4 31. Kg2 Qc2+ 32. R1d2 Qc6+ 33. Kh3 Re8 34. Kg4 Re4+ 35. Kh3 Qe6+ 36. Kg2 Re2+ 37. Kf1 Rxd2 38. Rxd2 Qh3+ 39. Ke1 Qe6+ 40. Re2 Qb3 41. h3 b5 42. Kf2 b4 43. Re3 Qc2+ 44. Kf3 b3 45. Re5 b2 46. Rh5+ Kg8 47. Rb5 b1=Q 48. Rxb1 Qxb1 49. Kf2 a5 50. Kf3 a4 51. Ke3 a3 52. Kf3 a2 53. Ke3 a1=Q 54. h4 Qa3+ 55. Kf4 Qbb4+ 56. Kg5 Qaa5# 0-1
'''

# ---------------- PGN validation ----------------
def test_parse_valid_with_clock_comments():
    g = p.parse_pgn('[White "a"]\n[Black "b"]\n\n1. e4 {[%clk 0:09:58]} e5 {[%clk 0:09:57]} 2. Nf3 *')
    assert len(list(g.mainline_moves())) == 3


@pytest.mark.parametrize("text,frag", [
    ("", "empty"), ("   \n ", "empty"), (None, "empty"),
    ("hello world, not chess", "no chess moves"),
    ("1. e4 e5 2. Ke4 *", "illegal"),
    ('[White "a"]\n[Black "b"]\n\n*', "no chess moves"),
    ("1. e4 e5 *\n\n1. d4 d5 *", "more than one"),
    ('[Variant "Chess960"]\n\n1. e4 e5 *', "standard"),
    ("x" * 200_001, "too large"),
])
def test_invalid_pgns(text, frag):
    with pytest.raises(p.PGNError) as e:
        p.parse_pgn(text)
    assert frag in str(e.value).lower()


def test_too_many_plies():
    moves = " ".join(["Nf3 Nf6 Ng1 Ng8"] * 101)           # 404 plies of shuffling
    with pytest.raises(p.PGNError, match="too long"):
        p.parse_pgn(moves)


def test_fen_start_position_supported():
    g = p.parse_pgn('[SetUp "1"]\n[FEN "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"]\n\n1. Ra8# 1-0')
    assert g.board().fen().startswith("6k1/5ppp")


def test_user_color_for():
    h = {"White": "Coach-Vishy", "Black": "BlackMoonBear25"}
    assert p.user_color_for(h, "blackmoonbear25") == "black"
    assert p.user_color_for(h, "coach-vishy") == "white"
    assert p.user_color_for(h, "someone") is None


# ---------------- scoring helpers ----------------
def test_move_accuracy_properties():
    assert p.move_accuracy(0.5, 0.5) == pytest.approx(100, abs=0.1)
    assert p.move_accuracy(0.5, 0.6) == pytest.approx(100, abs=0.1)      # improved: no penalty
    assert p.move_accuracy(0.9, 0.1) < 10
    a, b, c = (p.move_accuracy(0.6, 0.6 - d) for d in (0.02, 0.1, 0.3))
    assert a > b > c


def fake(color, label, wb, wa, ply=1):
    return MoveReview(ply=ply, move_number=1, color=color, san="x", uci="a1a2", fen_before="", fen_after="",
                      label=label, sub_label="", eval_before="", eval_after="", cp_before=0, cp_after=0,
                      win_before=wb, win_after=wa, loss=max(0, wb - wa), best_san="", best_uci="",
                      best_pv=[], is_best=False, explanation="")


def test_summarize_counts_and_accuracy():
    ms = [fake("white", "Good", .5, .5, 1), fake("black", "Bad", .5, .1, 2),
          fake("white", "Genius", .5, .5, 3), fake("black", "Good", .5, .5, 4)]
    s = p.summarize(ms)
    assert s["white"].counts["Genius"] == 1 and s["white"].counts["Good"] == 1
    assert s["black"].counts["Bad"] == 1 and set(s["white"].counts) == set(LABELS)
    assert s["white"].accuracy > s["black"].accuracy
    assert s["white"].moves == 2


def test_summarize_empty_side():
    s = p.summarize([fake("white", "Good", .5, .5)])
    assert s["black"].accuracy is None and s["black"].moves == 0


# ---------------- engine integration ----------------
try:
    find_stockfish(); HAVE_SF = True
except EngineError:
    HAVE_SF = False
sf = pytest.mark.skipif(not HAVE_SF, reason="stockfish not installed")


@pytest.fixture(scope="module")
def eng():
    with StockfishEngine() as e:
        yield e


FOOLS = "1. f3 e5 2. g4 Qh4# 0-1"


@sf
def test_fools_mate_review(eng):
    r = p.analyze_game(eng, FOOLS, depth=10, user_color="white")
    assert r.total_plies == r.analyzed_plies == 4 and not r.truncated
    g4, qh4 = r.moves[2], r.moves[3]
    assert g4.san == "g4" and g4.label == "Bad" and "mate in 1" in g4.explanation
    assert qh4.san == "Qh4#" and qh4.label in ("Great", "Genius")
    assert r.summary["black"].accuracy > r.summary["white"].accuracy
    assert r.worst_moves[0] == 3 and 4 in r.highlights
    assert len(r.eval_series) == 5 and r.eval_series[-1] == -10.0


@sf
def test_progress_callback_and_ply_numbers(eng):
    seen = []
    r = p.analyze_game(eng, "1. e4 e5 2. Nf3 Nc6 *", depth=8, progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert [m.ply for m in r.moves] == [1, 2, 3, 4]
    assert [m.color for m in r.moves] == ["white", "black", "white", "black"]


@sf
def test_time_budget_truncates(eng):
    r = p.analyze_game(eng, FOOLS, depth=10, max_seconds=0)
    assert r.truncated and r.analyzed_plies == 0 and r.summary["white"].accuracy is None


@sf
def test_fen_start_game_reviewed(eng):
    r = p.analyze_game(eng, '[SetUp "1"]\n[FEN "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"]\n\n1. Ra8# 1-0', depth=8)
    assert r.moves[0].san == "Ra8#" and r.moves[0].is_best


@sf
def test_bad_inputs_raise(eng):
    with pytest.raises(p.PGNError):
        p.analyze_game(eng, "")
    with pytest.raises(ValueError):
        p.analyze_game(eng, FOOLS, user_color="green")


@sf
def test_users_real_game_full_review(eng):
    r = p.analyze_game(eng, USER_PGN, depth=8, multipv=2, user_color="black", max_seconds=300)
    assert r.total_plies == r.analyzed_plies == 112 and not r.truncated
    assert r.moves[-1].san == "Qaa5#" and r.moves[-1].color == "black"
    assert r.headers["Black"] == "blackmoonbear25"
    assert sum(r.summary["white"].counts.values()) == 56
    assert sum(r.summary["black"].counts.values()) == 56
    assert all(0 <= s.accuracy <= 100 for s in r.summary.values())
    assert len(r.eval_series) == 113
    d = r.to_dict()                               # JSON-ready for the API layer
    import json; json.dumps(d)
    print(f"\nreal game: {r.elapsed_s}s  white={r.summary['white'].accuracy} black={r.summary['black'].accuracy}")
    print({c: s.counts for c, s in r.summary.items()}, "worst plies:", r.worst_moves)
