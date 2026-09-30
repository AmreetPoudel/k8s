# Scenario 01 — Auth Service: Users Cannot Login

## 🚨 Incident Alert (PagerDuty)

> **SEV-2**
> Users report intermittent login failures. Monitoring shows `auth-service` instances are unstable, flapping, and dropping active user sessions every ~20-30 seconds. Frontend is loading fine.

**Your job:** Triage, find root cause, fix, and verify stability.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: auth-service
  labels:
    app: auth-service
spec:
  replicas: 2
  selector:
    matchLabels:
      app: auth-service
  template:
    metadata:
      labels:
        app: auth-service
    spec:
      containers:
      - name: server
        image: nginx:1.25-alpine
        ports:
        - containerPort: 80
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 100m
            memory: 128Mi
        livenessProbe:
          httpGet:
            path: /healthz
            port: 80
          initialDelaySeconds: 5
          periodSeconds: 5
          failureThreshold: 2
---
apiVersion: v1
kind: Service
metadata:
  name: auth-service
spec:
  selector:
    app: auth-service
  ports:
  - port: 80
    targetPort: 80
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What is the pod STATUS and RESTARTS count?
2. What component is killing the container?
3. What HTTP status code is being returned and why?
4. What is the difference between a liveness probe failure and a readiness probe failure?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The `livenessProbe` is checking `GET /healthz` on port 80. Standard `nginx` does not have a `/healthz` endpoint — it returns **HTTP 404**.

Kubernetes treats any HTTP status `>= 400` as a probe failure. After `failureThreshold: 2` consecutive failures, Kubelet executes `kill -9` on the container and restarts it.

### Smoking Gun

```bash
kubectl describe pod -n lab -l app=auth-service
```

Look at **Events**:
```
Warning  Unhealthy  kubelet  Liveness probe failed: HTTP probe failed with statuscode: 404
Normal   Killing    kubelet  Container server failed liveness probe, will be restarted
```

```bash
kubectl logs -n lab -l app=auth-service
```
```
open() "/usr/share/nginx/html/healthz" failed (2: No such file or directory)
"GET /healthz HTTP/1.1" 404  "kube-probe/1.35"
```

`kube-probe` in the User-Agent = Kubelet is the one sending the HTTP request.

### Fix

Change the liveness probe path to `/` which nginx serves with HTTP 200:

```bash
kubectl edit deployment auth-service -n lab
# Change: path: /healthz
# To:     path: /
```

### Verify

```bash
kubectl get pods -n lab -l app=auth-service
# Both pods should show 1/1 Running with 0 new restarts
```

### Key Lesson

| Probe | Failure Action | Use For |
|-------|---------------|---------|
| `livenessProbe` | **Kills & restarts container** | Unrecoverable deadlocks only |
| `readinessProbe` | **Removes from Service endpoints** | Startup delays, temporary overload |

> **Warning:** A misconfigured liveness probe is one of the most dangerous things in Kubernetes. It creates infinite restart loops that look like app crashes.

</details>
