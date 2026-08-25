# 04. Join Control Plane Masters 2 & 3 (Forming 3-Node Raft Quorum)

> **Target Nodes**: `master-2` (`10.0.1.11`) and `master-3` (`10.0.1.12`)  
> **VIP**: `10.0.1.100`  
> **Execution Rule**: Join nodes **ONE BY ONE** (Join Master-2 $\rightarrow$ verify Ready $\rightarrow$ Join Master-3).

---

## ⚠️ The Golden Rule: Why Sequential Joining is Mandatory
In Raft consensus, adding a new member requires the current leader to update cluster membership and commit a new configuration entry across quorum. **If you start Master-2 and Master-3 at the exact same millisecond, they both attempt to join concurrently, causing an etcd configuration lock contention.** Always start Master-2 first, confirm it reaches `Ready`, then start Master-3.

---

## Step 1: Install RKE2 Server Binary on Master-2 and Master-3

### 🎯 The Command (Run on both `master-2` and `master-3`):
```bash
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="server" sh -
```

---

## Step 2: Configure Master-2 (`10.0.1.11`)

### 🎯 Run on `master-2`:
```bash
mkdir -p /etc/rancher/rke2

cat <<EOF | tee /etc/rancher/rke2/config.yaml
# Connect to Cluster via Keepalived VIP on Supervisor Port 9345
server: "https://10.0.1.100:9345"
token: "K8sSecureEnterpriseToken2026!#"

# Node Network Bindings
node-ip: "10.0.1.11"
bind-address: "10.0.1.11"
advertise-address: "10.0.1.11"

# Subject Alternative Names (SANs)
tls-san:
  - "10.0.1.100"
  - "10.0.1.11"
  - "master-2"
  - "k8s.internal"

cni:
  - "canal"

disable:
  - "rke2-ingress-nginx"

write-kubeconfig-mode: "0600"
EOF
```

### 🎯 Start Master-2:
```bash
systemctl enable rke2-server.service
systemctl start rke2-server.service
```

---

## Step 3: Configure Master-3 (`10.0.1.12`)

### 🎯 Run on `master-3`:
```bash
mkdir -p /etc/rancher/rke2

cat <<EOF | tee /etc/rancher/rke2/config.yaml
# Connect to Cluster via Keepalived VIP on Supervisor Port 9345
server: "https://10.0.1.100:9345"
token: "K8sSecureEnterpriseToken2026!#"

# Node Network Bindings
node-ip: "10.0.1.12"
bind-address: "10.0.1.12"
advertise-address: "10.0.1.12"

# Subject Alternative Names (SANs)
tls-san:
  - "10.0.1.100"
  - "10.0.1.12"
  - "master-3"
  - "k8s.internal"

cni:
  - "canal"

disable:
  - "rke2-ingress-nginx"

write-kubeconfig-mode: "0600"
EOF
```

### 🎯 Start Master-3:
```bash
systemctl enable rke2-server.service
systemctl start rke2-server.service
```

---

## Step 4: Configure CLI Environment on Master-2 and Master-3

Run this on both `master-2` and `master-3`:
```bash
ln -sf /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl
ln -sf /var/lib/rancher/rke2/bin/crictl /usr/local/bin/crictl

mkdir -p /root/.kube
cp /etc/rancher/rke2/rke2.yaml /root/.kube/config
sed -i 's/127.0.0.1/10.0.1.100/g' /root/.kube/config
chmod 600 /root/.kube/config

cat <<'EOF' | tee /etc/profile.d/rke2.sh
export PATH=$PATH:/var/lib/rancher/rke2/bin
export KUBECONFIG=/root/.kube/config
source <(kubectl completion bash)
EOF

source /etc/profile.d/rke2.sh
```

---

## ✅ Step 5: Verification of 3-Node HA Control Plane & etcd Quorum

Run these verification commands from any master:

### 1. Check All 3 Masters are in `Ready` State:
```bash
kubectl get nodes -o wide
```
*Expected Output:*
```text
NAME       STATUS   ROLES                       AGE     VERSION   INTERNAL-IP
master-1   Ready    control-plane,etcd,master   10m     v1.30.x   10.0.1.10
master-2   Ready    control-plane,etcd,master   5m      v1.30.x   10.0.1.11
master-3   Ready    control-plane,etcd,master   2m      v1.30.x   10.0.1.12
```

---

### 2. Verify 3-Node etcd Quorum Membership:
```bash
ETCDCTL_API=3 /var/lib/rancher/rke2/bin/etcdctl \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  --endpoints=https://10.0.1.10:2379,https://10.0.1.11:2379,https://10.0.1.12:2379 \
  member list -w table
```

*Expected Output:*
```text
+------------------+---------+----------+------------------------+------------------------+------------+
|        ID        | STATUS  |   NAME   |       PEER URLS        |      CLIENT URLS       | IS LEARNER |
+------------------+---------+----------+------------------------+------------------------+------------+
| 3a5b28394f1c9012 | started | master-1 | https://10.0.1.10:2380 | https://10.0.1.10:2379 |      false |
| 7c9d12048e5a3194 | started | master-2 | https://10.0.1.11:2380 | https://10.0.1.11:2379 |      false |
| 9e4f88123c7b6401 | started | master-3 | https://10.0.1.12:2380 | https://10.0.1.12:2379 |      false |
+------------------+---------+----------+------------------------+------------------------+------------+
```

---

### 3. Verify Endpoint Health Across All 3 Members:
```bash
ETCDCTL_API=3 /var/lib/rancher/rke2/bin/etcdctl \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  --endpoints=https://10.0.1.10:2379,https://10.0.1.11:2379,https://10.0.1.12:2379 \
  endpoint health
```

*Expected Output:*
```text
https://10.0.1.10:2379 is healthy: successfully committed proposal: took = 2.15ms
https://10.0.1.11:2379 is healthy: successfully committed proposal: took = 2.31ms
https://10.0.1.12:2379 is healthy: successfully committed proposal: took = 1.98ms
```

Once all 3 masters and etcd endpoints are verified healthy, proceed to **[05_join_worker_nodes.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/05_join_worker_nodes.md)**!
