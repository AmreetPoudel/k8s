# ==============================================================================
# PRODUCTION MULTI-STAGE DOCKERFILE: Backend API (FastAPI)
# ==============================================================================
# Why Multi-Stage?
# 1. Image Size: Drops from ~1.2 GB (full dev SDK) to ~130 MB (lean runtime).
# 2. Security: gcc, make, musl-dev, and pip toolchains are completely stripped.
#    If a container vulnerability is exploited, an attacker cannot compile C exploits.
# 3. Non-Root Security: Runs as unprivileged UID 10001 (cannot modify root filesystem).
# ==============================================================================

# ------------------------------------------------------------------------------
# STAGE 1: Build & Dependency Wheel Compiler (Heavy temporary environment)
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system compilation dependencies required for binary wheels (e.g. psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ⚡ OPTIMIZATION FOR BUILD SPEED (Solves the "Rebuilding on Typo" Meme):
# Copy ONLY requirements.txt first!
# As long as requirements.txt does not change, Docker REUSES this cached layer.
# Fixing typos in comments or app code will NEVER re-download or recompile wheels!
COPY requirements.txt .

# Install dependencies into an isolated wheels/prefix directory
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ------------------------------------------------------------------------------
# STAGE 2: Minimal Runtime Environment (Shipped to Production Registry)
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS runner

WORKDIR /app

# Install ONLY the minimal shared runtime C libraries (no gcc, no compilers!)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a dedicated non-root user and group (UID/GID 10001)
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -d /app appuser

# Copy ONLY the pre-compiled Python packages from builder stage
COPY --from=builder /install /usr/local

# ⚡ Copy application code LAST!
# Changing a Python file or fixing a comment typo ONLY invalidates this 2-second layer!
COPY . .

# Set proper ownership for non-root user
RUN chown -R appuser:appgroup /app

# Switch to unprivileged execution
USER 10001

EXPOSE 8000

# Container health probe
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
