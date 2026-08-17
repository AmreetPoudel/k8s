# Doc 01: Kubernetes & RKE2 Theory
## Before You Touch a Single Server

---

## 1.1 Why You Feel Lost (And Why That's Normal)

You administered a running Kubernetes cluster in production.  
You used `kubectl get pods`, you read logs, maybe you scaled deployments.  
But you felt like a mechanic who can change oil but has never seen an engine.

Here's the problem: **Kubernetes has 7+ major components, all TLS-mutually-authenticated, all event-driven, all distributed — and if you only ever `kubectl apply`, you never see any of it.**

This document gives you the engine diagram before you build it.

---

## 1.2 What Kubernetes Actually Is

Kubernetes is a **distributed state machine**.

You tell it: *"I want 3 replicas of my web server running."*  
Kubernetes's job: watch reality, compare it to your desire, and reconcile.  
Forever. Even when nodes die, pods crash, or disks fill up.

This pattern is called the **control loop** (or reconciliation loop). It's the most important concept in Kubernetes.

```
      ┌─────────────────────────────────┐
      │         Control Loop            │
      │                                 │
      │  Desired State  ──────────────► │
      │  (what you want)                │
      │                                 │
      │  Actual State   ──────────────► │
      │  (what exists)                  │
      │                                 │
      │  diff = Desired - Actual        │
      │  → take action to reconcile     │
      └─────────────────────────────────┘
```

Every Kubernetes controller does this. The Deployment controller, the ReplicaSet controller, the Node controller — all of them.

💡 **Interview**: *"How does Kubernetes ensure desired state?"*  
→ "Every controller runs a reconciliation loop. It watches the API server for changes to its resource type, compares actual state to desired state, and takes actions to converge. If a pod dies, the ReplicaSet controller notices the count dropped, creates a new pod spec, and schedules it. The controller never 'knows' what broke — it just sees the diff and fixes it."

---

## 1.3 The Control Plane — Every Component Explained

The **control plane** is the brain. In your cluster, master-1, master-2, master-3 are the control plane nodes.

