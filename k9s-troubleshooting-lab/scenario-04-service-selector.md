# Scenario 04 — Payment API: HTTP 503 Service Unavailable

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Revenue Impact)**
> Frontend checkout is failing. Monitoring reports `payment-api` is returning connection errors. Pods appear healthy with 0 restarts. No crash logs. Customers cannot complete payments.

**Your job:** Triage, find root cause, fix, and verify stability.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-api
  namespace: lab
  labels:
    app: payment-api
    tier: backend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: payment-api
  template:
    metadata:
      labels:
        app: payment-api
        tier: backend
        version: v2
    spec:
      containers:
      - name: api
        image: hashicorp/http-echo:latest
        args: ["-text=Payment API v2 Ready", "-listen=:8080"]
        ports:
        - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: payment-api
  namespace: lab
spec:
  type: ClusterIP
  selector:
    app: payment-processor
  ports:
  - name: http
    port: 80
    targetPort: 8080
EOF
```

Test from inside the cluster:
```bash
kubectl run test-curl --rm -it -n lab --image=curlimages/curl -- curl -m 3 http://payment-api
```

---

## Questions to Answer Before Looking at Solution

1. What does `kubectl get pods -n lab` show? Are pods healthy?
2. What is the "missing bridge" between a Service and its Pods?
3. What command reveals whether the Service has any Pod IPs registered?
4. Compare the Service `spec.selector` with the Pod `metadata.labels` — what do you notice?
5. How do you fix it without redeploying?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The Service `spec.selector` is looking for pods with label `app: payment-processor`, but the actual pods have label `app: payment-api`. Because no pods match the selector, the **Endpoints** object is empty — traffic hits a dead end.

### Smoking Gun

```bash
kubectl get endpoints payment-api -n lab
```
```
NAME          ENDPOINTS   AGE
payment-api   <none>      2m
```

`<none>` = zero pods matched the Service selector.

```bash
# What the Service is looking for:
kubectl get svc payment-api -n lab -o jsonpath='{.spec.selector}'
# {"app":"payment-processor"}

# What the pods actually have:
kubectl get pods -n lab --show-labels
# app=payment-api,tier=backend,version=v2
```

Mismatch! `payment-processor` ≠ `payment-api`

### Fix

```bash
kubectl patch svc payment-api -n lab -p '{"spec":{"selector":{"app":"payment-api"}}}'
```

### Verify

```bash
kubectl get endpoints payment-api -n lab
# Should now show: 10.42.x.x:8080,10.42.x.x:8080

kubectl run test-curl --rm -it -n lab --image=curlimages/curl -- curl -m 3 http://payment-api
# Should return: Payment API v2 Ready
```

### How Services Find Pods (The Bridge)

```
Service (selector: app=payment-processor)
     │
     │ searches for pods with matching labels
     ▼
Endpoints object   ← auto-populated by kube-controller-manager
     │
     ▼
Pod (labels: app=payment-api)  ← MISMATCH! Not added to Endpoints
```

### Key Lesson

When pods are healthy but the Service returns 503:
1. **First check:** `kubectl get endpoints <svc-name> -n <ns>`
2. **If `<none>`:** Compare Service selector vs Pod labels
3. **Fix:** Match the selector to the actual pod labels

> **Warning:** `kubectl get all` does NOT show Endpoints. Always check explicitly with `kubectl get endpoints`.

</details>
