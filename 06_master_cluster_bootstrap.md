# Doc 06: Bootstrap the Master Cluster
## Installing RKE2 and Building a 3-Master HA Control Plane

> **This is where you actually type commands.**  
> Read every comment. Know why each command exists before running it.

---

## 6.1 The RKE2 Config File — Every Option Explained

Before installing, you configure RKE2 via `/etc/rancher/rke2/config.yaml`.  
This file does NOT exist by default. You create it.

### Master-1 Config (Bootstrap Node)

```bash
# [M1] — create config directory and file
mkdir -p /etc/rancher/rke2

cat > /etc/rancher/rke2/config.yaml << 'EOF'
# The bind address for the RKE2 API and supervisor endpoint
# Use the node's primary private IP
bind-address: 10.0.1.10

# Advertise the PRIVATE IP to other cluster members
# This is what etcd peers and kubelet will use to reach this node
advertise-address: 10.0.1.10

# Additional SANs for the API server TLS cert
# Include every way kubectl or components might connect
tls-san:
  - 10.0.1.10       # master-1 IP
  - 10.0.1.11       # master-2 IP
  - 10.0.1.12       # master-3 IP
  - 10.0.1.100      # keepalived VIP
  - master-1
  - master-2
  - master-3
  - master-vip
  # Uncomment and add your public IP if kubectl-ing from laptop:
  # - <your-public-ip>

# Pod network CIDR — where pod IPs come from
# Default: 10.42.0.0/16 — leave this unless it conflicts with your network
cluster-cidr: 10.42.0.0/16

# Service CIDR — where ClusterIPs come from
# Default: 10.43.0.0/16
service-cidr: 10.43.0.0/16

# Node tainting — masters should NOT run workloads
# This is set AFTER joining all masters (done in Doc 07)
# Do NOT set it here, or the cluster can't schedule CoreDNS/Canal on masters during bootstrap

# Disable default cloud provider (we're not using AWS managed services)
disable-cloud-controller: true

# CIS hardening profile (optional but good practice)
# profile: cis-1.23

# Write kubeconfig to a known location
write-kubeconfig: /etc/rancher/rke2/rke2.yaml
write-kubeconfig-mode: "0644"  # readable without sudo (for convenience)

# CNI — Canal is default, but explicit is better
cni: canal

# Container log max size and rotation
container-runtime-endpoint: ""  # use bundled containerd

# etcd snapshot settings
etcd-snapshot-schedule-cron: "0 */6 * * *"   # every 6 hours
etcd-snapshot-retention: 5                     # keep 5 snapshots
etcd-snapshot-dir: /opt/etcd-snapshots         # where to store them

# Node labels (optional, but useful)
node-label:
  - "role=control-plane"
  - "node-type=master"
EOF
```

### Master-2 and Master-3 Config

```bash
# [M2] — master-2's config
mkdir -p /etc/rancher/rke2

cat > /etc/rancher/rke2/config.yaml << 'EOF'
# Point to master-1 (or VIP) to join the cluster
# Port 9345 is RKE2's supervisor/registration port (NOT the k8s API port)
server: https://10.0.1.10:9345

# Token — must match exactly what master-1 generated
# We'll fill this in after master-1 is running
token: REPLACE_WITH_TOKEN

bind-address: 10.0.1.11
advertise-address: 10.0.1.11

tls-san:
  - 10.0.1.10
  - 10.0.1.11
  - 10.0.1.12
  - 10.0.1.100
  - master-1
  - master-2
  - master-3
  - master-vip

cluster-cidr: 10.42.0.0/16
service-cidr: 10.43.0.0/16
disable-cloud-controller: true
write-kubeconfig: /etc/rancher/rke2/rke2.yaml
write-kubeconfig-mode: "0644"
cni: canal
etcd-snapshot-schedule-cron: "0 */6 * * *"
etcd-snapshot-retention: 5
etcd-snapshot-dir: /opt/etcd-snapshots
node-label:
  - "role=control-plane"
  - "node-type=master"
EOF
```

