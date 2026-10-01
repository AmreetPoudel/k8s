# Scenario 26 — API Gateway: ConfigMap Mount Overwriting Directory (subPath)

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Gateway Outage)**  
> Security team added custom security headers to the Nginx `api-gateway` via a ConfigMap. After applying the update, `api-gateway` pods immediately crash on startup (`CrashLoopBackOff`) with `open() "/etc/nginx/nginx.conf" failed (2: No such file or directory)`.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: security-headers
  namespace: lab
data:
  security-headers.conf: |
    # Security header configuration
    add_header X-Frame-Options "DENY";
    add_header X-Content-Type-Options "nosniff";
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: api-gateway
  template:
    metadata:
      labels:
        app: api-gateway
    spec:
      volumes:
      - name: header-config
        configMap:
          name: security-headers
      containers:
      - name: nginx
        image: nginx:alpine
        volumeMounts:
        - name: header-config
          mountPath: /etc/nginx
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
NAME                           READY   STATUS             RESTARTS      AGE
api-gateway-69d59d4d9-6945m    0/1     CrashLoopBackOff   3 (30s ago)   80s
```

### 2. Check Logs

```bash
kubectl logs -n lab -l app=api-gateway
```
Output:
```text
2026/10/01 10:32:34 [emerg] 1#1: open() "/etc/nginx/nginx.conf" failed (2: No such file or directory)
nginx: [emerg] open() "/etc/nginx/nginx.conf" failed (2: No such file or directory)
```

---

## Root Cause

In Linux (and Docker/Kubernetes), mounting a volume over an existing directory **shadows (hides) all original files** that were in that directory in the container image.

The Nginx container image comes with default files:
- `/etc/nginx/nginx.conf`
- `/etc/nginx/mime.types`
- `/etc/nginx/conf.d/default.conf`

When the Deployment mounts `header-config` directly to `mountPath: /etc/nginx`:
1. The entire `/etc/nginx` directory is replaced by the contents of the ConfigMap.
2. The ConfigMap only contains one file: `security-headers.conf`.
3. The original `nginx.conf` is completely masked/wiped out.
4. Nginx tries to start, cannot find its core configuration file `/etc/nginx/nginx.conf`, and terminates with error code 1.

---

## Solution: Use `subPath`

To inject a single file from a ConfigMap or Secret into an existing directory **without** overwriting other files in that directory, use **`subPath`**:

```yaml
        volumeMounts:
        - name: header-config
          mountPath: /etc/nginx/conf.d/security-headers.conf  # Full path to target file
          subPath: security-headers.conf                    # Key inside the ConfigMap
```

### Fixed Manifest

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: api-gateway
  template:
    metadata:
      labels:
        app: api-gateway
    spec:
      volumes:
      - name: header-config
        configMap:
          name: security-headers
      containers:
      - name: nginx
        image: nginx:alpine
        volumeMounts:
        - name: header-config
          mountPath: /etc/nginx/conf.d/security-headers.conf
          subPath: security-headers.conf
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

Check pod status:
```bash
kubectl get pods -n lab
```
Output:
```text
NAME                           READY   STATUS    RESTARTS   AGE
api-gateway-7c489b6574-z2vqx   1/1     Running   0          5s
```

Verify that both original files and the new security file exist inside the container:
```bash
kubectl exec -n lab deploy/api-gateway -- ls -la /etc/nginx/conf.d
```
Output:
```text
default.conf
security-headers.conf
```

---

## Key Takeaways

1. **Standard `mountPath` (Directory Mount):**
   - Replaces the entire directory.
   - Any pre-existing files in the container image at that path are **hidden**.
2. **`mountPath` with `subPath` (Single File Mount):**
   - Injects *only* the specific file into the directory.
   - Preserves all pre-existing files alongside the newly mounted file.
   - Note: Files mounted with `subPath` do not receive automatic updates when the ConfigMap is edited (requires pod restart).
