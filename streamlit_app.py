"""Chess Review - Streamlit edition (free hosting on Streamlit Community Cloud).

Same engine/classifier/pipeline as the FastAPI version; only the UI layer differs.
"""
import re
import time

import pandas as pd
import streamlit as st

from app import chesscom
from app.engine import EngineError, StockfishEngine
from app.pipeline import PGNError, analyze_game, parse_pgn
from app.render import LABEL_COLORS, LABEL_EMOJI, board_svg, svg_to_img_html, symbol
from app.stockfish_install import ensure_stockfish
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="Chess Review", page_icon="\u265E", layout="wide")

DEPTHS = {"Fast (depth 10)": 10, "Standard (depth 12)": 12, "Deep (depth 14, slow)": 14}
MAX_SECONDS = 150
LABELS = ["Genius", "Great", "Good", "Medium", "Bad"]


# ---------------- cached resources ----------------
@st.cache_resource(show_spinner="Starting Stockfish (first run may download it)...")
def get_engine() -> StockfishEngine:
    return StockfishEngine(path=ensure_stockfish(), threads=1, hash_mb=32)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_games(username: str, limit: int):
    return chesscom.fetch_recent_games(username, limit=limit)


def md_escape(text) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+!|<>~$])", r"\\\1", str(text))


# ---------------- analysis ----------------
def run_review(pgn: str, color, depth: int, title: str) -> None:
    try:
        parse_pgn(pgn)
    except PGNError as e:
        st.error(str(e))
        return
    bar = st.progress(0.0, text="Starting analysis...")

    def on_progress(done, total):
        bar.progress(done / total, text=f"Analyzing move {done} of {total}...")

    try:
        review = analyze_game(get_engine(), pgn, depth=depth, user_color=color,
                              max_seconds=MAX_SECONDS, progress=on_progress)
    except Exception as e:                           # engine crash / startup failure
        get_engine.clear()
        st.error(f"Analysis failed: {e}")
        return
    finally:
        bar.empty()
    if not review.moves:
        st.error("No moves could be analyzed within the time limit. Try the Fast depth.")
        return
    st.session_state.update(review=review, title=title, k=0, show_best=False,
                            flipped=(color == "black"))


# ---------------- navigation callbacks ----------------
def _go(k: int) -> None:
    n = len(st.session_state.review.moves)
    st.session_state.k = max(0, min(n, k))
    st.session_state.show_best = False


def _slider_changed() -> None:
    st.session_state.show_best = False


def _flip() -> None:
    st.session_state.flipped = not st.session_state.flipped


# ---------------- rendering ----------------
def board_for(r, k: int, flipped: bool, show_best: bool) -> str:
    if k == 0:
        return board_svg(r.moves[0].fen_before, flipped=flipped)
    m = r.moves[k - 1]
    if show_best:
        arrows = [] if m.is_best else [(m.uci, "#ca3431cc")]
        arrows.append((m.best_uci, "#81b64ccc"))
        return board_svg(m.fen_before, flipped=flipped, arrows=arrows)
    return board_svg(m.fen_after, flipped=flipped, lastmove=m.uci,
                     badge=(m.uci[2:4], LABEL_COLORS[m.label], symbol(m.label, m.sub_label, m.is_best)))


