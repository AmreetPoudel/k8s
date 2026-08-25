# 03. Bootstrap Control Plane Master-1 (Cluster Initialization)

> **Target Node**: `master-1` ONLY (`10.0.1.10`)  
> **VIP**: `10.0.1.100`  
> **Execution Mode**: Run as `root` (or `sudo -i`)

---

## Step 1: Install RKE2 Server Binary

### 🎯 The Command:
```bash
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="server" sh -
```

### ❓ Why are we doing this?
Downloads and verifies the official Rancher RKE2 server binaries (systemd unit files, `containerd`, `crictl`, `kubectl`, and static pod definitions) into `/usr/local/bin/` and `/var/lib/rancher/rke2/`.

### ⚖️ Is it even necessary?
**YES, MANDATORY.** 

---

## Step 2: Create the Master RKE2 Configuration (`config.yaml`)

### 🎯 The Command:
```bash
mkdir -p /etc/rancher/rke2

cat <<EOF | tee /etc/rancher/rke2/config.yaml
# Control Plane & etcd Configuration
token: "K8sSecureEnterpriseToken2026!#"
node-ip: "10.0.1.10"
bind-address: "10.0.1.10"
advertise-address: "10.0.1.10"

# Subject Alternative Names (SANs) for TLS Certificates
tls-san:
  - "10.0.1.100"       # Floating VIP
  - "10.0.1.10"        # Master-1 Physical IP
  - "master-1"         # Master-1 Hostname
  - "k8s.internal"     # Internal DNS Domain

# CNI Plugin
cni:
  - "canal"

# Disable default bundled ingress so we can deploy custom MetalLB + NGINX Ingress
disable:
  - "rke2-ingress-nginx"

# Automated etcd Snapshots (Every 4 hours, retain 14 snapshots)
etcd-snapshot-schedule-cron: "0 */4 * * *"
etcd-snapshot-retention: 14

# Security Hardening Profile
write-kubeconfig-mode: "0600"
EOF
```

---

### 🔍 Deep Parameter Breakdown: Why Each Line Exists

#### 1. `tls-san: ["10.0.1.100"]`
* **❓ Why?** When the API server generates its server TLS certificate (`/var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt`), it embeds these IP/domain names into the certificate's SAN extension.
* **⚠️ What happens if missing?** When `kubectl` or worker nodes connect to `https://10.0.1.100:6443`, the TLS handshake **fails with fatal error: `x509: certificate is valid for 10.0.1.10, not 10.0.1.100`**!

#### 2. `disable: ["rke2-ingress-nginx"]`
* **❓ Why?** RKE2 bundles a default NGINX Ingress controller that binds directly to host ports `80` and `443` on masters. We disable it so we can deploy a dedicated production Ingress Controller backed by **MetalLB Layer-2 LoadBalancers** on worker nodes (Doc 07).
* **⚠️ What happens if missing?** Default ingress binds to ports 80/443 on the master nodes, creating port conflicts and preventing MetalLB ingress services from working properly.

#### 3. `cni: ["canal"]`
* **❓ Why?** Canal combines **Flannel's lightweight VXLAN overlay (UDP 8472)** for cross-node packet delivery with **Calico's policy engine (Felix)** for strict Layer-3/4 `NetworkPolicy` security filtering.
* **⚠️ What happens if missing?** Pods cannot communicate across nodes, and `NetworkPolicy` objects have no enforcement engine.

---

## Step 3: Enable and Start the RKE2 Server Service

### 🎯 The Commands:
```bash
systemctl enable rke2-server.service
systemctl start rke2-server.service
```

### 🔍 What happens under the hood during startup?
1. RKE2 extracts `containerd` runtime at `t=0`.
2. Spins up the first **etcd Member 1** static pod and generates the dedicated etcd CA in `/var/lib/rancher/rke2/server/tls/etcd/`.
3. Generates the Kubernetes Root CA in `/var/lib/rancher/rke2/server/tls/`.
4. Starts `kube-apiserver`, `kube-controller-manager`, and `kube-scheduler` as static pods.
5. Launches the **RKE2 Supervisor on port 9345**, listening for incoming join requests from Master 2 and Master 3.

---

## Step 4: Configure CLI Tools & Environment Variables

### 🎯 The Commands:
```bash
# Symlink kubectl, crictl, and ctr
ln -sf /var/lib/rancher/rke2/bin/kubectl /usr/local/bin/kubectl
ln -sf /var/lib/rancher/rke2/bin/crictl /usr/local/bin/crictl
ln -sf /var/lib/rancher/rke2/bin/ctr /usr/local/bin/ctr

# Configure kubeconfig for root user
mkdir -p /root/.kube
cp /etc/rancher/rke2/rke2.yaml /root/.kube/config
chmod 600 /root/.kube/config

# Point Kubeconfig to VIP 10.0.1.100 (instead of 127.0.0.1)
sed -i 's/127.0.0.1/10.0.1.100/g' /root/.kube/config

# Configure bash completion and PATH permanently
cat <<'EOF' | tee /etc/profile.d/rke2.sh
export PATH=$PATH:/var/lib/rancher/rke2/bin
export KUBECONFIG=/root/.kube/config
source <(kubectl completion bash)
EOF

source /etc/profile.d/rke2.sh
```

---

## ✅ Step 5: Verification Checklist

Run these commands to verify that Master 1 is healthy:

```bash
# 1. Check Node Status (Must show Ready)
kubectl get nodes -o wide

# 2. Check all System Pods are Running
kubectl get pods -A -o wide

# 3. Check etcd health
/var/lib/rancher/rke2/bin/crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock ps | grep etcd
```

Once Master 1 is in `Ready` state, proceed to **[04_join_masters_2_and_3.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/04_join_masters_2_and_3.md)**!
