# Doc 13: Troubleshooting & Production Incident Runbook
## The Field Guide to Diagnosing and Fixing Kubernetes Outages

> **The mark of a Senior/Staff Engineer is not never having outages — it is ruthless, systematic isolation of root causes under pressure.**

---

## 13.1 The 5-Layer Triage Framework

When a P1/P0 incident strikes, junior engineers randomly test fixes. Senior engineers isolate the layer in < 2 minutes using this hierarchy:

```
┌────────────────────────────────────────────────────────┐
│  Layer 5: Application (Code bug, deadlock, OOM)        │
├────────────────────────────────────────────────────────┤
│  Layer 4: Ingress & Service Routing (DNS, iptables, LB)│
├────────────────────────────────────────────────────────┤
│  Layer 3: CNI & Pod Networking (VXLAN, MTU, IPAM)      │
├────────────────────────────────────────────────────────┤
│  Layer 2: Control Plane & etcd (Quorum, API, Kubelet)  │
├────────────────────────────────────────────────────────┤
│  Layer 1: Host & Kernel (Disk I/O, Conntrack, Memory)  │
└────────────────────────────────────────────────────────┘
```

### Rapid 60-Second Cluster Triaging Matrix

Run this immediate triage script from any admin machine:

```bash
# 1. Cluster nodes & pressure conditions
kubectl get nodes -o custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1].type,REASON:.status.conditions[-1].reason

# 2. Control plane pod status (etcd, apiserver, coredns)
kubectl get pods -n kube-system -o wide | grep -v "Running\|Completed"

# 3. Global Warning events in the last 15 minutes
kubectl get events -A --field-selector type=Warning --sort-by='.lastTimestamp' | tail -n 25

# 4. Any pods in CrashLoopBackOff, OOMKilled, or Terminating state
kubectl get pods -A | grep -E "CrashLoop|OOMKilled|Terminating|Error|Evicted|Pending"
```

---

## 13.2 Deep Dive: 10 Nasty Production Failure Modes

---

### Failure 1: The CoreDNS "5-Second Delay" (ndots:5 & Single-Request-reopen)

#### The Symptom:
Applications report random 5-second latencies when calling external APIs (e.g., AWS S3, Stripe, internal databases). `curl` inside pods intermittently hangs for exactly 5.00 seconds.

#### The Root Cause:
Inside pods, `/etc/resolv.conf` defaults to `options ndots:5` and lists 3 search domains (`<ns>.svc.cluster.local`, `svc.cluster.local`, `cluster.local`).
When a pod queries `api.stripe.com` (2 dots < 5), glibc queries:
1. `api.stripe.com.default.svc.cluster.local` (NXDOMAIN)
2. `api.stripe.com.svc.cluster.local` (NXDOMAIN)
3. `api.stripe.com.cluster.local` (NXDOMAIN)
4. `api.stripe.com` (SUCCESS)

Simultaneously, Linux kernel conntrack has a known race condition with parallel A and AAAA DNS lookups on UDP. When the race occurs, one UDP response is dropped by netfilter conntrack, causing the client to hit the default 5-second DNS retransmit timeout!

#### The Diagnosis:
```bash
# Exec into the failing pod
kubectl exec -it <pod-name> -- cat /etc/resolv.conf

# Measure DNS latency directly
kubectl exec -it <pod-name> -- time nslookup api.stripe.com
# Notice execution taking 5.002s intermittently!
```

#### The Fix:
1. In your Pod spec, add `dnsConfig`:
```yaml
spec:
  dnsConfig:
    options:
      - name: ndots
        value: "2"
      - name: single-request-reopen
```
2. Or use **NodeLocal DNSCache** to cache DNS queries locally on every node.
3. For fully qualified external domains, add a trailing dot: `api.stripe.com.` (forces direct lookup, skipping search paths).

---

### Failure 2: Conntrack Table Exhaustion ("nf_conntrack: table full, dropping packet")

