# Kubernetes Platform Manifests (Infrastructure as Code)

This folder contains all declarative Kubernetes manifests and configuration templates discussed across the documentation series.

Once your 6-node RKE2 cluster is booted and `kubectl get nodes` shows all nodes `Ready`, you can apply these manifests in order or selectively.

---

## 📁 Directory Structure & Apply Order

```
manifests/
├── 01_core/              # Core namespaces, ResourceQuotas, LimitRanges
│   ├── namespaces.yaml
│   ├── resource_quotas.yaml
│   └── limit_ranges.yaml
│
├── 02_rbac/              # Developer roles, CI/CD ServiceAccounts, Bindings
│   ├── developer_roles.yaml
│   └── cicd_serviceaccount.yaml
│
├── 03_networking/        # MetalLB IPPool, NetworkPolicies, Ingress
│   ├── metallb_config.yaml
│   ├── network_policies.yaml
│   └── ingress_routes.yaml
│
├── 04_storage/           # Longhorn & local-path StorageClasses, test PVCs
│   ├── storage_classes.yaml
│   └── pvc_examples.yaml
│
├── 05_monitoring/        # Prometheus custom alerts & ServiceMonitors
│   ├── prometheus_values.yaml
│   ├── custom_alert_rules.yaml
│   └── alertmanager_config_secret.yaml
│
├── 06_workloads/         # Sample stateless & stateful apps with HPA
│   ├── hello_app_deployment.yaml
│   ├── mysql_statefulset.yaml
│   └── hpa_autoscaling.yaml
│
└── kustomization.yaml    # Master Kustomize file to manage everything
```

---

## 🚀 How to Apply

### Option A: Apply All via Kustomize (One Command)
```bash
# Preview what will be applied:
kubectl kustomize manifests/

# Apply everything:
kubectl apply -k manifests/
```

### Option B: Apply Step-by-Step (Recommended for Learning)

```bash
# 1. Namespaces & Core Governance
kubectl apply -f manifests/01_core/

# 2. RBAC & Service Accounts
kubectl apply -f manifests/02_rbac/

# 3. MetalLB & Ingress (Ensure MetalLB CRDs exist first)
kubectl apply -f manifests/03_networking/

# 4. Storage PVCs & Classes
kubectl apply -f manifests/04_storage/

# 5. Monitoring Alert Rules
kubectl apply -f manifests/05_monitoring/

# 6. Sample Workloads & HPA
kubectl apply -f manifests/06_workloads/
```
