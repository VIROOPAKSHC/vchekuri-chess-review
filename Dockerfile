# Chess Review - Python (FastAPI) + official Stockfish
FROM python:3.12-slim

ARG STOCKFISH_TAG=sf_19
ARG TARGETARCH
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    STOCKFISH_PATH=/usr/local/bin/stockfish \
    PORT=8000

# Official Stockfish "universal" build (picks the best CPU instructions at runtime).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && case "${TARGETARCH:-amd64}" in \
      arm64) ASSET=stockfish-linux-arm64-universal ;; \
      *)     ASSET=stockfish-linux-x86-64-universal ;; \
    esac \
 && curl -fsSL -o /tmp/sf.tar.gz \
      "https://github.com/official-stockfish/Stockfish/releases/download/${STOCKFISH_TAG}/${ASSET}.tar.gz" \
 && mkdir -p /opt/stockfish \
 && tar -xzf /tmp/sf.tar.gz -C /opt/stockfish --strip-components=1 \
 && BIN="$(find /opt/stockfish -maxdepth 1 -type f -name 'stockfish-*' -perm -u+x | head -n 1)" \
 && test -n "$BIN" \
 && ln -s "$BIN" /usr/local/bin/stockfish \
 && rm -rf /tmp/sf.tar.gz /opt/stockfish/wiki /var/lib/apt/lists/* \
 && (printf 'uci\nquit\n' | stockfish | grep -q uciok)

WORKDIR /app
COPY requirements-api.txt ./requirements.txt
RUN pip install -r requirements.txt
COPY app ./app

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

# Hosts like Render/Fly/Railway inject $PORT; default is 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
