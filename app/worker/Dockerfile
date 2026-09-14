# ==============================================================================
# PRODUCTION MULTI-STAGE DOCKERFILE: Async Task Worker
# ==============================================================================
# Hardened, lightweight (~120 MB), non-root UID 10001
# ==============================================================================

# STAGE 1: Builder
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Layer caching: copy dependency manifest first
COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# STAGE 2: Runner
FROM python:3.11-slim AS runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -d /app appuser

COPY --from=builder /install /usr/local
COPY . .

RUN chown -R appuser:appgroup /app

USER 10001

CMD ["python", "-u", "worker.py"]
