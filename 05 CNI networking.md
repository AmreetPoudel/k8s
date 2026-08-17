# Doc 05: CNI & Cluster Networking
## How Packets Actually Move Between Pods

> **This is the most technically dense doc.** Take your time.  
> Networking is the #1 topic where people get exposed in interviews.

---

## 5.1 The Kubernetes Networking Model (Fundamental Rules)

Kubernetes defines 3 rules that every CNI plugin must satisfy:

1. **Every pod gets a unique IP address** (no NAT between pods)
2. **Pods can reach any other pod's IP directly** (across nodes, without NAT)
3. **Agents on a node (kubelet, kube-proxy) can reach all pods on that node**

These rules are by design. They eliminate the "which port does container X listen on?" complexity. Every pod is a first-class citizen with its own IP.

The **CNI plugin's job** is to implement these rules. There are many ways to do it. Canal uses **VXLAN overlay**. Cilium uses **eBPF routing**. Calico (in BGP mode) uses **direct routing**.

---

## 5.2 The Three Networks in Your Cluster

You need to understand these three non-overlapping IP spaces:

```
┌─────────────────────────────────────────────────────────────────┐
│  Network 1: NODE NETWORK (your real infrastructure)             │
│  Range: 10.0.1.0/24 (masters), 10.0.2.0/24 (workers)          │
│  These IPs are on the actual NIC of each server.               │
│  Packets here route normally through your switch/router.       │
├─────────────────────────────────────────────────────────────────┤
│  Network 2: POD CIDR (virtual, managed by CNI)                  │
│  Range: 10.42.0.0/16 (RKE2 default)                            │
│  Each node gets a /24 slice:                                   │
│    master-1: 10.42.0.0/24  (pods on master get IPs here)       │
│    worker-1: 10.42.1.0/24                                      │
│    worker-2: 10.42.2.0/24                                      │
│    worker-3: 10.42.3.0/24                                      │
│  Packets between pod CIDRs go through Flannel VXLAN tunnel.    │
├─────────────────────────────────────────────────────────────────┤
│  Network 3: SERVICE CIDR (virtual, managed by kube-proxy)       │
│  Range: 10.43.0.0/16 (RKE2 default)                            │
│  ClusterIPs are assigned from here.                            │
│  These IPs don't exist on any NIC — they're iptables rules.    │
│  The kubernetes Service itself = 10.43.0.1                     │
└─────────────────────────────────────────────────────────────────┘
```

⚠️ **These three ranges MUST NOT overlap with each other OR with your node network.** If they do, routing breaks silently. Check before installing RKE2:
```bash
# [M1] — verify your node network range doesn't overlap with 10.42.0.0/16 or 10.43.0.0/16
ip route show
```

---

## 5.3 Canal CNI Architecture

Canal = **Flannel** (overlay + IPAM) + **Calico** (NetworkPolicy enforcement)

```
┌─────────────────────────────────────────────────────────────────┐
│                     Canal Components                             │
│                                                                  │
│  Per-node DaemonSet pod: calico-node                            │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    calico-node                             │ │
│  │                                                            │ │
│  │  ┌──────────────────┐   ┌──────────────────┐             │ │
│  │  │   felix           │   │    flannel        │             │ │
│  │  │  (NetworkPolicy   │   │  (overlay +       │             │ │
│  │  │   iptables rules) │   │   IPAM)           │             │ │
│  │  └──────────────────┘   └──────────────────┘             │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  CNI binary: /opt/cni/bin/flannel (called by kubelet)           │
│  Config: /etc/cni/net.d/10-canal.conflist                       │
└─────────────────────────────────────────────────────────────────┘
```

### Flannel's Role: Pod IPAM and VXLAN

**IPAM** = IP Address Management. When a pod is created:
1. kubelet calls the CNI binary (`flannel`)
2. Flannel checks the node's allocated subnet (from etcd, via flannel's own backend)
3. Flannel assigns the next available IP from the node's subnet
4. Creates a `veth` pair: `eth0` in pod, `cali-xxxxx` on host
5. Connects them via a bridge (`cni0` or directly via routes)

**VXLAN** = Virtual eXtensible LAN. When pod on node-1 sends to pod on node-2:
```
Pod A (10.42.1.5) on worker-1 (10.0.2.10)
  → sends to Pod B (10.42.2.8) on worker-2 (10.0.2.11)

Step 1: Pod A sends packet:
  src: 10.42.1.5, dst: 10.42.2.8
  Packet exits veth into worker-1's network namespace

Step 2: Kernel checks routes on worker-1:
  Route: 10.42.2.0/24 via flannel.1  (Flannel's VXLAN device)
  Packet goes to flannel.1 interface

Step 3: Flannel encapsulates:
  Outer packet: src: 10.0.2.10 (worker-1's real IP), dst: 10.0.2.11 (worker-2's real IP)
  Inner packet: src: 10.42.1.5, dst: 10.42.2.8
  UDP port 8472 (VXLAN)
  VXLAN VNI (tunnel ID)

Step 4: Outer packet routes normally (10.0.2.10 → 10.0.2.11)

Step 5: worker-2's flannel.1 receives UDP:8472
  Decapsulates: extracts inner packet (src: 10.42.1.5, dst: 10.42.2.8)
  Delivers to Pod B's veth interface
```

