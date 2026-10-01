# Scenario 22 — Payment Gateway: Non-Root User Permission Denied (fsGroup & emptyDir)

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Security Hardening Outage)**  
> Security team updated `payment-gateway` to run as non-root user `10001` (`runAsNonRoot: true`, `runAsUser: 10001`). The pod immediately entered `CrashLoopBackOff` with `Permission denied`. Transactions cannot be audited or processed.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-gateway
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: payment-gateway
  template:
    metadata:
      labels:
        app: payment-gateway
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
      containers:
      - name: gateway
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting payment gateway as user $(id -u)..."
          echo "Initializing audit transaction log..."
          mkdir -p /var/log/audit && touch /var/log/audit/transactions.log
          if [ $? -ne 0 ]; then
            echo "FATAL: Failed to initialize /var/log/audit/transactions.log"
            exit 1
          fi
          echo "Payment gateway running and auditing transactions."
          sleep 3600
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
NAME                               READY   STATUS             RESTARTS     AGE
payment-gateway-5548cc4c45-8pz44   0/1     CrashLoopBackOff   2 (20s ago)  45s
```

### 2. Check Logs

```bash
kubectl logs -n lab -l app=payment-gateway
```
Output:
```text
Starting payment gateway as user 10001...
Initializing audit transaction log...
mkdir: can't create directory '/var/log/': Permission denied
FATAL: Failed to initialize /var/log/audit/transactions.log
```

---

## Root Cause

In container images (including Linux base images), standard directories such as `/var/log`, `/app/data`, or `/var/cache` are created during image build time and owned by `root:root` (`UID 0: GID 0`) with permissions `drwxr-xr-x` (`0755`).

When `securityContext.runAsUser: 10001` is enforced:
1. The process runs as UID 10001.
2. UID 10001 is categorized as "Others" under Linux POSIX permissions.
3. "Others" only have read (`r`) and execute (`x`) permissions — write (`w`) is denied.
4. Calling `mkdir -p /var/log/audit` returns `EACCES (Permission denied)`, and the container crashes.

---

## Solution

To resolve this without running as root:

1. **Mount an `emptyDir` volume** at `/var/log` so the directory is an independent volume.
2. **Set `fsGroup: 10001`** in the Pod's `securityContext`. 

`fsGroup` tells Kubernetes to automatically change the group ownership of all mounted volumes to GID `10001` and grant read/write group permissions (`rw-`).

### Fixed Manifest

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-gateway
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: payment-gateway
  template:
    metadata:
      labels:
        app: payment-gateway
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001             # <-- Sets volume group ownership to 10001
      volumes:
      - name: log-storage
        emptyDir: {}               # <-- Ephemeral volume for logs
      containers:
      - name: gateway
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting payment gateway as user $(id -u)..."
          echo "Initializing audit transaction log..."
          mkdir -p /var/log/audit && touch /var/log/audit/transactions.log
          if [ $? -ne 0 ]; then
            echo "FATAL: Failed to initialize /var/log/audit/transactions.log"
            exit 1
          fi
          echo "Payment gateway running and auditing transactions."
          sleep 3600
        volumeMounts:
        - name: log-storage
          mountPath: /var/log      # <-- Mount over /var/log
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

Check the pod:
```bash
kubectl get pods -n lab
```
Output:
```text
NAME                               READY   STATUS    RESTARTS   AGE
payment-gateway-6997b5f8c4-9k2zl   1/1     Running   0          8s
```

Check the logs:
```bash
kubectl logs -n lab -l app=payment-gateway
```
Output:
```text
Starting payment gateway as user 10001...
Initializing audit transaction log...
Payment gateway running and auditing transactions.
```

---

## Key Takeaways

1. **`runAsUser` vs POSIX Permissions:**
   - Enforcing `runAsUser` immediately exposes directory ownership issues because base images have files owned by root (`0:0`).
2. **`fsGroup` in Kubernetes:**
   - `fsGroup` defines a supplemental group ID.
   - Kubernetes automatically sets the group ownership of mounted volumes to this GID.
   - Files created inside the volume inherit the group ownership of `fsGroup`.
