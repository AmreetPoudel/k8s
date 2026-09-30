# Scenario 17 — Mailer Service: Crashing After ConfigMap Update

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Email Service Down)**
> `mailer-service` pods are crashing immediately after deployment. Team says no code changed — only a ConfigMap was updated by the infra team 10 minutes ago to change the SMTP host. Email sending is completely down.

**Your job:** Triage, find root cause, fix it.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: mailer-config
  namespace: lab
data:
  SMTP_PORT: "587"
  SMTP_USER: "noreply@company.com"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mailer-service
  namespace: lab
  labels:
    app: mailer-service
spec:
  replicas: 2
  selector:
    matchLabels:
      app: mailer-service
  template:
    metadata:
      labels:
        app: mailer-service
    spec:
      containers:
      - name: mailer
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Mailer starting..."
          echo "SMTP_HOST: $SMTP_HOST"
          echo "SMTP_PORT: $SMTP_PORT"
          if [ -z "$SMTP_HOST" ]; then
            echo "FATAL: SMTP_HOST is not configured!"
            exit 1
          fi
          echo "Mailer ready."
          sleep 3600
        env:
        - name: SMTP_HOST
          valueFrom:
            configMapKeyRef:
              name: mailer-config
              key: SMTP_HOSTNAME
        - name: SMTP_PORT
          valueFrom:
            configMapKeyRef:
              name: mailer-config
              key: SMTP_PORT
        resources:
          requests:
            cpu: "50m"
            memory: "64Mi"
          limits:
            cpu: "100m"
            memory: "128Mi"
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What is the pod status? `CrashLoopBackOff` or `CreateContainerConfigError`?
2. What does `kubectl describe pod` Events section say?
3. Run `kubectl describe configmap mailer-config -n lab`. What keys actually exist?
4. What key is the deployment referencing that does NOT exist?
5. What are the two ways to fix this?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The deployment references `configMapKeyRef.key: SMTP_HOSTNAME` but the ConfigMap only has `SMTP_PORT` and `SMTP_USER`. The key `SMTP_HOSTNAME` does not exist — Kubernetes cannot build the container environment, so it fails before the container starts.

### Smoking Gun

```bash
kubectl describe pod -n lab -l app=mailer-service
```
```
Events:
  Warning  Failed  kubelet
  Error: couldn't find key SMTP_HOSTNAME in ConfigMap lab/mailer-config
```

```bash
kubectl describe configmap mailer-config -n lab
```
```
Data
====
SMTP_PORT:   587
SMTP_USER:   noreply@company.com
# SMTP_HOSTNAME does not exist!
```

### Fix Option 1: Add the missing key to the ConfigMap

```bash
kubectl patch configmap mailer-config -n lab \
  --patch '{"data":{"SMTP_HOSTNAME":"smtp.company.com"}}'
```

Then restart pods to pick up the new key:
```bash
kubectl rollout restart deployment/mailer-service -n lab
```

### Fix Option 2: Fix the key reference in the deployment

```bash
kubectl edit deployment mailer-service -n lab
# Change: key: SMTP_HOSTNAME
# To:     key: SMTP_USER   (or whatever the correct key name is)
```

### Two Ways to Use ConfigMap in Pod

**Method 1: `configMapKeyRef` — individual key per env var**
```yaml
env:
- name: SMTP_HOST           # env var name inside container
  valueFrom:
    configMapKeyRef:
      name: mailer-config   # ConfigMap name
      key: SMTP_HOSTNAME    # MUST exactly match a key in ConfigMap data
```
Risk: Key name mismatch causes `CreateContainerConfigError`

**Method 2: `envFrom` — all keys at once**
```yaml
envFrom:
- configMapRef:
    name: mailer-config     # All keys → env vars automatically
```
No key mismatch possible. Every key in ConfigMap becomes env var.

### ConfigMap vs Secret — Same Behavior When Key Missing

| Resource | Key missing | Status |
|----------|------------|--------|
| ConfigMap | `configMapKeyRef` references missing key | `CreateContainerConfigError` |
| Secret | `secretKeyRef` references missing key | `CreateContainerConfigError` |
| ConfigMap | `configMapRef` (whole map) doesn't exist | `CreateContainerConfigError` |
| Secret | `secretRef` (whole secret) doesn't exist | `CreateContainerConfigError` |

### Key Lesson

When infra teams say *"we only updated the ConfigMap"* — always check if the **key names** in the ConfigMap still match what the deployment is referencing. A rename from `SMTP_HOSTNAME` to `SMTP_HOST` in the ConfigMap breaks all deployments referencing the old key name.

</details>
