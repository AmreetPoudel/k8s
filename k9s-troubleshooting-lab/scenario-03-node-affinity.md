# Scenario 03 — Checkout Gateway: No Pods Starting

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Stalled Deployment)**
> New `checkout-gateway` version was deployed 4 minutes ago. Zero pods are running. Traffic cannot be served. Checkout is completely stalled. No crash logs available.

**Your job:** Triage, find root cause, fix, and verify stability.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: checkout-gateway
  namespace: lab
  labels:
    app: checkout-gateway
spec:
  replicas: 3
  selector:
    matchLabels:
      app: checkout-gateway
  template:
    metadata:
      labels:
        app: checkout-gateway
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: disktype
                operator: In
                values:
                - nvme-ultra-fast
      containers:
      - name: gateway
        image: nginx:alpine
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 200m
            memory: 256Mi
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What is the pod STATUS? Is it `CrashLoopBackOff`, `Pending`, or something else?
2. Which Kubernetes component is responsible for this failure — Kubelet, Container Runtime, or Scheduler?
3. What does `kubectl describe pod` show in the Events section?
4. What is the difference between Node Affinity and Taints/Tolerations?
5. How do you fix this without changing the Deployment?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The Deployment requires nodes with label `disktype=nvme-ultra-fast`. No nodes in the cluster have this label. The `kube-scheduler` cannot find a valid node to place the pods, so they remain in `Pending` state indefinitely.

### Smoking Gun

```bash
kubectl describe pod <pod-name> -n lab
```

Look at **Events**:
```
Warning  FailedScheduling  default-scheduler
0/6 nodes are available: 6 node(s) didn't match Pod's node affinity/selector.
```

The Scheduler tried all 6 nodes and found none with the required label.

### Fix Option 1: Add the label to a worker node

```bash
kubectl label node worker-1 disktype=nvme-ultra-fast
```

Watch the pods schedule immediately:
```bash
kubectl get pods -n lab -w
```

### Fix Option 2: Remove or change the affinity rule

```bash
kubectl edit deployment checkout-gateway -n lab
# Remove the affinity block or change to preferredDuringSchedulingIgnoredDuringExecution
```

### Cleanup label after lab

```bash
kubectl label node worker-1 disktype-
```

### Node Affinity vs Taints/Tolerations

| Concept | Who defines it | Direction | Effect if no match |
|---------|---------------|-----------|-------------------|
| **Node Affinity** | Pod spec | Pod → chooses Node | Pod stays `Pending` |
| **Taint** | Node | Node → repels Pods | Pod not scheduled on node |
| **Toleration** | Pod spec | Pod → bypasses Taint | Pod allowed on tainted node |

### Key Lesson

`Pending` status = the **Scheduler** could not find a valid node.
Always check `kubectl describe pod` Events — the scheduler explains exactly WHY it rejected each node.

</details>
