# Chess Review

Review your chess games with **Stockfish**. Fetch recent games from chess.com (or upload a PGN) and get every
move rated **Genius / Great / Good / Medium / Bad**, with the engine's best move and a plain-English explanation,
per-side accuracy, and an evaluation graph. Pure Python, in **two flavours** sharing the same tested core:

| | Streamlit edition | FastAPI edition |
|---|---|---|
| Entry point | `streamlit_app.py` | `app/main.py` + `app/static/index.html` |
| Look | Streamlit dark theme, SVG board | chess.com-style custom single-page UI |
| Best for | Free, one-click hosting on Streamlit Community Cloud | Docker hosts (Render, Fly.io, ...) |
| Requirements | `requirements.txt` + `packages.txt` | `requirements-api.txt` + `Dockerfile` |

## Move ratings
Win probability is computed from Stockfish's evaluation. `loss` = win probability of the best move minus that of the
played move (mover's point of view).

| Label | Rule |
|---|---|
| **Genius** | Engine's top move that is a real sacrifice and clearly better than alternatives (or a rare quiet only-move with a huge swing) |
| **Great** | Engine's top move that beats every alternative by 12%+ (critical move) |
| **Good** | Loss up to 5% (sub-labels: Best / Excellent / Good) |
| **Medium** | Loss 5-12% (inaccuracy) |
| **Bad** | Loss over 12% (Mistake; Blunder at 25%+) |

Recaptures and forced moves are never Great or Genius. All thresholds are in `app/classifier.py` (`T`).

## Project layout
```
app/chesscom.py          chess.com monthly-games fetcher
app/engine.py            Stockfish (UCI) wrapper
app/stockfish_install.py finds Stockfish, or downloads the official build if missing
app/classifier.py        move rating + explanations
app/pipeline.py          PGN validation + full-game review + accuracy
app/render.py            SVG board (Streamlit edition)
app/jobs.py, main.py     background jobs + FastAPI app (FastAPI edition)
app/static/              single-page UI (FastAPI edition)
streamlit_app.py         Streamlit edition
tests/                   pytest suite (+ tests/ui: jsdom UI smoke test)
```

## Run locally
Needs Python 3.10+. Stockfish is found automatically (`apt install stockfish`, `STOCKFISH_PATH`, ...) and,
on Linux, **downloaded automatically** if it is missing.
```bash
pip install -r requirements.txt && streamlit run streamlit_app.py        # Streamlit edition
pip install -r requirements-api.txt && uvicorn app.main:app --reload     # FastAPI edition
```

## Deploy for free: Streamlit Community Cloud
1. Push this repo to GitHub (public).
2. Go to [share.streamlit.io](https://share.streamlit.io), **Create app**, pick the repo, branch `main`,
   main file `streamlit_app.py`, then **Deploy**.
3. Streamlit installs `requirements.txt` and (from `packages.txt`) the `stockfish` apt package automatically.
   If that package is missing or old, the app downloads the official Stockfish build on first use.

Limits (per Streamlit's docs; they can change without notice): apps sleep after 12 hours without traffic, and
resources are small and shared (about 1-2.7 GB RAM, up to 2 CPU cores), so use the **Fast** depth. The app
stops a review after 150 s and returns the moves analysed so far.

## Deploy with Docker (FastAPI edition)
**GitHub Pages cannot run this app** (static files only). Let a container host build the `Dockerfile` from your repo.
On Render: **New + > Blueprint**, select the repo (`render.yaml` configures it). The container listens on `$PORT`.

## Other hosts (check current free-tier terms before relying on them)
- **Replit:** the free Starter plan reportedly allows one published app (one source says the link expires after
  30 days). No Docker needed because Stockfish auto-downloads. Untested by the author: import the repo, then run
  `pip install -r requirements.txt && streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8080`
  (or `uvicorn app.main:app --host 0.0.0.0 --port 8080` for the FastAPI edition, after installing `requirements-api.txt`).
- **Hugging Face Spaces:** older docs describe a free 2 vCPU / 16 GB CPU tier, but current Hub docs say Docker and
  Gradio Spaces need a paid plan. Check before relying on it.
- **Fly.io, Railway, Koyeb, Google Cloud Run:** any Docker host works with the `Dockerfile`; free tiers vary and were not checked.

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest -q                         # needs Stockfish; RUN_NETWORK_TESTS=1 also tests the real download
cd tests/ui && npm ci                       # FastAPI UI smoke test: start the server on :8765 first
BASE=http://127.0.0.1:8765 node ui_smoke.js
```

## Configuration (FastAPI edition, environment variables)
`STOCKFISH_PATH`, `DEFAULT_DEPTH` (12), `MAX_DEPTH` (18), `MAX_SECONDS` (180), `MAX_QUEUE` (10),
`STOCKFISH_THREADS` (1), `STOCKFISH_HASH` (64 MB).

## API (FastAPI edition)
`GET /api/health`, `GET /api/chess-com/games?username=&limit=&year=&month=`, `POST /api/analyze`
(`{pgn, user_color?, depth?}` -> job), `GET /api/jobs/{id}`.

## Limitations
- Explanations describe what happens (mates, material, lost advantage), not deeper motifs like pins or forks.
- Accuracy is win-probability based, so it is forgiving in already-decided positions.
- One shared engine: reviews run one at a time. Standard chess only.

Stockfish is GPL-3.0 licensed. Chess.com data is fetched from its public API.