```
┌─────────────────── CONTROL PLANE NODE ────────────────────┐
│                                                            │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────┐  │
│  │ kube-apiserver│   │  etcd        │   │  kube-scheduler│  │
│  │ :6443         │   │  :2379       │   │               │  │
│  └──────┬───────┘   └──────────────┘   └───────────────┘  │
│         │                                                   │
│  ┌──────▼──────────────────────────────────────────────┐   │
│  │          kube-controller-manager                     │   │
│  │  (ReplicaSet, Node, Endpoint, Job, ... controllers)  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │          cloud-controller-manager (optional)          │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

### kube-apiserver
- **The only component everything talks to**. 
- It is a REST API server. Every `kubectl` command is an HTTP request to the API server.
- It validates requests, authenticates them (via certs or tokens), authorizes them (via RBAC), and writes to etcd.
- It does NOT schedule pods. It does NOT run containers. It is a dumb write-ahead store with auth.
- Runs on port **6443** (HTTPS always).

⚠️ **If the API server dies, nothing NEW can be scheduled or changed. But existing running pods KEEP RUNNING.** etcd and API server are stateless enough that pods don't die when API server dies. This is critical to understand.

💡 **Interview**: *"If I kill the API server, do pods die?"*  
→ "No. The kubelet on worker nodes runs containers independently. It caches the pod spec locally. If the API server is down, the kubelet can't report status or get new instructions, but existing pods keep running. Only NEW work (schedule, scale, delete) is blocked."

### etcd
- **The only stateful component** in Kubernetes.
- A distributed key-value store. Every Kubernetes object (pods, deployments, secrets, configmaps) is stored as a value in etcd.
- Uses the **Raft consensus algorithm** to ensure all 3 etcd nodes agree before any write is committed.
- API server is the ONLY component that reads/writes etcd directly. No other component touches etcd.
- Runs on port **2379** (client) and **2380** (peer/replication between etcd nodes).

🔍 **Why is etcd separate?** Because Kubernetes needs a *consistent* store. etcd's Raft protocol guarantees that even if a node crashes mid-write, the data is not corrupted. A regular SQL database could split-brain. etcd cannot — it either commits to quorum or rejects the write.

### kube-scheduler
- Watches for new pods that have no `nodeName` assigned.
- For each unscheduled pod, it runs a **filter + score** algorithm:
  - **Filter**: Remove nodes that can't run this pod (not enough CPU, wrong labels, taints, etc.)
  - **Score**: Rank remaining nodes (prefer less loaded nodes, spread replicas, etc.)
- Writes the chosen `nodeName` back to the pod spec via the API server.
- **The scheduler does NOT start containers**. It just assigns pods to nodes.

### kube-controller-manager
- A single binary that runs **20+ controllers** as goroutines.
- Key ones:
  - **ReplicaSet controller**: Ensures desired pod count
  - **Deployment controller**: Manages ReplicaSets for rolling updates
  - **Node controller**: Detects when nodes go NotReady, evicts pods after timeout
  - **Endpoints controller**: Populates Endpoints objects for Services
  - **Job controller**: Ensures Jobs run to completion
  - **ServiceAccount controller**: Creates default ServiceAccounts in new namespaces

---

## 1.4 The Worker Node — Every Component Explained

```
┌─────────────────── WORKER NODE ───────────────────────────┐
│                                                            │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────┐  │
│  │  kubelet      │   │  kube-proxy  │   │  container    │  │
│  │  :10250       │   │  (iptables)  │   │  runtime      │  │
│  └──────────────┘   └──────────────┘   │  (containerd) │  │
│                                         └───────────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  CNI plugin (Canal = Flannel + Calico)               │   │
│  │  (manages pod network interfaces)                    │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

### kubelet
- **The main agent on every node** (including masters).
- Watches the API server for pods scheduled to its node.
- Talks to the container runtime (containerd) via CRI (Container Runtime Interface) to start/stop containers.
- Reports node and pod status back to API server.
- Manages pod lifecycle, volume mounts, health probes (liveness/readiness).

💡 **Interview**: *"What is the CRI?"*  
→ "Container Runtime Interface. It's a gRPC API that kubelet uses to talk to container runtimes. This is an abstraction layer — kubelet doesn't care if you're using containerd, CRI-O, or Docker (via dockershim, now removed). It just calls CRI methods: RunPodSandbox, CreateContainer, StartContainer. This is why Docker was removable — you just swap the CRI implementation."

### kube-proxy
- Implements **Service networking** on each node.
- A Kubernetes Service is just a virtual IP (ClusterIP). kube-proxy makes that IP work.
- In **iptables mode** (default, what Canal uses): writes iptables rules that DNAT traffic from ClusterIP to a random pod IP.
- In **IPVS mode**: uses kernel IPVS for better performance at scale.
- kube-proxy watches the API server for Service and Endpoints changes, updates iptables rules in real time.

⚠️ **kube-proxy is NOT a proxy in the traditional sense.** It doesn't handle packets. It programs the kernel's netfilter so the kernel handles packets. This is a very common interview misconception.

### Container Runtime (containerd)
- RKE2 bundles its own **containerd** — you don't install Docker.
- containerd manages: pulling images, creating containers, managing namespaces, managing snapshots.
- It talks to the Linux kernel via **runc** (the actual OCI container runtime).

```
kubelet → CRI → containerd → runc → Linux kernel (namespaces + cgroups)
```

### CNI Plugin
- When a pod is created, kubelet calls the CNI plugin to set up networking.
- CNI creates a virtual ethernet pair (veth), puts one end in the pod's network namespace, one end on the host.
- The plugin then ensures the pod's IP is routable to other pods (via Flannel overlay in Canal).
- We go deep on this in Doc 05.

