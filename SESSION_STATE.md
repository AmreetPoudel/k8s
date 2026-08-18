# Comprehensive Session Context & Learning State
## RKE2 Kubernetes Mastery Series

> **Repository**: `https://github.com/AmreetPoudel/k8s.git`  
> **Last Updated**: August 18, 2026  
> **Current Status**: **Doc 01 (Theory & Architecture) 100% Mastered & Revised**  
> **Immediate Next Step**: **Doc 02 (Linux Node Preparation & Kernel Tuning)**

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

## 3. Knowledge Retention & Concepts Mastered (Doc 01)

1. **Active-Active vs. Active-Passive Control Plane**:
   * `kube-apiserver`: **Active-Active** across all 3 masters because it is 100% stateless (all state lives in etcd).
   * `kube-scheduler` & `kube-controller-manager`: **Active-Passive** with Lease locks. If two schedulers were active simultaneously, they would race and cause conflicting double-allocations.
2. **etcd Replication & Single Source of Truth**:
   * `etcd` is the **ONLY stateful component** in the control plane.
   * Uses **Full 100% Replication** (no sharding). All 3 nodes have the complete database. Backups are just `etcd` snapshot files (`.db`).
   * **The Golden Rule**: ONLY `kube-apiserver` reads/writes etcd directly. Controllers/Schedulers send HTTPS REST calls to the API server.
3. **The HTTP/2 WATCH API vs. Polling**:
   * Polling loops would melt CPU with thousands of TLS handshakes and JSON parsing per second.
   * Long-lived HTTP/2 Watch streams (`?watch=true`) use Linux `epoll` event-driven I/O, consuming **0% CPU when idle and ~3KB RAM**.
4. **The "Chicken-and-Egg" Bootstrap ($t=0$) & Static Pods**:
   * `containerd` and `kubelet` run as native host systemd services on the OS (NOT containers).
   * Kubelet reads local YAML files from `/var/lib/rancher/rke2/agent/pod-manifests/` (Static Pods) to launch `etcd` and `kube-apiserver` before any cluster exists.
5. **CRI (Container Runtime Interface)**:
   * CRI is an open gRPC interface standard.
   * In RKE2, `containerd` is the concrete implementation listening on `--container-runtime-endpoint=unix:///run/k3s/containerd/containerd.sock`.
6. **`kube-proxy` vs. CNI (Canal / Flannel / Calico)**:
   * `kube-proxy`: Writes **kernel DNAT rules** (Virtual Service ClusterIP `10.43.x.x` $\rightarrow$ Pod IP `10.42.x.x`). It is not an in-path proxy.
   * `CNI` (Flannel): Allocates real Pod IPs (`10.42.x.x`), creates `veth` pairs, and routes cross-node packets over VXLAN (UDP 8472).
   * `CNI` (Calico): Enforces NetworkPolicy firewall rules in the Linux kernel.
7. **Why Pod IPs Do NOT Change on Container Crash**:
   * The Pod IP is attached to the invisible **`pause` container network namespace** (the "hotel room").
   * When the app container crashes, Kubelet restarts it inside the existing namespace, preserving the IP address.
8. **Keepalived & VRRP Failover**:
   * Configured via `unicast_peer` to bypass cloud multicast blocks.
   * When `rke2-server` crashes, `/usr/local/bin/check-rke2.sh` fails and drops Master 1's priority ($101 - 20 = 81$), allowing Master 2 (priority 100) to take the VIP.
   * Master 2 broadcasts **Gratuitous ARP (GARP)** to update network switches in 1 millisecond.

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

When resuming in any new session:
* Proceed directly to **Doc 02: Node Preparation** ([02_node_preparation.md](./02_node_preparation.md)).
* Cover:
  1. Swap memory pressure and cgroups eviction failure.
  2. `br_netfilter` and `overlay` kernel modules.
  3. Sysctl parameters: `bridge-nf-call-iptables = 1`, `ip_forward = 1`, `nf_conntrack_max`.
  4. Port matrices and NTP time sync.
