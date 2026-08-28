# 02. Keepalived High-Availability Floating VIP Configuration

> **Target Nodes**: ALL 3 Control Plane Masters ONLY (`master-1`, `master-2`, `master-3`)  
> **Floating Virtual IP (VIP)**: `10.0.2.60`  
> **Interface**: `ens3` (or your primary network interface name, check with `ip -br a`)

---

## Step 1: Install Keepalived on All 3 Masters

### 🎯 The Command:
```bash
apt-get install -y keepalived
```

### ❓ Why are we doing this?
Keepalived provides automated Layer-3 IP failover using the Virtual Router Redundancy Protocol (VRRP). It ensures that if the active master crashes, the virtual IP (`10.0.2.60`) automatically migrates to a healthy backup master in under 1 second.

### ⚖️ Is it even necessary?
**YES, MANDATORY FOR HIGH AVAILABILITY.** Without a VIP or external hardware load balancer, worker nodes and `kubectl` would be hardcoded to a single master IP (e.g. `10.0.2.50`). If Master 1 dies, the entire cluster becomes unreachable even though Master 2 and Master 3 are healthy!

---

## Step 2: Create the API Server Health Check Script

### 🎯 The Command (Run on Master 1, 2, and 3):
```bash
cat <<'EOF' | tee /usr/local/bin/check-rke2.sh
#!/bin/bash
# Returns 0 if RKE2 API server is healthy, 1 if unhealthy
curl -sk --max-time 2 https://127.0.0.1:6443/healthz | grep -q "ok"
EOF

chmod +x /usr/local/bin/check-rke2.sh
```

---

## Step 3: Configure Keepalived (`keepalived.conf`)

### 🎯 On `master-1` (`10.0.2.50`):
```bash
cat <<EOF | tee /etc/keepalived/keepalived.conf
global_defs {
    router_id master-1
    enable_script_security
    script_user root
}

vrrp_script check_rke2 {
    script "/usr/local/bin/check-rke2.sh"
    interval 2
    weight -20
    fall 2
    rise 2
}

vrrp_instance VI_K8S {
    state MASTER
    interface ens3
    virtual_router_id 51
    priority 101
    advert_int 1
    dont_track_primary

    unicast_src_ip 10.0.2.50
    unicast_peer {
        10.0.2.51
        10.0.2.52
    }

    authentication {
        auth_type PASS
        auth_pass K8sVip12
    }

    virtual_ipaddress {
        10.0.2.60/24 dev ens3
    }

    track_script {
        check_rke2
    }
}
EOF
```

---

### 🎯 On `master-2` (`10.0.2.51`):
```bash
cat <<EOF | tee /etc/keepalived/keepalived.conf
global_defs {
    router_id master-2
    enable_script_security
    script_user root
}

vrrp_script check_rke2 {
    script "/usr/local/bin/check-rke2.sh"
    interval 2
    weight -20
    fall 2
    rise 2
}

vrrp_instance VI_K8S {
    state BACKUP
    interface ens3
    virtual_router_id 51
    priority 100
    advert_int 1
    dont_track_primary

    unicast_src_ip 10.0.2.51
    unicast_peer {
        10.0.2.50
        10.0.2.52
    }

    authentication {
        auth_type PASS
        auth_pass K8sVip12
    }

    virtual_ipaddress {
        10.0.2.60/24 dev ens3
    }

    track_script {
        check_rke2
    }
}
EOF
```

---

### 🎯 On `master-3` (`10.0.2.52`):
```bash
cat <<EOF | tee /etc/keepalived/keepalived.conf
global_defs {
    router_id master-3
    enable_script_security
    script_user root
}

vrrp_script check_rke2 {
    script "/usr/local/bin/check-rke2.sh"
    interval 2
    weight -20
    fall 2
    rise 2
}

vrrp_instance VI_K8S {
    state BACKUP
    interface ens3
    virtual_router_id 51
    priority 99
    advert_int 1
    dont_track_primary

    unicast_src_ip 10.0.2.52
    unicast_peer {
        10.0.2.50
        10.0.2.51
    }

    authentication {
        auth_type PASS
        auth_pass K8sVip12
    }

    virtual_ipaddress {
        10.0.2.60/24 dev ens3
    }

    track_script {
        check_rke2
    }
}
EOF
```

---

## Step 4: Enable and Start Keepalived

### 🎯 The Commands (Run on Master 1, 2, and 3):
```bash
systemctl enable keepalived
systemctl restart keepalived
```

---

## ✅ Step 5: Verification & Failover Test

1. **Verify Master 1 holds the VIP:**
```bash
# On master-1:
ip -br a show dev ens3
# Output MUST show: 10.0.2.50/24 and 10.0.2.60/24
```

2. **Verify Master 2 and Master 3 are in standby:**
```bash
# On master-2 and master-3:
ip -br a show dev ens3
# Output MUST show ONLY: 10.0.2.51/24 (or 10.0.2.52/24)
```

3. **Test Failover:**
```bash
# On master-1: Stop keepalived
systemctl stop keepalived

# Immediately on master-2: Check IP
ip -br a show dev ens3
# Output MUST now show: 10.0.2.60/24 (Master 2 acquired the VIP in <1s!)

# On master-1: Restart keepalived
systemctl start keepalived
# Master 1 preempts and takes the VIP back!
```

Once VIP failover is verified, proceed to **[03_bootstrap_master_1.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/03_bootstrap_master_1.md)**!

---

## 🧠 Deep Internals & VRRP Election FAQ

### Q1: Does a failing health check keep subtracting priority into negative numbers (-20, -40, -60...)?
**NO.** `weight -20` is a **one-time binary state shift**:
* **When Healthy**: Node runs at its configured base priority (`master-1 = 101`, `master-2 = 100`, `master-3 = 99`).
* **When Unhealthy (Script Fails)**: Priority drops by exactly 20 points (e.g. `100 - 20 = 80`). It stays at `80` indefinitely—it NEVER keeps decreasing.
* **When Recovered**: After 2 consecutive successful checks (`rise 2`), Keepalived immediately restores priority back to the full base value (`100`).

### Q2: Do backup nodes also run health checks?
**YES, continuously.** Even in `BACKUP` state, every node runs `check-rke2.sh` every 2 seconds. 
* If Master-2's API server crashes while in Backup mode, its priority drops to `80`.
* If Master-1 dies, Master-3 (Priority `99`) **automatically bypasses the sick Master-2 (Priority `80`)** and claims the VIP! The cluster will NEVER failover to an unhealthy node.

### Q3: How do backup nodes decide who becomes Master during a hard power crash?
When Master-1 physically dies, heartbeat advertisements stop completely.
* Master-2 and Master-3 run an election timer: $\text{Timer} = 3 \times \text{advert\_int} + \frac{256 - \text{Priority}}{256}$.
* Because **Master-2 has higher priority (100 vs 99)**, Master-2's timer finishes **faster**. Master-2 broadcasts a VRRP advertisement claiming the VIP first, and Master-3 yields and stays in Backup state.