#### The Symptom:
Random network connections across the cluster fail with "Connection timed out" or "Connection refused". Pings between nodes work, but high-throughput HTTP or database connections randomly drop.

#### The Root Cause:
Every TCP/UDP connection and NAT translation (all ClusterIP Services!) creates an entry in the kernel's `nf_conntrack` table. High connection churn without connection reuse (HTTP `keep-alive`) floods the table beyond `net.netfilter.nf_conntrack_max`. The kernel silently drops new packets.

#### The Diagnosis:
```bash
# Check kernel dmesg on the affected worker node
dmesg -T | grep -i conntrack
# Output: [Mon Aug 17 14:20:10 2026] nf_conntrack: table full, dropping packet

# Check current vs max conntrack entries
cat /proc/sys/net/netfilter/nf_conntrack_count
cat /proc/sys/net/netfilter/nf_conntrack_max
```

#### The Fix:
```bash
# 1. Immediate runtime bump:
sysctl -w net.netfilter.nf_conntrack_max=262144

# 2. Persist in /etc/sysctl.d/99-rke2.conf:
echo "net.netfilter.nf_conntrack_max = 262144" >> /etc/sysctl.d/99-rke2.conf
sysctl --system

# 3. Application level: Enforce HTTP connection pooling / keep-alives to prevent TCP SYN storms.
```

---

### Failure 3: Pod Stuck in `Terminating` Indefinitely (Finalizer Deadlock)

#### The Symptom:
You delete a Pod or Namespace with `kubectl delete`, but it remains stuck in `Terminating` for hours or days.

#### The Root Cause:
1. **Unreachable PV/NFS/iSCSI storage**: Kubelet cannot cleanly unmount the filesystem because the storage node is dead or network is cut.
2. **Stuck Finalizers**: A custom controller (or CSI driver) registered a metadata `finalizer`, but that controller crashed or cannot complete cleanup.

#### The Diagnosis:
```bash
# Inspect pod's finalizers and volume status
kubectl get pod <pod-name> -o yaml | grep -A 10 "finalizers"
kubectl describe pod <pod-name> | grep -A 10 "Volumes"
```

#### The Fix:
```bash
# Option A: Patch out the finalizers to release the object
kubectl patch pod <pod-name> -p '{"metadata":{"finalizers":null}}' --type=merge

# Option B: Force delete with grace-period 0 (only if volume is confirmed safe)
kubectl delete pod <pod-name> --force --grace-period=0

# If an entire namespace is stuck in Terminating:
kubectl get ns <stuck-ns> -o json | \
  jq '.spec.finalizers = []' | \
  kubectl replace --raw "/api/v1/namespaces/<stuck-ns>/finalize" -f -
```

---

### Failure 4: CNI IPAM IP Exhaustion ("no IP addresses available in range")

#### The Symptom:
New pods scheduled to a specific node remain stuck in `ContainerCreating`. `kubectl describe pod` reports `failed to allocate for range: no IP addresses available in network: canal/flannel`.

#### The Root Cause:
Flannel/Calico allocates a `/24` subnet (254 IPs) to each worker node. If high-churn batch jobs or crashed pods leaked IP leases, or if a single node hosts >250 ephemeral pods over time without CNI IPAM release, the node's local CIDR pool is exhausted.

#### The Diagnosis:
```bash
# On the affected node, check allocated CNI IP allocations:
ls /var/lib/cni/networks/cni0/
# Count active files:
ls -1 /var/lib/cni/networks/cni0/ | wc -l
```

#### The Fix:
```bash
# 1. Compare IP files on host against actual running containers:
CONTAINER_RUNTIME_ENDPOINT=unix:///run/k3s/containerd/containerd.sock crictl ps -q

# 2. Restart the Canal CNI DaemonSet pod on that node to force reconciliation:
kubectl delete pod -n kube-system -l k8s-app=canal --field-selector spec.nodeName=worker-2

# 3. Clean up orphaned IP leases in /var/lib/cni/networks/cni0/ for containers that no longer exist.
```

---

### Failure 5: Disk I/O Bottleneck Causing etcd Leader Flapping

