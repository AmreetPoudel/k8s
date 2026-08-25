# 01. Node Preparation & Linux Kernel Hardening

> **Target Nodes**: ALL 6 Nodes (`master-1`, `master-2`, `master-3`, `worker-1`, `worker-2`, `worker-3`)  
> **Operating System**: Ubuntu 24.04 LTS / 22.04 LTS  
> **Execution Mode**: Run as `root` (or `sudo -i`) on all nodes simultaneously.

---

## Step 1: Disable Linux Swap Memory

### 🎯 The Commands:
```bash
swapoff -a
sed -i '/\sswap\s/d' /etc/fstab
```

### ❓ Why are we doing this?
Kubernetes assigns pods specific memory requests and limits using Linux **cgroups** (`memory.max` and `memory.high`). Swap space undermines these guarantees by allowing memory-leaking containers to page anonymous memory to disk instead of being terminated by the kernel OOM-killer.

### ⚖️ Is it even necessary?
**YES, 100% MANDATORY.** By default, Kubelet will refuse to start on Linux if swap is enabled (unless explicitly overridden with experimental `--fail-swap-on=false`).

### ⚠️ What happens if this command is NOT run?
1. **Immediate Failure**: `rke2-server` or `rke2-agent` systemd service fails to start with error:  
   `Error: running with swap on is not supported, please disable swap!`.
2. **Hidden Latency Degradation**: If forced to run, disk paging creates massive CPU I/O wait latency, causing false liveness probe failures and cascading pod eviction loops across your cluster.

---

## Step 2: Load Required Linux Kernel Modules (`overlay` & `br_netfilter`)

### 🎯 The Commands:
```bash
cat <<EOF | tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF

modprobe overlay
modprobe br_netfilter
```

### ❓ Why are we doing this?
1. **`overlay`**: Enables OverlayFS, the union filesystem that layers container image layers (`lowerdir`) with writable container storage (`upperdir`) using Copy-on-Write (CoW).
2. **`br_netfilter`**: Installs a kernel hook that forces Layer-2 bridged packets passing through the virtual switch (`cni0`) to be inspected by Layer-3/4 `iptables`/`netfilter`.

### ⚖️ Is it even necessary?
**YES, 100% MANDATORY.** Without these two kernel modules, container runtimes (containerd) cannot start images, and Kubernetes Services cannot route traffic.

### ⚠️ What happens if this command is NOT run?
1. **If `overlay` is missing**: Containerd fails to unpack container images, throwing:  
   `failed to mount overlay: no such device`.
2. **If `br_netfilter` is missing**: Pods on the same worker node can ping each other directly, but **all calls to ClusterIP Services (`http://backend-service:8080`) silently hang and time out**. The bridge bypasses `iptables`, so kube-proxy's DNAT rule never rewrites the phantom Service IP into the real Pod IP!

---

## Step 3: Configure Linux Kernel Network Parameters (`sysctl`)

### 🎯 The Commands:
```bash
cat <<EOF | tee /etc/sysctl.d/99-kubernetes.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
net.netfilter.nf_conntrack_max      = 1048576
vm.max_map_count                    = 262144
fs.inotify.max_user_watches         = 524288
fs.inotify.max_user_instances       = 8192
EOF

sysctl --system
```

### ❓ Why are we doing this?
* `net.ipv4.ip_forward = 1`: Allows the Linux host to act as a router and forward packets between physical interfaces (`eth0`) and virtual pod interfaces (`cni0`/`flannel.1`).
* `net.bridge.bridge-nf-call-iptables = 1`: Activates the `br_netfilter` hook so IPv4 bridge traffic passes through `iptables PREROUTING`.
* `net.netfilter.nf_conntrack_max = 1048576`: Expands the connection tracking table to handle up to 1 million concurrent microservice connections without dropping packets.
* `vm.max_map_count = 262144` & `fs.inotify.*`: Increases memory map and file watcher limits required by databases (Elasticsearch, PostgreSQL, Kafka, and Longhorn).

### ⚖️ Is it even necessary?
**YES, 100% MANDATORY.** Linux defaults are tuned for desktop computers, not high-throughput container platforms.

