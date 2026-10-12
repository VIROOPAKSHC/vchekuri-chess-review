"""Find Stockfish, or download the official universal Linux build if none is installed.

Useful on hosts without Docker (Streamlit Community Cloud, Replit, plain VPS) where the
apt package may be missing or old. Order: STOCKFISH_PATH / PATH / common locations, then download.
"""
from __future__ import annotations

import os
import platform
import subprocess
import tarfile
from pathlib import Path
from typing import Optional

import requests

from .engine import EngineError, find_stockfish

SF_TAG = "sf_19"
_ASSETS = {"x86_64": "stockfish-linux-x86-64-universal", "amd64": "stockfish-linux-x86-64-universal",
           "aarch64": "stockfish-linux-arm64-universal", "arm64": "stockfish-linux-arm64-universal"}
_URL = "https://github.com/official-stockfish/Stockfish/releases/download/{tag}/{asset}.tar.gz"


def _responds(path: str) -> bool:
    try:
        out = subprocess.run([path], input="uci\nquit\n", capture_output=True, text=True, timeout=15)
        return "uciok" in out.stdout
    except Exception:
        return False


def download_stockfish(cache_dir: Optional[str] = None, tag: str = SF_TAG,
                       session: Optional[requests.Session] = None) -> str:
    """Download + cache the official binary; returns its path. Streams (no 80 MB temp archive)."""
    machine = platform.machine().lower()
    if platform.system() != "Linux" or machine not in _ASSETS:
        raise EngineError(f"Stockfish was not found and can't be auto-downloaded on "
                          f"{platform.system()}/{machine}. Install it and set STOCKFISH_PATH.")
    asset = _ASSETS[machine]
    cache = Path(cache_dir or os.environ.get("CHESSREVIEW_CACHE") or Path.home() / ".cache" / "chessreview")
    dest = cache / tag / asset
    if dest.is_file() and os.access(dest, os.X_OK) and _responds(str(dest)):
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        r = (session or requests).get(_URL.format(tag=tag, asset=asset), stream=True, timeout=120)
        r.raise_for_status()
        with tarfile.open(fileobj=r.raw, mode="r|gz") as tar:
            for member in tar:
                if member.isfile() and Path(member.name).name == asset:
                    src = tar.extractfile(member)          # once: stream mode can't seek back
                    with open(tmp, "wb") as out:
                        while chunk := src.read(1 << 20):
                            out.write(chunk)
                    break
            else:
                raise EngineError("Stockfish archive did not contain the expected binary.")
        tmp.chmod(0o755)
        tmp.replace(dest)
    except EngineError:
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise EngineError(f"Could not download Stockfish: {e}") from e
    if not _responds(str(dest)):
        dest.unlink(missing_ok=True)
        raise EngineError("Downloaded Stockfish did not respond to the UCI handshake.")
    return str(dest)


def ensure_stockfish(cache_dir: Optional[str] = None) -> str:
    try:
        return find_stockfish()
    except EngineError:
        return download_stockfish(cache_dir)
