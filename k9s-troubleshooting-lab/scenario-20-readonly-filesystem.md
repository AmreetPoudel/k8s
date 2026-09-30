# Scenario 20 — Order Service: Read-Only Root Filesystem Crash

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Production Deployment Failure)**  
> Security team enabled `readOnlyRootFilesystem: true` across all production deployments for CIS Kubernetes Benchmark compliance. Immediately after rollout, `order-service` pods began crashing in a continuous `CrashLoopBackOff` loop.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: order-service
  template:
    metadata:
      labels:
        app: order-service
    spec:
      containers:
      - name: order-api
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting order-service..."
          echo "Initializing runtime scratch directory..."
          echo "$$" > /tmp/order-api.pid
          if [ $? -ne 0 ]; then
            echo "FATAL: Could not write PID to /tmp/order-api.pid"
            exit 1
          fi
          echo "Order service initialized successfully!"
          sleep 3600
        securityContext:
          readOnlyRootFilesystem: true
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
NAME                             READY   STATUS             RESTARTS      AGE
order-service-78b7896685-qwkbn   0/1     CrashLoopBackOff   3 (45s ago)   90s
```

### 2. Check Container Logs

```bash
kubectl logs -n lab -l app=order-service
```
Output:
```text
sh: can't create /tmp/order-api.pid: Read-only file system
Starting order-service...
Initializing runtime scratch directory...
FATAL: Could not write PID to /tmp/order-api.pid
```

---

## Root Cause

The container's security context specifies `readOnlyRootFilesystem: true`. This mounts the container's entire root file system (`/`) as read-only. 

Many applications (like Nginx, Java JVM, Node.js, Python, or shell scripts) write runtime artifacts such as:
- PID files (`/tmp/*.pid` or `/var/run/*.pid`)
- Temporary caches (`/tmp`)
- Log buffers

When the application attempts to write to `/tmp`, the Linux kernel rejects the write system call with `EROFS` (`Read-only file system`), causing the process to fail immediately with exit code 1.

---

## Solution

The wrong fix is disabling `readOnlyRootFilesystem: false`, which violates security compliance.

The correct Kubernetes cloud-native solution is mounting an **`emptyDir`** volume specifically onto `/tmp` (or whichever directory requires write access). 

Mounted volumes are exempt from the container root filesystem's read-only restriction.

### Fixed Manifest

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: order-service
  template:
    metadata:
      labels:
        app: order-service
    spec:
      volumes:
      - name: tmp-scratch
        emptyDir: {}
      containers:
      - name: order-api
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting order-service..."
          echo "Initializing runtime scratch directory..."
          echo "$$" > /tmp/order-api.pid
          if [ $? -ne 0 ]; then
            echo "FATAL: Could not write PID to /tmp/order-api.pid"
            exit 1
          fi
          echo "Order service initialized successfully!"
          sleep 3600
        securityContext:
          readOnlyRootFilesystem: true
        volumeMounts:
        - name: tmp-scratch
          mountPath: /tmp
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
NAME                             READY   STATUS    RESTARTS   AGE
order-service-589f95f4c6-2kx9l   1/1     Running   0          12s
```

Logs confirmation:
```bash
kubectl logs -n lab -l app=order-service
```
Output:
```text
Starting order-service...
Initializing runtime scratch directory...
Order service initialized successfully!
```

---

## Key Takeaways

1. **Why `readOnlyRootFilesystem: true`?**
   - Immutability: If an attacker exploits a remote code execution (RCE) vulnerability, they cannot download malicious binaries, modify system tools, or alter application code.
2. **How `emptyDir` Solves It**:
   - `emptyDir` creates an ephemeral scratch volume managed by the kubelet on the host node.
   - It provides writable space strictly scoped to the mounted path (e.g. `/tmp`, `/var/cache`).
   - Root filesystem remains read-only and hardened.
