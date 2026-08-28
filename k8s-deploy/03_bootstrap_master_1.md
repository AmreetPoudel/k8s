# 03. Bootstrap Control Plane Master-1 (Cluster Initialization)

> **Target Node**: `master-1` ONLY (`10.0.2.50`)  
> **VIP**: `10.0.2.60`  
> **Execution Mode**: Run as `root` (or `sudo -i`)

---

## Step 1: Install RKE2 Server Binary

### 🎯 The Command:
```bash
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="server" sh -
```

---

## Step 2: Create the Master RKE2 Configuration (`config.yaml`)

### 🎯 The Command:
```bash
mkdir -p /etc/rancher/rke2

cat <<EOF | tee /etc/rancher/rke2/config.yaml
# Control Plane & etcd Configuration
token: "K8sSecureEnterpriseToken2026!#"
node-ip: "10.0.2.50"
advertise-address: "10.0.2.50"
# NOTE: Do NOT set 'bind-address: 10.0.2.50' here! Omitting it lets RKE2 bind to 0.0.0.0
# so it accepts traffic on both physical IP (10.0.2.50) and Floating VIP (10.0.2.60).

# Subject Alternative Names (SANs) for TLS Certificates
tls-san:
  - "10.0.2.60"        # Floating VIP
  - "10.0.2.50"        # Master-1 Physical IP
  - "master-1"         # Master-1 Hostname
  - "k8s-vip.local"    # VIP Local DNS

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

> [!WARNING]
> ### 🛑 Critical HA Architectural Gotcha: Why We Omit `bind-address`
> * **The Symptom**: Joining nodes (`master-2`, `workers`) fail with `Connection refused on https://10.0.2.60:9345`.
> * **The Root Cause**: If you set `bind-address: "10.0.2.50"`, the Linux kernel binds RKE2 sockets exclusively to `10.0.2.50`. When traffic arrives destined for the secondary Floating VIP (`10.0.2.60`), the kernel sends a TCP RST (`Connection Refused`)!
> * **The Fix**: Leave `bind-address` omitted (defaults to `0.0.0.0`) so the supervisor daemon listens on all local and floating IP aliases.

---

## Step 3: Enable and Start the RKE2 Server Service

### 🎯 The Commands:
```bash
systemctl enable rke2-server.service
systemctl start rke2-server.service
```

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

# Point Kubeconfig to VIP 10.0.2.60 (instead of 127.0.0.1)
sed -i 's/127.0.0.1/10.0.2.60/g' /root/.kube/config

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

```bash
# 1. Check Node Status (Must show Ready)
kubectl get nodes -o wide

# 2. Check all System Pods are Running
kubectl get pods -A -o wide

# 3. Check etcd health
/var/lib/rancher/rke2/bin/crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock ps | grep etcd
```

Once Master 1 is in `Ready` state, proceed to **[04_join_masters_2_and_3.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/04_join_masters_2_and_3.md)**!
