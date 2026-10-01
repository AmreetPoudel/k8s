# Scenario 27 — Edge Router: hostPort Port Collision (Pods Stuck in Pending)

## 🚨 Incident Alert (PagerDuty)

> **SEV-2 (Scaling Failure)**  
> Infrastructure team scaled the `edge-router` deployment to 8 replicas to handle incoming traffic spikes. However, only 3 pods are running and 5 pods remain stuck in `Pending` state. The cluster has ample CPU and memory, but the scheduler refuses to schedule the remaining pods.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: edge-router
  namespace: lab
spec:
  replicas: 8
  selector:
    matchLabels:
      app: edge-router
  template:
    metadata:
      labels:
        app: edge-router
    spec:
      containers:
      - name: router
        image: busybox:1.36
        command: ["sh", "-c", "httpd -f -p 8080 -h /"]
        ports:
        - containerPort: 8080
          hostPort: 9099
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

### 1. Check Pods Across Nodes

```bash
kubectl get pods -n lab -o wide
```
Output:
```text
NAME                           READY   STATUS    RESTARTS   AGE    IP           NODE
edge-router-767bdc55f6-5g8q2   1/1     Running   0          2m     10.42.5.69   worker-2
edge-router-767bdc55f6-5qzx4   0/1     Pending   0          2m     <none>       <none>
edge-router-767bdc55f6-f5pt9   0/1     Pending   0          2m     <none>       <none>
edge-router-767bdc55f6-qldf6   1/1     Running   0          2m     10.42.4.82   worker-3
edge-router-767bdc55f6-rzmlq   0/1     Pending   0          2m     <none>       <none>
edge-router-767bdc55f6-txqkd   0/1     Pending   0          2m     <none>       <none>
edge-router-767bdc55f6-vqcht   1/1     Running   0          2m     10.42.3.56   worker-1
edge-router-767bdc55f6-xbtfz   0/1     Pending   0          2m     <none>       <none>
```
Notice: Exactly **one pod** is running on `worker-1`, **one** on `worker-2`, and **one** on `worker-3`. All other replicas are `Pending`.

### 2. Check Scheduler Events on a Pending Pod

```bash
kubectl describe pod edge-router-767bdc55f6-5qzx4 -n lab
```
Output:
```text
Events:
  Type     Reason            Age   From               Message
  ----     ------            ----  ----               -------
  Warning  FailedScheduling  2m    default-scheduler  0/6 nodes are available: 3 node(s) didn't have free ports for the requested pod ports, 3 node(s) had untolerated taint(s).
```

---

## Root Cause

The Pod spec defines:
```yaml
ports:
- containerPort: 8080
  hostPort: 9099
```

`hostPort` binds the container port directly to the **physical host node's IP address** on port `9099`.

In Linux networking, only **one single process/container can bind to a specific TCP port on an IP address at a time**. 

In this cluster:
- 3 control-plane nodes have master taints (cannot run normal pods).
- 3 worker nodes (`worker-1`, `worker-2`, `worker-3`) each have 1 pod binding host port `9099`.
- The remaining 5 pods cannot be scheduled anywhere because port `9099` is already occupied on all 3 available worker nodes.

---

## Solution: Use a Kubernetes Service (Remove `hostPort`)

In Kubernetes, you should almost never use `hostPort` for application deployments.

Instead:
1. **Remove `hostPort`** from the Deployment.
2. Expose the deployment using a **Service** (ClusterIP, NodePort, or LoadBalancer).

A Service provides virtual load balancing across any number of replicas, even when multiple replicas run on the exact same worker node!

### Fixed Manifest

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: edge-router
  namespace: lab
spec:
  replicas: 8
  selector:
    matchLabels:
      app: edge-router
  template:
    metadata:
      labels:
        app: edge-router
    spec:
      containers:
      - name: router
        image: busybox:1.36
        command: ["sh", "-c", "httpd -f -p 8080 -h /"]
        ports:
        - containerPort: 8080       # <-- hostPort removed!
        resources:
          requests:
            cpu: "50m"
            memory: "32Mi"
          limits:
            cpu: "100m"
            memory: "64Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: edge-router
  namespace: lab
spec:
  type: ClusterIP
  selector:
    app: edge-router
  ports:
  - port: 80
    targetPort: 8080
EOF
```

### Verification

Check pods:
```bash
kubectl get pods -n lab
```
Output:
```text
NAME                           READY   STATUS    RESTARTS   AGE
edge-router-54bf9cf784-2vxlk   1/1     Running   0          5s
edge-router-54bf9cf784-4k9pm   1/1     Running   0          5s
edge-router-54bf9cf784-6lxnm   1/1     Running   0          5s
edge-router-54bf9cf784-9zq2p   1/1     Running   0          5s
edge-router-54bf9cf784-bf5ql   1/1     Running   0          5s
edge-router-54bf9cf784-j8rzt   1/1     Running   0          5s
edge-router-54bf9cf784-ptx99   1/1     Running   0          5s
edge-router-54bf9cf784-w5lqz   1/1     Running   0          5s
```
All 8 pods schedule and run cleanly across the worker nodes!

---

## Key Takeaways

| Port Type | Scope | Can Multiple Replicas Share a Node? |
| :--- | :--- | :---: |
| **`containerPort`** | Inside the Pod's isolated network namespace | **YES** (Each pod has its own unique Pod IP) |
| **`hostPort`** | Binds to Node's physical IP | **NO** (Max 1 pod per node per host port) |
| **`NodePort` (Service)** | Kube-proxy opens port on every node and proxies to Pods | **YES** |
