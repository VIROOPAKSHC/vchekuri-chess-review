"""Headless tests for streamlit_app.py using Streamlit's AppTest (real script, real Stockfish)."""
import base64
import re
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app import chesscom
from app.engine import EngineError, find_stockfish

try:
    find_stockfish(); HAVE_SF = True
except EngineError:
    HAVE_SF = False
sf = pytest.mark.skipif(not HAVE_SF, reason="stockfish not installed")

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
FOOLS = '[White "alice"]\n[Black "bob"]\n\n1. f3 e5 2. g4 Qh4# 0-1'


def new_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=90)
    return at.run()


def board_svg_of(at: AppTest) -> str:
    for m in at.markdown:
        hit = re.search(r"base64,([A-Za-z0-9+/=]+)", m.value)
        if hit:
            return base64.b64decode(hit.group(1)).decode()
    raise AssertionError("no board image found on the page")


def analyze_pgn(at, pgn=FOOLS, color="Not specified"):
    at.text_area(key="pgn_text").set_value(pgn)
    at.selectbox(key="pgn_color").select(color)
    at.button(key="analyze_pgn").click()
    return at.run()


@pytest.fixture(autouse=True)
def _fresh_engine_cache():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def test_page_loads_cleanly():
    at = new_app()
    assert not at.exception
    assert "Chess Review" in at.title[0].value
    assert [t.label for t in at.tabs] == ["chess.com username", "Upload / paste PGN"]
    assert at.session_state["review"] is None if "review" in at.session_state else True


@sf
def test_pgn_flow_end_to_end():
    at = analyze_pgn(new_app(), color="White")
    assert not at.exception and not at.error
    r = at.session_state["review"]
    assert r.total_plies == 4 and r.user_color == "white"
    assert len(at.metric) == 2 and "(you)" in at.metric[0].label
    # step to 2.g4 (ply 3) with the slider
    at.slider(key="k").set_value(3).run()
    page = " ".join(m.value for m in at.markdown)
    assert "g4" in page and "Bad" in page
    assert "mate" in " ".join(str(x.value) for x in at.markdown).lower()
    svg = board_svg_of(at)
    assert 'id="badge"' in svg and "#ca3431" in svg           # red Bad badge on the board


@sf
def test_navigation_buttons_and_flip():
    at = analyze_pgn(new_app(), color="Black")
    assert at.session_state["flipped"] is True and at.session_state["k"] == 0
    at.button(key="b_next").click().run();  assert at.session_state["k"] == 1
    at.button(key="b_next").click().run();  assert at.session_state["k"] == 2
    at.button(key="b_prev").click().run();  assert at.session_state["k"] == 1
    at.button(key="b_last").click().run();  assert at.session_state["k"] == 4
    at.button(key="b_next").click().run();  assert at.session_state["k"] == 4        # clamped
    at.button(key="b_first").click().run(); assert at.session_state["k"] == 0
    at.button(key="b_prev").click().run();  assert at.session_state["k"] == 0        # clamped
    at.button(key="b_flip").click().run();  assert at.session_state["flipped"] is False
    assert not at.exception


@sf
def test_show_best_move_toggle_draws_arrows_and_resets_on_navigation():
    at = analyze_pgn(new_app())
    at.slider(key="k").set_value(3).run()                     # 2.g4?? is Bad
    assert 'id="badge"' in board_svg_of(at)
    at.toggle(key="show_best").set_value(True).run()
    svg = board_svg_of(at)
    assert 'id="badge"' not in svg and "#81b64c" in svg and "#ca3431" in svg   # best + played arrows
    at.button(key="b_next").click().run()                     # navigating resets the toggle
    assert at.session_state["show_best"] is False and not at.exception


def test_invalid_and_empty_pgn_show_errors():
    at = analyze_pgn(new_app(), "1. e4 e5 2. Ke4 *")
    assert at.error and "illegal" in at.error[0].value.lower()
    at = analyze_pgn(new_app(), "   ")
    assert at.error and "empty" in at.error[0].value.lower()
    at = analyze_pgn(new_app(), "1. e4 e5 *\n\n1. d4 d5 *")
    assert at.error and "more than one" in at.error[0].value


@sf
def test_header_names_are_markdown_escaped():
    pgn = '[White "[click](http://evil.example)"]\n[Black "**bob**"]\n\n1. f3 e5 2. g4 Qh4# 0-1'
    at = analyze_pgn(new_app(), pgn)
    assert not at.exception
    assert "\\[click\\]\\(http://evil.example\\)" in at.metric[0].label
    assert "\\*\\*bob\\*\\*" in at.metric[1].label


# ---------------- chess.com flow (upstream mocked) ----------------
def make_game(opp="Coach", color="white", result_me="win", t=1790881529):
    raw = {"url": "https://www.chess.com/game/1", "pgn": FOOLS.replace('"alice"', f'"{opp}"' if color == "black" else '"alice"'),
           "rules": "chess", "time_class": "blitz", "time_control": "300", "end_time": t, "rated": True,
           "white": {"username": "alice" if color == "white" else opp, "rating": 1500, "result": result_me if color == "white" else "checkmated"},
           "black": {"username": opp if color == "white" else "alice", "rating": 1400, "result": result_me if color == "black" else "win"}}
    return chesscom.parse_game(raw, "alice")


@sf
def test_chesscom_flow(monkeypatch):
    monkeypatch.setattr(chesscom, "fetch_recent_games", lambda u, limit=10: [make_game("Rival", "white", "checkmated"), make_game("Other", "black", "win")])
    at = new_app()
    at.text_input(key="username").set_value("alice_flow1")
    at.button(key="fetch_btn").click().run()
    assert not at.exception and not at.error
    sel = at.selectbox(key="game_idx")
    assert len(sel.options) == 2 and "vs Rival" in sel.options[0] and "Lost as white" in sel.options[0]
    sel.select_index(1).run()
    at.button(key="analyze_cc").click().run()
    assert not at.exception and not at.error
    r = at.session_state["review"]
    assert r.user_color == "black" and at.session_state["flipped"] is True and "vs Other" in at.subheader[0].value


@pytest.mark.parametrize("exc,frag,user", [(chesscom.UserNotFound("chess.com user not found"), "not found", "ghost_a"),
                                            (chesscom.InvalidInput("Invalid chess.com username"), "Invalid", "ghost_b"),
                                            (chesscom.ChessComError("chess.com returned HTTP 503"), "503", "ghost_c")])
def test_chesscom_errors_are_shown(monkeypatch, exc, frag, user):
    def boom(*a, **k): raise exc
    monkeypatch.setattr(chesscom, "fetch_recent_games", boom)
    at = new_app()
    at.text_input(key="username").set_value(user)
    at.button(key="fetch_btn").click().run()
    assert not at.exception and at.error and frag in at.error[0].value


def test_no_games_message(monkeypatch):
    monkeypatch.setattr(chesscom, "fetch_recent_games", lambda u, limit=10: [])
    at = new_app()
    at.text_input(key="username").set_value("empty_user_x")
    at.button(key="fetch_btn").click().run()
    assert not at.exception and any("No standard games" in i.value for i in at.info)


def test_engine_startup_failure_is_reported_not_crashed(monkeypatch):
    from app import stockfish_install
    def nope(*a, **k): raise EngineError("no stockfish available")
    monkeypatch.setattr(stockfish_install, "ensure_stockfish", nope)
    at = analyze_pgn(new_app())
    assert not at.exception and at.error and "no stockfish available" in at.error[0].value
