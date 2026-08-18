# Doc 01: Kubernetes & RKE2 Deep Theory
## Before You Touch a Single Server

---

## 1.1 Why You Feel Lost (And Why That's Normal)

You administered a running Kubernetes cluster in production.  
You used `kubectl get pods`, you read logs, maybe you scaled deployments.  
But you felt like a mechanic who can change oil but has never seen an engine.

Here's the problem: **Kubernetes has 7+ major components, all TLS-mutually-authenticated, all event-driven, all distributed — and if you only ever `kubectl apply`, you never see any of it.**

This document gives you the complete engine diagram before you build it.

---

## 1.2 What Kubernetes Actually Is: The Distributed State Machine

Kubernetes is a **declarative distributed state machine**.

You tell it: *"I want 3 replicas of my web server running."*  
Kubernetes's job: watch reality, compare it to your desire, and reconcile.  
Forever. Even when nodes die, pods crash, or disks fill up.

This pattern is called the **control loop** (or reconciliation loop). It is the foundational heartbeat of Kubernetes.

```
      ┌─────────────────────────────────┐
      │         Control Loop            │
      │                                 │
      │  Desired State  ──────────────► │ (What you declared in YAML / etcd)
      │                                 │
      │  Actual State   ──────────────► │ (What containerd/kubelet reports)
      │                                 │
      │  diff = Desired - Actual        │
      │  → take action to reconcile     │
      └─────────────────────────────────┘
```

Every Kubernetes controller does this. The Deployment controller, the ReplicaSet controller, the Node controller — all of them run continuous loops comparing desired vs actual state.

---

## 1.3 The Control Plane — Every Component Explained

The **control plane** is the brain. In your cluster, `master-1`, `master-2`, and `master-3` are the control plane nodes.

```
┌─────────────────────────────────── CONTROL PLANE NODE ───────────────────────────────────┐
│                                                                                          │
│  ┌────────────────────────┐    ┌────────────────────────┐   ┌─────────────────────────┐  │
│  │   kube-apiserver       │    │         etcd           │   │     kube-scheduler      │  │
│  │   Port: 6443 (REST)    │    │   Port: 2379 / 2380    │   │  [Active-Passive Lease] │  │
│  │   [Active-Active]      │    │   [Raft Full Replic.]  │   │                         │  │
│  └───────────▲────────────┘    └───────────▲────────────┘   └────────────▲────────────┘  │
│              │ (Only apiserver             │                             │               │
│              │  touches etcd!)             │ (HTTP Watch Stream)         │ (HTTP Watch)  │
│              └─────────────────────────────┼─────────────────────────────┘               │
│                                            │                                             │
│  ┌─────────────────────────────────────────▼──────────────────────────────────────────┐  │
│  │                              kube-controller-manager                               │  │
│  │     (ReplicaSet, Deployment, Node, Endpoint, Job, ServiceAccount Controllers)      │  │
│  │                     [Active-Passive Lease Leader Election]                         │  │
│  └────────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                          │
│  ⚠️ Taints Enforced: node-role.kubernetes.io/control-plane:NoSchedule                    │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.3.1 Active-Active vs. Active-Passive in Master Nodes

Every master node runs all 4 control plane components, but they run in **two different distributed modes**:

#### A. `kube-apiserver` — **Active-Active (Stateless)**
* **All 3 API servers are active simultaneously.**
* Because the API server holds **zero state in memory** (everything lives in etcd), a request from `kubectl` or a worker node can hit Master 1, Master 2, or Master 3. They all respond identically.
* That's why our **Keepalived VIP (`10.0.1.100:6443`)** can route requests to whichever master is currently active with zero session affinity issues.

#### B. `etcd` — **Active Distributed Consensus (Raft)**
* All 3 etcd instances form **one single clustered database**.
* Raft consensus elects **one Leader** (e.g. Master 2), while the other two are **Followers**.
* Every write must be acknowledged by a quorum majority ($\lfloor 3/2 \rfloor + 1 = 2$) before it is committed.

#### C. `kube-scheduler` & `kube-controller-manager` — **Active-Passive (Leader Election)**
* All 3 masters run the process, but **only ONE master is actively making decisions**. The other two are hot standbys.
* **Why?** 
  * If two schedulers were active at the exact same millisecond, they would both see the same empty node, schedule two different pods to it, and overload the node.
  * If two controller managers were active, both would see a missing replica and create duplicate pods.
* **How it works**: They compete for a **Lease lock** (`kube-node-lease` / `kube-system`). Whichever master holds the lease is the active leader. If Master 2 crashes, Master 1 or 3 detects the expired lease within seconds and takes over instantly.

---

### 1.3.2 etcd: Full Replication vs. Sharding

🔍 **Is etcd data split across nodes (sharded) or duplicated?**  
**It is FULL REPLICATION.** Every single master node holds an exact, 100% complete copy of the entire database.

```
┌───────────────────────┐   ┌───────────────────────┐   ┌───────────────────────┐
│       MASTER 1        │   │       MASTER 2        │   │       MASTER 3        │
│                       │   │                       │   │                       │
│  [etcd on Master 1]   │   │  [etcd on Master 2]   │   │  [etcd on Master 3]   │
│                       │   │                       │   │                       │
│  📁 Full Copy of DB   │   │  📁 Full Copy of DB   │   │  📁 Full Copy of DB   │
│  - 500 Pod specs      │   │  - 500 Pod specs      │   │  - 500 Pod specs      │
│  - 50 Secrets         │   │  - 50 Secrets         │   │  - 50 Secrets         │
│  - 20 Namespaces      │   │  - 20 Namespaces      │   │  - 20 Namespaces      │
└───────────────────────┘   └───────────────────────┘   └───────────────────────┘
```

* **Why not sharding?** Kubernetes cluster metadata is small (typically a few megabytes to 1-2 GB max). Full replication ensures that if Master 1 and Master 2 are physically destroyed, Master 3 still has the complete cluster configuration ready for recovery.
* **The Only Stateful Component**: etcd is the **ONLY stateful component** in the control plane. If you have an etcd snapshot backup file (`.db`), you have your entire cluster back.

---

### 1.3.3 The Golden Rule: ONLY `kube-apiserver` Touches etcd!

Controllers, Schedulers, Kubelets, and `kubectl` **NEVER talk to etcd directly.**

```
                      ┌───────────────┐
                      │     etcd      │
                      └───────▲───────┘
                              │ (ONLY API Server touches etcd!)
                              ▼
                      ┌───────────────┐
                      │kube-apiserver │
                      └───▲───────▲───┘
            HTTP POST     │       │ HTTP POST
     "Create ReplicaSet"  │       │ "Create Pods"
                          │       │
       ┌──────────────────┴┐     ┌┴──────────────────┐
       │Deployment Controll│     │ReplicaSet Controll│
       └───────────────────┘     └───────────────────┘
