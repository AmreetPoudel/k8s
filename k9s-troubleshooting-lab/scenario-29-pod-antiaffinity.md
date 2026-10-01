# Scenario 29 — Auth Service: Strict Pod Anti-Affinity (Pending Pods)

## 🚨 Incident Alert (PagerDuty)

> **SEV-2 (High-Availability Scaling Stalled)**  
> Security team deployed `auth-service` with 5 replicas for zero downtime. However, only 3 replicas are running, and 2 replicas remain permanently stuck in `Pending` status. Cluster resources are underutilized.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: auth-service
  namespace: lab
spec:
  replicas: 5
  selector:
    matchLabels:
      app: auth-service
  template:
    metadata:
      labels:
        app: auth-service
    spec:
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
          - labelSelector:
              matchExpressions:
              - key: app
                operator: In
                values:
                - auth-service
            topologyKey: "kubernetes.io/hostname"
      containers:
      - name: auth
        image: busybox:1.36
        command: ["sh", "-c", "echo 'Auth service running'; sleep 3600"]
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

### 1. Check Pod Placement

```bash
kubectl get pods -n lab -o wide
```
Output:
```text
NAME                            READY   STATUS    RESTARTS   AGE   IP           NODE
auth-service-7f45778bd5-2kxlm   1/1     Running   0          2m    10.42.3.88   worker-1
auth-service-7f45778bd5-5qztp   1/1     Running   0          2m    10.42.5.91   worker-2
auth-service-7f45778bd5-9mvzq   1/1     Running   0          2m    10.42.4.99   worker-3
auth-service-7f45778bd5-p8xql   0/1     Pending   0          2m    <none>       <none>
auth-service-7f45778bd5-w7ztr   0/1     Pending   0          2m    <none>       <none>
```
Notice: Exactly **one pod** is running per worker node. The 4th and 5th pods are `Pending`.

### 2. Check Scheduler Events on Pending Pod

```bash
kubectl describe pod auth-service-7f45778bd5-p8xql -n lab
```
Output:
```text
Events:
  Type     Reason            Age   From               Message
  ----     ------            ----  ----               -------
  Warning  FailedScheduling  2m    default-scheduler  0/6 nodes are available: 3 node(s) didn't match pod anti-affinity rules, 3 node(s) had untolerated taint(s).
```

---

## Root Cause

### What does "Anti-Affinity" Mean?

- **Affinity:** "I want to be *together* with pods having label X."
- **Anti-Affinity:** "I want to be **separated** from pods having label X. Keep us on different physical machines!"

In the manifest:
```yaml
affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector:
        matchExpressions:
        - key: app
          operator: In
          values:
          - auth-service
      topologyKey: "kubernetes.io/hostname"
```

This rule states:
> **"It is STRICTLY REQUIRED that no two pods with label `app=auth-service` ever run on the same node hostname."**

Because the cluster has only **3 worker nodes**:
- Pod 1 scheduled on `worker-1`.
- Pod 2 scheduled on `worker-2`.
- Pod 3 scheduled on `worker-3`.
- For Pod 4 and Pod 5, every single worker node already hosts an `auth-service` pod.
- Because the rule is `requiredDuringScheduling...` (Hard constraint), the scheduler refuses to schedule Pods 4 and 5, leaving them stuck in `Pending`.

---

## Solution: Hard vs. Soft Anti-Affinity

In Kubernetes High Availability (HA) design:

| Type | Syntax | Behavior |
| :--- | :--- | :--- |
| **Hard (Strict)** | `requiredDuringSchedulingIgnoredDuringExecution` | If not enough nodes exist, **pods stay Pending forever**. |
| **Soft (Best Effort)** | `preferredDuringSchedulingIgnoredDuringExecution` | Tries to spread across nodes; if nodes run out, **schedules anyway**! |

### Fixed Manifest: Use `preferredDuringScheduling`

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: auth-service
  namespace: lab
spec:
  replicas: 5
  selector:
    matchLabels:
      app: auth-service
  template:
    metadata:
      labels:
        app: auth-service
    spec:
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              labelSelector:
                matchExpressions:
                - key: app
                  operator: In
                  values:
                  - auth-service
              topologyKey: "kubernetes.io/hostname"
      containers:
      - name: auth
        image: busybox:1.36
        command: ["sh", "-c", "echo 'Auth service running'; sleep 3600"]
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
kubectl get pods -n lab -o wide
```
Output:
```text
NAME                            READY   STATUS    RESTARTS   AGE   IP           NODE
auth-service-598bc675b8-4klmn   1/1     Running   0          6s    10.42.3.89   worker-1
auth-service-598bc675b8-7qpmr   1/1     Running   0          6s    10.42.5.92   worker-2
auth-service-598bc675b8-c8xvl   1/1     Running   0          6s    10.42.4.100  worker-3
auth-service-598bc675b8-jv8tw   1/1     Running   0          6s    10.42.3.90   worker-1
auth-service-598bc675b8-x2lpq   1/1     Running   0          6s    10.42.5.93   worker-2
```
All **5/5** replicas are now `Running`! 
The scheduler spread them as much as possible (1 per node), and placed the remaining 2 on available worker nodes without stalling!

---

## Key Takeaways

1. **Avoid `requiredDuringScheduling` Anti-Affinity for scalable services:**
   - Unless you have an autoscaling node pool or a fixed replica count matching your node count.
2. **Use `preferredDuringScheduling` with a `weight: 100`:**
   - Gives you High Availability when nodes are available.
   - Prevents scaling lockups when replicas exceed node count.