---

## 1.5 How RKE2 Is Different From Everything Else

You may hear: kubeadm, k3s, RKE1, RKE2, kops, Talos. Here's the map.

### The Kubernetes Distribution Landscape

```
┌──────────────────────────────────────────────────────────────────┐
│                    Kubernetes Distributions                      │
│                                                                  │
│  kubeadm  →  The bare-bones bootstrap tool (official)           │
│              Installs k8s on a single node or cluster           │
│              YOU manage certs, etcd, upgrades manually          │
│              No bundled CNI, no bundled ingress                  │
│                                                                  │
│  RKE1     →  Rancher's first distro (uses Docker daemon!)       │
│              Deprecated. Don't use.                              │
│                                                                  │
│  k3s      →  Lightweight k8s (single binary, SQLite or etcd)    │
│              Meant for edge, IoT, dev environments              │
│              Missing some enterprise features                    │
│                                                                  │
│  RKE2     →  "k3s but production-grade"                         │
│              Uses containerd (no Docker), bundled etcd          │
│              CIS Kubernetes Benchmark hardened by default       │
│              FIPS compliant option available                     │
│              What we're using.                                   │
│                                                                  │
│  EKS/GKE  →  Cloud-managed. Control plane is hidden from you.  │
│              You never see etcd, API server config, certs.      │
│              NOT what we're doing.                              │
└──────────────────────────────────────────────────────────────────┘
```

### RKE2 vs kubeadm — The Key Differences

| Feature | kubeadm | RKE2 |
|---------|---------|------|
| Control plane as | Systemd units | Static pods (managed by rke2 agent) |
| etcd | External or stacked | Bundled, embedded |
| Container runtime | You choose | containerd (bundled) |
| CIS hardening | Manual | Default |
| Certificate management | Manual rotation | Auto-renewal built in |
| Upgrades | Tricky | `rke2` binary upgrade |
| CNI | You install | Canal bundled (or choose) |
| Ingress | You install | NGINX bundled |

### How RKE2 Actually Starts

This is the part nobody explains. When you start RKE2 on a master:

1. The `rke2 server` process starts as a systemd service.
2. It writes **static pod manifests** to `/var/lib/rancher/rke2/agent/pod-manifests/`.
3. The embedded **kubelet** (yes, RKE2 has its own kubelet) reads those manifests and starts the control plane components as **pods**.
4. So `etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler` are ALL PODS — not raw binaries.
5. They live in the `kube-system` namespace but are not managed by the API server at startup (chicken-and-egg problem solved by static pods).

💡 **Interview**: *"How does Kubernetes boot without itself?"*  
→ "The chicken-and-egg problem. The API server is needed to run pods, but the control plane components themselves run as pods. The solution: static pods. The kubelet reads YAML manifests from a local directory and starts pods directly, WITHOUT contacting the API server. So etcd and the API server start as static pods managed by kubelet, then once the API server is up, everything else can start normally."

### RKE2's Embedded Components

When you install RKE2, you get ONE binary (`/usr/local/bin/rke2`) that contains:
- containerd
- kubelet
- kubectl
- etcd
- All control plane components
- Helm controller (for bundled addons)
- NGINX ingress controller
- Canal CNI
- CoreDNS
- metrics-server

You don't `apt install etcd` or `apt install containerd`. RKE2 manages them all.

⚠️ **This is why RKE2's containerd socket is at a non-standard path:**  
`/run/k3s/containerd/containerd.sock` (not `/run/containerd/containerd.sock`)  
This matters when you use `crictl` for debugging.

---

## 1.6 The RKE2 Bootstrap Process (Step by Step)

Understanding this sequence saves you hours of debugging.