```bash
# [M3] — master-3's config (same as master-2 but with its own IP)
mkdir -p /etc/rancher/rke2

cat > /etc/rancher/rke2/config.yaml << 'EOF'
server: https://10.0.1.10:9345
token: REPLACE_WITH_TOKEN

bind-address: 10.0.1.12
advertise-address: 10.0.1.12

tls-san:
  - 10.0.1.10
  - 10.0.1.11
  - 10.0.1.12
  - 10.0.1.100
  - master-1
  - master-2
  - master-3
  - master-vip

cluster-cidr: 10.42.0.0/16
service-cidr: 10.43.0.0/16
disable-cloud-controller: true
write-kubeconfig: /etc/rancher/rke2/rke2.yaml
write-kubeconfig-mode: "0644"
cni: canal
etcd-snapshot-schedule-cron: "0 */6 * * *"
etcd-snapshot-retention: 5
etcd-snapshot-dir: /opt/etcd-snapshots
node-label:
  - "role=control-plane"
  - "node-type=master"
EOF
```

🔍 **Why `server: https://10.0.1.10:9345` and not the VIP?**  
At this point, keepalived isn't set up yet. master-1 IS the only server, so we point directly to it. Once keepalived is configured (section 6.5), workers will point to the VIP. We can also update masters to use the VIP later.

---

## 6.2 Install RKE2 on Master-1

```bash
# [M1] — download and install RKE2
# The install script detects your OS and installs the appropriate package
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="server" sh -

# This installs:
# - /usr/local/bin/rke2        (main binary)
# - /usr/local/bin/kubectl     (bundled kubectl, symlinked)
# - systemd unit: rke2-server.service
# - /usr/share/rke2/           (bundled manifests, helm charts)

# Verify installation
rke2 --version
# rke2 version v1.29.x+rke2r1 (go1.21.x)
```

⚠️ **Do NOT start RKE2 yet.** Create the config first.

```bash
# [M1] — create etcd snapshot directory
mkdir -p /opt/etcd-snapshots

# Enable and start RKE2 server (ONLY on master-1 first)
systemctl enable rke2-server
systemctl start rke2-server

# Watch the startup logs (this takes 1-3 minutes)
journalctl -u rke2-server -f
```

### What You'll See in the Logs

```
Starting rke2
Preparing server
Generating CA certificates
Starting etcd
Starting kube-apiserver
Starting kube-controller-manager
Starting kube-scheduler
Bootstrapping cluster with name "local"
Canal CNI installed
CoreDNS deployed
...
Node master-1 registered
```

⚠️ **Wait until you see `Node master-1 registered` or similar before continuing.**

### Verify Master-1 Is Ready

```bash
# [M1] — set up kubectl access
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml

# Also add to PATH for convenience
export PATH=$PATH:/var/lib/rancher/rke2/bin

# Persist for future sessions
echo 'export KUBECONFIG=/etc/rancher/rke2/rke2.yaml' >> /root/.bashrc
echo 'export PATH=$PATH:/var/lib/rancher/rke2/bin' >> /root/.bashrc
source /root/.bashrc

# Check cluster status
kubectl get nodes
# NAME       STATUS   ROLES                       AGE   VERSION
# master-1   Ready    control-plane,etcd,master   2m    v1.29.x

kubectl get pods -A
# All pods should be Running or Completed
# kube-system: coredns, canal, metrics-server, rke2-ingress-nginx, etc.

# Get the join token (needed for master-2, master-3, and workers)
cat /var/lib/rancher/rke2/server/node-token
# Output: K10abc...::server:def456...
# COPY THIS VALUE — you need it for ALL other nodes
```

🔍 **What is the node-token?**  
The token is a shared secret used to authenticate joining nodes. It proves the joining node is allowed to join THIS specific cluster (prevents rogue nodes from joining). It's a cryptographic hash derived from the server CA. Format: `K10<server-ca-hash>::server:<random>`. The server validates the token hash against its CA fingerprint.

---

## 6.3 Join Master-2

