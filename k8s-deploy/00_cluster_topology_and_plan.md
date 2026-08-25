# 00. Cluster Topology & Master Architecture Plan

> **Environment**: On-Premise Nutanix AHV / Enterprise Private Cloud  
> **Kubernetes Distribution**: RKE2 (Rancher Government / High-Security Kubernetes v1.30+)  
> **CNI**: Canal (Flannel VXLAN UDP 8472 + Calico Felix NetworkPolicy)  
> **High Availability**: 3 Control Plane Nodes + Keepalived Unicast VRRP VIP (`10.0.1.100`)  
> **Storage**: Longhorn 3-Way Synchronous Replicated Block Storage  
> **Ingress & LB**: MetalLB Layer-2 ARP (`10.0.1.200-220`) + NGINX Ingress Controller  
> **GitOps & Observability**: ArgoCD + `kube-prometheus-stack` (Prometheus, Grafana, Alertmanager)

---

## 1. Physical & Virtual Machine Allocation Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 ENTERPRISE PRIVATE NETWORK (10.0.0.0/16)                               │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                        │
│  [ KEEPAlIVED FLOATING VIP: 10.0.1.100:6443 / 9345 ] (Bound to Active Master eth0)                     │
│                                                                                                        │
│  ┌─────────────────────────────┐ ┌─────────────────────────────┐ ┌─────────────────────────────┐       │
│  │ master-1 (10.0.1.10)        │ │ master-2 (10.0.1.11)        │ │ master-3 (10.0.1.12)        │       │
│  │ 4 vCPU | 8 GB RAM | 60GB    │ │ 4 vCPU | 8 GB RAM | 60GB    │ │ 4 vCPU | 8 GB RAM | 60GB    │       │
│  │ etcd Member 1               │ │ etcd Member 2               │ │ etcd Member 3               │       │
│  │ Taint: NoSchedule           │ │ Taint: NoSchedule           │ │ Taint: NoSchedule           │       │
│  └─────────────────────────────┘ └─────────────────────────────┘ └─────────────────────────────┘       │
│                                                                                                        │
│  ┌─────────────────────────────┐ ┌─────────────────────────────┐ ┌─────────────────────────────┐       │
│  │ worker-1 (10.0.2.10)        │ │ worker-2 (10.0.2.11)        │ │ worker-3 (10.0.2.12)        │       │
│  │ 4 vCPU | 16 GB RAM | 100GB  │ │ 4 vCPU | 16 GB RAM | 100GB  │ │ 4 vCPU | 16 GB RAM | 100GB  │       │
│  │ Longhorn Storage Node 1     │ │ Longhorn Storage Node 2     │ │ Longhorn Storage Node 3     │       │
│  │ MetalLB Speaker 1           │ │ MetalLB Speaker 2           │ │ MetalLB Speaker 3           │       │
│  │ NGINX Ingress Controller    │ │ Application Workloads       │ │ Prometheus & Grafana TSDB   │       │
│  └─────────────────────────────┘ └─────────────────────────────┘ └─────────────────────────────┘       │
│                                                                                                        │
│  [ METALLB LAYER-2 IP POOL: 10.0.1.200 - 10.0.1.220 ]                                                  │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. IP Address & Port Allocation Plan

| Node Name | Role | Physical IP | Subnet CIDR | Ports Open (Inbound) |
| :--- | :--- | :--- | :--- | :--- |
| **`master-1`** | Control Plane / etcd | `10.0.1.10` | `/24` | `6443` (API), `9345` (Supervisor), `2379-2380` (etcd), `10250` (Kubelet), `8472` (UDP VXLAN), `VRRP 112` |
| **`master-2`** | Control Plane / etcd | `10.0.1.11` | `/24` | `6443` (API), `9345` (Supervisor), `2379-2380` (etcd), `10250` (Kubelet), `8472` (UDP VXLAN), `VRRP 112` |
| **`master-3`** | Control Plane / etcd | `10.0.1.12` | `/24` | `6443` (API), `9345` (Supervisor), `2379-2380` (etcd), `10250` (Kubelet), `8472` (UDP VXLAN), `VRRP 112` |
| **`worker-1`** | Workloads / Storage / Ingress | `10.0.2.10` | `/24` | `10250` (Kubelet), `8472` (UDP VXLAN), `9500-9502` (Longhorn), `80/443` (Ingress) |
| **`worker-2`** | Workloads / Storage | `10.0.2.11` | `/24` | `10250` (Kubelet), `8472` (UDP VXLAN), `9500-9502` (Longhorn) |
| **`worker-3`** | Workloads / Storage / Monitoring | `10.0.2.12` | `/24` | `10250` (Kubelet), `8472` (UDP VXLAN), `9500-9502` (Longhorn) |
| **`API VIP`** | Floating LoadBalancer | `10.0.1.100` | `/24` | `6443` (K8s API), `9345` (RKE2 Supervisor) |
| **`Pod CIDR`** | Virtual Pod Network | `10.42.0.0/16` | N/A | Handled by Flannel VXLAN |
| **`Service CIDR`** | Virtual ClusterIPs | `10.43.0.0/16` | N/A | Handled by Linux Kernel netfilter / kube-proxy |
| **`MetalLB Pool`** | External Load Balancers | `10.0.1.200 - 10.0.1.220` | N/A | Handled by MetalLB Layer-2 ARP speakers |

---

## 3. The 8 Deployment Steps Sequence

1. [01_node_preparation.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/01_node_preparation.md): Linux kernel modules, sysctl, swap removal, firewall rules, and storage dependencies.
2. [02_keepalived_ha_floating_vip.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/02_keepalived_ha_floating_vip.md): Unicast VRRP configuration on all 3 masters with automated health check failover.
3. [03_bootstrap_master_1.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/03_bootstrap_master_1.md): Initializing the first control plane node, setting up SANs, and generating cluster tokens.
4. [04_join_masters_2_and_3.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/04_join_masters_2_and_3.md): Joining Master 2 and Master 3 to form the 3-node etcd Raft Quorum.
5. [05_join_worker_nodes.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/05_join_worker_nodes.md): Installing `rke2-agent` on Workers 1, 2, 3, labeling nodes, and applying master taints.
6. [06_longhorn_distributed_storage.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/06_longhorn_distributed_storage.md): Deploying 3-way replicated block storage over host iSCSI.
7. [07_metallb_and_ingress.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/07_metallb_and_ingress.md): Deploying MetalLB Layer-2 ARP IP pools and NGINX Ingress Controller.
8. [08_monitoring_and_gitops.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/08_monitoring_and_gitops.md): Deploying ArgoCD for GitOps and `kube-prometheus-stack` for full observability.