This is why you need **UDP port 8472 open** between all nodes. Blocking it = pods on different nodes can't communicate, and the error looks like random app failures, not a firewall issue.

### Calico Felix's Role: NetworkPolicy

When you create a NetworkPolicy:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all
spec:
  podSelector: {}   # matches all pods in namespace
  policyTypes: ["Ingress"]  # block all incoming traffic
```

Felix (the Calico agent) translates this to **iptables rules** on each node. Specifically, it adds rules in the `filter` table's `FORWARD` chain that drop packets not explicitly allowed by any NetworkPolicy.

```bash
# [ANY-W] — see the iptables rules Felix wrote
iptables -L -n -v | grep -E "cali-|DROP|ACCEPT" | head -30
```

💡 **Interview**: *"How does NetworkPolicy work under the hood?"*  
→ "NetworkPolicy is implemented by the CNI plugin, not Kubernetes itself. With Calico (part of Canal), the Felix agent watches the API server for NetworkPolicy objects and translates them into iptables rules on each node. For each policy, Felix creates iptables chains for ingress/egress, adds CIDR or pod selector rules using ipsets (for efficiency), and inserts these chains into the FORWARD table. Packets that don't match any allow rule get dropped. The key insight: NetworkPolicy is purely enforced at the network layer — the application never knows a packet was dropped."

---

## 5.4 The veth Pair — How Pods Connect to the Node

Every running pod has a **veth pair** (virtual ethernet pair):

```bash
# [ANY-W] — find veth pairs for running pods
ip link show | grep -E "cali|veth"
# Output: calixxxxxxxxxx@if3: <BROADCAST,MULTICAST,UP> ...

# Find which pod owns a veth
# Get pod's container ID
kubectl get pod nginx -o jsonpath='{.status.containerStatuses[0].containerID}'

# On the node running nginx:
nsenter -t <PID-OF-PAUSE-CONTAINER> -n ip addr
# Shows the pod's eth0 with its pod IP

# From the host, find the veth peer
ip link show cali<xxxxx>  # the host end of the pair
```

The veth is like a **pipe with two ends**:
- One end is inside the pod's network namespace (`eth0` in the pod)
- The other end is on the host namespace (`caliXXX` on the node)
- Packets in one end come out the other — the kernel moves them between namespaces

---

## 5.5 How Services Work (ClusterIP Deep Dive)

A **Service ClusterIP** (e.g., `10.43.15.100`) doesn't exist on any interface. There's no process listening on that IP. It's pure iptables magic.

```bash
# [M1] — look at iptables DNAT rules for a service
kubectl get svc my-service
# Shows: ClusterIP 10.43.15.100, Port 80

# On a worker node, find the kube-proxy rule:
iptables -t nat -L KUBE-SERVICES -n | grep 10.43.15.100
# Output: KUBE-SVC-XXXXXXXX  tcp  0.0.0.0/0  10.43.15.100  tcp dpt:80

iptables -t nat -L KUBE-SVC-XXXXXXXX -n
# Shows the probability-based DNAT rules:
# KUBE-SEP-AAA  all  0.0.0.0/0  0.0.0.0/0  /* probability */ 0.33333...
# KUBE-SEP-BBB  all  0.0.0.0/0  0.0.0.0/0  /* probability */ 0.5...
# KUBE-SEP-CCC  all  0.0.0.0/0  0.0.0.0/0

iptables -t nat -L KUBE-SEP-AAA -n
# Output: DNAT  tcp  0.0.0.0/0  0.0.0.0/0  tcp to:10.42.1.5:8080
# This is where the actual pod IP is!
```

**The full flow when a pod calls `http://my-service:80`:**
```
1. Pod resolves "my-service" via CoreDNS → 10.43.15.100
2. Pod sends packet to 10.43.15.100:80
3. Packet enters host network namespace via veth
4. iptables nat PREROUTING hook processes packet
5. KUBE-SERVICES chain matches destination 10.43.15.100:80
6. KUBE-SVC-XXX chain: randomly pick one of 3 endpoints (33% each)
7. KUBE-SEP-AAA: DNAT destination from 10.43.15.100:80 to 10.42.1.5:8080
8. Packet now has real pod IP as destination
9. Routes to the correct node via Flannel VXLAN (if cross-node)
10. Pod B receives the packet on its eth0
```

