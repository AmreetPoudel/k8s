# Complete Production Architecture Reference
## 6-Node High-Availability Bare-Metal / Cloud Kubernetes (RKE2)

> **Platform Specs**: 3 Control Plane (Master) Nodes + 3 Worker Nodes  
> **CNI**: Canal (Flannel VXLAN Overlay + Calico NetworkPolicy)  
> **Storage**: Longhorn Distributed Block Storage (3-way replication)  
> **Ingress & LB**: NGINX Ingress Controller + MetalLB (Layer 2)  
> **HA VIP**: Keepalived Unicast VRRP (`10.0.1.100`)

---

## 1. High-Level Master Architecture Diagram

```
═════════════════════════════════════════════════════════════════════════════════════════════════════════
                                    EXTERNAL TRAFFIC & CLIENTS
═════════════════════════════════════════════════════════════════════════════════════════════════════════
          │                                                                      │
          │ (kubectl / CI/CD Admin API Traffic)                                  │ (Public Web Traffic)
          ▼                                                                      ▼
  ┌───────────────────────────────┐                                    ┌───────────────────────────┐
  │   Keepalived Virtual IP (VIP) │                                    │  MetalLB L2 VIP Pool      │
  │   10.0.1.100:6443 & :9345     │                                    │  10.0.1.200 - 10.0.1.220  │
  └───────────────┬───────────────┘                                    └─────────────┬─────────────┘
                  │                                                                  │
══════════════════╪══════════════════════════════════════════════════════════════════╪════════════════════
                  │ CONTROL PLANE LAYER (Public / Admin Subnet: 10.0.1.0/24)         │
══════════════════╪══════════════════════════════════════════════════════════════════╪════════════════════
                  │                                                                  │
      ┌───────────┴───────────────────────┬───────────────────────────┐              │
      ▼                                   ▼                           ▼              │
┌───────────────────────────┐ ┌───────────────────────────┐ ┌───────────────────────────┐  │
│  MASTER 1 (10.0.1.10)     │ │  MASTER 2 (10.0.1.11)     │ │  MASTER 3 (10.0.1.12)     │  │
│                           │ │                           │ │                           │  │
│  [Keepalived: Priority 101│ │  [Keepalived: Priority 100│ │  [Keepalived: Priority 99 ]│  │
│   (Active VIP Owner)]     │ │   (Backup Standby)]       │ │   (Backup Standby)]       │  │
│                           │ │                           │ │                           │  │
│  kube-apiserver (:6443)   │ │  kube-apiserver (:6443)   │ │  kube-apiserver (:6443)   │  │
│  [Active-Active]          │ │  [Active-Active]          │ │  [Active-Active]          │  │
│            │              │ │            │              │ │            │              │  │
│            ▼              │ │            ▼              │ │            ▼              │  │
│  etcd (:2379/:2380) ◄─────┼─┼─►etcd (:2379/:2380) ◄─────┼─┼─►etcd (:2379/:2380)       │  │
│  [Follower]               │ │  [RAFT LEADER]            │ │  [Follower]               │  │
│                           │ │                           │ │                           │  │
│  kube-scheduler           │ │  kube-scheduler           │ │  kube-scheduler           │  │
│  [Standby]                │ │  [ACTIVE LEASE LEADER]    │ │  [Standby]                │  │
│                           │ │                           │ │                           │  │
│  kube-controller-manager  │ │  kube-controller-manager  │ │  kube-controller-manager  │  │
│  [Standby]                │ │  [ACTIVE LEASE LEADER]    │ │  [Standby]                │  │
│                           │ │                           │ │                           │  │
│  ⚠️ TAINT: NoSchedule     │ │  ⚠️ TAINT: NoSchedule     │ │  ⚠️ TAINT: NoSchedule     │  │
└───────────────────────────┘ └───────────────────────────┘ └───────────────────────────┘  │
                  ▲                                                           ▲              │
                  │              gRPC Watch / HTTPS Registration              │              │
                  └─────────────────────────────┬─────────────────────────────┘              │
                                                │                                            │
════════════════════════════════════════════════╪════════════════════════════════════════════╪════════════
                  WORKER LAYER (Workload Subnet: 10.0.2.0/24 or Flat Network)                │
════════════════════════════════════════════════╪════════════════════════════════════════════╪════════════
                                                │                                            │
          ┌─────────────────────────────────────┼────────────────────────────────────┐       │
          ▼                                     ▼                                    ▼       ▼
┌───────────────────────────────┐ ┌───────────────────────────────┐ ┌───────────────────────────────┐
│  WORKER 1 (10.0.2.10)         │ │  WORKER 2 (10.0.2.11)         │ │  WORKER 3 (10.0.2.12)         │
│                               │ │                               │ │                               │
│  kubelet (:10250)             │ │  kubelet (:10250)             │ │  kubelet (:10250)             │
│  kube-proxy (iptables rules)  │ │  kube-proxy (iptables rules)  │ │  kube-proxy (iptables rules)  │
│  containerd runtime           │ │  containerd runtime           │ │  containerd runtime           │
│                               │ │                               │ │                               │
│  Canal CNI (Pod CIDR: /24)    │ │  Canal CNI (Pod CIDR: /24)    │ │  Canal CNI (Pod CIDR: /24)    │
│  [10.42.1.0/24]               │ │  [10.42.2.0/24]               │ │  [10.42.3.0/24]               │
│                               │ │                               │ │                               │
│  NGINX Ingress (HostPort:80)  │ │  NGINX Ingress (HostPort:80)  │ │  NGINX Ingress (HostPort:80)  │
│  MetalLB Speaker (ARP L2)     │ │  MetalLB Speaker (ARP L2)     │ │  MetalLB Speaker (ARP L2)     │
│                               │ │                               │ │                               │
│  Longhorn Storage Node        │ │  Longhorn Storage Node        │ │  Longhorn Storage Node        │
│  (/var/lib/longhorn)          │ │  (/var/lib/longhorn)          │ │  (/var/lib/longhorn)          │
│                               │ │                               │ │                               │
│  ┌─────────────────────────┐  │ │  ┌─────────────────────────┐  │ │  ┌─────────────────────────┐  │
│  │ WORKLOAD PODS           │  │ │  │ WORKLOAD PODS           │  │ │  │ WORKLOAD PODS           │  │
│  │ - Frontend Web Replicas │  │ │  │ - Frontend Web Replicas │  │ │  │ - MySQL StatefulSet     │  │
│  │ - Monitoring Agents     │  │ │  │ - Prometheus Server     │  │ │  │ - Grafana Dashboard     │  │
│  └─────────────────────────┘  │ │  └─────────────────────────┘  │ │  └─────────────────────────┘  │
└───────────────▲───────────────┘ └───────────────▲───────────────┘ └───────────────▲───────────────┘
                │                                 │                                 │
                └═════════════════════════════════╩═════════════════════════════════┘
                       Canal VXLAN Overlay Tunnel (UDP Port 8472 / VNI 1)
                       + Longhorn 3-Way Replicated Storage Mesh (iSCSI/TCP)
```