```bash
# [M1] — get the token
cat /var/lib/rancher/rke2/server/node-token
# Copy this output

# [M2] — install RKE2
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="server" sh -

mkdir -p /opt/etcd-snapshots

# Update config with the actual token
# Edit /etc/rancher/rke2/config.yaml and replace REPLACE_WITH_TOKEN
TOKEN="K10abc...::server:def456..."    # paste your actual token
sed -i "s/REPLACE_WITH_TOKEN/$TOKEN/" /etc/rancher/rke2/config.yaml

# Verify config looks right
cat /etc/rancher/rke2/config.yaml

# Start RKE2 on master-2
systemctl enable rke2-server
systemctl start rke2-server

# Watch logs
journalctl -u rke2-server -f
```

### What Happens During Join

```
master-2 contacts master-1:9345
  → Authenticates with token
  → Downloads cluster CA certs
  → Gets etcd cluster info (existing member IDs, peer URLs)
  
master-2 starts etcd (learner mode first)
  → Learner receives all data from master-1's etcd
  → Once caught up, promoted to full voting member
  
master-2 starts kube-apiserver
  → Connects to etcd cluster (now 2 members)
  
etcd now has 2 members but still needs 3 for quorum
  (during this time, cluster is slightly less resilient)
```

### Verify From Master-1

```bash
# [M1]
kubectl get nodes
# NAME       STATUS   ROLES                       AGE   VERSION
# master-1   Ready    control-plane,etcd,master   10m   v1.29.x
# master-2   Ready    control-plane,etcd,master   2m    v1.29.x

# Check etcd members
/var/lib/rancher/rke2/bin/etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
  member list
# Should show: master-1 (leader), master-2
```

---

## 6.4 Join Master-3

```bash
# [M3] — same process as master-2
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="server" sh -

mkdir -p /opt/etcd-snapshots

TOKEN="K10abc...::server:def456..."
sed -i "s/REPLACE_WITH_TOKEN/$TOKEN/" /etc/rancher/rke2/config.yaml

systemctl enable rke2-server
systemctl start rke2-server

journalctl -u rke2-server -f
```

### Verify HA Is Achieved

```bash
# [M1]
kubectl get nodes
# All 3 masters should be Ready

# etcd quorum check
/var/lib/rancher/rke2/bin/etcdctl \
  --endpoints=https://10.0.1.10:2379,https://10.0.1.11:2379,https://10.0.1.12:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
  endpoint status --cluster -w table
# Should show all 3, one as LEADER=true

# Test HA: kill rke2-server on master-1
# (Don't do this yet if you're not ready, but here's what to expect:)
# systemctl stop rke2-server  # on master-1
# Then from master-2: kubectl get nodes → should still work
# master-2 or master-3 will be elected etcd leader
```

---

## 6.5 Configure keepalived for the Virtual IP

This section sets up the VIP `10.0.1.100` that floats between masters.

### Install keepalived

```bash
# [ALL-M]
apt install -y keepalived
```

### Configure keepalived on Master-1 (Initial MASTER role)

```bash
# [M1]
cat > /etc/keepalived/keepalived.conf << 'EOF'
# Global settings
global_defs {
   router_id LVS_MASTER1
   script_user root
   enable_script_security
}

# Health check script — is our local RKE2 API responding?
vrrp_script check_rke2 {
   script "/usr/local/bin/check-rke2.sh"
   interval 3      # check every 3 seconds
   weight -20      # if script fails, reduce priority by 20
   fall 2          # need 2 consecutive failures to trigger
   rise 2          # need 2 consecutive successes to recover
}

# VRRP Instance — the actual VIP configuration
vrrp_instance VI_1 {
    state MASTER      # this node starts as MASTER (highest priority)
    interface eth0    # ⚠️ CHANGE THIS to your actual interface name
                      # check with: ip link show | grep -E "^[0-9]"
                      # might be: ens3, ens5, enp0s3, eth0

    virtual_router_id 51   # unique ID for this VRRP group (1-255)
                           # must be same on all 3 masters

    priority 101      # highest priority = preferred MASTER
                      # master-2 will be 100, master-3 will be 99

    advert_int 1      # send VRRP advertisements every 1 second

    # Unicast instead of multicast — works on AWS and most cloud VPCs
    # (Cloud VPCs block multicast; unicast VRRP works everywhere)
    unicast_src_ip 10.0.1.10    # this node's IP
    unicast_peer {
        10.0.1.11                # master-2
        10.0.1.12                # master-3
    }

    # Authentication (prevents rogue keepalived from taking VIP)
    authentication {
        auth_type PASS
        auth_pass rke2vip123     # ⚠️ Change this to a random secret
    }

    # The Virtual IP itself
    virtual_ipaddress {
        10.0.1.100/24 dev eth0   # ⚠️ Change eth0 to your interface
    }

    # Run the health check
    track_script {
        check_rke2
    }
}
EOF
```

