# Scenario 13 — Billing API: Crashing on Boot

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Complete Service Failure)**
> `billing-api` deployed 5 minutes ago. Pods are crashing immediately on startup. Billing service completely down. Finance team escalating.

**Your job:** Triage, find root cause, create the missing resource, verify service recovers.

---

## Lab

First apply this (no secret exists yet — that's the bug):

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: billing-api
  namespace: lab
  labels:
    app: billing-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: billing-api
  template:
    metadata:
      labels:
        app: billing-api
    spec:
      containers:
      - name: api
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Billing API starting..."
          echo "STRIPE_KEY: $STRIPE_SECRET_KEY"
          if [ -z "$STRIPE_SECRET_KEY" ]; then
            echo "FATAL: STRIPE_SECRET_KEY is not set!"
            exit 1
          fi
          echo "All config loaded. Starting server..."
          sleep 3600
        envFrom:
        - secretRef:
            name: billing-secrets
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

1. What is the pod STATUS? Note: it is different from `CrashLoopBackOff` — what is it?
2. What is `CreateContainerConfigError` and how is it different from `CrashLoopBackOff`?
3. What is the fix? What resource needs to be created?
4. After creating the resource, do the pods automatically pick it up or do you need to do something?
5. **Bonus:** What is the `--previous` flag on `kubectl logs` used for?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The container spec references `envFrom.secretRef.name: billing-secrets` but this Secret does not exist in the `lab` namespace. Kubernetes cannot construct the container's environment, so it fails before the container even starts.

### Smoking Gun — Status Difference

```bash
kubectl get pods -n lab
```
```
NAME                          READY   STATUS                       RESTARTS
billing-api-xxx               0/1     CreateContainerConfigError   0
```

`CreateContainerConfigError` ≠ `CrashLoopBackOff`!

| Status | What happened |
|--------|--------------|
| `CrashLoopBackOff` | Container **started**, ran, then **crashed**. App code ran. |
| `CreateContainerConfigError` | Container **never started**. Kubernetes failed to build the config (missing Secret/ConfigMap). |

```bash
kubectl describe pod <pod-name> -n lab
```
```
Events:
  Warning  Failed  kubelet
  Error: secret "billing-secrets" not found
```

### Fix — Create the Missing Secret

```bash
kubectl create secret generic billing-secrets -n lab \
  --from-literal=STRIPE_SECRET_KEY=sk_test_abc123 \
  --from-literal=DB_URL=postgres://billing:pass@db:5432/billing
```

### Restart Pods to Pick Up the Secret

Existing pods were created before the Secret existed — they need to be recreated:

```bash
kubectl rollout restart deployment/billing-api -n lab
```

### Verify

```bash
kubectl get pods -n lab
# 1/1 Running ✅

kubectl logs -n lab -l app=billing-api
# Billing API starting...
# STRIPE_KEY: sk_test_abc123
# All config loaded. Starting server...
```

### Inspect Secret Safely

```bash
kubectl describe secret billing-secrets -n lab
```
```
Data
====
DB_URL:              48 bytes    ← Key name + size shown, value hidden
STRIPE_SECRET_KEY:   14 bytes
```

Kubernetes never shows secret values in describe or get output. To decode:
```bash
kubectl get secret billing-secrets -n lab -o jsonpath='{.data.STRIPE_SECRET_KEY}' | base64 -d
```

### ⚠️ The Bash Heredoc Trap

When applying YAML with `kubectl apply -f - <<EOF`, bash expands `$VARIABLE` in your terminal session **before** sending to kubectl. If `$STRIPE_SECRET_KEY` is not set in your shell, it becomes empty in the YAML!

**Fix:** Use `\$STRIPE_SECRET_KEY` (escaped) or `<<'EOF'` (quoted delimiter) to prevent bash expansion.

### Key Lesson

`--previous` flag shows logs from the **previous crashed container instance**, not the current one:
```bash
kubectl logs <pod-name> --previous
```

Essential when a pod restarts — `kubectl logs` by default shows the NEW container's startup logs, not the last crash.

</details>
