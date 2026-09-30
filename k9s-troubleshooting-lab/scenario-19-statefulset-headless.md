# Scenario 19 — Redis Cluster: Replicas Cannot Find Primary

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Database Cluster Broken)**
> `redis-cluster` StatefulSet has 3 replicas but replica pods keep crashing. Only the primary (`redis-cluster-0`) is running. Session storage for the entire platform is degraded — only 1 of 3 shards serving data.

**Your job:** Triage, find why replicas cannot find the primary, fix it.

---

## Lab

```bash
# Deploy StatefulSet WITHOUT the required headless service
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-cluster
  namespace: lab
spec:
  serviceName: redis-cluster
  replicas: 3
  selector:
    matchLabels:
      app: redis-cluster
  template:
    metadata:
      labels:
        app: redis-cluster
    spec:
      containers:
      - name: redis
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          POD_INDEX=${HOSTNAME##*-}
          echo "Starting redis-cluster pod index: $POD_INDEX"
          if [ "$POD_INDEX" = "0" ]; then
            echo "Primary node ready"
            sleep 3600
          else
            echo "Replica checking primary..."
            nslookup redis-cluster-0.redis-cluster.lab.svc.cluster.local
            if [ $? -ne 0 ]; then
              echo "FATAL: Cannot reach primary node!"
              exit 1
            fi
            sleep 3600
          fi
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

## Questions to Answer Before Looking at Solution

1. `kubectl get pods -n lab` — which pods are Running and which are crashing?
2. `kubectl logs redis-cluster-1 -n lab` — what DNS error appears?
3. `NXDOMAIN` vs `connection timed out` — what is the difference?
4. What is a **headless service** and how is it different from a regular ClusterIP service?
5. Why does a StatefulSet specifically NEED a headless service?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The StatefulSet's `serviceName: redis-cluster` references a Service that does not exist. Without the headless Service, CoreDNS has no entries for individual pod DNS names like `redis-cluster-0.redis-cluster.lab.svc.cluster.local`. Replica pods cannot resolve the primary's address and crash.

### Smoking Gun

```bash
kubectl logs redis-cluster-1 -n lab
```
```
Starting redis-cluster pod index: 1
Replica checking primary...
** server can't find redis-cluster-0.redis-cluster.lab.svc.cluster.local: NXDOMAIN
FATAL: Cannot reach primary node!
```

`NXDOMAIN` = DNS works, the domain simply doesn't exist.
(vs `connection timed out` = DNS itself is blocked by NetworkPolicy)

### Fix — Create the Headless Service

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: redis-cluster
  namespace: lab
spec:
  clusterIP: None        # This makes it a Headless Service
  selector:
    app: redis-cluster
  ports:
  - port: 6379
EOF
```

Watch pods recover:
```bash
kubectl get pods -n lab -w
```

### Regular Service vs Headless Service

| | Regular Service (ClusterIP) | Headless Service (clusterIP: None) |
|-|---------------------------|-----------------------------------|
| DNS result | Single VIP: `10.43.x.x` | Individual pod IPs |
| Pod DNS | No individual names | `pod-0.svc.ns.svc.cluster.local` |
| Load balancing | Yes (kube-proxy) | No (app handles it) |
| Use case | Stateless apps | StatefulSets, databases, Kafka |

### StatefulSet Pod DNS Pattern

```
<pod-name>.<service-name>.<namespace>.svc.cluster.local

redis-cluster-0.redis-cluster.lab.svc.cluster.local → 10.42.1.15
redis-cluster-1.redis-cluster.lab.svc.cluster.local → 10.42.2.8
redis-cluster-2.redis-cluster.lab.svc.cluster.local → 10.42.3.4
```

This only works when a headless service with matching name exists.

### StatefulSet vs Deployment

| | Deployment | StatefulSet |
|--|-----------|-------------|
| Pod names | Random (`pod-7d8f9-abc`) | Ordered (`pod-0`, `pod-1`, `pod-2`) |
| Start order | Parallel (all at once) | Sequential (`0` → `1` → `2`) |
| Per-pod DNS | No | Yes — requires headless service |
| Storage | Shared or none | Each pod gets its own PVC |
| Use case | Stateless apps | Databases, Redis, Kafka, Elasticsearch |

### Key Lesson

StatefulSets always need a **headless service** for pod-to-pod DNS to work. Without it, replica pods cannot discover the primary by stable DNS name. This is one of the most common mistakes when migrating database workloads to Kubernetes.

</details>
