# Doc 02: Node Preparation
## Getting All 6 Servers Ready Before Touching RKE2

> **Run order**: Do everything in this doc on ALL 6 NODES (3 masters, 3 workers)  
> unless explicitly labeled `[M only]` or `[W only]`.

---

## 2.1 The Philosophy of Node Prep

Every step in this document exists because Kubernetes or RKE2 will **silently misbehave** if you skip it. Not fail loudly — silently misbehave. That's worse.

Examples of silent failures:
- Swap enabled → kubelet starts but memory pressure evictions behave wrong
- `br_netfilter` not loaded → pod-to-pod traffic drops packets silently
- Clock drift between nodes → etcd rejects writes (Raft requires time agreement)
- Wrong hostname → kubelet registers as wrong node name, confusing the API server

Let's prevent all of these.

---

## 2.2 Your Node Inventory

Before anything, know your nodes. Fill this in for your actual setup:

| Role | Hostname | Private IP | Public IP |
|------|----------|------------|-----------|
| master-1 | master-1 | 10.0.1.10 | x.x.x.x |
| master-2 | master-2 | 10.0.1.11 | x.x.x.x |
| master-3 | master-3 | 10.0.1.12 | x.x.x.x |
| master-VIP | — | 10.0.1.100 | — |
| worker-1 | worker-1 | 10.0.2.10 | — |
| worker-2 | worker-2 | 10.0.2.11 | — |
| worker-3 | worker-3 | 10.0.2.12 | — |

⚠️ **Replace all IPs in this doc with your actual IPs.** The IPs above are placeholders.

---

## 2.3 Initial SSH Setup

SSH into each node. If on AWS, use your keypair:

```bash
# [LOCAL] — from your laptop
ssh -i ~/.ssh/your-key.pem ubuntu@<public-ip-of-node>
```

First thing: become root or set up passwordless sudo. RKE2 requires root.

```bash
# [ALL] — on every node
sudo -i   # become root, stay root for all node prep
```

---

## 2.4 Set Hostnames

Kubernetes uses the node's **hostname** as the Node name in the cluster.  
If all nodes have hostname `ubuntu` (default on EC2), your cluster will have 6 nodes  
all trying to register as `ubuntu` — chaos ensues.

```bash
# [M1] — on master-1
hostnamectl set-hostname master-1

# [M2] — on master-2
hostnamectl set-hostname master-2

# [M3] — on master-3
hostnamectl set-hostname master-3

# [W1] — on worker-1
hostnamectl set-hostname worker-1

# [W2] — on worker-2
hostnamectl set-hostname worker-2

# [W3] — on worker-3
hostnamectl set-hostname worker-3
```

Verify:
```bash
# [ALL]
hostname
# Should return the hostname you just set
```

🔍 **Why hostname matters**: The kubelet registers itself with the API server using `--hostname-override` (or the system hostname if not set). This becomes the Node object name. If two nodes have the same hostname, the second one overwrites the first's registration — you lose a node silently.

---

## 2.5 Configure /etc/hosts

Nodes need to resolve each other's names. In production you'd use DNS. For this setup, we use `/etc/hosts` — simpler, no external dependency.

```bash
# [ALL] — add to /etc/hosts on EVERY node
cat >> /etc/hosts << 'EOF'

# RKE2 Cluster Nodes
10.0.1.10   master-1
10.0.1.11   master-2
10.0.1.12   master-3
10.0.1.100  master-vip   # keepalived VIP — masters will respond here
10.0.2.10   worker-1
10.0.2.11   worker-2
10.0.2.12   worker-3
EOF
```

Verify from master-1:
```bash
# [M1]
ping -c 2 master-2
ping -c 2 worker-1
```

⚠️ **If you're on AWS, use PRIVATE IPs in /etc/hosts.** Public IPs can change after restart. RKE2 will embed these IPs in TLS certificates — if the IP changes, certs become invalid and the cluster breaks.

---

## 2.6 Disable Swap

```bash
# [ALL]
swapoff -a

# Make it permanent (survive reboot)
sed -i '/\sswap\s/d' /etc/fstab

# Verify
free -h | grep Swap
# Should show: Swap:      0B      0B      0B
```

