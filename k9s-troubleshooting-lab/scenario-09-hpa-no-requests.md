# Scenario 09 — User Profile API: HPA Not Scaling

## 🚨 Incident Alert (PagerDuty)

> **SEV-2 (Degraded Performance)**
> `user-profile-api` has been under heavy CPU load for 10 minutes. HPA is configured to scale at 50% CPU utilization but pod count remains stuck at 1. Users are experiencing extreme latency and timeouts.

**Your job:** Triage, find root cause, fix, and verify HPA starts reading metrics.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: user-profile-api
  namespace: lab
  labels:
    app: user-profile-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: user-profile-api
  template:
    metadata:
      labels:
        app: user-profile-api
    spec:
      containers:
      - name: api
        image: nginx:alpine
        resources:
          requests:
            memory: "32Mi"
          limits:
            memory: "64Mi"
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: user-profile-hpa
  namespace: lab
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: user-profile-api
  minReplicas: 1
  maxReplicas: 5
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 50
EOF
```

---

## Questions to Answer Before Looking at Solution

1. Run `kubectl get hpa -n lab`. What does the `TARGETS` column show?
2. Run `kubectl describe hpa user-profile-hpa -n lab`. What warning is in the **Events** section?
3. Look at the Deployment YAML — what specific resource field is missing from the container spec?
4. Why does HPA REQUIRE this specific field to calculate CPU utilization percentage?
5. What is the fix?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The container spec defines `requests.memory` and `limits.memory` but **no `requests.cpu`**. HPA calculates CPU utilization as a percentage using this formula:

```
CPU Utilization % = (Actual CPU usage) / (CPU Request) × 100
```

Without a CPU request as the denominator, HPA cannot calculate a percentage and reports `<unknown>` in the TARGETS column.

### Smoking Gun

```bash
kubectl get hpa -n lab
```
```
NAME               REFERENCE                TARGETS         MINPODS   MAXPODS   REPLICAS
user-profile-hpa   Deployment/user-...      <unknown>/50%   1         5         1
```

```bash
kubectl describe hpa user-profile-hpa -n lab
```
```
Events:
  Warning  FailedGetResourceMetric  ...
  missing request for cpu in container api of Pod user-profile-api-xxx
```

### Fix

Add CPU requests and limits to the container:

```bash
kubectl patch deployment user-profile-api -n lab --patch '
spec:
  template:
    spec:
      containers:
      - name: api
        resources:
          requests:
            memory: "32Mi"
            cpu: "50m"
          limits:
            memory: "64Mi"
            cpu: "100m"'
```

### Verify

```bash
kubectl get hpa -n lab
```
```
NAME               TARGETS       MINPODS   MAXPODS   REPLICAS
user-profile-hpa   cpu: 0%/50%   1         5         1
```

`0%/50%` = HPA is now reading CPU metrics correctly!

### What HPA Requires

| HPA metric type | Required field |
|----------------|---------------|
| CPU-based scaling | `resources.requests.cpu` (MANDATORY) |
| Memory-based scaling | `resources.requests.memory` (MANDATORY) |
| Custom metrics | Metrics Server or custom metrics adapter |

`limits` are NOT required for HPA — they are enforced by the kernel for throttling/OOM.

### HPA vs Cluster Autoscaler

| | HPA | Cluster Autoscaler |
|-|-----|--------------------|
| Scales | **Pods** (replicas) | **Nodes** (VMs) |
| Trigger | CPU/Memory above threshold | Pods stuck Pending due to resource shortage |
| Speed | ~15-30 seconds | ~2-5 minutes (boots new VM) |
| Needs cloud | No | Yes (AWS ASG, GCP MIG, etc.) |

</details>