#### The Symptom:
`kubectl` commands randomly return `Error from server: etcdserver: leader changed` or `etcdserver: request timed out`. Pods take a long time to schedule.

#### The Root Cause:
etcd is extremely sensitive to sequential write latency for its Write-Ahead Log (`fdatasync`). If master nodes share disks with heavy logging or use slow cloud volumes (e.g., standard HDD or non-provisioned IOPS), an fsync taking $>10\text{ms}$ causes the etcd leader to miss its heartbeat deadline ($100\text{ms}$). Followers initiate a new election, causing cluster-wide API stalls.

#### The Diagnosis:
```bash
# On master nodes, check disk write latency in real time:
iostat -xz 1 10
# Look at 'await' and '%util' columns. If 'await' > 10.00 ms on the etcd disk, that's your smoking gun!

# Check journal logs for leader election drops:
journalctl -u rke2-server | grep -E "lost leader|took too long|leader changed"
```

#### The Fix:
1. Relocate etcd data directory (`/var/lib/rancher/rke2/server/db/etcd`) to a dedicated NVMe SSD or provisioned IOPS volume (`io2`).
2. Move `/var/log` and container logs to a separate physical disk.
3. Configure `ionice` on the etcd/rke2-server process:
```bash
ionice -c2 -n0 -p $(pgrep rke2)
```

---

### Failure 6: Keepalived VIP Split-Brain (Two Masters Claiming 10.0.1.100)

#### The Symptom:
API connections from `kubectl` or worker kubelets constantly reset with `connection reset by peer` or SSL certificate validation mismatches.

#### The Root Cause:
If firewall rules block VRRP unicast traffic between Master 1 and Master 2, both nodes stop hearing each other's advertisements. Both promote themselves to `MASTER` state and bind `10.0.1.100` to their `eth0` interfaces. ARP tables on the network flap wildly between the two MAC addresses.

#### The Diagnosis:
```bash
# Check IP on Master 1:
ip addr show eth0 | grep "10.0.1.100"

# Check IP on Master 2:
ip addr show eth0 | grep "10.0.1.100"
# IF BOTH SHOW THE IP -> SPLIT BRAIN!

# Check keepalived logs:
journalctl -u keepalived -n 50
# Look for: "VRRP_Instance(VI_1) forcing a new master election"
```

#### The Fix:
1. Verify unicast peer IPs inside `/etc/keepalived/keepalived.conf` on all masters.
2. Check firewall permissions for VRRP protocol (IP protocol 112) or UDP unicast communication.
3. Test connectivity with `tcpdump`:
```bash
tcpdump -i eth0 vrrp -n
```

---

### Failure 7: Silent Silent Packet Drop Due to MTU Mismatch

#### The Symptom:
Small HTTP requests (`GET /`) return instantly, but large POST requests with payloads or large JSON responses hang and time out after 60 seconds.

#### The Root Cause:
VXLAN adds a 50-byte header to every packet. If the host network MTU is 1500 and the CNI assigns an MTU of 1500 to pod `eth0`, packets with 1500-byte payloads become 1550 bytes on the wire. Routers with Don't Fragment (DF) flags enabled drop these packets silently without returning ICMP "Fragmentation Needed".

#### The Diagnosis:
```bash
# Test MTU between two pods across nodes:
kubectl exec -it pod-a -- ping -M do -s 1472 <pod-b-ip>
# If it fails with "Frag needed and DF set", MTU is misconfigured!

# Check current MTU on flannel interface:
ip link show flannel.1
```

#### The Fix:
1. Set Canal/Calico MTU to `1450` (or `1440` for extra safety, or `8950` on Jumbo frame networks).
2. Edit RKE2 Canal ConfigMap:
```bash
kubectl edit cm -n kube-system canal-config
# Set: "veth_mtu": "1450"
kubectl rollout restart ds/canal -n kube-system
```

---

### Failure 8: CrashLoopBackOff with Exit Code 137 vs 143 vs 1 vs 139