```

When any component wants to read or write cluster state, it sends an HTTPS REST request to `kube-apiserver`. The API server authenticates the client, validates the schema, checks RBAC permissions, and writes to etcd.

---

### 1.3.4 Why Kubernetes Doesn't Poll: The HTTP/2 WATCH API

If 1,000 nodes polled `GET /api/v1/pods` every 1 second, the API server would melt doing thousands of TLS handshakes and JSON serializations per second.

Instead, Kubernetes uses a push-based event streaming mechanism called the **WATCH API (`?watch=true`)**:

```
┌──────────────┐         gRPC Stream         ┌───────────────┐
│     etcd     ├────────────────────────────►│kube-apiserver │
└──────────────┘    "Key /deployments/nginx  └───────┬───────┘
                     was just ADDED!"                │
                                                     │ HTTP/2 Stream Event:
                                                     │ {"type":"ADDED", "object":...}
                                                     ▼
                                            ┌──────────────────┐
                                            │Deployment Control│
                                            └──────────────────┘
                                             (Wakes up in <1ms!)
```

* **Zero CPU when Idle**: An idle long-lived TCP connection in Linux costs **0% CPU** because the kernel uses `epoll` (event-driven I/O). The process sleeps until a packet arrives.
* **Tiny Memory**: Each open connection is just a Linux File Descriptor (FD), consuming only ~2KB to 4KB of RAM.
* **HTTP/2 Multiplexing**: A single TCP connection between a worker node and the API server streams events for Pods, Secrets, ConfigMaps, and Services over one single socket.

---

### 1.3.5 The 5-Step Relay Race: How a Pod Comes to Life

Look at the complete assembly line when you create a workload:

```
1. YOU (The User)
   Run: kubectl apply -f deployment.yaml (3 replicas)
   ──► Sends HTTP POST to kube-apiserver, which writes Deployment to etcd.
        │
        ▼
2. Deployment Controller (Watches API server)
   "Desired is 3 nginx pods. I will create a ReplicaSet object."
   ──► Sends HTTP POST to kube-apiserver, which writes ReplicaSet to etcd.
        │
        ▼
3. ReplicaSet Controller (Watches API server)
   "Desired count is 3, but 0 exist! I will create 3 Pod specs with nodeName: '' (blank)."
   ──► Sends HTTP POST to kube-apiserver, which writes 3 Pods to etcd.
        │
        ▼
4. kube-scheduler (Watches API server for unscheduled pods)
   "I see 3 unassigned pods!
    Phase 1 (Filtering): Which nodes CAN run them? (Check CPU, RAM, taints)
    Phase 2 (Scoring): Which nodes are BEST? (Least loaded, image cached)
    Decision: Pod 1 -> worker-1, Pod 2 -> worker-2, Pod 3 -> worker-3."
   ──► Sends HTTP POST to kube-apiserver to bind nodeName on the pods.
        │
        ▼