### ⚠️ What happens if this command is NOT run?
1. **If `ip_forward = 0`**: Cross-node networking is completely dead. Packets arriving on `eth0` from other nodes cannot reach local pod interfaces.
2. **If `nf_conntrack_max` is at default (65,536)**: During high-traffic spikes, the kernel logs `nf_conntrack: table full, dropping packet`, causing random connection dropouts across the entire cluster.

---

## Step 4: Install Host Storage & System Dependencies (`open-iscsi`, `nfs-common`, `curl`, `jq`)

### 🎯 The Commands:
```bash
apt-get update -y
apt-get install -y open-iscsi nfs-common curl jq tar socat ebtables ipset chrony
systemctl enable --now iscsid
```

### ❓ Why are we doing this?
* **`open-iscsi` & `iscsid.service`**: Longhorn block storage connects distributed volumes to worker nodes over the Linux iSCSI protocol (`/dev/longhorn/`).
* **`nfs-common`**: Required for `ReadWriteMany` (RWX) shared storage mounts and backup targets.
* **`chrony`**: Enforces microsecond NTP clock synchronization across all nodes to keep etcd Raft heartbeats stable.
* **`ipset` & `ebtables`**: Required by Calico Felix to enforce NetworkPolicy rules at kernel speed.

### ⚖️ Is it even necessary?
**YES, 100% MANDATORY.** 

### ⚠️ What happens if this command is NOT run?
1. **If `iscsid` is not running**: Any Pod requesting persistent storage via Longhorn hangs permanently in `ContainerCreating` with error:  
   `MountVolume.SetUp failed for volume: iscsiadm: can not connect to iSCSI daemon`.
2. **If `chrony`/NTP is missing**: Node clocks drift by $>1$ second, causing etcd followers to trigger continuous leader election storms, taking down the Kubernetes API.

---

## Step 5: Configure Host Firewall Ports (UFW / Cloud Security Groups)

### 🎯 The Commands:
If UFW is enabled on Ubuntu:
```bash
# Allow SSH management
ufw allow 22/tcp

# Allow Control Plane & Worker communication
ufw allow 6443/tcp    # Kubernetes API Server
ufw allow 9345/tcp    # RKE2 Supervisor Bootstrap
ufw allow 2379:2380/tcp # etcd Client & Peer
ufw allow 10250/tcp   # Kubelet API
ufw allow 8472/udp    # Canal/Flannel VXLAN Overlay
ufw allow 9500:9502/tcp # Longhorn Engine & Replica Communication
ufw allow 80/tcp      # HTTP Ingress
ufw allow 443/tcp     # HTTPS Ingress
ufw allow proto vrrp  # Keepalived VRRP Protocol 112

# Enable and reload
ufw --force enable
ufw reload
```
*(Or in enterprise environments with perimeter firewalls, disable local UFW: `systemctl disable --now ufw`).*

### ❓ Why are we doing this?
Kubernetes components communicate across strict internal ports (API on `6443`, node bootstrap on `9345`, overlay encapsulation on `UDP 8472`, and Keepalived heartbeats on `VRRP 112`).

### ⚖️ Is it even necessary?
**YES.** If internal ports are blocked, nodes cannot discover each other or pass traffic.

### ⚠️ What happens if this command is NOT run?
1. **If `8472/udp` is blocked**: Pods on the same node talk fine, but cross-node pod-to-pod networking fails silently with 100% packet loss.
2. **If `9345/tcp` is blocked**: Worker nodes cannot join the cluster and fail with `connection refused to https://10.0.1.100:9345`.
3. **If `VRRP` is blocked**: Master-1 and Master-2 experience a split-brain condition and both try to claim the VIP `10.0.1.100` simultaneously.

---

## ✅ Step 6: Verification Checklist

Run this single validation test across all 6 nodes:

```bash
# 1. Verify swap is 0
free -m | grep -i swap
# Output must show: Swap: 0 0 0

# 2. Verify kernel modules are loaded
lsmod | grep -E 'overlay|br_netfilter'
# Output must list both modules!

# 3. Verify sysctl ip_forward
sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables
# Output must show: = 1 for both!

# 4. Verify iscsid is active
systemctl is-active iscsid
# Output must show: active
```

Once all 6 nodes pass this check, proceed to **[02_keepalived_ha_floating_vip.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/02_keepalived_ha_floating_vip.md)**!
