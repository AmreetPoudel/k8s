# Comprehensive Session Context & Learning State
## RKE2 Kubernetes Mastery Series

> **Repository**: `https://github.com/AmreetPoudel/k8s.git`  
> **Last Updated**: August 29, 2026  
> **Current Status**: **PHASE 2 LIVE MULTI-NODE BOOTSTRAP 100% COMPLETE & GITOPS SYNCED!**  
> **Topology**: 6 Bare-Metal VMs (3 Masters + 3 Workers on Subnet `10.0.2.0/24`)  
> **High Availability**: Keepalived VRRP Floating VIP (`10.0.2.60`) + 3-Node etcd Raft Quorum  
> **GitOps Continuous Delivery**: ArgoCD in-cluster engine pulling from private repo `AmreetPoudel/k8s` via SSH Deploy Keys  
> **Next Milestone**: **PHASE 3: PLATFORM WORKLOADS (Longhorn 3-Way Storage Smoke Test, MetalLB/Ingress verification, Prometheus Stack)**

---

## 1. Live Infrastructure Topology & Verified State

| Node Name | Role | IP Address | Specs | Status | Key Components |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`master-1`** | Control Plane / etcd | `10.0.2.50` | 4 vCPU, 8 GB RAM | 🟢 **Ready** | RKE2 Server, etcd #1, Keepalived (Prio 101, VIP `10.0.2.60`) |
| **`master-2`** | Control Plane / etcd | `10.0.2.51` | 4 vCPU, 8 GB RAM | 🟢 **Ready** | RKE2 Server, etcd #2, Keepalived (Prio 100) |
| **`master-3`** | Control Plane / etcd | `10.0.2.52` | 4 vCPU, 8 GB RAM | 🟢 **Ready** | RKE2 Server, etcd #3, Keepalived (Prio 99) |
| **`worker-1`** | Workload Worker | `10.0.2.53` | 4 vCPU, 16 GB RAM | 🟢 **Ready** | RKE2 Agent, iscsid, MetalLB Speaker, Longhorn Replica |
| **`worker-2`** | Workload Worker | `10.0.2.54` | 4 vCPU, 16 GB RAM | 🟢 **Ready** | RKE2 Agent, iscsid, MetalLB Speaker, Longhorn Replica |
| **`worker-3`** | Workload Worker | `10.0.2.55` | 4 vCPU, 16 GB RAM | 🟢 **Ready** | RKE2 Agent, iscsid, MetalLB Speaker, Longhorn Replica |
| **`VIP`** | Floating IP | `10.0.2.60` | VRRP Alias | 🟢 **Active** | Primary cluster entrypoint (`k8s-vip.local`) |
| **`LB Pool`** | LoadBalancer Pool | `10.0.2.56-59` | 4 Public IPs | 🟢 **Assigned** | MetalLB Layer-2 ARP LoadBalancer Pool |

---

## 2. Real-World Battle-Tested Troubleshooting & War Stories

### 1. The Linux Socket `bind-address` Gotcha (Supervisor Port 9345 Connection Refused)
* **Symptom**: `master-2` failed to join cluster with `curl: (7) Failed to connect to 10.0.2.60 port 9345: Connection refused`.
* **Root Cause**: Setting `bind-address: "10.0.2.50"` forced RKE2 sockets to listen strictly on the physical IP, rejecting packets arriving on the secondary Keepalived Floating VIP `10.0.2.60`.
* **Fix**: Omit `bind-address` (defaults to `0.0.0.0`) so the daemon accepts traffic on both physical IP and VIP aliases.

### 2. Predictable Network Interface Names (`ens3` vs `eth0`)
* **Symptom**: Keepalived failed with `Non-existent interface specified in configuration`.
* **Root Cause**: Bare-metal AHV VMs use systemd predictable interface names (`ens3` or `enp0s3`), not `eth0`.
* **Fix**: Updated `keepalived.conf` across all masters with `interface ens3` and shortened `auth_pass` to 8 characters (`K8sVip12`).

### 3. CRD 256KB Metadata Annotation Limit on ArgoCD
* **Symptom**: `kubectl apply -f install.yaml` failed with `metadata.annotations: Too long: may not be more than 262144 bytes` on `applicationsets.argoproj.io`.
* **Root Cause**: Client-side apply attempts to store massive CRDs inside `kubectl.kubernetes.io/last-applied-configuration`.
* **Fix**: Used Server-Side Apply: `kubectl apply --server-side=true --force-conflicts -n argocd -f install.yaml`.

### 4. GitOps Dependency Ordering & Missing CRDs
* **Symptom**: ArgoCD initial sync reported `The Kubernetes API could not find metallb.io/IPAddressPool`.
* **Root Cause**: Custom Resources cannot be applied before their parent operator/CRD is installed in the cluster.
* **Fix**: Installed MetalLB native operator first (`metallb-native.yaml`), triggering instant self-healing sync.

### 5. Private Git Repository Authentication via SSH Deploy Keys
* **Implementation**: Generated ed25519 deploy key pair, placed public key on GitHub (`AmreetPoudel/k8s`), and injected private key into Kubernetes Secret labeled `argocd.argoproj.io/secret-type: repository`.
* **Result**: ArgoCD syncs private manifests with zero manual `kubectl` intervention.
