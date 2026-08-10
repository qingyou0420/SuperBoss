# python:3.13-slim
FROM python@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --no-cache-dir uv==0.12.3
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# python:3.13-slim
FROM python@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 superboss \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin superboss \
    && install -d -o superboss -g superboss /app /var/lib/celery

WORKDIR /app

COPY --from=builder --chown=superboss:superboss /app/.venv /app/.venv
COPY --chown=superboss:superboss alembic.ini ./
COPY --chown=superboss:superboss migrations ./migrations

USER superboss

EXPOSE 8000

CMD ["uvicorn", "superboss.main:app", "--host", "0.0.0.0", "--port", "8000"]
