# Kubernetes Troubleshooting Lab Guide

> **Format used in every scenario:**  
> `Symptoms` → `Diagnosis commands` → `Fix steps` → `Verify`  
> No lab setup instructions — only how to debug and fix.

---

## Index

| Part | Category | Scenarios |
|------|----------|-----------|
| [Part 1](./part1-pod-resource-failures.md) | **Pod Resource Failures** | OOMKilled · CPU throttling · CrashLoopBackOff · ImagePullBackOff · Pending (resources) |
| [Part 2](./part2-pod-scheduling-health-checks.md) | **Pod Scheduling & Health Checks** | Taints/tolerations · Node affinity · Liveness probe · Readiness probe · Init containers |
| [Part 3](./part3-node-level-failures.md) | **Node-Level Failures** | NotReady node · DiskPressure · MemoryPressure · PIDPressure · containerd crash |
| [Part 4](./part4-networking.md) | **Networking** | CNI failure · CoreDNS · No endpoints · NetworkPolicy · Ingress 502s |
| [Part 5](./part5-certificates-auth.md) | **Certificates & Auth** | Expired certs · kubelet cert · Cert rotation (RKE2) · SA tokens · RBAC 403 |
| [Part 6](./part6-control-plane-etcd.md) | **Control Plane / etcd** | etcd quorum loss · etcd disk latency · API server HA · Scheduler stuck · Controller-manager stuck |
| [Part 7](./part7-storage.md) | **Storage** | PVC Pending · Volume attach/detach · Multi-attach RWO · StatefulSet mount · CSI driver |
| [Part 8](./part8-cluster-upgrades.md) | **Cluster Upgrades** | Version skew · Upgrade order · Drain mistakes · Rollback · CRD/API deprecation |
| [Part 9](./part9-observability-misc.md) | **Observability & Misc** | Metrics-server/HPA · Log gaps · Quota exhaustion · PDB blocking · Cluster autoscaler |

---

## How to use this guide

1. **Identify the category** from your symptom (pod, node, network, storage, etc.)
2. **Open the relevant part** and find the matching scenario
3. **Run the diagnosis commands first** — understand what's happening before applying any fix
4. **Apply fix steps in order** — starting with the least invasive
5. **Always verify** before closing the incident

---

## RKE2-Specific Notes

All commands are verified against RKE2. Key paths:

| Component | Path |
|-----------|------|
| Config | `/etc/rancher/rke2/config.yaml` |
| TLS certs | `/var/lib/rancher/rke2/server/tls/` |
| etcd data | `/var/lib/rancher/rke2/server/db/etcd/` |
| Binaries | `/var/lib/rancher/rke2/bin/` |
| containerd socket | `/run/k3s/containerd/containerd.sock` |
| kubelet data | `/var/lib/kubelet/` |
| Server service | `rke2-server` |
| Agent service | `rke2-agent` |
| etcdctl wrapper | `/var/lib/rancher/rke2/bin/etcdctl` |

## Common First Steps for Any Issue

```bash
# 1. Check node health
kubectl get nodes

# 2. Check pod status across all namespaces
kubectl get pods --all-namespaces | grep -v Running | grep -v Completed

# 3. Check recent events
kubectl get events --all-namespaces --sort-by=lastTimestamp | tail -30

# 4. Check system pod health
kubectl get pods -n kube-system

# 5. Check for resource pressure
kubectl top nodes
kubectl top pods --all-namespaces --sort-by=cpu
```
