import io, os, tarfile, platform
import pytest
from app import stockfish_install as si
from app.engine import EngineError

FAKE_ENGINE = "#!/bin/sh\nwhile read l; do [ \"$l\" = uci ] && echo uciok; [ \"$l\" = quit ] && exit 0; done\n"


def make_tar(binary_name, content=FAKE_ENGINE, extra=True):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        if extra:
            d = tarfile.TarInfo("stockfish/Copying.txt"); data = b"license"; d.size = len(data)
            t.addfile(d, io.BytesIO(data))
        b = tarfile.TarInfo(f"stockfish/{binary_name}"); data = content.encode(); b.size = len(data); b.mode = 0o755
        t.addfile(b, io.BytesIO(data))
    buf.seek(0)
    return buf


class FakeResp:
    def __init__(self, fileobj, status=200): self.raw, self.status = fileobj, status
    def raise_for_status(self):
        if self.status >= 400: raise RuntimeError(f"HTTP {self.status}")


class FakeSession:
    def __init__(self, resp): self.resp, self.urls = resp, []
    def get(self, url, **kw): self.urls.append(url); return self.resp


linux_only = pytest.mark.skipif(platform.system() != "Linux" or platform.machine().lower() not in si._ASSETS,
                                reason="auto-download is Linux x86_64/arm64 only")


@linux_only
def test_download_extracts_only_the_binary_and_caches(tmp_path):
    asset = si._ASSETS[platform.machine().lower()]
    s = FakeSession(FakeResp(make_tar(asset)))
    path = si.download_stockfish(str(tmp_path), session=s)
    assert os.path.basename(path) == asset and os.access(path, os.X_OK)
    assert s.urls == [si._URL.format(tag=si.SF_TAG, asset=asset)]
    assert not (tmp_path / si.SF_TAG / "Copying.txt").exists()          # only the binary is kept
    again = si.download_stockfish(str(tmp_path), session=FakeSession(FakeResp(make_tar("x"), 500)))
    assert again == path                                                 # cache hit: no network used


@linux_only
def test_download_http_error_is_engine_error(tmp_path):
    with pytest.raises(EngineError, match="Could not download"):
        si.download_stockfish(str(tmp_path), session=FakeSession(FakeResp(io.BytesIO(b""), 404)))
    assert not list(tmp_path.rglob("*.part"))                            # no partial file left


@linux_only
def test_archive_without_binary_and_non_uci_binary_rejected(tmp_path):
    with pytest.raises(EngineError, match="expected binary"):
        si.download_stockfish(str(tmp_path), session=FakeSession(FakeResp(make_tar("something-else"))))
    asset = si._ASSETS[platform.machine().lower()]
    with pytest.raises(EngineError, match="UCI"):
        si.download_stockfish(str(tmp_path / "b"), session=FakeSession(FakeResp(make_tar(asset, "#!/bin/sh\nexit 0\n"))))


def test_unsupported_platform_message(monkeypatch, tmp_path):
    monkeypatch.setattr(si.platform, "system", lambda: "Windows")
    with pytest.raises(EngineError, match="STOCKFISH_PATH"):
        si.download_stockfish(str(tmp_path))


def test_ensure_prefers_installed(monkeypatch):
    monkeypatch.setattr(si, "find_stockfish", lambda: "/x/stockfish")
    monkeypatch.setattr(si, "download_stockfish", lambda *a, **k: pytest.fail("should not download"))
    assert si.ensure_stockfish() == "/x/stockfish"


def test_ensure_falls_back_to_download(monkeypatch):
    def nope(): raise EngineError("none")
    monkeypatch.setattr(si, "find_stockfish", nope)
    monkeypatch.setattr(si, "download_stockfish", lambda cache_dir=None: "/cache/stockfish")
    assert si.ensure_stockfish() == "/cache/stockfish"


@linux_only
@pytest.mark.skipif(not os.environ.get("RUN_NETWORK_TESTS"), reason="opt-in: RUN_NETWORK_TESTS=1")
def test_real_download_from_github(tmp_path):
    path = si.download_stockfish(str(tmp_path))
    assert si._responds(path)