---

## 2. The 3 IP Addressing Planes

| Plane | CIDR Range | Managed By | Purpose |
|---|---|---|---|
| **Node Network (Underlay)** | `10.0.1.0/24` (Masters)<br>`10.0.2.0/24` (Workers) | Physical Switch / AWS VPC | Real network interfaces (`eth0`) on physical servers or VMs. |
| **Pod Network (Overlay)** | `10.42.0.0/16` | Canal (Flannel IPAM) | Virtual private IPs assigned to pods. Each node gets an isolated `/24` slice (254 IPs). Encapsulated over VXLAN UDP 8472. |
| **Service Network (Virtual)** | `10.43.0.0/16` | kube-proxy (iptables) | Virtual ClusterIP addresses. Do not exist on any physical NIC; evaluated in-kernel by netfilter DNAT. |
| **Keepalived HA VIP** | `10.0.1.100` | Keepalived (VRRP) | Single floating management entrypoint for `kubectl` and Kubelet agent registration. |
| **MetalLB External Pool** | `10.0.1.200 - 10.0.1.220` | MetalLB (Layer 2 ARP) | Public/LAN accessible IP addresses dynamically allocated to `type: LoadBalancer` Services. |

---

## 3. Node-by-Node Component Architecture

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ MASTER NODE ARCHITECTURE (master-1, master-2, master-3)                                │
│                                                                                        │
│  [keepalived Daemon] ──► Holds floating VIP (10.0.1.100) via check-rke2.sh healthz    │
│                                                                                        │
│  [RKE2 Server Systemd Service]                                                         │
│    │                                                                                   │
│    ├── static-pod: etcd (Port 2379 Client / Port 2380 Peer Raft Sync)                 │
│    ├── static-pod: kube-apiserver (Port 6443 REST API / Port 9345 Supervisor)         │
│    ├── static-pod: kube-scheduler (Active-Passive Lease Lock Leader)                   │
│    └── static-pod: kube-controller-manager (Active-Passive Lease Lock Leader)          │
│                                                                                        │
│  [Embedded Kubelet & containerd] ──► Runs & monitors the control plane static pods     │
│  [Canal DaemonSet] ──► Flannel VXLAN routing + Calico Felix NetworkPolicies            │
│  [CoreDNS Pods] ──► High-availability internal cluster DNS resolution                 │
│                                                                                        │
│  🔒 Taints Enforced: node-role.kubernetes.io/control-plane:NoSchedule                  │
└────────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│ WORKER NODE ARCHITECTURE (worker-1, worker-2, worker-3)                                │
│                                                                                        │
│  [RKE2 Agent Systemd Service]                                                          │
│    │                                                                                   │
│    ├── kubelet (Port 10250) ──► Talks to containerd via CRI socket                     │
│    └── kube-proxy ──► Programs Linux iptables NAT tables for Service ClusterIPs        │
│                                                                                        │
│  [Canal calico-node DaemonSet] ──► Creates pod veth pairs (caliXXX) & flannel.1 VTEP   │
│  [rke2-ingress-nginx DaemonSet] ──► Binds HostPort 80/443 for external HTTP routing   │
│  [MetalLB speaker DaemonSet] ──► Answers ARP requests for LoadBalancer Service IPs    │
│  [Longhorn CSI DaemonSet] ──► iSCSI initiator & distributed block storage engine       │
│                                                                                        │
│  🚀 Workloads: Microservices, StatefulSets, Databases, HPA Scaled Pods                │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Key Packet Flow Paths

