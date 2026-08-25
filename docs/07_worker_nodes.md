# Doc 07: Worker Nodes
## Joining Workers and Configuring Workload Scheduling

---

## 7.1 Workers vs Masters — The Architecture Rule

In your cluster:
- **Masters**: Run etcd, API server, scheduler, controller manager. **No user workloads.**
- **Workers**: Run user workloads (pods). **No etcd, no control plane.**

This separation means: if your application crashes every pod on workers, the control plane keeps running. If you deployed everything on masters and a buggy deployment eats all CPU, the API server itself could die.

We enforce this with **Taints**.

---

## 7.2 What Are Taints and Tolerations?

**Taints** are on nodes. They repel pods.  
**Tolerations** are on pods. They allow pods to tolerate specific taints.

```
Taint format:  key=value:effect

Effects:
  NoSchedule     → New pods without toleration won't be scheduled here
  PreferNoSchedule → Scheduler tries to avoid this node but may still use it
  NoExecute      → New pods won't schedule + existing pods without toleration get EVICTED
```

Master nodes in Kubernetes are tainted with:
```
node-role.kubernetes.io/control-plane:NoSchedule
```

Only system pods (CoreDNS, Canal, metrics-server) have tolerations for this taint.  
Your application pods don't → they get scheduled on workers only.

💡 **Interview**: *"How do you prevent application pods from running on master nodes?"*  
→ "By applying a NoSchedule taint to master nodes: `kubectl taint node master-1 node-role.kubernetes.io/control-plane:NoSchedule`. This prevents any pod without a matching toleration from being scheduled there. The kube-system pods like CoreDNS and Canal have this toleration explicitly in their pod specs. Application deployments typically don't have this toleration, so they're automatically excluded from masters. This is separate from node roles — the taint is what enforces it mechanically."

---

## 7.3 Install RKE2 Agent on Workers

RKE2 on workers runs in **agent mode** — it starts kubelet and kube-proxy, but not etcd or API server.

```bash
# [ALL-W] — run on all 3 worker nodes
# Install RKE2 as agent (not server)
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="agent" sh -

# This installs:
# - /usr/local/bin/rke2 (same binary)
# - systemd unit: rke2-agent.service (different unit from server)
```

---

## 7.4 Configure Workers

```bash
# [W1] — worker-1 config
mkdir -p /etc/rancher/rke2

cat > /etc/rancher/rke2/config.yaml << 'EOF'
# Point to the VIP (now that keepalived is set up, use VIP)
# Port 9345 = RKE2 supervisor endpoint
server: https://10.0.1.100:9345

# Same token as masters
token: K10abc...::server:def456...    # ← paste your actual token

# This node's private IP
node-ip: 10.0.2.10

# Label this as a worker
node-label:
  - "role=worker"
  - "node-type=worker"
EOF
```

```bash
# [W2] — worker-2 config
mkdir -p /etc/rancher/rke2

cat > /etc/rancher/rke2/config.yaml << 'EOF'
server: https://10.0.1.100:9345
token: K10abc...::server:def456...
node-ip: 10.0.2.11
node-label:
  - "role=worker"
  - "node-type=worker"
EOF
```

```bash
# [W3] — worker-3 config
mkdir -p /etc/rancher/rke2

cat > /etc/rancher/rke2/config.yaml << 'EOF'
server: https://10.0.1.100:9345
token: K10abc...::server:def456...
node-ip: 10.0.2.12
node-label:
  - "role=worker"
  - "node-type=worker"
EOF
```

---

## 7.5 Start RKE2 Agent on Workers

```bash
# [ALL-W] — start the agent (run on each worker)
systemctl enable rke2-agent
systemctl start rke2-agent

# Watch logs
journalctl -u rke2-agent -f

# You'll see:
# Connecting to supervisor https://10.0.1.100:9345
# Successfully authenticated
# Running kubelet
# Node worker-1 registered with API server
```

### What the Agent Does on Startup

```
1. Contacts 10.0.1.100:9345 (VIP → master that owns it)
2. Authenticates with the token
3. Downloads: server CA certs, kubelet config, kubeconfig
4. Starts containerd
5. Starts kubelet (registers node with API server)
6. Starts kube-proxy (sets up iptables for Services)
7. CNI plugin sets up networking (Canal creates cali* interfaces)
8. Node becomes Ready
```

### Verify From Master-1

```bash
# [M1]
kubectl get nodes -o wide
# NAME       STATUS   ROLES                       AGE   VERSION   INTERNAL-IP
# master-1   Ready    control-plane,etcd,master   30m   v1.29.x   10.0.1.10
# master-2   Ready    control-plane,etcd,master   25m   v1.29.x   10.0.1.11
# master-3   Ready    control-plane,etcd,master   20m   v1.29.x   10.0.1.12
# worker-1   Ready    <none>                      5m    v1.29.x   10.0.2.10
# worker-2   Ready    <none>                      4m    v1.29.x   10.0.2.11
# worker-3   Ready    <none>                      3m    v1.29.x   10.0.2.12
```

---

## 7.6 Taint the Masters

```bash
# [M1] — apply NoSchedule taint to all 3 masters
kubectl taint nodes master-1 node-role.kubernetes.io/control-plane:NoSchedule
kubectl taint nodes master-2 node-role.kubernetes.io/control-plane:NoSchedule
kubectl taint nodes master-3 node-role.kubernetes.io/control-plane:NoSchedule

# Also taint as master (some helm charts check this)
kubectl taint nodes master-1 node-role.kubernetes.io/master:NoSchedule
kubectl taint nodes master-2 node-role.kubernetes.io/master:NoSchedule
kubectl taint nodes master-3 node-role.kubernetes.io/master:NoSchedule

# Verify taints
kubectl describe node master-1 | grep -A 5 "Taints:"
# Taints: node-role.kubernetes.io/control-plane:NoSchedule
#         node-role.kubernetes.io/master:NoSchedule
```

