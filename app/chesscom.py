"""Component 1: fetch games from chess.com's monthly endpoint.

GET https://api.chess.com/pub/player/{user}/games/{YYYY}/{MM}
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import chess.pgn
import requests

BASE = "https://api.chess.com/pub"
HEADERS = {"User-Agent": "chessreview/0.1 (self-hosted game analysis)"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,50}$")
_DRAWS = {"agreed", "repetition", "stalemate", "insufficient",
          "50move", "timevsinsufficient"}


class ChessComError(Exception):
    """Error with a user-presentable message."""


class UserNotFound(ChessComError):
    pass


class InvalidInput(ChessComError):
    """Bad username / month supplied by the caller."""


@dataclass
class Game:
    url: str
    pgn: str                  # full PGN incl. headers; moves alternate White, Black
    white: str
    black: str
    white_rating: Optional[int]
    black_rating: Optional[int]
    user_color: str           # "white" | "black"
    user_outcome: str         # "win" | "loss" | "draw"
    time_class: str
    time_control: str
    end_time: int             # unix seconds
    rated: bool
    opening: Optional[str] = None
    termination: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _outcome(result: str) -> str:
    if result == "win":
        return "win"
    return "draw" if result in _DRAWS else "loss"


def _headers(pgn: str) -> dict:
    game = chess.pgn.read_game(io.StringIO(pgn))
    return dict(game.headers) if game else {}


def _opening(h: dict) -> Optional[str]:
    url = h.get("ECOUrl", "")
    if not url:
        return None
    name = url.rstrip("/").split("/")[-1]
    name = re.split(r"-\d+\.", name)[0]       # drop trailing "-2...Nc6" etc.
    return name.replace("-", " ")


def parse_game(raw: dict, username: str) -> Optional[Game]:
    """Raw chess.com game dict -> Game, or None if unusable / not user's game."""
    if raw.get("rules", "chess") != "chess" or not raw.get("pgn"):
        return None
    white, black = raw["white"], raw["black"]
    if white["username"].lower() == username.lower():
        color, me = "white", white
    elif black["username"].lower() == username.lower():
        color, me = "black", black
    else:
        return None
    try:
        h = _headers(raw["pgn"])
    except Exception:
        h = {}
    return Game(
        url=raw.get("url", ""),
        pgn=raw["pgn"],
        white=white["username"], black=black["username"],
        white_rating=white.get("rating"), black_rating=black.get("rating"),
        user_color=color,
        user_outcome=_outcome(me.get("result", "")),
        time_class=raw.get("time_class", ""),
        time_control=raw.get("time_control", ""),
        end_time=int(raw.get("end_time", 0)),
        rated=bool(raw.get("rated", False)),
        opening=_opening(h),
        termination=h.get("Termination"),
    )


def _get_json(url: str, session: requests.Session, timeout: int = 15) -> dict:
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise ChessComError(f"Could not reach chess.com: {e}") from e
    if r.status_code == 404:
        raise UserNotFound("chess.com user not found")
    if r.status_code == 429:
        raise ChessComError("chess.com rate limit hit; try again shortly")
    if not r.ok:
        raise ChessComError(f"chess.com returned HTTP {r.status_code}")
    return r.json()


def _check_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise InvalidInput("Invalid chess.com username")
    return username


def fetch_month_games(username: str, year: int, month: int,
                      session: Optional[requests.Session] = None) -> list[Game]:
    """All standard-chess games for one month, newest first."""
    username = _check_username(username)
    if not 1 <= month <= 12:
        raise InvalidInput("Month must be 1-12")
    url = f"{BASE}/player/{username.lower()}/games/{year}/{month:02d}"
    data = _get_json(url, session or requests.Session())
    games = [g for g in (parse_game(r, username) for r in data.get("games", [])) if g]
    return sorted(games, key=lambda g: g.end_time, reverse=True)


def fetch_recent_games(username: str, limit: int = 10, max_months: int = 6,
                       session: Optional[requests.Session] = None,
                       now: Optional[datetime] = None) -> list[Game]:
    """Walk back month by month from now until `limit` games are collected."""
    session = session or requests.Session()
    now = now or datetime.now(timezone.utc)
    limit = max(1, min(limit, 50))
    year, month = now.year, now.month
    out: list[Game] = []
    for _ in range(max_months):
        out.extend(fetch_month_games(username, year, month, session))
        if len(out) >= limit:
            break
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return out[:limit]
