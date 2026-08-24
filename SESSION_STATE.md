# Comprehensive Session Context & Learning State
## RKE2 Kubernetes Mastery Series

> **Repository**: `https://github.com/AmreetPoudel/k8s.git`  
> **Last Updated**: August 24, 2026  
> **Current Status**: **ALL 14 Curriculum Documents (Docs 01 through 14) 100% MASTERED & DRILLED**  
> **Master Artifact**: [`QA.md`](file:///Users/amritpoudel/k8s-rke2/QA.md) (1,055-line comprehensive Staff/Senior DevOps Q&A and Runbook)  
> **Next Milestone**: **PHASE 2: LIVE MULTI-NODE CLUSTER BOOTSTRAP (3 Masters + 3 Workers on VMs/Cloud)**

---

## 1. User Profile, Goals & Teaching Style

* **Background**: Experienced with day-to-day production Kubernetes administration (`kubectl`, logs, scaling, debugging), but the underlying control plane, bare-metal networking, kernel packet flows, and bootstrapping were previously a "black box".
* **Goal**: Master raw Kubernetes (RKE2) on 6 bare-metal / cloud VMs from scratch to a Senior/Staff DevOps & SRE interview level before moving to EKS.
* **Target Milestone**: **Phase 2 Live Deployment** — Hands-on deployment of the complete 3-Master, 3-Worker RKE2 cluster from scratch with every command executed manually to build deep muscle memory.
* **Learning Style & Preferences**:
  1. **Concrete over Abstract**: Wants physical kernel flows, side-by-side configuration files, and real Linux commands (`ip addr`, `curl`, `sysctl`, `iptables`, `ip route`, `bridge fdb`).
  2. **Revision Method**: 1 open-ended reasoning question at a time (NO multiple-choice). If stuck, provide clues/hints and prompt to try again.
  3. **The "1 Component = 1 Job" Isolation Framework**: Isolate responsibilities with zero blur.
  4. **Deployment Strategy**: Manifests in `manifests/` will be deployed using **ArgoCD (GitOps)** via `kustomization.yaml` and Helm charts.

---

## 2. Cluster Architecture & Topology

* **Nodes**: 6 nodes total (3 Masters, 3 Workers) on Ubuntu 24.04 LTS.
  * `master-1` (`10.0.1.10`), `master-2` (`10.0.1.11`), `master-3` (`10.0.1.12`).
  * `worker-1` (`10.0.2.10`), `worker-2` (`10.0.2.11`), `worker-3` (`10.0.2.12`).
* **High Availability**: Keepalived Unicast VRRP Virtual IP (`10.0.1.100:6443` and `:9345`) bound as a secondary IP on real physical `eth0`.
* **CNI Choice**: Canal (Flannel VXLAN overlay on UDP 8472 for Pod IPAM `10.42.0.0/16` + Calico Felix for NetworkPolicies).
* **Storage**: Longhorn 3-way distributed synchronous block storage (`/var/lib/longhorn`) over host `open-iscsi` (`iscsid`).
* **Ingress / Load Balancer**: NGINX Ingress Controller (HostPort 80/443) + MetalLB Layer 2 ARP pool (`10.0.1.200 - 10.0.1.220`).
* **Master Taints**: `node-role.kubernetes.io/control-plane:NoSchedule` (zero user workloads on control plane).
* **Monitoring Pipeline**: Prometheus TSDB + Alertmanager + Grafana + `node-exporter` + `kube-state-metrics` via `kube-prometheus-stack` Helm chart.

---

## 3. Knowledge Retention & Concepts Mastered (Docs 01 to 14)

### From Doc 01 & 01b (Architecture & Raft Consensus):
1. **Active-Active vs. Active-Passive Control Plane**: `kube-apiserver` is stateless (Active-Active); `scheduler` & `controller-manager` use Lease locks in etcd (Active-Passive).
2. **etcd Replication & Single Source of Truth**: 100% full replication; only `kube-apiserver` talks to etcd directly.
3. **HTTP/2 WATCH API vs. Polling**: `epoll` event-driven streams consume ~0% CPU at idle vs polling storm.
4. **Chicken-and-Egg Bootstrap ($t=0$)**: Native systemd Kubelet + Static Pod manifests (`/var/lib/rancher/rke2/agent/pod-manifests/`).
5. **CRI & Pause Container**: `containerd` via gRPC socket; `pause` container namespace holds IP across pod app restarts.
6. **Raft Quorum Math**: $\lfloor N/2 \rfloor + 1$ majority rule. 3 nodes tolerate 1 failure; 5 nodes tolerate 2. Odd node counts prevent split-brain.

### From Doc 02 (Node Preparation & Linux Kernel Networking):
7. **`overlay` Module (OverlayFS)**: Provides union mount filesystem layering (`lowerdir` + `upperdir`) for Copy-on-Write (CoW).
8. **Linux Bridge & `br_netfilter`**: Forces Layer 2 bridged packets through Layer 3/4 `netfilter`/`iptables` so `kube-proxy` DNAT works for phantom Service IPs.
9. **Conntrack Session Tracking**: Tracks DNAT mappings to correctly reverse-NAT return responses; conntrack table exhaustion fixes (`nf_conntrack_max`).
10. **Swap & cgroups**: Swap disabled to preserve accurate kubelet memory limits and avoid false evictions.

### From Doc 03 (PKI & Certificate Infrastructure):
11. **mTLS & Client Identity**: Both client and server authenticate via certificates; `CN` (Username) & `O` (Group) map to RBAC.
12. **SANs (Subject Alternative Names)**: The IP/domain whitelist embedded in the API server certificate (e.g. VIP `10.0.1.100`).
13. **`~/.kube/config` Auth Flow**: Client verifies server cert with CA + SAN check $\rightarrow$ client presents certificate $\rightarrow$ API server checks RBAC.
14. **etcd CA Isolation**: Dedicated `/var/lib/rancher/rke2/server/tls/etcd/` CA isolates the raw database from standard cluster client certs.

### From Doc 04 (etcd Operations & Disaster Recovery):
15. **MVCC & Space Quotas**: Revisions accumulate $\rightarrow$ fix with `compact` + `defrag` + `alarm disarm`.
16. **Point-in-Time Snapshot Backups**: `rke2 etcd-snapshot save` creates consistent snapshots without data tearing.
17. **Multi-Master Disaster Recovery**: Stop all 3 masters $\rightarrow$ run `--cluster-reset` on Master-1 only $\rightarrow$ wipe `/db/` on Master-2 & Master-3 $\rightarrow$ restart M1, then M2 & M3.

### From Doc 05 (CNI Networking & The 7-Actor Framework):
18. **The 7-Actor Isolation Framework**:
    * `CoreDNS` = The Phonebook (`my-svc` $\rightarrow$ `10.43.x.x`).
    * `veth/eth0` = The Virtual Cable (Pod $\leftrightarrow$ Host).
    * `cni0` = The Virtual Switch on the node (L2 MAC forwarding).
    * `br_netfilter` = The Checkpoint Guard (forces bridge into `iptables`).
    * `kube-proxy` = The Address Translator (DNAT phantom Service IP $\rightarrow$ real Pod IP).
    * `Flannel` = The Delivery Truck (encapsulates cross-node packets in UDP 8472).
    * `Calico` = The Security Bouncer (enforces NetworkPolicy ALLOW/DROP).
19. **VXLAN Overlay (UDP Port 8472)**: Encapsulates inner pod packets (`10.42.x.x`) inside outer node UDP packets (`10.0.x.x:8472`).
20. **Routing & Forwarding Databases**: `ip route` maps remote subnets to `flannel.1`; `bridge fdb` maps virtual MACs to remote physical node IPs.

### From Doc 06 & 07 (Master Bootstrap, Workers & Taints):
21. **Keepalived Floating VIP**: Bound as a secondary IP on real physical `eth0`; Gratuitous ARP (GARP) failover in <1s; Unicast VRRP prevents cloud multicast blocks.
22. **RKE2 Server vs Agent**: `rke2-server` contains control plane & etcd; `rke2-agent` runs kubelet, kube-proxy, and containerd.
23. **Supervisor Port 9345 vs API Port 6443**: Port 9345 handles node joining and initial certificate generation; Port 6443 serves standard API traffic.
24. **Master Taints (`NoSchedule`)**: Isolates application workloads from control plane nodes.

### From Doc 08 (RBAC & Security):
25. **The RBAC 4-Object Model**: `Role` & `RoleBinding` (namespaced) vs. `ClusterRole` & `ClusterRoleBinding` (cluster-wide).
26. **The Reusable ClusterRole Pattern**: Binding a built-in `ClusterRole` (e.g. `view`) via a namespaced `RoleBinding`.
27. **The 3-Gate API Request Lifecycle**: Authentication (AuthN: Cert/JWT) $\rightarrow$ Authorization (AuthZ: RBAC whitelist) $\rightarrow$ Admission Control (PSS: `restricted` policies & ResourceQuotas).

### From Doc 09 (Storage & Longhorn):
28. **PV vs. PVC vs. StorageClass**: StorageClass is the automated disk provisioner; PVC is the request; PV is the bound storage block.
29. **Longhorn 3-Way Synchronous Replication**: Block-level network RAID 1 across all 3 workers; synchronous writes guarantee zero data loss; relies on host `open-iscsi` (`iscsid`).
30. **Storage Access Modes**: `ReadWriteOnce` (single node exclusive write) vs `ReadWriteMany` (shared multi-node write).

### From Doc 10 (Ingress & MetalLB):
31. **MetalLB Layer-2 ARP**: Solves the bare-metal `<pending>` LoadBalancer problem with IP address pools (`10.0.1.200 - 10.0.1.220`).
32. **NGINX Ingress Routing & TLS**: Layer-7 reverse proxy with Host/Path routing and TLS secret termination.
33. **Client IP Preservation**: `externalTrafficPolicy: Local` eliminates cross-node hops and preserves original client IP by avoiding SNAT.

### From Doc 11 (Helm & Workloads):
34. **StatefulSets vs. Deployments**: StatefulSets provide ordinal indexing (`app-0`, `app-1`), ordered rollouts, and persistent dedicated storage via `volumeClaimTemplates`.
35. **Headless Services (`clusterIP: None`)**: Disables virtual ClusterIP load balancing so CoreDNS returns direct Pod IPs for database clustering.
36. **Helm Architecture**: Package manager and template engine; stores release history natively as Kubernetes Secrets; integrated with ArgoCD for GitOps pull reconciliation.

### From Doc 12 (Monitoring & Observability):
37. **The 5-Tool Monitoring Pipeline**: Prometheus (TSDB) + Grafana (Dashboards) + Alertmanager (Routing) + Node Exporter (Host metrics) + Kube-State-Metrics (K8s object states).
38. **ServiceMonitor CRD**: Declarative Prometheus Operator pattern for dynamic scrape discovery.
39. **Golden PromQL Queries**: Detecting CrashLoopBackOffs, node disk pressure, and API server p99 latency.

### From Doc 13 & 14 (Troubleshooting & Senior War Stories):
40. **CoreDNS 5-Second Delay (`ndots:5`)**: glibc internal search domain race with Linux conntrack parallel UDP queries; fixed with `ndots:2` and `single-request-reopen`.
41. **VXLAN MTU Clamping Bug (1450 vs 1500)**: 50-byte VXLAN header overhead drops large packets with DF bit; fixed with `veth_mtu: 1450`.
42. **Zombie Namespace Finalizers**: Removing stuck finalizers via raw `/finalize` API subresource.
43. **The 4 Senior Mental Models & STAR War Stories**: Architectural depth, failure domain isolation, and production incident post-mortems.

---

## 4. Repository Structure & Artifacts

* `00_overview_and_index.md`: Master index and cluster topology.
* `01_kubernetes_and_rke2_theory.md` through `14_interview_prep.md`: Full 14-document technical curriculum.
* `architecture.md`: Comprehensive network planes, component layers, and packet flow maps.
* `QA.md`: **Master 1,055-line Senior/Staff DevOps interview-ready guide & production incident runbook.**
* `manifests/`: Declarative Kustomize manifests (Core, Secrets, RBAC, Networking, Storage, Monitoring, Workloads).

---

## 5. Next Action Plan: Phase 2 Live Cluster Bootstrap

1. **Environment Setup**: Provision 6 Ubuntu 24.04 VMs (3 Masters, 3 Workers).
2. **Node Prep Execution (Doc 02)**: Run kernel module loads (`overlay`, `br_netfilter`), disable swap, configure sysctl, install `open-iscsi` and `nfs-common`.
3. **Master-1 Bootstrap (Doc 06)**: Configure `/etc/rancher/rke2/config.yaml` with `tls-san`, start `rke2-server`, deploy Keepalived VIP.
4. **Master-2 & Master-3 Join (Doc 06)**: Join control plane nodes to VIP `10.0.1.100:9345` with cluster token.
5. **Workers Join (Doc 07)**: Install `rke2-agent` on Workers 1-3 and verify cluster readiness.
6. **Platform Layer Deployment**: Deploy Longhorn, MetalLB, NGINX Ingress, and Prometheus stack via ArgoCD.
