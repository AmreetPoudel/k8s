# Enterprise Task Microservices Suite (Source Repository)

A high-performance, hardened asynchronous microservices application built for bare-metal Kubernetes and GitOps orchestration.

## Architecture

```
[ Ingress Controller ]
          │
          ├── / (Web UI) ──────────► [ Frontend (NGINX Alpine) ]
          │
          └── /api (REST API) ────► [ Backend API (FastAPI) ]
                                            │
                                            ├── (1) Push Task ──► [ Redis Message Queue ]
                                            │                                │
                                            │                      (2) BRPOP Async Task
                                            │                                │
                                            ▼                                ▼
                                    [ PostgreSQL 16 ] ◄─── (3) Save Result ── [ Python Worker ]
                                    (Synology Enterprise NAS NFS)
```

## Microservices Directory

- **`frontend/`**: Alpine NGINX web server serving the high-aesthetic real-time dark-mode dashboard. Image size: `~23 MB`.
- **`backend/`**: FastAPI asynchronous REST API handling task admission, status aggregation, and Redis queueing. Image size: `~130 MB` (multi-stage).
- **`worker/`**: Python async background consumer pulling tasks from Redis, performing work, and updating PostgreSQL. Image size: `~120 MB` (multi-stage).

## Docker Build Optimizations (Anti-Meme Guide)

### 1. Multi-Stage vs Single-Stage
- **Single-Stage (`Dockerfile.single-stage`)**: Contains `gcc`, `make`, development headers, and package caches. Bloated to **~1.2 GB**, runs as root (UID 0), and has 200+ CVEs.
- **Multi-Stage (`Dockerfile`)**: Compiles dependencies in a temporary build container, transfers only the compiled virtualenv to a clean runner, drops root privileges (`USER 10001`), and weighs only **~130 MB**.

### 2. Eliminating Rebuilds for Typo Fixes
Notice the order in `backend/Dockerfile` and `worker/Dockerfile`:
```dockerfile
# 1. Dependency manifest copied FIRST
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# 2. Application code copied LAST
COPY . .
```
- Because Docker evaluates cache line-by-line, editing Python code or fixing a comment typo **never invalidates** the expensive `pip install` layer!
- Rebuilding after a code change takes **~1.5 seconds** instead of 5 minutes!

## How Cross-Repo GitOps Image Tag Updating Works

1. Developer pushes code to this application repository.
2. GitHub Actions CI builds multi-stage container images, scans them with Trivy, and pushes them to GHCR tagged with `sha-<commit-hash>`.
3. CI job `gitops-sync` uses a GitHub Personal Access Token (`GITOPS_SYNC_PAT`) to checkout the GitOps manifests repository (`AmreetPoudel/k8s`).
4. CI runs `sed` / `yq` to update the image tag in `manifests/07-microservices/03-backend.yaml` and `04-worker.yaml`.
5. CI commits: `ci(gitops): deploy microservices release sha-<commit-hash> [skip ci]`.
6. ArgoCD watches the manifests repository, detects the commit, and triggers a zero-downtime rolling update on the bare-metal RKE2 cluster!