🔍 **Why no swap?**  
The kubelet calculates pod memory limits using cgroups. When swap is enabled, a pod that hits its memory limit can silently write to swap instead of being OOM-killed. This means:
1. The cgroup memory limit is bypassed
2. The pod runs slowly (swap is slow) but doesn't crash
3. Kubernetes thinks everything is fine
4. Your actual application is slow and no alert fires

kubelet by default will **refuse to start** if swap is enabled (unless you explicitly configure `--fail-swap-on=false`, which you should not do for production).

💡 **Interview**: *"Why does Kubernetes require swap to be disabled?"*  
→ "Kubernetes uses cgroup-based memory limits. Swap undermines this by allowing processes to exceed their memory limit by using swap space. kubelet can't accurately report memory usage or enforce limits when swap is active. Also, swap causes unpredictable latency spikes, which breaks health probes and causes false pod restarts. The kubelet refuses to start with swap enabled by default for these reasons."

---

## 2.7 Load Required Kernel Modules

Kubernetes networking (via CNI) requires specific kernel modules.

```bash
# [ALL]
# Load modules now (immediate effect)
modprobe overlay
modprobe br_netfilter

# Make modules load on boot
cat > /etc/modules-load.d/rke2.conf << 'EOF'
overlay
br_netfilter
EOF

# Verify they're loaded
lsmod | grep -E "overlay|br_netfilter"
# Should show both modules listed
```

🔍 **What these modules do:**

**`overlay`**: The overlay filesystem driver. containerd uses overlayfs to create container filesystems efficiently. Instead of copying an entire image for each container, it uses copy-on-write layering. Container reads from lower layers (image), writes go to an upper layer (container-specific). Without this, containerd falls back to slower storage drivers.

```
Container FS:  [your changes]  ← upper layer (writable)
               [image layer 3] ← lower layer (read-only)
               [image layer 2] ← lower layer (read-only)
               [image layer 1] ← lower layer (read-only)
```

**`br_netfilter`**: Makes the Linux kernel pass bridge traffic through iptables/netfilter. Without this, traffic between pods on the same node (which goes through a Linux bridge) bypasses iptables. This means:
- kube-proxy's iptables rules don't apply to pod traffic
- Service ClusterIP doesn't work for pods on the same node
- NetworkPolicy rules don't apply to same-node pod traffic

⚠️ **This is one of the most common "my Services don't work" bugs.** Always verify this module is loaded.

---

## 2.8 Configure Kernel Parameters (sysctl)

```bash
# [ALL]
cat > /etc/sysctl.d/99-rke2.conf << 'EOF'
# Allow iptables to see bridged traffic
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1

# Enable IP forwarding (needed for pod routing)
net.ipv4.ip_forward = 1

# Increase connection tracking table (for busy clusters)
net.netfilter.nf_conntrack_max = 131072

# Increase inotify limits (kubelet watches many files)
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 512

# Increase file descriptor limits
fs.file-max = 2097152

# TCP optimization
net.core.somaxconn = 32768
net.ipv4.tcp_max_syn_backlog = 8192
EOF

# Apply immediately
sysctl --system

# Verify key settings
sysctl net.ipv4.ip_forward
# Should show: net.ipv4.ip_forward = 1

sysctl net.bridge.bridge-nf-call-iptables
# Should show: net.bridge.bridge-nf-call-iptables = 1
```

🔍 **What each setting does:**

| Setting | Why It's Needed |
|---------|----------------|
| `bridge-nf-call-iptables = 1` | Required with `br_netfilter` — makes bridged pod traffic go through iptables so kube-proxy and NetworkPolicy work |
| `ip_forward = 1` | Linux by default doesn't forward packets between interfaces. This turns your node into a router — needed for pod-to-pod traffic across network namespaces |
| `nf_conntrack_max` | Linux tracks every active connection. In a busy cluster, thousands of pod connections can exhaust this table, causing "connection refused" errors that look like app bugs |
| `inotify.max_user_watches` | kubelet uses inotify to watch config files, certificates for changes. If too low, kubelet silently stops watching for cert rotations |
| `file-max` | Every container's stdout/stderr is a file descriptor. High pod density exhausts default limits |
| `somaxconn` | API server handles many concurrent connections. Low backlog drops connections during high load |

