# Comprehensive Session Context & Learning State
## RKE2 Kubernetes Mastery Series

> **Repository**: `https://github.com/AmreetPoudel/k8s.git`  
> **Last Updated**: August 19, 2026  
> **Current Status**: **Doc 01 (Theory), Doc 01b (Raft), and Doc 02 (Node Preparation & Linux Networking) 100% Mastered**  
> **Immediate Next Step**: **Doc 03 (PKI & Certificates) / Doc 04 (etcd Deep Dive) or Cluster Bootstrap**

---

## 1. User Profile, Goals & Teaching Style

* **Background**: Experienced with day-to-day production Kubernetes administration (`kubectl`, logs, scaling), but the underlying control plane, bare-metal networking, and bootstrapping were a "black box" because previous clusters were pre-built.
* **Goal**: Master raw Kubernetes (RKE2) on 6 bare-metal / cloud VMs from scratch to a Senior/Staff DevOps & SRE interview level.
* **Learning Style & Preferences**:
  1. **Concrete over Abstract**: Wants side-by-side configuration files and real Linux commands (`ip addr`, `curl`, `sysctl`, `iptables`).
  2. **Revision Method**: 1 open-ended writing question at a time (NO multiple-choice). If a mistake is made, give a hint and prompt to try again.
  3. **Cost / Lab Constraint**: Will run the live 6-node cluster on cloud for a focused 4–6 hour sprint (or locally on Multipass).
  4. **Deployment Strategy**: Manifests in `manifests/` will be deployed using **ArgoCD (GitOps)** via `kustomization.yaml`.

---

## 2. Cluster Architecture & Topology

* **Nodes**: 6 nodes total (3 Masters, 3 Workers) on Ubuntu 24.04 LTS.
  * `master-1` (`10.0.1.10`), `master-2` (`10.0.1.11`), `master-3` (`10.0.1.12`).
  * `worker-1` (`10.0.2.10`), `worker-2` (`10.0.2.11`), `worker-3` (`10.0.2.12`).
* **High Availability**: Keepalived Unicast VRRP Virtual IP (`10.0.1.100:6443` and `:9345`).
* **CNI Choice**: Canal (Flannel VXLAN overlay on UDP 8472 for Pod IPAM `10.42.0.0/16` + Calico Felix for NetworkPolicies).
* **Storage**: Longhorn 3-way distributed block storage (`/var/lib/longhorn`).
* **Ingress / Load Balancer**: NGINX Ingress Controller (HostPort 80/443) + MetalLB Layer 2 ARP pool (`10.0.1.200 - 10.0.1.220`).
* **Master Taints**: `node-role.kubernetes.io/control-plane:NoSchedule` (zero user workloads on control plane).

---

## 3. Knowledge Retention & Concepts Mastered

### From Doc 01 & 01b (Theory & Raft):
1. **Active-Active vs. Active-Passive Control Plane**: `kube-apiserver` is stateless (Active-Active); `scheduler` & `controller-manager` use Lease locks (Active-Passive).
2. **etcd Replication & Single Source of Truth**: 100% full replication; only `kube-apiserver` talks to etcd directly.
3. **HTTP/2 WATCH API vs. Polling**: `epoll` event-driven streams consume ~0% CPU at idle vs polling storm.
4. **Chicken-and-Egg Bootstrap ($t=0$)**: Native systemd Kubelet + Static Pod manifests (`/var/lib/rancher/rke2/agent/pod-manifests/`).
5. **CRI & Pause Container**: `containerd` via gRPC socket; `pause` container namespace holds IP across pod app restarts.
6. **Keepalived & VRRP**: Unicast VRRP failover with health scripts and Gratuitous ARP (GARP).

### From Doc 02 (Node Preparation & Linux Kernel Networking):
7. **`overlay` Module (OverlayFS)**:
   * Provides union mount filesystem layering (`lowerdir` image layers + `upperdir` writable layer).
   * Enables Copy-on-Write (CoW), allowing instant container startup and shared read-only base image disk space.
8. **Linux Bridge as Layer 2 Software Switch**:
   * Bridges (`cni0` / `cbr0`) exist in the host kernel to connect pod `veth` pairs.
   * Being Layer 2 switches, they normally forward frames by MAC address and bypass Layer 3/4 firewalls.
9. **`br_netfilter` & `bridge-nf-call-iptables`**:
   * Forces Layer 2 bridged packets through Layer 3/4 `netfilter`/`iptables`.
   * Essential because ClusterIPs are "phantom" virtual IPs with no physical/MAC presence; `iptables` (via `kube-proxy`) must intercept and perform **DNAT** to rewrite ClusterIP $\rightarrow$ real Pod IP.
   * Without `br_netfilter`, same-node pod-to-service traffic drops silently.
10. **Conntrack Session Tracking**:
    * Records DNAT translations in the Linux connection tracking table so return traffic undergoes reverse-NAT (un-NAT) back to the Service IP.
11. **Swap & cgroups Memory Management**:
    * Swap undermines cgroup memory limits and causes unpredictable latency spikes / false evictions.

---

## 4. Repository Structure & Artifacts

* `00_overview_and_index.md`: Master index and cluster topology.
* `01_kubernetes_and_rke2_theory.md`: Complete theoretical curriculum.
* `02_node_preparation.md`: OS hardening, swap removal, kernel modules, sysctl, and firewall ports.
* `03_pki_and_certs.md` through `14_interview_prep.md`: Full technical suite.
* `architecture.md`: Comprehensive network planes, component layers, and packet flow maps.
* `manifests/`: Declarative Kustomize manifests (Core, Secrets, RBAC, Networking, Storage, Monitoring, Workloads).

---

## 5. Next Planned Action

* Proceed to **Doc 03: PKI and Certificate Infrastructure** ([03_pki_and_certs.md](./03_pki_and_certs.md)) or **Doc 04: etcd Deep Dive** ([04_etcd_deepdive.md](./04_etcd_deepdive.md)).
