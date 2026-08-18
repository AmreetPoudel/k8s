# RKE2 Kubernetes: From Zero to Interview-Ready
## Complete Learning Documentation for a 3-Master / 3-Worker Cluster

> **Goal**: You administered Kubernetes in production but never set it up.  
> These docs fix that — permanently. You will understand *why* every command exists,  
> *what breaks* if you skip it, and *how to explain it in an interview*.

---

## What You're Building

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         YOUR NETWORK (AWS or Nutanix)                    │
│                                                                          │
│   Public Subnet (Masters)              Private Subnet (Workers)          │
│  ┌────────────────────────┐           ┌────────────────────────┐         │
│  │  master-1  10.0.1.10   │           │  worker-1  10.0.2.10   │         │
│  │  master-2  10.0.1.11   │           │  worker-2  10.0.2.11   │         │
│  │  master-3  10.0.1.12   │           │  worker-3  10.0.2.12   │         │
│  │                        │           │                        │         │
│  │  VIP: 10.0.1.100       │           │  (Reached via masters  │         │
│  │  (keepalived)          │           │   or NAT GW)           │         │
│  └────────────────────────┘           └────────────────────────┘         │
│           ↑                                      ↑                       │
│    Port 6443 (API)                       Workloads run here              │
│    Port 9345 (RKE2 join)                 Masters TAINTED                 │
│    etcd :2379/:2380                      (no workloads on masters)       │
└──────────────────────────────────────────────────────────────────────────┘
```

**Specs per node (recommended for learning):**
- CPU: 2 vCPU minimum (4 recommended for masters)
- RAM: 4 GB minimum (8 GB recommended for masters)
- Disk: 50 GB OS + 50 GB for etcd/data (masters), 50 GB (workers)
- OS: Ubuntu 24.04 LTS
- CNI: **Canal** (Flannel overlay + Calico NetworkPolicy) — explained in Doc 05

---

## Documentation Index

Read these **in order**. Each doc builds on the previous one.

| # | Document | What You Learn |
|---|----------|----------------|
| [01](./01_kubernetes_and_rke2_theory.md) | Kubernetes & RKE2 Theory | What k8s is, control plane anatomy, how RKE2 differs from kubeadm/k3s/RKE1 |
| [01b](./01b_raft_consensus_made_simple.md) | Raft Consensus Visual Guide | Step-by-step visual Raft breakdown (Election, Log Replication, Network Partitions, etcd) |
| [02](./02_node_preparation.md) | Node Preparation | OS hardening, kernel parameters, firewall rules, why each setting exists |
| [03](./03_pki_and_certs.md) | PKI & Certificates | How k8s TLS works, what breaks if certs expire, SANs, CA chain |
| [04](./04_etcd_deepdive.md) | etcd Deep Dive | Raft consensus, quorum math, backup/restore, etcdctl commands |
| [05](./05_cni_networking.md) | CNI & Networking | Canal vs Cilium, overlay networks, iptables/eBPF, pod-to-pod routing |
| [06](./06_master_cluster_bootstrap.md) | Bootstrap Masters | Install RKE2 on master-1, join master-2 and master-3, keepalived VIP |
| [07](./07_worker_nodes.md) | Join Workers | Install RKE2 agent, join cluster, taint masters, verify scheduling |
| [08](./08_rbac_and_security.md) | RBAC & Security | Roles, ClusterRoles, ServiceAccounts, CIS benchmark, audit logs |
| [09](./09_storage_longhorn.md) | Storage & Longhorn | CSI, PV/PVC/StorageClass, Longhorn install, distributed storage |
| [10](./10_ingress_and_lb.md) | Ingress & LoadBalancer | NGINX ingress, MetalLB, how traffic reaches your pods |
| [11](./11_helm_and_workloads.md) | Helm & Workloads | Deploy apps, Helm charts, operators, namespace strategy |
| [12](./12_monitoring.md) | Monitoring Stack | Prometheus, Grafana, alerting, what to monitor in RKE2 |
| [13](./13_troubleshooting.md) | Troubleshooting | crictl, journalctl, etcd debugging, CNI issues, cert failures |
| [14](./14_interview_prep.md) | Interview Prep | 60+ questions with deep answers, mental models, war stories |
| [architecture.md](./architecture.md) | **Production Architecture** | Complete 6-node network topology, component layers, storage mesh, and packet flow paths |
| [manifests/](./manifests/README.md) | **IaC Manifests** | Ready-to-apply Kustomize manifests for namespaces, RBAC, storage, ingress, monitoring |

---

## How to Use These Docs

1. **Read each doc fully before typing any command** — the theory matters
2. Commands are shown like this:
   ```bash
   # [NODE: master-1] — means run this on master-1 only
   sudo some-command
   ```
3. ⚠️ Warning blocks = things that silently break your cluster
4. 💡 Insight blocks = interview gold
5. Every command has a comment explaining WHY it exists

---

## Shorthand Used Throughout

| Symbol | Meaning |
|--------|---------|
| `[M1]` | Run on master-1 only |
| `[ALL-M]` | Run on all 3 masters |
| `[ALL-W]` | Run on all 3 workers |
| `[ALL]` | Run on all 6 nodes |
| `[LOCAL]` | Run on your laptop |
| ⚠️ | Critical — skipping this breaks things |
| 💡 | Interview insight |
| 🔍 | Deep explanation |

---

*Start with [Doc 01 →](./01_kubernetes_and_rke2_theory.md)*
