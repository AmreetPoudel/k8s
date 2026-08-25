# 05. Join Worker Nodes & Configure Master Taints

> **Target Nodes**: `worker-1` (`10.0.2.10`), `worker-2` (`10.0.2.11`), `worker-3` (`10.0.2.12`)  
> **VIP**: `10.0.1.100`  
> **Execution Mode**: Run as `root` (or `sudo -i`)

---

## Step 1: Install RKE2 Agent Binary on All 3 Workers

### 🎯 The Command (Run on `worker-1`, `worker-2`, and `worker-3`):
```bash
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="agent" sh -
```

### ❓ Why `agent` instead of `server`?
The `rke2-agent` binary installs **ONLY** the node execution components (`kubelet`, `containerd`, and `kube-proxy`). It does **NOT** run `etcd`, `kube-apiserver`, or `kube-scheduler`, keeping workers completely dedicated to application and storage workloads.

---

## Step 2: Configure Worker Nodes (`/etc/rancher/rke2/config.yaml`)

### 🎯 On `worker-1` (`10.0.2.10`):
```bash
mkdir -p /etc/rancher/rke2

cat <<EOF | tee /etc/rancher/rke2/config.yaml
server: "https://10.0.1.100:9345"
token: "K8sSecureEnterpriseToken2026!#"
node-ip: "10.0.2.10"
EOF

systemctl enable rke2-agent.service
systemctl start rke2-agent.service
```

---

### 🎯 On `worker-2` (`10.0.2.11`):
```bash
mkdir -p /etc/rancher/rke2

cat <<EOF | tee /etc/rancher/rke2/config.yaml
server: "https://10.0.1.100:9345"
token: "K8sSecureEnterpriseToken2026!#"
node-ip: "10.0.2.11"
EOF

systemctl enable rke2-agent.service
systemctl start rke2-agent.service
```

---

### 🎯 On `worker-3` (`10.0.2.12`):
```bash
mkdir -p /etc/rancher/rke2

cat <<EOF | tee /etc/rancher/rke2/config.yaml
server: "https://10.0.1.100:9345"
token: "K8sSecureEnterpriseToken2026!#"
node-ip: "10.0.2.12"
EOF

systemctl enable rke2-agent.service
systemctl start rke2-agent.service
```

---

## Step 3: Configure Master Node Taints & Worker Labels

Run these commands from **`master-1`** using `kubectl`:

### 🎯 1. Taint Master Nodes (Prevent App Pods from Contaminating Control Plane):
```bash
kubectl taint nodes master-1 node-role.kubernetes.io/control-plane:NoSchedule --overwrite
kubectl taint nodes master-2 node-role.kubernetes.io/control-plane:NoSchedule --overwrite
kubectl taint nodes master-3 node-role.kubernetes.io/control-plane:NoSchedule --overwrite
```

### ❓ Why are we doing this?
Master nodes host etcd and the Kubernetes API. If a memory-leaking application or high-CPU pod runs on a master, it can starve etcd of CPU/IOPS, triggering Raft heartbeat timeouts and taking down the entire cluster.

### ⚠️ What happens if taints are omitted?
Application pods will be scheduled onto masters, increasing the risk of control-plane resource starvation and security exposure.

---

### 🎯 2. Label Worker Nodes:
```bash
kubectl label nodes worker-1 node-role.kubernetes.io/worker=worker --overwrite
kubectl label nodes worker-2 node-role.kubernetes.io/worker=worker --overwrite
kubectl label nodes worker-3 node-role.kubernetes.io/worker=worker --overwrite
```

---

## ✅ Step 4: Verification of 6-Node Cluster State

Run this command from `master-1`:

```bash
kubectl get nodes -o wide
```

### 📋 Expected Production Output:
```text
NAME       STATUS   ROLES                       AGE   VERSION   INTERNAL-IP   OS-IMAGE             KERNEL-VERSION
master-1   Ready    control-plane,etcd,master   25m   v1.30.x   10.0.1.10     Ubuntu 24.04.1 LTS   6.8.0-xx-generic
master-2   Ready    control-plane,etcd,master   20m   v1.30.x   10.0.1.11     Ubuntu 24.04.1 LTS   6.8.0-xx-generic
master-3   Ready    control-plane,etcd,master   15m   v1.30.x   10.0.1.12     Ubuntu 24.04.1 LTS   6.8.0-xx-generic
worker-1   Ready    worker                      10m   v1.30.x   10.0.2.10     Ubuntu 24.04.1 LTS   6.8.0-xx-generic
worker-2   Ready    worker                      8m    v1.30.x   10.0.2.11     Ubuntu 24.04.1 LTS   6.8.0-xx-generic
worker-3   Ready    worker                      5m    v1.30.x   10.0.2.12     Ubuntu 24.04.1 LTS   6.8.0-xx-generic
```

Once all 6 nodes show `Ready`, proceed to **[06_longhorn_distributed_storage.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/06_longhorn_distributed_storage.md)**!