def render_review(r) -> None:
    n = len(r.moves)
    h = r.headers
    white, black = h.get("White", "White"), h.get("Black", "Black")
    st.divider()
    st.subheader(md_escape(st.session_state.get("title") or f"{white} vs {black}"))
    if r.truncated:
        st.warning(f"Analysis stopped early (time limit): {r.analyzed_plies} of {r.total_plies} moves reviewed.")

    c1, c2, c3 = st.columns([1, 1, 2])
    for col, side, name in ((c1, "white", white), (c2, "black", black)):
        s = r.summary[side]
        tag = " (you)" if r.user_color == side else ""
        col.metric(md_escape(name) + tag, "-" if s.accuracy is None else f"{s.accuracy:.1f}% accuracy")
    counts = pd.DataFrame({"White": [r.summary["white"].counts[l] for l in LABELS],
                           "Black": [r.summary["black"].counts[l] for l in LABELS]},
                          index=[f"{LABEL_EMOJI[l]} {l}" for l in LABELS])
    c3.dataframe(counts, width="stretch", height=215)

    left, right = st.columns([3, 2], gap="large")
    k = st.session_state.k
    with left:
        st.markdown(svg_to_img_html(board_for(r, k, st.session_state.flipped, st.session_state.show_best)),
                    unsafe_allow_html=True)
        b = st.columns(5)
        b[0].button("\u23EE", on_click=_go, args=(0,), key="b_first", width="stretch", help="First position")
        b[1].button("\u25C0", on_click=_go, args=(k - 1,), key="b_prev", width="stretch", help="Previous move")
        b[2].button("\u25B6", on_click=_go, args=(k + 1,), key="b_next", width="stretch", help="Next move")
        b[3].button("\u23ED", on_click=_go, args=(n,), key="b_last", width="stretch", help="Last move")
        b[4].button("\u21C5 Flip", on_click=_flip, key="b_flip", width="stretch")
        st.slider("Move", 0, n, key="k", on_change=_slider_changed)

    with right:
        if k == 0:
            st.markdown("### Game review")
            st.caption(f"{h.get('Result', '')} {h.get('Termination', '')}".strip())
            st.write("Use the buttons or the slider to step through the game.")
        else:
            m = r.moves[k - 1]
            num = f"{m.move_number}{'.' if m.color == 'white' else '...'}"
            st.markdown(f"### {LABEL_EMOJI[m.label]} {md_escape(num + m.san)}: {m.label}")
            st.caption(f"{m.sub_label} \u00B7 eval {m.eval_before} \u2192 {m.eval_after}")
            st.write(m.explanation)
            if not m.is_best:
                st.write(f"**Best move:** {md_escape(m.best_san)}  \n{md_escape(' '.join(m.best_pv[:5]))}")
            st.toggle("Show best move on board", key="show_best")
        st.caption("Evaluation (pawns, White's view)")
        st.line_chart(pd.DataFrame({"eval": r.eval_series}), height=170)

    with st.expander("All moves"):
        rows = [{"#": f"{m.move_number}{'.' if m.color == 'white' else '...'}", "Move": m.san,
                 "Rating": f"{LABEL_EMOJI[m.label]} {m.label}", "Detail": m.sub_label,
                 "Eval": m.eval_after, "Best": "-" if m.is_best else m.best_san} for m in r.moves]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ---------------- page ----------------
st.title("\u265E Chess Review")
st.caption("Review your games with Stockfish: every move rated Genius / Great / Good / Medium / Bad, "
           "with the best move and an explanation.")

depth_label = st.sidebar.selectbox("Analysis depth", list(DEPTHS), index=0,
                                   help="Deeper is more accurate but slower. Free hosting has limited CPU.")
depth = DEPTHS[depth_label]
st.sidebar.caption("Powered by Stockfish. Games are analysed one at a time.")

tab_cc, tab_pgn = st.tabs(["chess.com username", "Upload / paste PGN"])

with tab_cc:
    cc1, cc2, cc3 = st.columns([3, 1, 1])
    username = cc1.text_input("chess.com username", placeholder="e.g. blackmoonbear25", key="username")
    limit = cc2.selectbox("Games", [10, 20, 30], key="limit")
    cc3.write("")
    cc3.write("")
    if cc3.button("Fetch games", width="stretch", key="fetch_btn"):
        try:
            st.session_state.games = fetch_games(username.strip(), limit)
            st.session_state.games_user = username.strip()
            if not st.session_state.games:
                st.info("No standard games found in the last few months.")
        except chesscom.ChessComError as e:
            st.session_state.games = []
            st.error(str(e))
    games = st.session_state.get("games") or []
    if games:
        def label(i):
            g = games[i]
            opp, orating = (g.black, g.black_rating) if g.user_color == "white" else (g.white, g.white_rating)
            when = time.strftime("%b %d", time.gmtime(g.end_time))
            res = {"win": "Won", "loss": "Lost", "draw": "Draw"}[g.user_outcome]
            return f"{when} | vs {opp} ({orating}) | {g.time_class} | {res} as {g.user_color}"
        idx = st.selectbox("Pick a game", range(len(games)), format_func=label, key="game_idx")
        if st.button("Analyze this game", type="primary", key="analyze_cc"):
            g = games[idx]
            run_review(g.pgn, g.user_color, depth, label(idx))

with tab_pgn:
    up = st.file_uploader("PGN file", type=["pgn", "txt"])
    text = st.text_area("...or paste a PGN", height=160, key="pgn_text",
                        placeholder='[Event "..."]\n\n1. e4 e5 2. Nf3 ...')
    pc1, pc2 = st.columns([1, 1])
    who = pc1.selectbox("I played as", ["Not specified", "White", "Black"], key="pgn_color")
    pc2.write("")
    pc2.write("")
    if pc2.button("Analyze PGN", type="primary", width="stretch", key="analyze_pgn"):
        pgn = up.getvalue().decode("utf-8", errors="replace") if up is not None else text
        run_review(pgn, None if who == "Not specified" else who.lower(), depth, "Uploaded game")

if st.session_state.get("review") is not None:
    render_review(st.session_state.review)
