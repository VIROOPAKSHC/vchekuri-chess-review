from datetime import datetime, timezone
import pytest
from app import chesscom as cc

# Real-format sample supplied by the user (game moves trimmed to the first 4 plies).
PGN = ('[Event "Play vs Coach"]\n[Site "Chess.com"]\n[Date "2026.10.01"]\n[Round "-"]\n'
       '[White "Coach-Vishy"]\n[Black "blackmoonbear25"]\n[Result "0-1"]\n[ECO "C44"]\n'
       '[ECOUrl "https://www.chess.com/openings/Kings-Pawn-Opening-Kings-Knight-Variation-2...Nc6"]\n'
       '[Termination "blackmoonbear25 won by checkmate"]\n\n'
       '1. e4 Nc6 2. Nf3 e5 0-1\n')


def raw(white="Coach-Vishy", black="blackmoonbear25", wres="checkmated", bres="win",
        t=100, rules="chess"):
    return {"url": f"https://www.chess.com/game/daily/{t}", "pgn": PGN, "rules": rules,
            "time_class": "daily", "time_control": "-", "end_time": t, "rated": False,
            "white": {"rating": 1200, "result": wres, "username": white},
            "black": {"rating": 490, "result": bres, "username": black}}


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.ok = data, status, status < 400
    def json(self): return self._d


class Sess:
    def __init__(self, routes): self.routes, self.calls = routes, []
    def get(self, url, **kw):
        self.calls.append(url)
        return Resp(self.routes[url]) if url in self.routes else Resp({}, 404)


U = "https://api.chess.com/pub/player/blackmoonbear25/games"


def test_parse_user_sample():
    g = cc.parse_game(raw(), "blackmoonbear25")
    assert g.user_color == "black" and g.user_outcome == "win"
    assert g.opening == "Kings Pawn Opening Kings Knight Variation"
    assert g.termination == "blackmoonbear25 won by checkmate"
    assert "1. e4 Nc6" in g.pgn


def test_loss_and_draw():
    assert cc.parse_game(raw(wres="win", bres="resigned"), "blackmoonbear25").user_outcome == "loss"
    assert cc.parse_game(raw(wres="agreed", bres="agreed"), "blackmoonbear25").user_outcome == "draw"


def test_skips_variants_and_foreign_games():
    assert cc.parse_game(raw(rules="chess960"), "blackmoonbear25") is None
    assert cc.parse_game(raw(), "carol") is None


def test_month_url_zero_padded_and_sorted():
    s = Sess({f"{U}/2026/10": {"games": [raw(t=1), raw(t=9), raw(t=5)]}})
    games = cc.fetch_month_games("BlackMoonBear25", 2026, 10, s)
    assert s.calls == [f"{U}/2026/10"]
    assert [g.end_time for g in games] == [9, 5, 1]


def test_recent_walks_back_across_year_boundary():
    s = Sess({f"{U}/2026/01": {"games": [raw(t=30)]},
              f"{U}/2025/12": {"games": [raw(t=20), raw(t=10)]}})
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    games = cc.fetch_recent_games("blackmoonbear25", limit=3, session=s, now=now)
    assert [g.end_time for g in games] == [30, 20, 10]


def test_recent_stops_when_limit_reached():
    s = Sess({f"{U}/2026/10": {"games": [raw(t=1), raw(t=2)]}})
    cc.fetch_recent_games("blackmoonbear25", limit=2, session=s,
                          now=datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert len(s.calls) == 1


def test_errors():
    with pytest.raises(cc.UserNotFound):
        cc.fetch_month_games("nobody", 2026, 10, Sess({}))
    with pytest.raises(cc.ChessComError):
        cc.fetch_month_games("bad name!", 2026, 10, Sess({}))
    with pytest.raises(cc.ChessComError):
        cc.fetch_month_games("alice", 2026, 13, Sess({}))