5. Kubelet on worker-1 (Watches API server for pods assigned to worker-1)
   "Pod 1 is assigned to me!
    - Calls CNI (Canal) ──► Creates network namespace and veth pair.
    - Calls CSI (Longhorn) ──► Mounts persistent storage disk.
    - Calls CRI (containerd) ──► Pulls image and starts container process!"
        │
        ▼
   🎉 CONTAINER IS RUNNING ON THE WORKER NODE! 🎉
```

---

## 1.4 The Worker Node — Every Component Explained

```
┌─────────────────────────────────── WORKER NODE ───────────────────────────────────┐
│                                                                                   │
│  ┌────────────────────────┐   ┌────────────────────────┐   ┌───────────────────┐  │
│  │        kubelet         │   │       kube-proxy       │   │  containerd       │  │
│  │   Port: 10250 (Host)   │   │  (iptables DNAT rules) │   │  CRI Engine       │  │
│  └───────────▲────────────┘   └───────────▲────────────┘   └─────────▲─────────┘  │
│              │                            │                          │            │
│              │ (CRI Socket)               │ (Watches Services)       │            │
│              └────────────────────────────┼──────────────────────────┘            │
│                                           │                                       │
│  ┌────────────────────────────────────────▼────────────────────────────────────┐  │
│  │  Canal CNI (Flannel VXLAN UDP 8472 + Calico Felix NetworkPolicies)          │  │
│  │  - Manages Pod IP allocations (10.42.x.x) and veth virtual cables           │  │
│  └─────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### 1.4.1 Kubelet (The Node Captain)
- **Runs as a native Linux systemd service** directly on the host OS (NOT inside a container).
- Watches the API server for pods assigned to its specific node hostname.
- Talks to `containerd` via the **CRI (Container Runtime Interface)** Unix socket.
- Manages health probes (`livenessProbe`, `readinessProbe`, `startupProbe`).
- If a container process crashes with exit code 1, **Kubelet restarts it locally** without needing the Master nodes to get involved!

---

### 1.4.2 CRI (Container Runtime Interface): The Universal Socket

**CRI is NOT a program; it is an open gRPC API specification.**

```
┌─────────┐      CRI gRPC over Unix Socket       ┌────────────┐
│ Kubelet ├─────────────────────────────────────►│ containerd │
└─────────┘   /run/containerd/containerd.sock    └────────────┘
```

* **Why CRI exists**: So Kubernetes doesn't write custom code for Docker, Podman, or Containerd. Kubelet just speaks standard CRI methods: `RunPodSandbox()`, `PullImage()`, `CreateContainer()`, `StartContainer()`, `StopContainer()`.
* **The Engines You Can Plug In**:
  * **`containerd`**: Industry standard (used by RKE2, EKS, GKE). Ultra-lightweight and fast.
  * **`CRI-O`**: Red Hat runtime designed specifically for Kubernetes / Podman.
  * **`Kata Containers`**: Hardware-isolated microVMs for extreme security.
  * **`Docker (via cri-dockerd)`**: Docker speaks the Docker REST API, so it requires the `cri-dockerd` adapter service to translate CRI calls.

---

### 1.4.3 `kube-proxy` vs. CNI (Canal / Flannel / Calico)

| Component | Real-World Role | What It Actually Does |
|---|---|---|
| **`kube-proxy`** | **GPS / Address Translator** | It is **NOT an in-path proxy**. It writes **DNAT rules** into the Linux kernel (`iptables`/`IPVS`) so virtual Service ClusterIPs (`10.43.x.x`) get rewritten to real Pod IPs (`10.42.x.x`) at hardware wire speed. |
| **CNI (Flannel)** | **Plumbing & Roads** | Assigns real IP addresses (`10.42.x.x`) to pods, creates the `veth` virtual cable pair, and encapsulates cross-node packets in **VXLAN UDP port 8472** tunnels. |
| **CNI (Calico)** | **Firewall** | Translates Kubernetes `NetworkPolicies` into Linux `iptables` `DROP`/`ACCEPT` rules to secure inter-pod communication. |

#### 🔍 Does a Pod's IP change when its container crashes and restarts?
**NO!** The IP address belongs to the **Pod Sandbox (`pause` container / network namespace)**, NOT the individual application container. When Node.js or NGINX crashes and Kubelet restarts it, the container restarts inside the existing network namespace with the exact same IP!

---

## 1.5 The Bootstrap Sequence: How Kubernetes Boots at Time Zero ($t = 0$)

The chicken-and-egg problem: *The API server is needed to run pods, but the control plane components themselves run as pods. How does it start?*

**The Answer: Static Pods.**