⚠️ **After applying taints, check that system pods still work.** Canal, CoreDNS, and metrics-server have tolerations for these taints and should continue running on masters. If something breaks, it means a kube-system pod was missing the toleration.

```bash
# [M1]
kubectl get pods -A -o wide
# All kube-system pods should still be Running
# Canal DaemonSet runs on ALL nodes (masters + workers) — it tolerates the taint
```

### Label Workers With Role

```bash
# [M1] — add the worker role label (for display purposes)
kubectl label nodes worker-1 node-role.kubernetes.io/worker=worker
kubectl label nodes worker-2 node-role.kubernetes.io/worker=worker
kubectl label nodes worker-3 node-role.kubernetes.io/worker=worker

kubectl get nodes
# Now workers show ROLES: worker
```

---

## 7.7 Test Workload Scheduling

```bash
# [M1] — deploy a test workload
cat << 'EOF' | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-nginx
  namespace: default
spec:
  replicas: 6   # enough to spread across all workers
  selector:
    matchLabels:
      app: test-nginx
  template:
    metadata:
      labels:
        app: test-nginx
    spec:
      # Ensure pods spread across nodes (anti-affinity)
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: test-nginx
      containers:
        - name: nginx
          image: nginx:stable
          ports:
            - containerPort: 80
          resources:
            requests:
              cpu: "100m"
              memory: "64Mi"
            limits:
              cpu: "200m"
              memory: "128Mi"
EOF

# Watch pods come up
kubectl get pods -o wide -w

# Verify all pods are on WORKERS only (not masters)
kubectl get pods -o wide | grep test-nginx
# NODE column should show: worker-1, worker-2, worker-3
# master-* should NOT appear

# Clean up
kubectl delete deployment test-nginx
```

### Test Cross-Node Communication

```bash
# [M1] — deploy pods on two different workers
cat << 'EOF' | kubectl apply -f -
---
apiVersion: v1
kind: Pod
metadata:
  name: pod-sender
  labels:
    app: sender
spec:
  nodeSelector:
    kubernetes.io/hostname: worker-1
  containers:
  - name: sender
    image: nicolaka/netshoot
    command: ["sleep", "3600"]
---
apiVersion: v1
kind: Pod
metadata:
  name: pod-receiver
  labels:
    app: receiver
spec:
  nodeSelector:
    kubernetes.io/hostname: worker-2
  containers:
  - name: receiver
    image: nginx:stable
EOF

# Wait for both to be Running
kubectl get pods -o wide

# Get pod-receiver's IP
kubectl get pod pod-receiver -o jsonpath='{.status.podIP}'
# Example: 10.42.2.5

# From pod-sender, ping pod-receiver (cross-node VXLAN test)
kubectl exec pod-sender -- ping -c 4 10.42.2.5
# Should succeed — proves Flannel VXLAN is working

# Also test DNS resolution
kubectl exec pod-sender -- nslookup kubernetes.default.svc.cluster.local
# Should resolve to 10.43.0.1 — proves CoreDNS is working

# Cleanup
kubectl delete pod pod-sender pod-receiver
```

---

## 7.8 Understanding Node Resources

```bash
# [M1] — see actual allocatable resources on each node
kubectl describe node worker-1 | grep -A 10 "Allocatable:"
# Allocatable:
#   cpu:                1900m     (1.9 cores — kernel/kubelet keep 100m)
#   memory:             7Gi       (some reserved for system)
#   ephemeral-storage:  45Gi
#   pods:               110       (max 110 pods per node by default)

# See how much of that is currently used
kubectl describe node worker-1 | grep -A 20 "Allocated resources:"
# Shows CPU and memory requests/limits currently on this node

# Easier summary of all nodes
kubectl top nodes   # requires metrics-server (pre-installed with RKE2)
```

🔍 **The 110 pod limit per node:**  
The default maximum is 110 pods per node (`--max-pods=110` in kubelet). This limit exists because of IP address management — each pod needs an IP from the node's /24 subnet (/24 = 254 addresses). Also, kubelet performance degrades with too many pods. In large clusters, this is a key capacity planning constraint.

💡 **Interview**: *"A deployment can't scale beyond a certain point. What are the potential causes?"*  
→ "Several possibilities: First, check node resource availability — `kubectl describe node` shows allocatable CPU/memory. If pods have resource requests, they might not fit. Second, check the node pod limit — each node defaults to 110 pods maximum. Third, check if pods have anti-affinity rules preventing them from co-scheduling. Fourth, check if there are taints on nodes that the pods don't tolerate. Fifth, check the PodDisruptionBudget if this is a scale-down. Use `kubectl get events` and `kubectl describe pod <failing-pod>` to see the exact reason the scheduler can't place pods."

---

## 7.9 Summary

You now have a **full 6-node RKE2 cluster**:
- ✅ 3 masters with etcd HA (Raft quorum)
- ✅ 3 workers ready for workloads
- ✅ Masters tainted — no user workloads run there
- ✅ keepalived VIP for API server HA
- ✅ Canal CNI — pod-to-pod and cross-node networking verified
- ✅ CoreDNS — DNS resolution verified

**Next**: [Doc 08 - RBAC & Security →](./08_rbac_and_security.md)  
How to control who can do what in your cluster. ServiceAccounts, Roles, ClusterRoles, audit logs, and CIS benchmark hardening.

---

*Doc 07 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