### Configure keepalived on Master-2 (BACKUP)

```bash
# [M2]
cat > /etc/keepalived/keepalived.conf << 'EOF'
global_defs {
   router_id LVS_MASTER2
   script_user root
   enable_script_security
}

vrrp_script check_rke2 {
   script "/usr/local/bin/check-rke2.sh"
   interval 3
   weight -20
   fall 2
   rise 2
}

vrrp_instance VI_1 {
    state BACKUP          # starts as BACKUP
    interface eth0        # ⚠️ change to your interface
    virtual_router_id 51  # same as master-1
    priority 100          # lower than master-1 (101), higher than master-3 (99)
    advert_int 1

    unicast_src_ip 10.0.1.11
    unicast_peer {
        10.0.1.10
        10.0.1.12
    }

    authentication {
        auth_type PASS
        auth_pass rke2vip123
    }

    virtual_ipaddress {
        10.0.1.100/24 dev eth0
    }

    track_script {
        check_rke2
    }
}
EOF
```

### Configure keepalived on Master-3 (BACKUP)

```bash
# [M3]
cat > /etc/keepalived/keepalived.conf << 'EOF'
global_defs {
   router_id LVS_MASTER3
   script_user root
   enable_script_security
}

vrrp_script check_rke2 {
   script "/usr/local/bin/check-rke2.sh"
   interval 3
   weight -20
   fall 2
   rise 2
}

vrrp_instance VI_1 {
    state BACKUP
    interface eth0        # ⚠️ change to your interface
    virtual_router_id 51
    priority 99           # lowest priority
    advert_int 1

    unicast_src_ip 10.0.1.12
    unicast_peer {
        10.0.1.10
        10.0.1.11
    }

    authentication {
        auth_type PASS
        auth_pass rke2vip123
    }

    virtual_ipaddress {
        10.0.1.100/24 dev eth0
    }

    track_script {
        check_rke2
    }
}
EOF
```

### Create the Health Check Script

```bash
# [ALL-M] — health check that keepalived calls
cat > /usr/local/bin/check-rke2.sh << 'EOF'
#!/bin/bash
# Returns 0 if RKE2 API is responding, 1 if not
# keepalived uses this to decide whether to keep/release the VIP

# Check if the API server is responding on localhost
# We use --insecure here because we're on localhost — just checking liveness
curl -sk https://127.0.0.1:6443/healthz -o /dev/null -w "%{http_code}" | grep -q "200"
EOF

chmod +x /usr/local/bin/check-rke2.sh

# Test the script
/usr/local/bin/check-rke2.sh
echo $?   # should be 0 if RKE2 is running
```

🔍 **How the health check integrates with VIP failover:**
- keepalived runs `check-rke2.sh` every 3 seconds
- If script returns non-zero twice (`fall 2`): priority drops by 20 (weight: -20)
- master-1 was priority 101, now drops to 81
- master-2 has priority 100 — it's now highest
- master-2 sends VRRP advertisement with higher priority
- master-1 sees a higher-priority VRRP ad and gives up MASTER role
- master-2 assigns `10.0.1.100` to its eth0
- VIP moves, all traffic shifts to master-2

### Start keepalived

```bash
# [ALL-M]
systemctl enable keepalived
systemctl start keepalived

# Check keepalived status
systemctl status keepalived

# Verify VIP is on master-1
# [M1]:
ip addr show eth0 | grep "10.0.1.100"
# Should show: inet 10.0.1.100/24 scope global secondary eth0

# [M2, M3]:
ip addr show eth0 | grep "10.0.1.100"
# Should show nothing (they don't own the VIP)
```