💡 **Interview**: *"How does a Kubernetes Service actually work?"*  
→ "A ClusterIP Service is implemented entirely in iptables by kube-proxy. When you create a Service, kube-proxy writes iptables DNAT rules to the nat table on every node. When a pod sends a packet to the Service's ClusterIP, iptables intercepts it and rewrites the destination to one of the backing pod IPs, chosen randomly (which is the load balancing). The clever part: this all happens in the kernel before the packet even leaves the node. There's no userspace proxy involved — it's pure netfilter. kube-proxy's job is just to program the kernel, then get out of the way."

---

## 5.6 NodePort Services

When you create a Service with `type: NodePort`:

```yaml
type: NodePort
ports:
  - port: 80
    targetPort: 8080
    nodePort: 30080  # or auto-assigned from 30000-32767
```

kube-proxy adds an iptables rule that catches traffic on port 30080 on ANY node's IP, then DNATs it to the Service's ClusterIP (which then routes to a pod):

```bash
# [ANY-W] — see NodePort rule
iptables -t nat -L KUBE-NODEPORTS -n | grep 30080
# Matches: any IP:30080 → KUBE-SVC-XXXXX → pod IP
```

This means: hit `http://worker-2:30080` even if the pod is on `worker-3` — it works because the DNAT routes it through the overlay.

---

## 5.7 CoreDNS — How Pod DNS Works

Every pod in Kubernetes can resolve `my-service.my-namespace.svc.cluster.local`.

CoreDNS runs as a Deployment in `kube-system` and is the cluster's DNS server.

```bash
# [M1] — look at CoreDNS
kubectl get pods -n kube-system | grep coredns
kubectl get svc -n kube-system kube-dns
# Shows ClusterIP, often 10.43.0.10 in RKE2
```

When a pod starts, kubelet writes `/etc/resolv.conf` inside the pod:
```
nameserver 10.43.0.10     # CoreDNS ClusterIP
search default.svc.cluster.local svc.cluster.local cluster.local
options ndots:5
```

With `ndots:5`: if a name has fewer than 5 dots, DNS tries appending search domains first:
- `my-service` → tries `my-service.default.svc.cluster.local` first → CoreDNS resolves to ClusterIP ✓

CoreDNS reads Kubernetes Services and Endpoints from the API server and serves DNS responses from memory — it watches the API server for changes.

💡 **Interview**: *"A pod can't resolve an external hostname. What do you check?"*  
→ "First, check if CoreDNS pods are running and healthy: `kubectl get pods -n kube-system`. Then exec into the failing pod and check `/etc/resolv.conf` — the nameserver should be the CoreDNS ClusterIP. Test with `nslookup google.com <CoreDNS-IP>`. If that fails, check CoreDNS logs: `kubectl logs -n kube-system -l k8s-app=kube-dns`. Check if CoreDNS has an upstream forwarder configured (its Corefile configmap). Check if iptables rules for the CoreDNS Service exist: `iptables -t nat -L | grep <CoreDNS-ClusterIP>`. Also check if UDP port 53 is blocked by NetworkPolicy on the CoreDNS pods."

---

## 5.8 Packet Tracing Commands

These commands help you debug networking issues:

```bash
# Test pod-to-pod connectivity
kubectl exec -it pod-a -- ping 10.42.2.8   # pod-b's IP

# Test pod-to-service
kubectl exec -it pod-a -- curl http://my-service:80

# Capture traffic on a node (see what's happening)
tcpdump -i flannel.1 -n   # VXLAN traffic (between nodes)
tcpdump -i eth0 udp port 8472   # see raw VXLAN encapsulation
tcpdump -i cali<xxxxx> -n   # traffic to/from specific pod

# Check routes on a node
ip route show table main | grep 10.42   # pod CIDR routes

# Check if flannel knows about all nodes
cat /run/flannel/subnet.env   # flannel's subnet info
# OR check flannel's etcd entries:
etcdctl_cmd get /coreos.com/network --prefix --keys-only

# Check iptables for a specific ClusterIP
iptables -t nat -L -n --line-numbers | grep 10.43.x.x

# Check conntrack (active connection tracking entries)
conntrack -L | grep 10.42.1.5   # connections to/from a pod IP

# Check if a pod's veth is properly connected
ip link show | grep cali
# Both ends should be UP
```

---

## 5.9 Summary

You now understand:
- ✅ The three networks (node, pod, service) and why they can't overlap
- ✅ How VXLAN encapsulation moves traffic between nodes
- ✅ Canal = Flannel (VXLAN + IPAM) + Calico (NetworkPolicy via Felix/iptables)
- ✅ How ClusterIP Services work via iptables DNAT
- ✅ How NodePort extends that to external traffic
- ✅ How CoreDNS resolves service names
- ✅ How veth pairs connect pods to the node network
- ✅ Commands to debug network issues

**Next**: [Doc 06 - Bootstrapping the Master Cluster →](./06-master-cluster-bootstrap.md)  
Now we actually install RKE2 and build the cluster, step by step, with every command explained.

---

*Doc 05 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
