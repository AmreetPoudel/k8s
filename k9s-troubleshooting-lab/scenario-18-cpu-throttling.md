# Scenario 18 — API Server: Extreme Latency, Pod Healthy

## 🚨 Incident Alert (PagerDuty)

> **SEV-2 (Performance Degradation)**
> `api-server` response times jumped from 50ms to 4000ms. Pods show `1/1 Running` with 0 restarts. Memory is fine. No errors in logs. Service is alive but extremely slow. Users are timing out.

**Your job:** Triage, identify the bottleneck, fix it.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-server
  namespace: lab
  labels:
    app: api-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: api-server
  template:
    metadata:
      labels:
        app: api-server
    spec:
      containers:
      - name: api
        image: python:3.9-slim
        command: ["python", "-c"]
        args:
        - |
          import time, math
          print("API server started...")
          while True:
              result = sum(math.sqrt(i) for i in range(1000000))
              print(f"Processed request, result={result:.2f}")
        resources:
          requests:
            cpu: "100m"
            memory: "64Mi"
          limits:
            cpu: "100m"
            memory: "128Mi"
EOF
```

---

## Questions to Answer Before Looking at Solution

1. `kubectl get pods -n lab` — what is STATUS and RESTARTS?
2. `kubectl top pod -n lab` — what is CPU usage? Notice anything about the number?
3. Why is the pod NOT crashing if it's resource constrained?
4. What is the difference between CPU throttling and OOMKilled?
5. Why is `requests.cpu == limits.cpu` dangerous?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

CPU `requests` and `limits` are both set to `100m`. The Python process is CPU-intensive (computing square roots in a loop). The Linux kernel's CFS (Completely Fair Scheduler) quota hard-caps the container at `100m` CPU. The pod has zero headroom to burst — any CPU demand above 100m is throttled (paused by the kernel).

### Smoking Gun

```bash
kubectl top pod -n lab
```
```
NAME                        CPU(cores)   MEMORY(bytes)
api-server-xxx              100m         4Mi
```

CPU is pinned at exactly `100m` — the limit. Memory is fine (`4Mi` of `128Mi`).

```bash
kubectl get pods -n lab
```
```
NAME             READY   STATUS    RESTARTS
api-server-xxx   1/1     Running   0
```

Pod is `Running` with 0 restarts — CPU throttling NEVER kills the container.

### CPU Throttling vs OOMKilled

| Resource | Hits Limit → | Container Killed? | How to Spot |
|----------|-------------|------------------|-------------|
| **Memory** | OOMKilled (SIGKILL) | **YES** (Exit 137) | `CrashLoopBackOff`, restart count climbing |
| **CPU** | Throttled (kernel pauses execution) | **NO** | Pod healthy, but latency/slowness |

This is why CPU throttling is the **hardest performance issue to diagnose** in Kubernetes — no errors, no restarts, pod looks perfectly healthy.

### Fix

Raise CPU limit to allow bursting:

```bash
kubectl patch deployment api-server -n lab --patch '
spec:
  template:
    spec:
      containers:
      - name: api
        resources:
          requests:
            cpu: "200m"
            memory: "64Mi"
          limits:
            cpu: "500m"
            memory: "128Mi"'
```

### Why `requests == limits` for CPU is Dangerous

```yaml
# BAD - zero burst headroom:
requests:
  cpu: "100m"   # Scheduler reserves 100m
limits:
  cpu: "100m"   # Kernel hard caps at 100m → instant throttle on any spike

# GOOD - allows bursting:
requests:
  cpu: "100m"   # Normal baseline usage (for scheduling)
limits:
  cpu: "500m"   # Can burst up to 500m under load
```

### When System is Slow — Checklist

```
Pod slow / high latency?
    │
    ├── kubectl top pod → CPU at limit? → CPU throttling ← THIS SCENARIO
    │
    ├── kubectl top pod → Memory at limit? → OOMKilled likely incoming
    │
    ├── kubectl top pod → Both fine? → Check app logs, DB connections, external API
    │
    └── kubectl top node → Node itself saturated? → Node-level resource pressure
```

### Key Lesson

`kubectl top pod` is your first tool for performance issues — not `kubectl describe` or `kubectl logs`. Always check CPU and memory usage numbers relative to limits before diving deeper.

</details>