#### Exit Code Cheatsheet:

| Exit Code | Signal | Meaning & Triage |
|---|---|---|
| **137** | `SIGKILL` ($128 + 9$) | **OOMKilled** or hard killed by kubelet. Check `kubectl describe pod` for `OOMKilled: true`. Increase `resources.limits.memory`. |
| **143** | `SIGTERM` ($128 + 15$) | Graceful termination initiated by Kubernetes (e.g. eviction, scale down, failing readiness/liveness probe). |
| **139** | `SIGSEGV` ($128 + 11$) | Segmentation fault in application binary (C/C++, Go runtime corruption, incompatible libc). |
| **1** | Generic Error | Application threw an uncaught exception, missing configuration, syntax error, or failed DB connection on startup. Check `kubectl logs --previous`. |
| **2** | Misuse of shell | Missing entrypoint script, wrong shebang (`#!/bin/bash` when only `/bin/sh` exists). |
| **126** | Cannot execute | Permissions issue on container binary (`chmod +x` missing). |
| **127** | Command not found | Entrypoint executable does not exist inside the image path. |

---

### Failure 9: kube-apiserver Throttling Client Requests (HTTP 429 Too Many Requests)

#### The Symptom:
CI/CD pipelines or operators start failing with `HTTP 429: Too Many Requests` or `Client-side throttling, not priority and fairness`.

#### The Root Cause:
Kubernetes **API Priority and Fairness (APF)** protects the API server from request floods by categorizing requests into flow schemas and queues. An aggressive controller or monitoring script making thousands of uncached `LIST` calls fills the priority queue.

#### The Diagnosis:
```bash
# Check APF metrics
kubectl get flowschemas
kubectl get prioritylevelconfigurations

# Look at Prometheus metric:
# apiserver_flowcontrol_rejected_requests_total
```

#### The Fix:
1. Ensure client applications use **Informers / SharedIndexInformers** (which watch cached data) instead of polling `LIST` requests.
2. Adjust concurrency limits in `PriorityLevelConfiguration` for specific service accounts.

---

### Failure 10: Inactive Ingress / MetalLB ARP Blackhole

#### The Symptom:
An external client cannot reach `http://10.0.1.200` (MetalLB IP). `arping` from a laptop receives no ARP replies.

#### The Root Cause:
In MetalLB Layer 2 mode, a single node's `speaker` pod is elected leader to reply to ARP queries for a given VIP. If that node's `speaker` container crashes or loses network connectivity without releasing leadership in memberlist, no node responds to ARP queries.

#### The Diagnosis:
```bash
# Check MetalLB speaker logs:
kubectl logs -n metallb-system -l app=metallb -c speaker --tail=50

# Check which node currently claims the service IP:
kubectl get service <service-name> -o yaml
kubectl get l2advertisements -n metallb-system
```

#### The Fix:
```bash
# Restart the MetalLB speaker daemonset:
kubectl rollout restart ds/speaker -n metallb-system

# Send gratuitous ARP from the active node manually if network switch cache is stale:
arping -U -c 3 -I eth0 10.0.1.200
```

---

## 13.3 Emergency Command Quick-Reference

```bash
# Dump raw containerd container logs when kubelet is broken:
CONTAINER_RUNTIME_ENDPOINT=unix:///run/k3s/containerd/containerd.sock crictl ps -a
CONTAINER_RUNTIME_ENDPOINT=unix:///run/k3s/containerd/containerd.sock crictl logs <container-id>

# Run a privileged Swiss-Army-Knife debug pod on a specific broken node:
kubectl run debug-node --rm -it --privileged \
  --image=nicolaka/netshoot \
  --overrides='{"spec":{"nodeName":"worker-1","hostNetwork":true,"hostPID":true}}' -- bash

# Trace network syscalls on a container process:
strace -fp <PID> -e trace=network

# Monitor real-time iptables packet drops:
iptables -t raw -I PREROUTING -p tcp --dport 80 -j TRACE
xtables-monitor --trace
```

---

*Doc 13 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
