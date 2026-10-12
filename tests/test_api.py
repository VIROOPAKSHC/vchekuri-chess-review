import time
import pytest
from fastapi.testclient import TestClient

from app import chesscom
from app.engine import find_stockfish, EngineError, StockfishEngine
from app.jobs import JobManager
from app.main import create_app

try:
    find_stockfish(); HAVE_SF = True
except EngineError:
    HAVE_SF = False
sf = pytest.mark.skipif(not HAVE_SF, reason="stockfish not installed")

FOOLS = "1. f3 e5 2. g4 Qh4# 0-1"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def wait_done(client, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.1)
    raise AssertionError("job timed out")


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<!doctype html>" in r.text.lower()


def test_health(client):
    r = client.get("/api/health").json()
    assert r["stockfish"] is HAVE_SF and r["status"] in ("ok", "degraded")


# ---------- chess.com endpoint (upstream monkeypatched) ----------
class G:
    def __init__(self, n): self.n = n
    def to_dict(self): return {"url": f"u{self.n}", "pgn": FOOLS}


def test_games_recent(client, monkeypatch):
    monkeypatch.setattr(chesscom, "fetch_recent_games", lambda u, limit=10: [G(1), G(2)][:limit])
    r = client.get("/api/chess-com/games", params={"username": " bob ", "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "bob" and body["count"] == 2 and body["games"][0]["url"] == "u1"


def test_games_specific_month(client, monkeypatch):
    seen = {}
    def fake(u, y, m): seen["args"] = (u, y, m); return [G(1), G(2), G(3)]
    monkeypatch.setattr(chesscom, "fetch_month_games", fake)
    r = client.get("/api/chess-com/games", params={"username": "bob", "year": 2026, "month": 10, "limit": 2})
    assert r.status_code == 200 and r.json()["count"] == 2 and seen["args"] == ("bob", 2026, 10)


@pytest.mark.parametrize("exc,code", [(chesscom.UserNotFound("nf"), 404),
                                       (chesscom.InvalidInput("bad"), 400),
                                       (chesscom.ChessComError("down"), 502)])
def test_games_error_mapping(client, monkeypatch, exc, code):
    def boom(*a, **k): raise exc
    monkeypatch.setattr(chesscom, "fetch_recent_games", boom)
    r = client.get("/api/chess-com/games", params={"username": "bob"})
    assert r.status_code == code and r.json()["detail"] == str(exc)


def test_games_param_validation(client):
    assert client.get("/api/chess-com/games").status_code == 422
    assert client.get("/api/chess-com/games", params={"username": "a", "limit": 500}).status_code == 422
    assert client.get("/api/chess-com/games", params={"username": "a", "month": 13, "year": 2026}).status_code == 422


# ---------- analyze / jobs ----------
def test_analyze_rejects_bad_pgn(client):
    for bad in ["", "garbage", "1. e4 e5 2. Ke4 *", "1. e4 e5 *\n\n1. d4 d5 *"]:
        r = client.post("/api/analyze", json={"pgn": bad})
        assert r.status_code == 400 and r.json()["detail"], bad


def test_analyze_validates_body(client):
    assert client.post("/api/analyze", json={}).status_code == 422
    assert client.post("/api/analyze", json={"pgn": FOOLS, "user_color": "green"}).status_code == 422


def test_unknown_job(client):
    assert client.get("/api/jobs/nope").status_code == 404


@sf
def test_full_flow_and_cache(client):
    r = client.post("/api/analyze", json={"pgn": FOOLS, "user_color": "white", "depth": 8})
    assert r.status_code == 202 and r.json()["progress"]["total"] == 4
    done = wait_done(client, r.json()["id"])
    assert done["status"] == "done" and done["progress"] == {"done": 4, "total": 4}
    res = done["result"]
    assert res["user_color"] == "white" and len(res["moves"]) == 4
    assert res["moves"][2]["san"] == "g4" and res["moves"][2]["label"] == "Bad"
    assert set(res["summary"]) == {"white", "black"}
    # identical request is served from cache instantly
    again = client.post("/api/analyze", json={"pgn": FOOLS, "user_color": "white", "depth": 8}).json()
    assert again["status"] == "done" and again["cached"] is True and again["result"] == res


@sf
def test_depth_is_clamped(client):
    r = client.post("/api/analyze", json={"pgn": "1. e4 e5 *", "depth": 999}).json()
    done = wait_done(client, r["id"])
    assert done["result"]["depth"] == 18          # MAX_DEPTH
    r = client.post("/api/analyze", json={"pgn": "1. d4 d5 *", "depth": 1}).json()
    assert wait_done(client, r["id"])["result"]["depth"] == 6        # MIN_DEPTH


@sf
def test_queue_limit_returns_429():
    import threading
    gate = threading.Event()

    def slow_factory():                 # keeps job #1 "running" until we open the gate
        gate.wait(15)
        return StockfishEngine()

    mgr = JobManager(engine_factory=slow_factory, max_active=1)
    with TestClient(create_app(mgr)) as c:
        first = c.post("/api/analyze", json={"pgn": "1. e4 e5 *"})
        assert first.status_code == 202
        second = c.post("/api/analyze", json={"pgn": "1. d4 d5 *"})
        assert second.status_code == 429 and "busy" in second.json()["detail"].lower()
        gate.set()
        assert wait_done(c, first.json()["id"])["status"] == "done"
        third = c.post("/api/analyze", json={"pgn": "1. d4 d5 *"})   # capacity is free again
        assert third.status_code == 202


def test_engine_failure_becomes_job_error():
    def broken(): raise EngineError("no stockfish here")
    mgr = JobManager(engine_factory=broken)
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/analyze", json={"pgn": "1. e4 e5 *"})
        assert r.status_code == 202
        done = wait_done(c, r.json()["id"])
        assert done["status"] == "error" and "no stockfish here" in done["error"]
