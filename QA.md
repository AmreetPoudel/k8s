# Kubernetes & RKE2 Mastery: Staff & Senior DevOps Interview-Ready Q&A Guide
## Production Scenarios, Kernel Mechanics, 15-Second Pitches & Senior Gotchas

> **Repository**: `https://github.com/AmreetPoudel/k8s.git`  
> **Target Audience**: Senior DevOps Engineers, SREs, Platform Engineers, and Kubernetes Architects.  
> **Format for Each Question**:
> 1. **🎯 The Interview Question & Scenario**
> 2. **⚡ 15-Second Direct Answer (The Elevator Pitch)**
> 3. **🔍 Deep-Dive Technical Mechanics (Kernel & Architecture)**
> 4. **⚠️ Senior SRE Production Gotcha / Real-World Pitfall**
> 5. **🛠️ Key Commands / YAML Cheat Sheet**

---

## Table of Contents
1. [Core Kubernetes Architecture & Control Plane (Doc 01)](#1-core-kubernetes-architecture--control-plane-doc-01)
2. [Raft Consensus & Distributed Systems (Doc 01b)](#2-raft-consensus--distributed-systems-doc-01b)
3. [Node Preparation, Kernel Tuning & Linux Subsystems (Doc 02)](#3-node-preparation-kernel-tuning--linux-subsystems-doc-02)
4. [PKI & Certificate Infrastructure (Doc 03)](#4-pki--certificate-infrastructure-doc-03)
5. [etcd Deep Dive, Operations & Disaster Recovery (Doc 04)](#5-etcd-deep-dive-operations--disaster-recovery-doc-04)
6. [CNI & Cluster Networking — Canal, Flannel & Calico (Doc 05)](#6-cni--cluster-networking--canal-flannel--calico-doc-05)
7. [Master Bootstrap, HA & Keepalived Floating VIP (Doc 06)](#7-master-bootstrap-ha--keepalived-floating-vip-doc-06)
8. [Worker Nodes, Taints & Supervisor Port 9345 (Doc 07)](#8-worker-nodes-taints--supervisor-port-9345-doc-07)
9. [RBAC & API Server 3-Gate Security (Doc 08)](#9-rbac--api-server-3-gate-security-doc-08)
10. [Persistent Storage & Longhorn 3-Way Replication (Doc 09)](#10-persistent-storage--longhorn-3-way-replication-doc-09)
11. [Ingress & MetalLB Layer-2 Load Balancing (Doc 10)](#11-ingress--metallb-layer-2-load-balancing-doc-10)

---

## 1. Core Kubernetes Architecture & Control Plane (Doc 01)

### Q1.1: Active-Active vs. Active-Passive Control Plane Design
**🎯 Interview Question**: *"In a High-Availability 3-master Kubernetes cluster, why is `kube-apiserver` active on all masters while `kube-scheduler` and `kube-controller-manager` run in active-passive mode?"*

**⚡ 15-Second Pitch**:  
`kube-apiserver` is completely stateless—all cluster state lives in etcd—so any instance can process concurrent reads and writes safely. Schedulers and controller managers run stateful decision loops; having multiple schedulers active simultaneously would trigger race conditions and conflicting pod assignments.

**🔍 Deep-Dive Mechanics**:
* The API server acts as a stateless REST gateway over etcd.
* Schedulers and controller managers use **Leader Election (Lease locks in etcd)** via the `coordination.k8s.io` API.
* The active leader holds a Lease object and renews it every few seconds. If the leader crashes, standby instances detect the expired lease and elect a new leader within seconds.

**⚠️ Senior SRE Gotcha**:  
If etcd experiences high disk write latency, the active scheduler may fail to renew its lease in time. This causes **Leader Flapping** (leadership constantly bouncing between masters), stalling pod scheduling even though all master nodes are online.

---

### Q1.2: The Single Source of Truth & Direct etcd Access
**🎯 Interview Question**: *"Can worker nodes, kubelets, or controllers talk directly to etcd to improve performance?"*

**⚡ 15-Second Pitch**:  
Never. The `kube-apiserver` is the ONLY component in the entire universe allowed to talk directly to etcd. All other components must talk to the API server via HTTPS.

**🔍 Deep-Dive Mechanics**:
* Direct access would bypass authentication (mTLS), authorization (RBAC), admission controller policies, schema validation, and audit logging.
* The API server acts as the data access layer, maintaining an in-memory watch cache that protects etcd from excessive read load.

**⚠️ Senior SRE Gotcha**:  
In post-mortems where clusters were compromised, attackers who gained network access to port 2379 were able to dump all cluster secrets in plaintext using `etcdctl`. Always isolate etcd using dedicated mTLS CAs and private network firewalls.

---

### Q1.3: HTTP/2 WATCH API vs. REST Polling Loops
**🎯 Interview Question**: *"How does Kubernetes scale to thousands of nodes without overwhelming the control plane with polling requests?"*

**⚡ 15-Second Pitch**:  
Kubernetes uses HTTP/2 long-lived streaming connections with the `?watch=true` parameter. Instead of nodes repeatedly polling the database, the API server uses Linux `epoll` event-driven notifications to push updates to clients in real-time.

**🔍 Deep-Dive Mechanics**:
* Traditional polling (1,000 nodes polling every second) requires 1,000 TLS handshakes and database queries per second, consuming massive CPU and bandwidth.
* An HTTP/2 watch connection stays open indefinitely. When an object in etcd changes, the API server broadcasts the diff over the existing stream. An idle watch connection consumes **~0% CPU and ~3KB RAM**.

---

### Q1.4: The "Chicken-and-Egg" Bootstrap ($t=0$) & Static Pods
**🎯 Interview Question**: *"When you turn on a brand-new Kubernetes server at $t=0$, how do `etcd` and `kube-apiserver` start if there is no Kubernetes cluster running to schedule them?"*

**⚡ 15-Second Pitch**:  
They run as **Static Pods** managed directly by the host `kubelet` systemd service. Kubelet monitors a local directory on disk and launches the core control plane containers via the Container Runtime Interface (CRI) with zero API server dependencies.

**🔍 Deep-Dive Mechanics**:
* In RKE2, `containerd` and `kubelet` run as native Linux systemd services.
* On startup, Kubelet scans `/var/lib/rancher/rke2/agent/pod-manifests/`.
* When it finds `etcd.yaml` and `kube-apiserver.yaml`, Kubelet creates the containers directly. Once the API server is up, Kubelet creates "Mirror Pods" so they appear in `kubectl get pods -n kube-system`.

**🛠️ Key Verification**:
```bash
# View static pod manifests on a master node:
ls -la /var/lib/rancher/rke2/agent/pod-manifests/
```

---

### Q1.5: The `pause` Container ("The Hotel Room")
**🎯 Interview Question**: *"Why does a Kubernetes Pod retain its IP address when its application container crashes and restarts?"*

**⚡ 15-Second Pitch**:  
The Pod IP is not attached to your application container; it is attached to an invisible, lightweight **`pause` container** that holds the Linux Network Namespace open for the lifetime of the Pod.

**🔍 Deep-Dive Mechanics**:
* When Kubelet creates a Pod, it starts the `pause` container (written in C, uses ~100KB RAM) which calls `unshare()` to create network, IPC, and UTS namespaces.
* Application containers join that existing namespace using `--net=container:<pause-id>`.
* If the app container crashes, Kubelet restarts it inside the still-running `pause` namespace, preserving the IP address, open ports, and localhost networking.

---

## 2. Raft Consensus & Distributed Systems (Doc 01b)

### Q2.1: The Quorum Formula & Why Odd Master Counts
**🎯 Interview Question**: *"Why do production Kubernetes clusters strictly require 3 or 5 master nodes? Why not 2 or 4?"*

**⚡ 15-Second Pitch**:  
Raft requires a strict majority **Quorum ($\lfloor N/2 \rfloor + 1$)** to commit writes. An even number of nodes increases network communication overhead without increasing fault tolerance, and risks 50/50 split-brain partitions.

**🔍 Deep-Dive Mechanics**:
* **2 Nodes**: Quorum = 2. Tolerates **0** failures (if 1 node dies, $1 < 2$, cluster goes read-only).
* **3 Nodes**: Quorum = 2. Tolerates **1** failure ($3 - 1 = 2 \ge 2$ ✅).
* **4 Nodes**: Quorum = 3. Tolerates **1** failure ($4 - 1 = 3 \ge 3$; same tolerance as 3 nodes, but higher latency).
* **5 Nodes**: Quorum = 3. Tolerates **2** failures.

**⚠️ Senior SRE Gotcha**:  
If a 3-master cluster loses 2 masters, the remaining single master **cannot accept any new writes** (no deployments, no pod restarts). However, **existing pods on workers continue running** because their local containers and iptables rules remain active in the kernel.

---

### Q2.2: Raft Step-by-Step Write Protocol
**🎯 Interview Question**: *"Walk me through the exact packet sequence when a deployment is created in a 3-master etcd cluster."*

**⚡ 15-Second Pitch**:  
The client writes to the API Server $\rightarrow$ API Server forwards to the etcd Leader $\rightarrow$ Leader writes uncommitted entry to disk $\rightarrow$ Leader replicates to all followers $\rightarrow$ Once Quorum (2 of 3) acknowledge disk write $\rightarrow$ Leader marks entry as COMMITTED $\rightarrow$ Returns success to client.

```
Client ──► API Server ──► etcd Leader
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   Follower 1 (Disk Write)           Follower 2 (Disk Write)
            │                                 │
            └──────────────► ACK ◄────────────┘
                             │
                     (Quorum Reached: 2/3)
                             │
            Leader Marks COMMITTED & Replies Success!
```

---

## 3. Node Preparation, Kernel Tuning & Linux Subsystems (Doc 02)

### Q3.1: The Kernel Reason Why Swap Must Be Disabled
**🎯 Interview Question**: *"Why does Kubernetes require swap to be disabled at the Linux kernel level?"*

**⚡ 15-Second Pitch**:  
Kubernetes enforces pod memory limits using Linux **cgroups**. Swap space undermines cgroup guarantees, allowing memory-leaking containers to page to disk instead of triggering an OOM-kill, leading to silent performance degradation and broken node eviction calculations.

**🔍 Deep-Dive Mechanics**:
* Kubelet monitors `memory.available` from cgroups to trigger node-pressure evictions.
* When swap is enabled, anonymous memory pages are swapped to disk, masking true memory usage from Kubelet.
* Disk paging causes severe latency spikes in CPU-bound applications, leading to false liveness probe failures and cascading pod restart loops.

**🛠️ Production Command**:
```bash
# Disable swap immediately and permanently:
swapoff -a
sed -i '/\sswap\s/d' /etc/fstab
```

---

### Q3.2: The `overlay` Kernel Module & Copy-on-Write (CoW)
**🎯 Interview Question**: *"What does the `overlay` kernel module do in container runtimes?"*

**⚡ 15-Second Pitch**:  
It enables **OverlayFS**, a union-mount filesystem that layers read-only container image layers (`lowerdir`) underneath a thin, writable container layer (`upperdir`) using Copy-on-Write (CoW).

**🔍 Deep-Dive Mechanics**:
* Without OverlayFS, container runtimes must copy gigabytes of image data for every single pod instance.
* With OverlayFS, 100 pods running the same image share the exact same underlying read-only disk blocks. The container runtime only allocates disk space when a file inside the container is modified.

---

### Q3.3: `br_netfilter` & `bridge-nf-call-iptables` (The #1 Networking Bug)
**🎯 Interview Question**: *"You deploy a cluster, but pods on the same worker node cannot talk to their own ClusterIP Service. Direct pod-to-pod IP works fine. What is broken?"*

**⚡ 15-Second Pitch**:  
The **`br_netfilter`** kernel module is missing or `net.bridge.bridge-nf-call-iptables` is set to `0`. Linux bridge traffic is operating purely at Layer 2 and bypassing Layer 3 `iptables`, preventing `kube-proxy` from performing DNAT on the Service IP.

**🔍 Deep-Dive Mechanics**:
* Linux bridges (like `cni0`) act as Layer 2 switches and forward packets by MAC address without consulting netfilter.
* Kubernetes ClusterIPs are "phantom" virtual IPs that exist solely as `iptables` DNAT rules.
* `br_netfilter` hooks into the bridge data path and forces bridged packets into `iptables PREROUTING`, where kube-proxy rewrites the destination from Service IP to Pod IP.

```
[Pod A] ──► [Linux Bridge (cni0)]
                   │
                   ▼ (Forced by br_netfilter)
             [iptables / netfilter]
             DNAT: Service IP -> Pod B IP
                   │
                   ▼
[Pod B] ◄── [Linux Bridge (cni0)]
```

**🛠️ Production Command**:
```bash
modprobe br_netfilter
echo "net.bridge.bridge-nf-call-iptables = 1" >> /etc/sysctl.d/99-kubernetes.conf
sysctl --system
```

---

### Q3.4: Clock Drift & etcd Raft Stability
**🎯 Interview Question**: *"What happens if master node clocks drift by 2 seconds?"*

**⚡ 15-Second Pitch**:  
Clock drift breaks Raft heartbeat election timeouts. Followers prematurely assume the leader has died, triggering constant, cascading leader election cycles that make the API server unavailable.

**🔍 Deep-Dive Mechanics**:
* Raft election timeout is typically 1,000ms.
* Clock skew > 1 second causes followers to drop out of sync and trigger elections. Clock skew > 5 seconds can cause split-brain instability and read transaction timeouts.
* Always enforce NTP synchronization using `timedatectl` or `chrony`.

---

## 4. PKI & Certificate Infrastructure (Doc 03)

### Q4.1: How Kubernetes Authenticates Components (mTLS & Identity)
**🎯 Interview Question**: *"Explain how `kube-apiserver` authenticates a connection from `kube-scheduler` without using passwords or tokens."*

**⚡ 15-Second Pitch**:  
Via **Mutual TLS (mTLS)**. The scheduler presents an X.509 client certificate signed by the cluster CA. The API server verifies the signature, extracts the **Common Name (`CN=system:kube-scheduler`)** as the username, and authorizes permissions via RBAC.

**🔍 Deep-Dive Mechanics**:
* Both parties verify each other: the client verifies the server certificate against the cluster CA, and the server verifies the client certificate.
* Client certificates contain:
  * **`CN` (Common Name)** = Kubernetes Username
  * **`O` (Organization)** = Kubernetes Group (e.g. `system:masters`)
* No database lookup is needed; authentication is a local cryptographic signature verification.

---

### Q4.2: The Subject Alternative Name (SAN) Error
**🎯 Interview Question**: *"You set up a Keepalived VIP at `10.0.1.100`. When connecting with kubectl from your laptop, you get `x509: certificate is valid for 10.0.1.10, not 10.0.1.100`. How do you fix this?"*

**⚡ 15-Second Pitch**:  
The API server's TLS certificate does not have the VIP listed in its **Subject Alternative Names (SANs)** whitelist. The client TLS handshake rejects it to prevent MITM attacks. Add the VIP to `tls-san` in RKE2 config and restart the server.

**🛠️ Production Fix in `/etc/rancher/rke2/config.yaml`**:
```yaml
tls-san:
  - "10.0.1.100"               # Keepalived VIP
  - "k8s.mycompany.com"         # DNS Load Balancer
```

---

### Q4.3: Why etcd Uses a Dedicated Certificate Authority
**🎯 Interview Question**: *"Why does RKE2 generate a separate CA for etcd inside `/var/lib/rancher/rke2/server/tls/etcd/` instead of using the main Kubernetes cluster CA?"*

**⚡ 15-Second Pitch**:  
**Principle of Least Privilege / Blast Radius Containment**. It prevents standard Kubernetes client certificates (like a compromised developer or kubelet cert) from ever connecting directly to etcd on port 2379 to dump raw secrets.

---

## 5. etcd Deep Dive, Operations & Disaster Recovery (Doc 04)

### Q5.1: The Space Quota Exhaustion Bug (`mvcc: database space exceeded`)
**🎯 Interview Question**: *"Your cluster has only 15 pods running, but all write requests fail with `etcdserver: mvcc: database space exceeded`. What happened and how do you resolve it in production?"*

**⚡ 15-Second Pitch**:  
etcd uses Multi-Version Concurrency Control (MVCC) and keeps historic revisions of all created, updated, and deleted keys. Over time, historical tombstones exhausted the default 2GB quota. You must **Compact**, **Defrag**, and **Disarm** the alarm.

**🛠️ The 3-Step SRE Fix**:
```bash
# 1. Compact old revisions up to current revision
REV=$(etcdctl endpoint status --write-out="json" | jq .[0].Status.header.revision)
etcdctl compact $REV

# 2. Defrag to shrink the .db file on disk and return space to OS
etcdctl defrag

# 3. Disarm the NOSPACE write-lock alarm
etcdctl alarm disarm
```

---

### Q5.2: Multi-Master etcd Disaster Recovery
**🎯 Interview Question**: *"All 3 masters in your cluster have corrupted etcd state. You have a valid backup snapshot file. What is the exact recovery procedure?"*

**⚡ 15-Second Pitch**:  
Stop RKE2 on all 3 masters $\rightarrow$ Restore the snapshot on **Master-1 ONLY** using `--cluster-reset` $\rightarrow$ **Wipe the `/db/` folder on Master-2 and Master-3** $\rightarrow$ Start Master-1, then start Master-2 and Master-3 so they join as clean members and replicate via Raft.

**⚠️ Senior SRE Gotcha**:  
If you restore the snapshot on all 3 masters independently, each node will generate different cluster IDs and refuse to form a Raft quorum, resulting in a permanent split-brain lock.

---

## 6. CNI & Cluster Networking — Canal, Flannel & Calico (Doc 05)

### Q6.1: Flannel vs. Calico Division of Labor in Canal
**🎯 Interview Question**: *"What is the difference between Flannel and Calico in RKE2's default Canal CNI?"*

**⚡ 15-Second Pitch**:  
**Flannel is the Plumber**: it manages IPAM (assigning pod IP subnets) and encapsulates cross-node packets using VXLAN (UDP port 8472).  
**Calico (Felix) is the Security Guard**: it watches `NetworkPolicy` objects and writes `iptables`/`ipset` firewall rules to allow or drop traffic.

---

### Q6.2: Step-by-Step Cross-Node Packet Walk (VXLAN)
**🎯 Interview Question**: *"Trace a packet from Pod A (`10.42.1.5` on Worker-1) to Pod B (`10.42.2.8` on Worker-2). What does the physical router see?"*

**⚡ 15-Second Pitch**:  
The physical router **never sees Pod IPs**. Flannel wraps the pod packet inside an **Outer UDP Packet** (`10.0.2.10 -> 10.0.2.11` on Port 8472). The destination node decapsulates it and delivers the inner packet to Pod B.

```
[ Pod A: 10.42.1.5 ] ──► [ flannel.1 ]
                              │
                              ▼ (Encapsulates)
┌─────────────────────────────────────────────────────────────┐
│ Outer Header : Src 10.0.2.10 (Worker-1) -> Dst 10.0.2.11   │ (UDP 8472)
│ Inner Payload: Src 10.42.1.5 (Pod A)    -> Dst 10.42.2.8    │ (TCP 80)
└─────────────────────────────────────────────────────────────┘
                              │
               (Crosses Physical Cloud Switch)
                              │
[ Pod B: 10.42.2.8 ] ◄── [ Calico Ingress Check ] ◄── [ flannel.1 Decapsulates ]
```

---

### Q6.3: Network Troubleshooting Matrix
**🎯 Interview Question**: *"How do you diagnose why a pod cannot reach another service?"*

| Symptom | Broken Component | Debug Step |
| :--- | :--- | :--- |
| `Could not resolve host: my-svc` | **CoreDNS** | `kubectl logs -n kube-system -l k8s-app=kube-dns` |
| Ping to Pod IP works, Service IP fails | **`kube-proxy`** | `iptables -t nat -L KUBE-SERVICES -n \| grep <ClusterIP>` |
| Pods on same node talk, cross-node fails | **Flannel / UDP 8472** | Verify UDP port 8472 is open in Cloud Security Groups |
| Connection times out on specific ports | **Calico NetworkPolicy** | `iptables -L -n -v \| grep DROP` |

---

## 7. Master Bootstrap, HA & Keepalived Floating VIP (Doc 06)

### Q7.1: How Keepalived Binds Floating VIPs to Physical Interfaces
**🎯 Interview Question**: *"Does Keepalived create a virtual network interface for its VIP (`10.0.1.100`), and how does failover happen in under 1 second?"*

**⚡ 15-Second Pitch**:  
Keepalived binds the VIP directly to the **real physical network interface (e.g. `eth0`) as a Secondary IP**. On failover, the new master adds the secondary IP to its NIC and broadcasts a **Gratuitous ARP (GARP)** packet to update the network switch's MAC address table immediately.

**🔍 Deep-Dive Mechanics**:
* Linux interfaces natively support multiple IP addresses (`ip addr show eth0`).
* When Master-1 fails its health script (`/usr/local/bin/check-rke2.sh`), its VRRP priority drops. Master-2 takes over, assigns `10.0.1.100` to its `eth0`, and sends GARP packets so all future frames for `10.0.1.100` go to Master-2's MAC address.

---

### Q7.2: Unicast VRRP vs. Multicast
**🎯 Interview Question**: *"Why does our Keepalived configuration use `unicast_peer` instead of standard VRRP multicast?"*

**⚡ 15-Second Pitch**:  
Standard VRRP uses multicast (`224.0.0.18`), which is **blocked by default in AWS VPCs, Azure, GCP, and hardened enterprise VLANs**. Unicast explicitly targets the private IPs of peer masters, guaranteeing failover works on any cloud or virtualized network.

---

## 8. Worker Nodes, Taints & Supervisor Port 9345 (Doc 07)

### Q8.1: Port 9345 (Supervisor) vs. Port 6443 (API Server)
**🎯 Interview Question**: *"Why do joining worker nodes connect to port `9345` instead of standard Kubernetes API port `6443`?"*

**⚡ 15-Second Pitch**:  
Port `6443` is pure upstream Kubernetes that requires clients to already have valid TLS certificates. Port `9345` is RKE2's lightweight supervisor registration endpoint where brand-new empty nodes present their **node token** to download cluster CAs and bootstrap their initial certificates.

---

### Q8.2: Master Taints & Blast Radius Protection
**🎯 Interview Question**: *"Why must master nodes be tainted with `node-role.kubernetes.io/control-plane:NoSchedule` in production?"*

**⚡ 15-Second Pitch**:  
To prevent user application workloads from being scheduled on control plane nodes. If an application pod experiences a memory leak, CPU spike, or fork bomb, it will only crash worker nodes and will **never starve `etcd` or `kube-apiserver` of resources**.

---

## 9. RBAC & API Server 3-Gate Security (Doc 08)

### Q9.1: The Reusable `ClusterRole` + Namespaced `RoleBinding` Pattern
**🎯 Interview Question**: *"How do you grant a developer read-only access to only the `dev` namespace using the built-in `view` ClusterRole?"*

**⚡ 15-Second Pitch**:  
Create a namespaced **`RoleBinding`** inside the `dev` namespace that references the `ClusterRole: view`. The `RoleBinding` restricts the cluster-wide definition strictly to the `dev` namespace.

**🛠️ Production YAML**:
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: alice-view
  namespace: dev                   # Strictly scoped to "dev"
subjects:
- kind: User
  name: alice
roleRef:
  kind: ClusterRole                # Reusable definition
  name: view
  apiGroup: rbac.authorization.k8s.io
```

---

### Q9.2: The 3 Gates of the Kubernetes API Server
**🎯 Interview Question**: *"A user with full `cluster-admin` RBAC permissions tries to deploy a pod with `privileged: true` and gets rejected with a 403 Forbidden error. Why did this happen?"*

**⚡ 15-Second Pitch**:  
RBAC only controls Gate 2 (**Authorization**). The request was rejected at Gate 3 (**Admission Control / Pod Security Standards**) because the target namespace enforces a `restricted` Pod Security policy that bans privileged root containers regardless of RBAC.

```
Request ──► [Gate 1: AuthN] ──► [Gate 2: AuthZ / RBAC] ──► [Gate 3: Admission Control] ──► etcd
            (Who are you?)      (Allowed to verb?)          (Is YAML compliant & safe?)
```

---

### Q9.3: SRE Debugging with `kubectl auth can-i`
**🎯 Interview Question**: *"A CI/CD ServiceAccount in the `staging` namespace is failing to deploy. How do you test its exact permissions without logging in as that bot?"*

**⚡ 15-Second Pitch**:  
Use `kubectl auth can-i` with the `--as` impersonation flag:

```bash
kubectl auth can-i create deployments \
  --namespace=staging \
  --as=system:serviceaccount:staging:github-actions-deployer
# Returns: yes or no
```

---

## 10. Persistent Storage & Longhorn 3-Way Replication (Doc 09)

### Q10.1: PV vs. PVC vs. StorageClass
**🎯 Interview Question**: *"Explain the relationship between PersistentVolume (PV), PersistentVolumeClaim (PVC), and StorageClass."*

**⚡ 15-Second Pitch**:  
* **StorageClass** = The automated disk factory / provisioner (e.g. `longhorn`).
* **PVC** = The user's order ticket (e.g. *"I need 20GB ReadWriteOnce"*).
* **PV** = The actual provisioned storage volume bound exclusively to the PVC.

---

### Q10.2: Access Modes: RWO vs. RWX (The Common Node Trap)
**🎯 Interview Question**: *"What does `ReadWriteOnce` (RWO) actually mean? Can two pods mount the same RWO volume?"*

**⚡ 15-Second Pitch**:  
RWO means the volume can be mounted read-write by **only ONE worker node at a time**. Two pods can mount the same RWO volume **only if they are running on the exact same worker node**. For multi-node concurrent writes, you must use **`ReadWriteMany` (RWX)**.

---

### Q10.3: Why Longhorn is Needed on Bare Metal (3-Way Replication)
**🎯 Interview Question**: *"If a worker node running a MySQL database experiences a motherboard failure, what happens with local storage vs. Longhorn storage?"*

**⚡ 15-Second Pitch**:  
* **Local Storage (`local-path`)**: Data is pinned to that physical node. When the node dies, the database is lost.
* **Longhorn Storage**: Replicates every disk block synchronously across **3 separate worker nodes** over iSCSI. When Kubernetes reschedules MySQL on Worker-2, Longhorn attaches the healthy local replica on Worker-2 with **zero data loss**.

---

## 11. Ingress & MetalLB Layer-2 Load Balancing (Doc 10)

### Q11.1: Why MetalLB is Required on Bare Metal
**🎯 Interview Question**: *"Why does a Kubernetes Service with `type: LoadBalancer` get stuck in `<pending>` on bare metal, and how does MetalLB solve this?"*

**⚡ 15-Second Pitch**:  
Bare-metal environments lack cloud provider controller APIs (like AWS NLB/ALB) to provision external load balancers. MetalLB manages a pool of real network IP addresses (e.g. `10.0.1.200 - 10.0.1.220`) and uses **Layer-2 ARP** to respond to network switches on behalf of the assigned LoadBalancer IP.

**🔍 Deep-Dive Mechanics**:
* In AWS, the cloud-controller-manager provisions an external ELB when `type: LoadBalancer` is detected.
* On bare-metal, MetalLB runs a `controller` pod (allocates IPs from `IPAddressPool`) and a `speaker` DaemonSet on every node.
* In Layer-2 mode, when a service is assigned `10.0.1.200`, the `speaker` pod on one worker node answers ARP requests for that IP, attracting all incoming Layer-2 frames to its network card.

**⚠️ Senior SRE Gotcha**:  
In MetalLB Layer-2 mode, **all traffic for a single LoadBalancer IP flows through a single active worker node** (single-node failover, not multi-node ECMP load balancing). If that worker node dies, MetalLB sends a Gratuitous ARP (GARP) to shift the IP to another node in ~1-3 seconds. For true multi-node bandwidth distribution on bare metal, you must use **MetalLB BGP mode** paired with upstream ToR (Top-of-Rack) hardware switches.

---

### Q11.2: NGINX Ingress Controller Routing & TLS Secret Termination
**🎯 Interview Question**: *"How does NGINX Ingress Controller handle Host/Path-based routing and manage SSL/TLS certificate termination across multiple microservices?"*

**⚡ 15-Second Pitch**:  
NGINX Ingress acts as an intelligent Layer-7 reverse proxy listening on standard Ports `80` and `443`. It inspects the HTTP **Host Header** and **URI Path**, terminates TLS using certificates stored in `kubernetes.io/tls` Secrets, and proxies plain HTTP traffic directly to backing pod endpoints.

**🔍 Deep-Dive Mechanics**:
* NGINX Ingress Controller watches the Kubernetes API for `Ingress` and `Secret` objects in real-time.
* It dynamically updates its internal `nginx.conf` and in-memory Lua routing table with zero reload downtime.
* When HTTPS traffic hits port 443, NGINX executes the SSL handshake using the private key and cert from the referenced TLS Secret, offloading CPU-intensive cryptography from application containers.

**🛠️ Production YAML Cheat Sheet**:
```yaml
# 1. Create the TLS Secret:
# kubectl create secret tls shop-tls --cert=tls.crt --key=tls.key -n production

# 2. Ingress Resource with Host + Path Routing & TLS:
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: shop-ingress
  namespace: production
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - shop.mycompany.com
    secretName: shop-tls           # Holds SSL cert and private key
  rules:
  - host: shop.mycompany.com
    http:
      paths:
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: backend-api-svc
            port:
              number: 8080

```
## 12. Helm & Workloads — StatefulSets vs. Deployments (Doc 11)

### Q12.1: Deployment vs. StatefulSet ("Cattle vs. Pets")
**🎯 Interview Question**: *"Why can't you run a production database (like MySQL or Kafka) inside a standard Kubernetes Deployment, and what guarantees does a StatefulSet provide?"*

**⚡ 15-Second Pitch**:  
Deployments treat pods as interchangeable, stateless "cattle" with random hashes and shared storage. StatefulSets treat pods as distinct "pets" with stable network identities (`mysql-0`, `mysql-1`), ordered sequential startups/teardowns, and dedicated, permanently attached storage volumes via `volumeClaimTemplates`.

**🔍 Deep-Dive Mechanics**:
* **Identity**: StatefulSet pods have a zero-indexed ordinal index (`app-0`, `app-1`, `app-2`) that persists across restarts and node rescheduling.
* **Ordered Execution**: The controller starts pod `N` only after pod `N-1` is in `Running and Ready` state. Scaling down terminates in strict reverse order (`2 -> 1 -> 0`).
* **Dedicated Storage (`volumeClaimTemplates`)**: The controller dynamically generates an exclusive PVC for each pod (e.g. `data-mysql-0`, `data-mysql-1`). If `mysql-0` dies and is recreated on a different node, the volume binder re-attaches the existing `data-mysql-0` PVC to the new pod instance.

---

### Q12.2: Headless Services (`clusterIP: None`) & Direct Pod Addressing
**🎯 Interview Question**: *"What is a Headless Service, why does it set `clusterIP: None`, and how do stateful distributed databases use it?"*

**⚡ 15-Second Pitch**:  
A Headless Service disables the virtual ClusterIP and proxy load balancing. Instead of returning a single shared IP, CoreDNS returns A-records pointing directly to the individual Pod IPs, allowing clients and database nodes to address specific instances directly (e.g. `mysql-0.mysql-headless`).

**🔍 Deep-Dive Mechanics**:
* Standard Service: DNS lookup for `my-svc` returns single ClusterIP $\rightarrow$ kube-proxy load balances randomly across pods.
* Headless Service (`clusterIP: None`): CoreDNS creates individual SRV/A records for every pod matching the selector:
  `<pod-name>.<service-name>.<namespace>.svc.cluster.local`
* Primary/Replica databases use this to establish replication streams directly between peers and route writes strictly to `pod-0`.

**🛠️ Production YAML Cheat Sheet**:
```yaml
# 1. Headless Service (No Virtual ClusterIP):
apiVersion: v1
kind: Service
metadata:
  name: mysql-headless
  namespace: production
spec:
  clusterIP: None                  # Headless!
  selector:
    app: mysql
  ports:
    - port: 3306
      name: mysql
---
# 2. StatefulSet with Dedicated PVC Templates:
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mysql
  namespace: production
spec:
  serviceName: mysql-headless      # Required link to headless service
  replicas: 3
  selector:
    matchLabels:
      app: mysql
  template:
    metadata:
      labels:
        app: mysql
    spec:
      containers:
      - name: mysql
        image: mysql:8.0
        ports:
        - containerPort: 3306
        volumeMounts:
        - name: data
          mountPath: /var/lib/mysql
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      storageClassName: longhorn
## 13. Monitoring & Observability — Prometheus, Grafana & Alertmanager (Doc 12)

### Q13.1: The Kubernetes Monitoring Stack Architecture
**🎯 Interview Question**: *"Explain the end-to-end flow of the Prometheus monitoring architecture in a Kubernetes cluster: who collects what, where is it stored, and how do alerts reach SREs?"*

**⚡ 15-Second Pitch**:  
Prometheus uses a pull-based model to scrape `/metrics` endpoints every 15-30s. `node-exporter` gathers host OS metrics, `kube-state-metrics` gathers Kubernetes object health, and core components (etcd/apiserver) expose internal telemetry. Prometheus stores metrics in its TSDB, Grafana queries Prometheus for dashboards, and Alertmanager handles alert deduplication and routing to Slack/PagerDuty.

**🔍 Deep-Dive Mechanics**:
* **Prometheus Server**: Core time-series database (TSDB) and query engine (PromQL). Periodically executes HTTP GET requests against discovered scrape targets.
* **`node-exporter` (DaemonSet)**: Runs on every single node, reading kernel statistics from `/proc` and `/sys` (CPU, memory paging, network dropped packets, disk IOPS).
* **`kube-state-metrics` (Deployment)**: Watches the Kubernetes API and generates metrics about object health (e.g. `kube_pod_status_phase`, `kube_deployment_status_replicas_unavailable`).
* **`Alertmanager`**: Decoupled alert processor. When Prometheus PromQL alert expressions evaluate to `true` for `for: 5m`, Prometheus sends the firing alert payload to Alertmanager to handle grouping, silencing, and notification delivery.

---

### Q13.2: Static Scrape Configs vs. Prometheus Operator `ServiceMonitor`
**🎯 Interview Question**: *"What is a `ServiceMonitor` in Kubernetes, and why is it preferred over editing `prometheus.yaml`?"*

**⚡ 15-Second Pitch**:  
A `ServiceMonitor` is a Kubernetes Custom Resource (CRD) managed by the Prometheus Operator. It allows application teams to declaratively define scrape targets using Kubernetes label selectors, enabling Prometheus to dynamically discover and scrape new microservices without restarting or editing central configuration files.

**🛠️ Production YAML Cheat Sheet**:
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: app-metrics-monitor
  namespace: production
  labels:
    release: prometheus            # Must match Prometheus Operator release selector
spec:
  selector:
    matchLabels:
      app: backend-api             # Discovers any Service matching this label
```
## 14. Production Troubleshooting & Disaster Scenarios (Doc 13)

### Q14.1: The CoreDNS "5-Second Delay" & `ndots:5` UDP Race
**🎯 Interview Question**: *"Your microservices intermittently experience exactly 5.00-second latency spikes when calling external APIs (e.g. Stripe, AWS S3). How do you diagnose and fix this at the kernel/DNS level?"*

**⚡ 15-Second Pitch**:  
Pods default to `ndots:5` in `/etc/resolv.conf`, causing glibc to query internal cluster search domains before external names. When paired with Linux kernel `conntrack` UDP race conditions on parallel A/AAAA lookups, one packet is dropped, triggering the default 5-second DNS retransmit timeout. Fix by setting `ndots:2` and `single-request-reopen`.

**🔍 Deep-Dive Mechanics**:
* When resolving `api.stripe.com` (2 dots < 5), the resolver queries:
  1. `api.stripe.com.default.svc.cluster.local` (NXDOMAIN)
  2. `api.stripe.com.svc.cluster.local` (NXDOMAIN)
  3. `api.stripe.com.cluster.local` (NXDOMAIN)
  4. `api.stripe.com` (SUCCESS)
* Under high concurrency, netfilter conntrack drops the parallel UDP response due to port collisions, forcing the client to wait 5000ms.

**🛠️ Production Fix in Pod Spec**:
```yaml
spec:
  dnsConfig:
    options:
      - name: ndots
        value: "2"
      - name: single-request-reopen
```

---

### Q14.2: Linux Conntrack Table Exhaustion
**🎯 Interview Question**: *"During peak traffic, applications report random connection timeouts, but node CPU/memory is healthy and pings succeed. What kernel subsystem failed?"*

**⚡ 15-Second Pitch**:  
The Linux kernel **`nf_conntrack` table is full**. Every ClusterIP Service requires iptables NAT session tracking. High TCP connection churn without `keep-alive` exceeds `nf_conntrack_max`, causing the kernel to silently drop new connection packets.

**🔍 Deep-Dive Mechanics**:
* Inspect with `dmesg -T | grep "nf_conntrack: table full"`.
* View active count: `cat /proc/sys/net/netfilter/nf_conntrack_count`.
* View maximum capacity: `cat /proc/sys/net/netfilter/nf_conntrack_max`.

**🛠️ Production Kernel Tuning**:
```bash
# Double the conntrack capacity:
sysctl -w net.netfilter.nf_conntrack_max=1048576
echo "net.netfilter.nf_conntrack_max=1048576" >> /etc/sysctl.d/99-kubernetes.conf
```

---

### Q14.3: Zombie Namespace Stuck in `Terminating`
**🎯 Interview Question**: *"A namespace is stuck in `Terminating` state for hours. Why does this happen, and how do you unblock it?"*

**⚡ 15-Second Pitch**:  
A resource inside the namespace has an unfulfilled **Finalizer** (often from a deleted Custom Resource Definition or admission webhook). The API server blocks deletion until the finalizer clears. You unblock it by stripping the `spec.finalizers` array via the raw `/finalize` API subresource.

**🛠️ Production Instant Fix**:
## 15. Senior & Staff DevOps Interview War Stories (Doc 14)

### The 4 Senior Mental Models
When answering ANY Kubernetes architecture question in an interview, anchor your response in these 4 principles:
1. **Reconciliation Loop Over Imperative Scripting**: Kubernetes is not "running scripts"; it is asynchronous controllers continuously converging real-world state to desired state in etcd.
2. **Blast Radius & Failure Domain Isolation**: Always discuss failure modes (what happens if a master dies, AZ is lost, or disk is full?).
3. **The Abstraction Cost**: Know what's under the hood (Services = iptables/IPVS, Pods = Linux namespaces + cgroups, CNI = veth + VXLAN).
4. **Day-2 Operations Over Day-1 Setup**: Anyone can run `helm install`; seniors master zero-downtime upgrades, certificate rotations, and disaster recovery.

---

### War Story 1: The "Silent 504 Gateway Timeout" (VXLAN MTU Mismatch)
* **Situation**: Small requests (`GET /healthz`) succeeded in 2ms, but large API POST payloads ($>2\text{KB}$) and PDF downloads hung indefinitely before timing out with `504 Gateway Timeout`.
* **Task**: Isolate the packet loss without rolling back the entire cluster.
* **Action**:
  1. Deployed a `netshoot` pod to run layer-by-layer network diagnostics.
  2. Suspected MTU fragmentation: ran a non-fragmenting ICMP sweep `ping -M do -s 1472 <pod-ip>` $\rightarrow$ failed with `Frag needed and DF set`.
  3. **Root Cause**: Host physical NIC was MTU 1500, but Flannel VXLAN encapsulation added 50 bytes of UDP header. Because the host set Don't Fragment (DF), all packets $>1450$ bytes were dropped by network switches.
  4. Patched Canal ConfigMap with `veth_mtu: 1450` and restarted CNI DaemonSet.
* **Result**: Large payload drops dropped to zero immediately. Added automated CNI MTU validation checks to CI/CD pipelines.

---

### War Story 2: The Cascading "OOM & Node Eviction Avalanche"
* **Situation**: During a traffic surge, 1 worker node ran out of memory. Within 8 minutes, 5 of 12 worker nodes turned `NotReady`, and 60 pods entered `CrashLoopBackOff`.
* **Task**: Stabilize the cluster and prevent cascading node death.
* **Action**:
  1. Discovered microservices were deployed without CPU/memory requests/limits (`BestEffort` QoS).
  2. When Node 1 ran out of memory, Kubelet evicted 25 `BestEffort` pods simultaneously.
  3. The Scheduler immediately rescheduled all 25 pods onto Node 2 and Node 3.
  4. The sudden memory flood triggered the Linux kernel OOM killer on Nodes 2 and 3, killing `containerd` and `kubelet`, turning nodes `NotReady`.
  5. Cordoned flapping nodes, applied emergency `LimitRange` rules to prevent unbounded pod creation, and configured node-level kubelet `--kube-reserved` and `--system-reserved` memory pools.
* **Result**: Restored cluster stability in 12 minutes. Enforced `LimitRanges`, `ResourceQuotas`, and `PodDisruptionBudgets` (PDBs) across all namespaces.

---

### War Story 3: The 3:00 AM etcd Leader-Flapping Crisis (Disk IOPS Saturation)
* **Situation**: High API server latency ($>5000\text{ms}$) and `etcdserver: leader changed` every 30 seconds.
* **Task**: Restore etcd stability without data corruption.
* **Action**:
  1. Checked Prometheus: `etcd_disk_wal_fsync_duration_seconds` p99 latency was spiking to $180\text{ms}$ (healthy is $<10\text{ms}$).
  2. Correlated with `iostat -xz 1`: a logging agent on Master 1 was flushing debug logs to `/var/log` on the exact same disk partition as etcd's Write-Ahead Log (`/var/lib/rancher/rke2/server/db/etcd`).
  3. The heavy I/O write locks caused etcd's `fdatasync` to miss the $100\text{ms}$ Raft heartbeat window, triggering constant elections.
  4. Throttled the logging agent, moved its buffer to a RAM disk (`tmpfs`), and prioritized etcd I/O using `ionice -c2 -n0`.
* **Result**: etcd p99 latency dropped to $2.1\text{ms}$, leadership stabilized, and API error rate dropped to 0%. Migrated etcd to dedicated NVMe disks.





