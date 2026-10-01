# Scenario 23 — Metrics Collector: Executable Not Found (command vs args)

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Pod Startup Failure)**  
> New monitoring agent `metrics-collector` was deployed to collect node telemetry, but pods refuse to start. The pod status is stuck in `CreateContainerError` / `CrashLoopBackOff` with `exec: "-c": executable file not found in $PATH`.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metrics-collector
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: metrics-collector
  template:
    metadata:
      labels:
        app: metrics-collector
    spec:
      containers:
      - name: collector
        image: busybox:1.36
        command:
        - "-c"
        - "echo 'Collecting system metrics...'; sleep 3600"
        resources:
          requests:
            cpu: "50m"
            memory: "32Mi"
          limits:
            cpu: "100m"
            memory: "64Mi"
EOF
```

---

## Diagnosis

### 1. Check Pod Status

```bash
kubectl get pods -n lab
```
Output:
```text
NAME                                 READY   STATUS                 RESTARTS   AGE
metrics-collector-7c588978cf-w2kxp   0/1     CreateContainerError   0          18s
```

### 2. Check Pod Events / Logs

```bash
kubectl describe pod -n lab -l app=metrics-collector
```
Output:
```text
Error: failed to create containerd task: failed to create shim task: OCI runtime create failed: runc create failed: unable to start container process: error during container init: exec: "-c": executable file not found in $PATH: unknown
```

---

## Root Cause

In Docker vs Kubernetes, terminology maps differently:

| Docker Term | Kubernetes Term | What it actually is |
| :--- | :--- | :--- |
| **`ENTRYPOINT`** | **`command`** | The executable program/binary (e.g. `sh`, `python3`, `node`) |
| **`CMD`** | **`args`** | The arguments/flags passed to the binary (e.g. `-c`, `server.py`) |

In the broken manifest:
```yaml
command:
- "-c"
- "echo 'Collecting system metrics...'; sleep 3600"
```

The developer omitted the executable (`sh`)!

Because `command` in Kubernetes overrides the Docker image `ENTRYPOINT`:
1. Kubernetes interpreted `-c` as the **executable binary name**.
2. containerd searched `$PATH` (`/bin`, `/usr/bin`, etc.) for an executable file named `-c`.
3. No such binary exists on Linux.
4. Container startup failed immediately with `exec: "-c": executable file not found in $PATH`.

---

## Solution

Provide the shell executable (`sh`) in `command`:

### Fixed Manifest

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metrics-collector
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: metrics-collector
  template:
    metadata:
      labels:
        app: metrics-collector
    spec:
      containers:
      - name: collector
        image: busybox:1.36
        command: ["sh", "-c"]              # <-- Binary ('sh') + shell flag ('-c')
        args:
        - "echo 'Collecting system metrics...'; sleep 3600"
        resources:
          requests:
            cpu: "50m"
            memory: "32Mi"
          limits:
            cpu: "100m"
            memory: "64Mi"
EOF
```

### Verification

```bash
kubectl get pods -n lab
```
Output:
```text
NAME                                 READY   STATUS    RESTARTS   AGE
metrics-collector-697bc5f899-m4zql   1/1     Running   0          6s
```

Check logs:
```bash
kubectl logs -n lab -l app=metrics-collector
```
Output:
```text
Collecting system metrics...
```

---

## Key Takeaways

1. **`command` = Binary (`ENTRYPOINT`)**: Always specifies the program to execute (`sh`, `bash`, `python3`, `/usr/local/bin/app`).
2. **`args` = Arguments (`CMD`)**: The flags and inputs passed to that program.
3. If you write `command: ["-c", ...]`, Kubernetes does not guess that you meant `sh`. It literally searches for a program named `-c` and fails.