### Test VIP Failover

```bash
# [LOCAL] — from your laptop (or any node), ping the VIP
ping 10.0.1.100   # should respond (master-1 owns it)

# [M1] — stop RKE2 (simulates master-1 failure)
systemctl stop rke2-server

# Wait ~6 seconds (fall:2 * interval:3)
# [LOCAL] — VIP should still respond (master-2 took over)
ping 10.0.1.100

# Check which master owns VIP now
# [M2]:
ip addr show eth0 | grep "10.0.1.100"
# Should now show the VIP on master-2

# [M1] — restart RKE2 (master-1 comes back)
systemctl start rke2-server

# Wait ~6 seconds (rise:2)
# [M1] — VIP returns to master-1 (it has highest priority 101)
ip addr show eth0 | grep "10.0.1.100"
```

💡 **Interview**: *"How does your HA Kubernetes control plane handle master node failure?"*  
→ "I use keepalived with VRRP to maintain a floating Virtual IP across all three masters. keepalived runs a health check script every 3 seconds that tests the local API server. If the check fails twice, the node's VRRP priority drops below the backup nodes, causing the backup to claim the VIP. We use unicast VRRP instead of multicast so it works on cloud VPCs. For the etcd layer, Raft handles leader re-election automatically when a node fails. As long as 2 of 3 masters are running, both the VIP and etcd quorum are maintained. Total VIP failover takes roughly 6-10 seconds."

---

## 6.6 Set Up kubectl From Your Laptop

```bash
# [M1] — get the kubeconfig
cat /etc/rancher/rke2/rke2.yaml
```

Copy the file content to your laptop. Then edit the `server` line:
```bash
# [LOCAL] — on your laptop
mkdir -p ~/.kube
# Paste the kubeconfig content into ~/.kube/config
# Edit the server line to use the VIP or a master's public IP:
nano ~/.kube/config
# Change: server: https://127.0.0.1:6443
# To:     server: https://<master-1-public-ip>:6443
# (or VIP's public IP if you set that up)

# Test
kubectl get nodes
```

⚠️ **The kubeconfig contains the admin client private key.** Treat it like a password. Never commit it to git, never share it without understanding the implications.

---

## 6.7 Verify the Full Control Plane

```bash
# [M1] — comprehensive control plane check
kubectl get nodes -o wide
# All 3 masters should be Ready, ROLES should include control-plane,etcd,master

kubectl get pods -A
# Key pods that must be Running:
# kube-system   coredns-*                    2/2   Running   ← DNS
# kube-system   canal-*                      2/2   Running   ← CNI (one per node)
# kube-system   rke2-ingress-nginx-*                        ← Ingress
# kube-system   rke2-metrics-server-*                       ← Metrics
# kube-system   kube-proxy-*                                ← Service routing

# Check static pods (control plane itself)
kubectl get pods -n kube-system | grep -E "etcd|apiserver|controller|scheduler"
# These are static pods — their names include the node hostname

# Component health (deprecated but still useful)
kubectl get componentstatuses
# scheduler, controller-manager, etcd-0, etcd-1, etcd-2 should all be Healthy

# Test cluster functionality
kubectl create deployment test-nginx --image=nginx --replicas=1
kubectl get pods -w   # watch it come up
kubectl delete deployment test-nginx
```

---

## 6.8 Summary

You have now:
- ✅ Installed RKE2 on master-1 (bootstrap)
- ✅ Joined master-2 and master-3 (HA etcd, 3-member quorum)
- ✅ Configured keepalived VIP with unicast VRRP (works on AWS + Nutanix)
- ✅ Health check script for automatic VIP failover
- ✅ kubectl set up locally
- ✅ Verified the control plane is healthy

**Next**: [Doc 07 - Worker Nodes →](./07-worker-nodes.md)  
Join the 3 workers, taint the masters (so workloads only run on workers), and verify the full 6-node cluster.

---

*Doc 06 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
