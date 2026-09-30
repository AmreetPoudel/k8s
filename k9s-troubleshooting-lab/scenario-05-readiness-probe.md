# Scenario 05 — Inventory API: Traffic Black-Holed

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Silent Outage)**
> Customer orders are failing with HTTP 503. The DevOps dashboard shows `inventory-api` pods are `Running` with 0 restarts. Engineers report traffic is completely black-holed into this service. No crash logs found.

**Your job:** Triage, find root cause, fix, and verify stability.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: inventory-api
  namespace: lab
  labels:
    app: inventory-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: inventory-api
  template:
    metadata:
      labels:
        app: inventory-api
    spec:
      containers:
      - name: api
        image: nginx:alpine
        ports:
        - containerPort: 80
        readinessProbe:
          httpGet:
            path: /ready
            port: 80
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: inventory-api
  namespace: lab
spec:
  type: ClusterIP
  selector:
    app: inventory-api
  ports:
  - port: 80
    targetPort: 80
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What does `kubectl get pods -n lab` show for STATUS and READY column?
2. What is the difference between `0/1 Running` and `1/1 Running`?
3. What does `kubectl get endpoints inventory-api -n lab` show?
4. This is NOT a liveness probe issue — what type of probe is failing here and what is the difference in behavior?
5. How do you fix it?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The `readinessProbe` checks `GET /ready` on port 80. Standard nginx does not have a `/ready` endpoint — it returns HTTP 404. Kubernetes removes the pod from the Service Endpoints list, so all traffic is dropped. The container never restarts (0 restarts) because readiness failures do NOT kill the container.

### Smoking Gun

```bash
kubectl get pods -n lab
```
```
NAME                             READY   STATUS    RESTARTS
inventory-api-xxx                0/1     Running   0
```

`0/1` = Pod is alive but NOT ready. The `1` means 1 container expected, `0` means 0 containers passing readiness.

```bash
kubectl describe pod -n lab -l app=inventory-api
```
```
Warning  Unhealthy  kubelet  Readiness probe failed: HTTP probe failed with statuscode: 404
```

```bash
kubectl get endpoints inventory-api -n lab
```
```
NAME            ENDPOINTS   AGE
inventory-api   <none>      3m
```

Endpoints are empty even though pods exist — because pods are not Ready.

### Fix

Change the readiness probe path to `/` which nginx serves with 200 OK:

```bash
kubectl edit deployment inventory-api -n lab
# Change: path: /ready
# To:     path: /
```

### Liveness vs Readiness — The Critical Difference

| Probe | Failure Action | Container Restarts? | Traffic Served? |
|-------|---------------|---------------------|-----------------|
| `livenessProbe` | Kills & restarts container | **YES** | No |
| `readinessProbe` | Removes from Endpoints | **NO** | **No** |

### The Dangerous Trap

> A pod showing `STATUS: Running` with `RESTARTS: 0` looks 100% healthy to a junior engineer. But `READY: 0/1` means every single user request is being dropped silently with 503!

**Always check the READY column, not just STATUS.**

### Key Lesson

Use readiness probes for:
- App startup warm-up time
- Loading large caches before serving traffic
- Temporarily overloaded conditions

Use liveness probes ONLY for unrecoverable deadlocks — and be very careful with the path and timing.

</details>
