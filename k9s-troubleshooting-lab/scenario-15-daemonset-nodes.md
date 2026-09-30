# Scenario 15 — Log Collector: Missing from 3 Nodes

## 🚨 Incident Alert (PagerDuty)

> **SEV-2 (Monitoring Gap)**
> The `log-collector` agent is supposed to run on ALL nodes. Infra team reports logs are missing from 3 out of 6 nodes. Applications on those nodes are completely invisible to the logging system.

**Your job:** Triage, find root cause, fix so it runs on all 6 nodes.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: log-collector
  namespace: lab
  labels:
    app: log-collector
spec:
  selector:
    matchLabels:
      app: log-collector
  template:
    metadata:
      labels:
        app: log-collector
    spec:
      nodeSelector:
        node-role: worker
      containers:
      - name: collector
        image: busybox:1.36
        command: ["sh", "-c", "echo 'Log collector running'; sleep 3600"]
        resources:
          requests:
            cpu: "10m"
            memory: "32Mi"
          limits:
            cpu: "50m"
            memory: "64Mi"
EOF
```

---

## Questions to Answer Before Looking at Solution

1. Run `kubectl get pods -n lab -o wide`. Which nodes have a pod? Which don't?
2. What does a DaemonSet do differently from a Deployment?
3. What in the manifest restricts which nodes get a pod?
4. Why won't removing `nodeSelector` alone fix it for master nodes?
5. What are the two things needed to run on ALL 6 nodes?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

Two separate issues preventing pods from running on all 6 nodes:

1. `nodeSelector: node-role: worker` — only nodes with this label get a pod. Master nodes don't have this label.
2. Master nodes have taint `node-role.kubernetes.io/control-plane:NoSchedule` — even without the nodeSelector, pods won't land on masters without an explicit toleration.

### Smoking Gun

```bash
kubectl get pods -n lab -o wide
```
```
NAME                 NODE       STATUS
log-collector-xxx    worker-1   Running
log-collector-xxx    worker-2   Running
log-collector-xxx    worker-3   Running
# master-1, master-2, master-3 have NO pods!
```

```bash
kubectl describe node master-1 | grep Taints
```
```
Taints: node-role.kubernetes.io/control-plane:NoSchedule
```

### Fix — Remove nodeSelector + Add Toleration

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: log-collector
  namespace: lab
  labels:
    app: log-collector
spec:
  selector:
    matchLabels:
      app: log-collector
  template:
    metadata:
      labels:
        app: log-collector
    spec:
      tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule
      containers:
      - name: collector
        image: busybox:1.36
        command: ["sh", "-c", "echo 'Log collector running'; sleep 3600"]
        resources:
          requests:
            cpu: "10m"
            memory: "32Mi"
          limits:
            cpu: "50m"
            memory: "64Mi"
EOF
```

### Tolerate ALL taints (run on every node no matter what)

```yaml
tolerations:
- operator: Exists   # No key specified = tolerate every taint on any node
```

### DaemonSet vs Deployment

| | DaemonSet | Deployment |
|-|-----------|------------|
| **Pod count** | 1 per node (automatically) | Fixed replicas count |
| **Use case** | Node-level agents (logs, metrics, network) | Application workloads |
| **Scaling** | Automatic — new node = new pod | Manual or HPA |
| **Examples** | Fluentd, Datadog agent, node-exporter, Canal CNI | nginx, API servers, workers |

### Key Rule

```
Worker nodes → No taints → Pod schedules freely ✅
Master nodes → Has taint: control-plane:NoSchedule → Must tolerate it ✅
```

DaemonSets still respect:
- `nodeSelector` — must match
- `nodeAffinity` — must match
- Taints — must be tolerated

There is no "deploy everywhere no matter what" flag. The closest is `tolerations: [{operator: Exists}]`.

</details>
