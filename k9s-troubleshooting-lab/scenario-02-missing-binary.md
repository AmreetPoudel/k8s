# Scenario 02 — Order Processor: All Processing Halted

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Critical Revenue Impact)**
> Release v2.4.1 of `order-processor` was deployed 3 minutes ago. All background order processing has completely halted. Pods keep failing immediately after startup. Customer orders are queueing up unprocessed.

**Your job:** Triage, find root cause, fix, and verify stability.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-processor
  namespace: lab
  labels:
    app: order-processor
spec:
  replicas: 2
  selector:
    matchLabels:
      app: order-processor
  template:
    metadata:
      labels:
        app: order-processor
    spec:
      containers:
      - name: worker
        image: busybox:1.36
        command: ["/bin/sh"]
        args: ["-c", "echo 'Booting order worker...'; sleep 2; ./process-orders --env=prod --queue=orders"]
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 100m
            memory: 128Mi
EOF
```

---

## Questions to Answer Before Looking at Solution

1. Do the pods exist in `kubectl get pods`? What is the STATUS?
2. Should you check the Deployment events or Pod events/logs first? Why?
3. What is the **Exit Code** of the failed container?
4. What does that exit code tell you?
5. What is the immediate production action before waiting for developers to fix the image?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The container command tries to execute `./process-orders` — a binary that does not exist inside the `busybox:1.36` image. The shell exits immediately with **Exit Code 127** (command not found).

### Smoking Gun

```bash
kubectl logs -n lab -l app=order-processor
```
```
Booting order worker...
/bin/sh: ./process-orders: not found
```

```bash
kubectl describe pod <pod-name> -n lab
```
```
Last State: Terminated
  Reason:    Error
  Exit Code: 127
```

### Exit Code Cheatsheet

| Exit Code | Meaning | Common Cause |
|-----------|---------|--------------|
| `0` | Clean exit | Normal completion |
| `1` | General error | App exception, config error |
| `126` | Permission denied | Binary exists but not executable |
| `127` | **Command not found** | **Binary missing from image** |
| `137` | SIGKILL (128+9) | OOMKilled or forceful kill |
| `143` | SIGTERM (128+15) | Graceful shutdown |

### Immediate Production Fix (Rollback)

Do NOT wait for developers to rebuild the image. Roll back instantly:

```bash
kubectl rollout undo deployment/order-processor -n lab
```

Check rollout history before rolling back:
```bash
kubectl rollout history deployment/order-processor -n lab
```

### Key Lesson

Pods exist in the cluster → the Deployment did its job successfully.
The failure is at the **container runtime level** — always check Pod logs and exit codes, not Deployment events.

</details>
