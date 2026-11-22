# gmail-mcp in a container: Streamable HTTP on port 8767, the sign-in kept in the /data volume. Attachments are
# converted in memory; nothing is kept (GMAIL_MCP_DOWNLOADS is unset).
#
#   docker compose run --rm -p 127.0.0.1:8780:8780 gmail login --no-browser --port 8780 --bind 0.0.0.0
#   (once: open the printed URL in a browser on this machine, or through an SSH tunnel: see README)
#   docker compose up -d
#
# The health check (/healthz) is healthy only while there's a sign-in: `docker ps` shows "unhealthy" until you
# sign in, and again if the sign-in is removed.

FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin gmail

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    GMAIL_MCP_TOKEN_CACHE=file \
    GMAIL_MCP_CACHE_FILE=/data/token-cache.json \
    GMAIL_MCP_PORT=8767

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

RUN mkdir -p /data && chown gmail:gmail /data && chmod 700 /data
USER gmail

EXPOSE 8767
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"GMAIL_MCP_PORT\"]}/healthz', timeout=4)"]

ENTRYPOINT ["gmail-mcp"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0"]
