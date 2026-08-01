FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server.py auth_config.py ./
RUN chown -R app:app /app

USER app

CMD ["/app/.venv/bin/python", "server.py"]
