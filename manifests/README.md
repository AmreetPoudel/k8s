# Kubernetes Platform Manifests (Infrastructure as Code)

This directory contains declarative Kubernetes manifests, Secrets, ConfigMaps, and Kustomize configurations for the complete 6-node RKE2 cluster.

---

## 📁 Directory Structure & Resource Map

```
manifests/
├── 01_core/
│   ├── namespaces.yaml            # production, staging, dev with PSS labels
│   ├── resource_quotas.yaml       # CPU/Memory hard quotas per namespace
│   └── limit_ranges.yaml          # Default request/limit boundaries
│
├── 02_secrets_and_configs/
│   ├── docker_registry_secret.yaml# Docker Hub / GHCR imagePullSecret credentials
│   ├── backend_secrets.yaml       # MySQL passwords, DB connection strings, JWT keys
│   ├── frontend_secrets_and_config.yaml # Frontend ConfigMap (NGINX/env) & Secrets
│   └── tls_ingress_secret.yaml    # HTTPS TLS certificate for Ingress
│
├── 02_rbac/
│   ├── developer_roles.yaml       # Namespace-scoped view & debug RBAC roles
│   └── cicd_serviceaccount.yaml   # CI/CD deployer SA, Roles & RoleBindings
│
├── 03_networking/
│   ├── metallb_config.yaml        # MetalLB L2 IPAddressPool & L2Advertisement
│   ├── network_policies.yaml      # Default-deny, web ingress & CoreDNS egress
│   └── ingress_routes.yaml        # NGINX Ingress rules with TLS termination
│
├── 04_storage/
│   ├── storage_classes.yaml       # Longhorn 3-way replicated StorageClass
│   └── pvc_examples.yaml          # Sample PersistentVolumeClaims
│
├── 05_monitoring/
│   ├── prometheus_values.yaml     # Helm values for kube-prometheus-stack
│   ├── custom_alert_rules.yaml    # PrometheusRules for node/etcd/pod alerts
│   └── alertmanager_config_secret.yaml # Slack alert notification webhook
│
├── 06_workloads/
│   ├── hello_app_deployment.yaml  # Multi-replica web app (uses secrets + configmaps)
│   ├── mysql_statefulset.yaml     # MySQL StatefulSet (uses backend-database-secret)
│   └── hpa_autoscaling.yaml       # HorizontalPodAutoscaler with scale policies
│
├── kustomization.yaml             # Master Kustomize bundle
└── README.md                      # This guide
```

---

## 🔐 How to Create and Manage Secrets

### 1. Docker Registry Secret (`imagePullSecrets`)

Generate your actual Docker Hub / GHCR credential secret via `kubectl`:
```bash
# Docker Hub
kubectl create secret docker-registry dockerhub-registry-secret \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username="<YOUR_DOCKERHUB_USERNAME>" \
  --docker-password="<YOUR_DOCKERHUB_TOKEN_OR_PASSWORD>" \
  --docker-email="<YOUR_EMAIL>" \
  --namespace=production \
  --dry-run=client -o yaml > manifests/02_secrets_and_configs/docker_registry_secret.yaml
```

### 2. Ingress TLS Certificate Secret

Create a self-signed or real TLS certificate secret for HTTPS:
```bash
# Generate private key & cert
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt \
  -subj "/CN=app.example.local/O=Enterprise"

# Create Secret in production namespace
kubectl create secret tls app-tls-cert \
  --cert=tls.crt \
  --key=tls.key \
  --namespace=production
```

---

## 🚀 How to Apply the Manifests

### One-Command Deployment (Kustomize)
```bash
# Validate and render:
kubectl kustomize manifests/

# Apply all resources to cluster:
kubectl apply -k manifests/
```

### Step-by-Step Deployment
```bash
# 1. Namespaces & Governance
kubectl apply -f manifests/01_core/

# 2. Secrets & Configurations
kubectl apply -f manifests/02_secrets_and_configs/

# 3. RBAC & Service Accounts
kubectl apply -f manifests/02_rbac/

# 4. Networking, MetalLB & Ingress
kubectl apply -f manifests/03_networking/

# 5. StorageClasses & PVCs
kubectl apply -f manifests/04_storage/

# 6. Monitoring Rules
kubectl apply -f manifests/05_monitoring/

# 7. Workloads & Autoscaling
kubectl apply -f manifests/06_workloads/
```