💡 **Interview**: *"What is conntrack and why does it matter for Kubernetes?"*  
→ "Conntrack (connection tracking) is a kernel module that tracks the state of network connections. kube-proxy uses it for DNAT — when a request hits a Service ClusterIP, kube-proxy's iptables rule DNATs it to a pod IP. Conntrack remembers this mapping so return packets get reverse-NATted correctly. In a large cluster, thousands of pod connections can exhaust the conntrack table, causing new connections to fail with 'nf_conntrack: table full, dropping packet' in dmesg. The fix is to increase `nf_conntrack_max`."

---

## 2.9 Configure Firewall Rules

⚠️ **Critical section.** RKE2 needs specific ports open. Wrong firewall = mysterious failures.

### Option A: Disable UFW entirely (simple, OK for learning)
```bash
# [ALL]
ufw disable
systemctl disable ufw
```

### Option B: Configure UFW properly (better practice)

If you keep UFW, open these ports:

```bash
# [ALL-M] — on all MASTER nodes
ufw allow 22/tcp           # SSH
ufw allow 6443/tcp         # Kubernetes API server (kubectl, kubelet)
ufw allow 9345/tcp         # RKE2 supervisor (node join endpoint)
ufw allow 2379/tcp         # etcd client (API server to etcd)
ufw allow 2380/tcp         # etcd peer (etcd to etcd between masters)
ufw allow 10250/tcp        # kubelet API (API server to kubelet, for exec/logs)
ufw allow 10257/tcp        # kube-controller-manager metrics
ufw allow 10259/tcp        # kube-scheduler metrics
ufw allow 8472/udp         # Flannel VXLAN (Canal CNI overlay)
ufw allow 51820/udp        # WireGuard (if using encrypted CNI)
ufw allow 179/tcp          # BGP (if using Calico BGP mode)
ufw allow 4789/udp         # VXLAN alternative port
ufw allow 5473/tcp         # Calico Typha (optional, for large clusters)
ufw enable

# [ALL-W] — on all WORKER nodes
ufw allow 22/tcp           # SSH
ufw allow 10250/tcp        # kubelet API
ufw allow 30000:32767/tcp  # NodePort range (external traffic to services)
ufw allow 8472/udp         # Flannel VXLAN
ufw allow 51820/udp        # WireGuard (if using encrypted CNI)
ufw enable
```

🔍 **Port breakdown:**

| Port | Protocol | Component | Why |
|------|----------|-----------|-----|
| 6443 | TCP | API server | Every kubectl command, all kubelet communication |
| 9345 | TCP | RKE2 supervisor | Node join/registration, health checks |
| 2379 | TCP | etcd client | API server reads/writes to etcd |
| 2380 | TCP | etcd peer | etcd nodes replicate to each other via Raft |
| 10250 | TCP | kubelet | API server contacts kubelet for exec, logs, port-forward |
| 8472 | UDP | Flannel VXLAN | Pod overlay traffic between nodes |
| 30000-32767 | TCP | NodePort | External traffic entering via NodePort services |

⚠️ **etcd ports 2379/2380 should ONLY be accessible between masters and from the API server.** These ports expose raw cluster state. In production, use a security group / firewall rule to restrict to master-subnet only.

---

## 2.10 Configure Time Synchronization

```bash
# [ALL]
# Check if chrony or systemd-timesyncd is running
systemctl status chronyd 2>/dev/null || systemctl status systemd-timesyncd

# For Ubuntu 24.04, systemd-timesyncd is default and sufficient
# Ensure it's running and synchronized
systemctl enable systemd-timesyncd
systemctl start systemd-timesyncd

# Check sync status
timedatectl status
# Look for: "System clock synchronized: yes"

# If not synchronized, force sync
timedatectl set-ntp true
```

Check clock drift between nodes:
```bash
# [ALL-M] — compare these timestamps across all masters
date +%s%N   # nanosecond timestamp

# They should be within a few milliseconds of each other
```

🔍 **Why time sync matters for etcd:**  
etcd uses the **Raft consensus algorithm**. Raft relies on timeouts — if a leader doesn't hear from a follower within a timeout, it assumes the follower is dead and starts an election. If nodes have significantly different clocks, these timeouts behave unpredictably. etcd documentation states clock skew > 1 second can cause leadership instability. > 5 seconds can cause repeated leader elections and a "split brain" scenario.