```
Step 1: Start master-1 (first server — bootstrap mode)
  ↓
  rke2 writes certs to /var/lib/rancher/rke2/server/tls/
  ↓
  rke2 starts embedded etcd (single node, not yet HA)
  ↓
  rke2 starts API server (uses etcd)
  ↓
  rke2 writes join token to /var/lib/rancher/rke2/server/node-token
  ↓
  kubeconfig written to /etc/rancher/rke2/rke2.yaml
  ↓
  master-1 is ready. Cluster exists but is NOT HA.

Step 2: Start master-2 and master-3
  ↓
  rke2 config points to master-1's VIP (10.0.1.100:9345)
  ↓
  master-2 downloads certs and cluster info from master-1
  ↓
  master-2 joins etcd cluster (now etcd has 2 nodes — still needs 3 for quorum)
  ↓
  master-3 joins → etcd now has 3 nodes → QUORUM achieved → HA cluster

Step 3: Start worker nodes
  ↓
  rke2 agent points to VIP (10.0.1.100:9345)
  ↓
  kubelet registers node with API server
  ↓
  CNI sets up networking
  ↓
  Node goes Ready
```

💡 **Why port 9345 for joining, not 6443?**  
RKE2 uses port 9345 as a **registration endpoint** — a simpler HTTP(S) endpoint that returns the cluster join information (server list, token validation). The API server on port 6443 is for kubectl and component traffic. This separation means you can have a different LB policy for join traffic vs. API traffic.

---

## 1.7 The Virtual IP Strategy (keepalived)

You asked about a single VIP for the masters. Here's the concept.

```
         Your laptop / workers
               ↓
        10.0.1.100:6443  ← Virtual IP (VIP) — doesn't belong to any NIC by default
               ↓
         ┌─────────────────────────────────┐
         │        keepalived               │
         │  Running on all 3 masters       │
         │  Uses VRRP protocol             │
         │                                 │
         │  master-1 (MASTER role):        │
         │    → Owns 10.0.1.100            │
         │    → Has it on its eth0         │
         │                                 │
         │  master-2, master-3 (BACKUP):   │
         │    → Watching master-1          │
         │    → If master-1 dies:          │
         │      master-2 takes the VIP     │
         └─────────────────────────────────┘
```

**VRRP (Virtual Router Redundancy Protocol)**: keepalived masters send VRRP heartbeat packets to each other (multicast). If the MASTER doesn't send a heartbeat within a timeout, a BACKUP promotes itself to MASTER and assigns the VIP to its NIC.

⚠️ **On AWS, VRRP multicast doesn't work natively.** You need to either:
1. Use unicast VRRP (supported by keepalived, just extra config)
2. Use an AWS NLB (but you said no managed services)
3. Use HAProxy + keepalived on masters

We'll configure keepalived with unicast in Doc 06 so it works on both AWS and Nutanix.

---

## 1.8 Why Canal? (CNI Choice Explained)

You asked me to explain and choose. Here's the full picture.

### What a CNI Must Do
1. Assign a unique IP to every pod (from a pod CIDR, e.g., `10.42.0.0/16`)
2. Ensure pod-to-pod traffic works across nodes
3. Optionally enforce NetworkPolicy (firewall between pods)

### Canal = Flannel + Calico

| Component | Job |
|-----------|-----|
| **Flannel** | Pod IP assignment + overlay network (VXLAN) |
| **Calico** | NetworkPolicy enforcement (iptables/eBPF rules) |

**Flannel's VXLAN overlay**: When pod on node-1 talks to pod on node-2, Flannel wraps the packet in a VXLAN UDP packet (port 8472) addressed to node-2's real IP, then unwraps it on arrival. This is called **encapsulation**. It works on ANY network — AWS, Nutanix, bare metal — because it uses normal UDP.

```
Pod A (10.42.1.5)  →  Flannel  →  UDP packet to node-2:8472  →  node-2 Flannel  →  Pod B (10.42.2.3)
[overlay]                                [underlay/real network]                     [overlay]
```

