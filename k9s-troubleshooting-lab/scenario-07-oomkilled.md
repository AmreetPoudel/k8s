# Scenario 07 — Report Generator: Crashing Every 20 Seconds

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Service Instability)**
> The PDF report generation service boots up fine, processes requests for ~20 seconds, then suddenly dies without warning. The cycle repeats. Customers are receiving half-generated reports and dropped connections.

**Your job:** Triage, find root cause, fix, and explain the permanent production fix.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: report-generator
  namespace: lab
  labels:
    app: report-generator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: report-generator
  template:
    metadata:
      labels:
        app: report-generator
    spec:
      containers:
      - name: worker
        image: python:3.9-slim
        command: ["python", "-c"]
        args:
        - |
          import time
          print("Report Generator started...")
          data = []
          while True:
              data.append('X' * (1024 * 1024))
              print(f"Allocated {len(data)} MB of memory")
              time.sleep(0.3)
        resources:
          requests:
            cpu: 50m
            memory: 30Mi
          limits:
            cpu: 100m
            memory: 64Mi
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What is the pod STATUS? What does it keep cycling through?
2. What is the **Exit Code** from `kubectl describe pod`?
3. Who killed the container — the application itself, Kubernetes, or the Linux kernel?
4. What does `kubectl logs -n lab -l app=report-generator --previous` show? What was the last log line before death?
5. Is this a **memory leak** or an **under-provisioned limit**? How do you tell the difference?
6. Why would just increasing the memory limit NOT fix a memory leak?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The Python script continuously appends 1MB chunks to a list without ever releasing memory (a classic memory leak). The container's memory limit is 64Mi. After approximately 64 allocations × 0.3 seconds ≈ **~19 seconds**, the container hits the cgroup memory limit and the Linux kernel sends **SIGKILL (signal 9)** — Exit Code 137.

### Smoking Gun

```bash
kubectl describe pod -n lab -l app=report-generator
```
```
Last State: Terminated
  Reason:    OOMKilled
  Exit Code: 137
```

```bash
kubectl logs -n lab -l app=report-generator --previous
```
```
Report Generator started...
Allocated 1 MB of memory
Allocated 2 MB of memory
...
Allocated 63 MB of memory
Allocated 64 MB of memory
[process terminated here — no Python exception, killed by kernel]
```

Note: **No Python exception or traceback** — the Linux kernel killed it mid-execution. The app never got a chance to throw an error.

### Memory Leak vs Under-Provisioned Limit

| Scenario | Memory pattern | Fix |
|----------|---------------|-----|
| **Memory Leak** | Starts at 30Mi, climbs steadily to 64Mi, crashes, resets, repeats — Sawtooth graph | Fix code + set limit as temporary protection |
| **Under-provisioned** | Normal workload needs 600Mi, limit is 256Mi. Crashes on any traffic spike | Raise the limit to match real usage |

Watch live memory to identify:
```bash
kubectl top pod -n lab
```

If memory climbs monotonically even under zero traffic → **Memory Leak**.

### The Wrong Fix

Increasing the limit to `512Mi` does NOT fix a memory leak. It just means the pod crashes every ~2.5 minutes instead of every 20 seconds.

### Correct Production Response

1. **Immediate:** Raise limit temporarily to buy time + alert dev team
2. **Hotfix:** Dev team patches the garbage collection bug in the app
3. **Long-term:** Add memory monitoring + HPA or pod restart policies

### Requests vs Limits

| Field | Enforced by | When exceeded |
|-------|-------------|---------------|
| `requests.memory` | kube-scheduler (reservation) | Pod not scheduled if node has less available |
| `limits.memory` | Linux kernel cgroups | **Container killed instantly (SIGKILL)** |
| `requests.cpu` | kube-scheduler | Pod not scheduled |
| `limits.cpu` | Linux kernel CFS quota | Container **throttled** (slowed, not killed) |

### QoS Classes

```bash
kubectl get pod -n lab -l app=report-generator -o jsonpath='{.items[0].status.qosClass}'
```

| Class | Condition | Eviction priority |
|-------|-----------|------------------|
| `Guaranteed` | requests == limits for all resources | Last to be evicted |
| `Burstable` | requests < limits | Middle |
| `BestEffort` | No requests or limits defined | First to be evicted |

</details>