💡 **Interview**: *"What happens if etcd nodes have clock drift?"*  
→ "etcd uses heartbeat timeouts in Raft. If a follower's clock is ahead, it may time out the leader prematurely, triggering spurious leader elections. If clock skew exceeds the election timeout, you can get a cycle of constant re-elections where no leader stabilizes. This manifests as API server errors like 'etcd cluster is unavailable' even though all etcd processes are running. Always verify clock sync with `timedatectl` before debugging etcd."

---

## 2.11 Disk Setup for etcd [Masters Only]

etcd is extremely sensitive to disk I/O latency. High latency = leader elections = API server instability.

```bash
# [ALL-M] — check disk performance
apt install -y fio

# Test sequential write performance
fio --rw=write --ioengine=sync --fdatasync=1 --directory=/var/lib/rancher \
    --size=22m --bs=2300 --name=etcd-test

# Look for: IOPS= and lat (msec)=
# etcd needs: 50+ IOPS, <10ms average latency
# If lat is >50ms, your disk is too slow for etcd
```

For AWS: Use **io1 or io2 EBS volumes** (provisioned IOPS) for master nodes, not gp2.  
For Nutanix: Pin the VM to SSD-backed storage pools.

```bash
# [ALL-M]
# Create the etcd data directory in advance
mkdir -p /var/lib/rancher/rke2/server/db
```

🔍 **What happens with slow disk on masters:**
- etcd writes take too long
- Leader heartbeat is delayed
- Followers time out waiting for heartbeat
- New leader election triggers
- During election (~300ms-1.5s), API server requests fail
- Users see: `etcdserver: request timed out`
- kubectl shows: `Error from server: etcdserver: leader changed`
- You assume the cluster is broken; actually it's a disk problem

---

## 2.12 Package Updates and Dependencies

```bash
# [ALL]
apt update && apt upgrade -y

# Required packages
apt install -y \
    curl \
    wget \
    bash-completion \
    jq \
    net-tools \
    iputils-ping \
    traceroute \
    tcpdump \
    vim \
    htop \
    iotop \
    nfs-common       # needed for NFS-based PVs (optional but useful)
```

---

## 2.13 Verify Node Readiness

Run this checklist on EVERY node before proceeding:

```bash
# [ALL] — run these as a final check
echo "=== Hostname ==="
hostname

echo "=== Swap (should be 0) ==="
free -h | grep Swap

echo "=== Kernel modules ==="
lsmod | grep -E "overlay|br_netfilter"

echo "=== sysctl ==="
sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables

echo "=== Time sync ==="
timedatectl | grep "synchronized"

echo "=== /etc/hosts ==="
grep -E "master|worker" /etc/hosts

echo "=== DNS resolution ==="
ping -c 1 master-1 && ping -c 1 worker-1

echo "=== Disk write test ==="
dd if=/dev/zero of=/tmp/test bs=512 count=1000 oflag=dsync 2>&1 | grep -E "MB/s|copied"
rm /tmp/test
```

Expected output:
```
=== Hostname ===
master-1   (or whatever node you're on)

=== Swap (should be 0) ===
Swap:         0B      0B      0B

=== Kernel modules ===
br_netfilter           32768  0
overlay               151552  0

=== sysctl ===
net.ipv4.ip_forward = 1
net.bridge.bridge-nf-call-iptables = 1

=== Time sync ===
System clock synchronized: yes

=== /etc/hosts ===
10.0.1.10   master-1
10.0.1.11   master-2
... (all entries)

=== DNS resolution ===
PING master-1 ... 1 received
PING worker-1 ... 1 received

=== Disk write test ===
512000 bytes copied, 0.012345 s, 41.5 MB/s
```

If anything above shows wrong values — fix it before moving to Doc 03.

---

## 2.14 Summary

You have now:
- ✅ Set unique hostnames on all 6 nodes
- ✅ Configured /etc/hosts for name resolution
- ✅ Disabled swap (kubelet requirement)
- ✅ Loaded `overlay` and `br_netfilter` kernel modules
- ✅ Applied sysctl parameters for Kubernetes networking
- ✅ Opened the right firewall ports
- ✅ Verified time sync across nodes
- ✅ Validated disk performance for etcd

**Next**: [Doc 03 - PKI & Certificates →](./03-pki-and-certificates.md)  
Before RKE2 generates certs for you, you need to understand what those certs are, what SANs they need, and what breaks when they expire.

---

*Doc 02 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