```
Time 0: No API Server. No etcd. No cluster.
        Only a raw Linux host with systemd, containerd, and Kubelet.
```

1. **Systemd starts Kubelet and containerd** on Master 1 directly on the host OS.
2. **Kubelet scans a local directory on the hard drive**:
   👉 `/var/lib/rancher/rke2/agent/pod-manifests/`
3. Inside are 4 static YAML manifests: `etcd.yaml`, `kube-apiserver.yaml`, `kube-controller-manager.yaml`, `kube-scheduler.yaml`.
4. **Kubelet starts them directly via containerd (without an API server)**.
5. The moment `kube-apiserver` and `etcd` start listening on port `6443` and `2379`, the cluster is born!

---

## 1.6 Keepalived, Floating VIP & VRRP Deep Dive

To prevent hardcoding Master 1's IP (`10.0.1.10`) into your `kubeconfig` (which would be a single point of failure), we configure a **floating Virtual IP (VIP): `10.0.1.100`** managed by **Keepalived**.

```
                        Laptop / Worker Nodes
                                 │
                                 ▼
                     Requests to 10.0.1.100:6443
                                 │
           ┌─────────────────────┼─────────────────────┐
           │ (Dead)              ▼                     │
┌───────────────────────┐   ┌───────────────────────┐  │┌───────────────────────┐
│       MASTER 1        │   │       MASTER 2        │  ││       MASTER 3        │
│   Real: 10.0.1.10     │   │   Real: 10.0.1.11     │  ││   Real: 10.0.1.12     │
│      [CRASHED]        │   │   Priority: 100       │  ││   Priority: 99        │
│                       │   │   [State: PROMOTED]   │  ││   [State: BACKUP]     │
│                       │   │ *NOW OWNS 10.0.1.100* │  ││                       │
└───────────────────────┘   └───────────────────────┘  │└───────────────────────┘
```

### 1.6.1 How Keepalived Knows Who the Other Masters Are (`unicast_peer`)
Each master's `/etc/keepalived/keepalived.conf` explicitly lists the other master IPs:

```nginx
# On Master 1 (10.0.1.10)
vrrp_instance VI_1 {
    state MASTER
    interface eth0
    virtual_router_id 51
    priority 101
    unicast_src_ip 10.0.1.10
    unicast_peer {
        10.0.1.11       # Master 2
        10.0.1.12       # Master 3
    }
    virtual_ipaddress {
        10.0.1.100/24 dev eth0
    }
}
```

* **`virtual_router_id 51`**: The VRRP Team ID shared across all 3 masters.
* **`unicast_peer`**: Sends 1-second heartbeats directly between master IPs. (We use **Unicast** instead of multicast because AWS VPCs and cloud networks block multicast traffic!).

---

### 1.6.2 How Keepalived Detects Failures:
1. **Server Power Loss**: Master 2 stops receiving the 1-second VRRP heartbeat. After 3 missed intervals (3s), Master 2 promotes itself.
2. **Kubernetes API Crash (Health Check Script)**:
   Keepalived runs `/usr/local/bin/check-rke2.sh` every 3 seconds (`curl -sk https://127.0.0.1:6443/healthz`).
   * If the API server crashes on Master 1, the script fails.
   * Keepalived drops Master 1's priority by 20 ($101 - 20 = \mathbf{81}$).
   * Master 2 (priority 100) sees $100 > 81$ and claims the VIP!

---

### 1.6.3 The Switch Hand-off: Gratuitous ARP (GARP)
When Master 2 claims `10.0.1.100`, it broadcasts a **Gratuitous ARP** packet to the network switch:
*"Hey switch! 10.0.1.100 is now at Master 2's MAC address!"*  
The switch updates its MAC table in **1 millisecond**, routing all traffic to Master 2 with zero manual intervention.

---

## 1.7 Summary & What Comes Next

You now have the complete foundational theory:
- ✅ Why `kube-apiserver` is Active-Active while Schedulers/Controllers are Active-Passive
- ✅ Why `etcd` is the ONLY stateful component and uses Full 100% Replication
- ✅ How the HTTP/2 WATCH API eliminates polling and saves CPU
- ✅ The 5-step relay race from `kubectl apply` to Kubelet container execution
- ✅ CRI vs CNI vs `kube-proxy` division of labor
- ✅ How Static Pods bootstrap the control plane at $t=0$
- ✅ How Keepalived Unicast VRRP and Gratuitous ARP manage floating VIP failover

**Next**: [Doc 02 - Node Preparation →](./02_node_preparation.md)  
You'll prepare all 6 Ubuntu 24.04 servers with the exact kernel settings, swap rules, and firewall ports RKE2 needs to not silently fail.

---

*Doc 01 of 14 | Complete RKE2 Kubernetes Architecture & SRE Mastery Series*