### Why NOT Cilium (for now)?
Cilium uses **eBPF** — a mechanism to run sandboxed programs directly in the Linux kernel. It replaces iptables entirely and is faster, but:
- Requires kernel 5.4+ (Ubuntu 24.04 has 6.8 — fine)
- Harder to debug when learning (eBPF isn't visible like iptables rules)
- `kubectl exec` into pods + `iptables -L` works with Canal; doesn't show CNI rules with Cilium

**Choose Canal because**: When something breaks (and it will), you can inspect with `iptables -L`, `ip route`, `ip link` — standard Linux tools you already know. Once you understand networking deeply, migrate to Cilium as a learning exercise.

💡 **Interview**: *"Why would you choose Cilium over Canal?"*  
→ "Cilium uses eBPF to implement networking in the kernel, bypassing iptables entirely. This gives better performance at scale (no iptables chain traversal), better observability (Hubble), and L7 NetworkPolicy support (HTTP-aware rules). For large clusters (1000+ nodes), the iptables rule count becomes a performance bottleneck. Cilium also enables transparent encryption without a sidecar. The tradeoff is operational complexity and steeper debugging learning curve."

---

## 1.9 Kubernetes Resource Model (The API Objects)

Everything in Kubernetes is an **API resource**. When you `kubectl apply -f deployment.yaml`, you're making an HTTP POST to `/apis/apps/v1/namespaces/default/deployments`.

### The Resource Hierarchy

```
Cluster
├── Nodes
├── Namespaces
│   ├── Pods
│   │   └── Containers (NOT a k8s object — managed by kubelet)
│   ├── Deployments → ReplicaSets → Pods
│   ├── StatefulSets → Pods
│   ├── DaemonSets → Pods (one per node)
│   ├── Services (ClusterIP, NodePort, LoadBalancer)
│   ├── Ingress
│   ├── ConfigMaps
│   ├── Secrets
│   ├── ServiceAccounts
│   ├── PersistentVolumeClaims → PersistentVolumes
│   └── NetworkPolicies
├── ClusterRoles + ClusterRoleBindings (cluster-scoped RBAC)
├── StorageClasses
└── PersistentVolumes (cluster-scoped)
```

### The Pod is the Atom

A pod is the smallest deployable unit. It contains:
- One or more containers (sharing network namespace + optional volumes)
- One network IP (all containers in a pod share the same IP, different ports)
- Optionally: init containers, sidecar containers

💡 **Interview**: *"Why do containers in a pod share an IP?"*  
→ "Because Kubernetes networking model says one IP per pod, not per container. When the pod starts, the kubelet creates a 'pause container' (also called sandbox or infra container) whose only job is to hold the network namespace open. All other containers join that namespace. This is why containers in a pod can reach each other on localhost — they share the same network stack."

### API Versioning

You'll see `apiVersion: v1` vs `apiVersion: apps/v1` vs `apiVersion: networking.k8s.io/v1`. This is the API group system:

- `v1` = core group (Pod, Service, ConfigMap, Secret, PersistentVolume)
- `apps/v1` = apps group (Deployment, ReplicaSet, DaemonSet, StatefulSet)
- `networking.k8s.io/v1` = networking group (Ingress, NetworkPolicy)
- `rbac.authorization.k8s.io/v1` = RBAC group

---

## 1.10 Summary & What Comes Next

You now know:
- ✅ What every control plane component does
- ✅ What every worker node component does  
- ✅ How RKE2 differs from kubeadm, k3s, RKE1
- ✅ Why RKE2 uses static pods to bootstrap
- ✅ Why Canal is the right CNI choice for learning
- ✅ How keepalived VIP works for HA masters
- ✅ The reconciliation loop mental model

**Next**: [Doc 02 - Node Preparation →](./02_node_preparation.md)  
You'll prepare all 6 Ubuntu 24.04 servers with the exact settings RKE2 needs to not silently fail.

---

*Doc 01 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
