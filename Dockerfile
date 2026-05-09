# syntax=docker/dockerfile:1.7

# ---- builder stage ----------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock ./

# Export a deterministic, dev-free requirements file from the lock.
RUN uv export --frozen --no-dev --no-emit-project -o requirements.txt

# ---- runtime stage ----------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Runtime deps only.
COPY --from=builder /build/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && rm requirements.txt

# Application code.
COPY app ./app

# Cloud Run sets PORT; default to 8080 for local docker run -p 8080:8080.
EXPOSE 8080

# Run uvicorn directly (no reload, single worker — required for in-memory state).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
