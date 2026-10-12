"""Board rendering for the Streamlit UI: python-chess SVG + rating badge + chess.com colours."""
from __future__ import annotations

import base64
import re
from typing import Optional, Sequence

import chess
import chess.svg

BOARD_COLORS = {"square light": "#ebecd0", "square dark": "#739552",
                "square light lastmove": "#f5f682", "square dark lastmove": "#b9ca43",
                "margin": "#262421", "coord": "#c9c9c4"}
LABEL_COLORS = {"Genius": "#26c2a3", "Great": "#749bbf", "Good": "#96bc4b",
                "Medium": "#f7c631", "Bad": "#ca3431"}
LABEL_EMOJI = {"Genius": "\U0001F48E", "Great": "\U0001F537", "Good": "\U0001F7E2",
               "Medium": "\U0001F7E1", "Bad": "\U0001F534"}


def symbol(label: str, sub_label: str = "", is_best: bool = False) -> str:
    if label == "Genius":
        return "!!"
    if label == "Great":
        return "!"
    if label == "Good":
        return "\u2605" if is_best else "\u2713"
    if label == "Medium":
        return "?!"
    return "??" if sub_label == "Blunder" else "?"


def _margin(svg: str) -> float:
    w = float(re.search(r'viewBox="0 0 ([\d.]+) ', svg).group(1))
    return (w - 8 * chess.svg.SQUARE_SIZE) / 2


def _badge(svg: str, square: str, color: str, text: str, flipped: bool) -> str:
    sq = chess.parse_square(square)
    f, r = chess.square_file(sq), chess.square_rank(sq)
    m, s = _margin(svg), chess.svg.SQUARE_SIZE
    x = m + (7 - f if flipped else f) * s
    y = m + (r if flipped else 7 - r) * s
    cx, cy = x + s - 5, y + 5
    g = (f'<g id="badge"><circle cx="{cx}" cy="{cy}" r="11" fill="{color}" stroke="#00000055" stroke-width="1"/>'
         f'<text x="{cx}" y="{cy + 4.2}" font-family="Arial,Helvetica,sans-serif" font-size="12" '
         f'font-weight="bold" fill="#fff" text-anchor="middle">{text}</text></g>')
    return svg[: svg.rindex("</svg>")] + g + "</svg>"


def board_svg(fen: str, *, flipped: bool = False, size: int = 520, lastmove: Optional[str] = None,
              arrows: Sequence[tuple[str, str]] = (), badge: Optional[tuple[str, str, str]] = None) -> str:
    """`arrows`: (uci, colour) pairs. `badge`: (square, colour, text)."""
    board = chess.Board(fen)
    svg = chess.svg.board(
        board, size=size, coordinates=True, colors=BOARD_COLORS,
        orientation=chess.BLACK if flipped else chess.WHITE,
        lastmove=chess.Move.from_uci(lastmove) if lastmove else None,
        arrows=[chess.svg.Arrow(chess.parse_square(u[:2]), chess.parse_square(u[2:4]), color=c)
                for u, c in arrows])
    if badge:
        svg = _badge(svg, badge[0], badge[1], badge[2], flipped)
    return svg


def svg_to_img_html(svg: str, max_width: int = 520) -> str:
    b64 = base64.b64encode(svg.encode()).decode()
    return (f'<img alt="chess board" src="data:image/svg+xml;base64,{b64}" '
            f'style="width:100%;max-width:{max_width}px;border-radius:6px;display:block"/>')
