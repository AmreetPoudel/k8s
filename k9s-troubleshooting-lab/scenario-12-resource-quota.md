# Scenario 12 — Payment Worker: Deployment Completely Blocked

## 🚨 Incident Alert (PagerDuty)

> **SEV-2 (Deployment Blocked)**
> Backend team deployed `payment-worker` 10 minutes ago. Deployment shows `0/3` ready. Only partial pods are running. Payment processing queue is backing up rapidly.

**Your job:** Triage, find root cause, explain the fix.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: ResourceQuota
metadata:
  name: lab-quota
  namespace: lab
spec:
  hard:
    pods: "2"
    requests.cpu: "500m"
    requests.memory: "512Mi"
    limits.cpu: "1"
    limits.memory: "1Gi"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-worker
  namespace: lab
  labels:
    app: payment-worker
spec:
  replicas: 3
  selector:
    matchLabels:
      app: payment-worker
  template:
    metadata:
      labels:
        app: payment-worker
    spec:
      containers:
      - name: worker
        image: nginx:alpine
        resources:
          requests:
            cpu: "200m"
            memory: "200Mi"
          limits:
            cpu: "400m"
            memory: "400Mi"
EOF
```

---

## Questions to Answer Before Looking at Solution

1. Do pods exist at all in `kubectl get pods -n lab`?
2. What does `kubectl describe replicaset -n lab` show in Events?
3. What resource object is enforcing limits on this namespace?
4. Do the math: quota says `pods: "2"`, deployment wants `replicas: 3`. What happens?
5. What is the fix?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

A `ResourceQuota` named `lab-quota` limits the namespace to a maximum of `2 pods`. The Deployment requests `3 replicas`. Kubernetes creates 2 pods successfully, then the ReplicaSet controller tries to create the 3rd pod and the API server rejects it — quota exceeded.

### Smoking Gun

```bash
kubectl describe replicaset -n lab
```
```
Events:
  Warning  FailedCreate  replicaset-controller
  Error creating: pods "payment-worker-xxx" is forbidden:
  exceeded quota: lab-quota,
  requested: pods=1,
  used: pods=2,
  limited: pods=2
```

```bash
kubectl describe resourcequota lab-quota -n lab
```
```
Resource          Used   Hard
--------          ----   ----
limits.cpu        800m   1
limits.memory     800Mi  1Gi
pods              2      2      ← FULL!
requests.cpu      400m   500m
requests.memory   400Mi  512Mi
```

### Fix Options

**Option 1:** Reduce replicas to fit within quota:
```bash
kubectl scale deployment payment-worker -n lab --replicas=2
```

**Option 2:** Increase the quota (requires cluster-admin):
```bash
kubectl patch resourcequota lab-quota -n lab \
  --patch '{"spec":{"hard":{"pods":"5"}}}'
```

### Why `kubectl get all` Misses This

`kubectl get all` does NOT show ResourceQuota. Always check:
```bash
kubectl get resourcequota -n <namespace>
kubectl describe resourcequota -n <namespace>
```

### ResourceQuota in Production

Platform/infra teams use ResourceQuota to:
- Prevent one team from consuming all cluster resources
- Enforce per-namespace cost allocation
- Protect shared clusters from runaway deployments

When engineers say *"my pods aren't creating and I don't know why"* — ResourceQuota exhaustion is always on the checklist.

### Key Lesson

The Deployment and ReplicaSet do their jobs correctly — they request pod creation. The **API server** rejects creation at the quota enforcement layer. This is why the failure shows in ReplicaSet events, not Pod events (pods were never created).

</details>