### Path A: `kubectl` Command Execution Flow
```
Developer Laptop 
  ──► HTTPS request to https://10.0.1.100:6443 (VIP)
  ──► Keepalived routes to active Master 1
  ──► kube-apiserver authenticates client cert (CN=admin, O=system:masters)
  ──► Authorizes via RBAC ClusterRole
  ──► Reads/Writes object in etcd (via Raft leader consensus)
  ──► Returns JSON response to developer
```

### Path B: Inbound Public Web Traffic Flow
```
Internet User 
  ──► Enters via Ingress IP (10.0.1.200 via MetalLB or Worker HostPort :80)
  ──► NGINX Ingress Controller processes Host header (app.example.local)
  ──► Evaluates Ingress Path rules & SSL/TLS certificate
  ──► Proxies packet to ClusterIP Service (10.43.x.x:80)
  ──► Linux kernel iptables DNAT rewrites destination to Pod IP (10.42.2.15:8080)
  ──► Packet enters Worker 2 veth interface -> Container serves HTTP response
```

### Path C: Pod-to-Pod Cross-Node Network Flow (Canal VXLAN)
```
Pod A (10.42.1.5 on Worker 1) 
  ──► Sends TCP payload to Pod B (10.42.2.8 on Worker 2)
  ──► Leaves container via eth0 -> enters host caliXXX veth interface
  ──► Host routing table directs packet to flannel.1 virtual device
  ──► Flannel encapsulates packet: Inner IP (10.42.1.5->10.42.2.8) + Outer UDP (10.0.2.10:8472 -> 10.0.2.11:8472)
  ──► Transits physical underlay network switch
  ──► Worker 2 flannel.1 decapsulates outer UDP header
  ──► Calico Felix checks NetworkPolicy firewall rules (ACCEPT)
  ──► Delivers clean original TCP packet to Pod B's eth0 interface
```

### Path D: Distributed Storage Read/Write Flow (Longhorn)
```
Pod MySQL (on Worker 3) 
  ──► Writes database transaction to mounted volume path (/var/lib/mysql)
  ──► Linux kernel passes I/O to Longhorn block device (/dev/longhorn/pvc-xxx)
  ──► Longhorn Engine intercepts block write
  ──► Broadcasts synchronous TCP block write across 3 replicas:
        - Replica 1: Local NVMe disk on Worker 3
        - Replica 2: Network disk on Worker 1
        - Replica 3: Network disk on Worker 2
  ──► Once all 3 acknowledge disk write -> confirms I/O success to MySQL
```

---

## 5. Port Requirements Matrix

| Port | Protocol | Source | Destination | Component / Purpose |
|---|---|---|---|---|
| `6443` | TCP | Laptop, Workers, CI/CD | Masters (VIP) | Kubernetes API Server HTTPS |
| `9345` | TCP | Workers, Joining Masters | Masters (VIP) | RKE2 Supervisor / Node Join API |
| `2379` | TCP | Masters only | Masters only | etcd Client Requests |
| `2380` | TCP | Masters only | Masters only | etcd Peer Raft Replication |
| `10250` | TCP | Masters | All Nodes | Kubelet API (Logs, Exec, Metrics) |
| `8472` | UDP | All Nodes | All Nodes | Canal Flannel VXLAN Overlay Traffic |
| `51820` | UDP | All Nodes | All Nodes | WireGuard Encrypted Overlay (Optional) |
| `80 / 443` | TCP | Public / Clients | Worker Nodes | NGINX Ingress Web Traffic |
| `30000-32767`| TCP | External Clients | Worker Nodes | Kubernetes NodePort Service Range |
| `9500-9502` | TCP | Worker Nodes | Worker Nodes | Longhorn Storage Engine & Replica Comms |
| `112` | IP/VRRP | Master Nodes | Master Nodes | Keepalived VRRP Heartbeat Advertisements |

---

*This architecture specification reflects the exact layout built across Docs 01–14.*
