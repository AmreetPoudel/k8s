# 08. Observability (`kube-prometheus-stack`) & GitOps (`ArgoCD`)

> **Components**: Prometheus TSDB, Grafana, Alertmanager, Node Exporter, Kube-State-Metrics, ArgoCD  
> **Storage Backend**: Backed by Longhorn 3-way replicated storage (`longhorn-replicated`)  
> **Execution Location**: Run commands from `master-1` using `helm` and `kubectl`.

---

## Part 1: Deploy ArgoCD (GitOps Continuous Delivery Engine)

### 🎯 Step 1: Install ArgoCD via Declarative Server-Side Apply
```bash
# 1. Create dedicated namespace
kubectl create namespace argocd

# 2. Apply official declarative manifests with Server-Side Apply & Force Conflicts
kubectl apply --server-side=true --force-conflicts -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

---

### 🔑 Step 2: Configure Authentication for Private Git Repositories (SSH Deploy Keys)

In real enterprise environments, 100% of infrastructure manifests live in **Private Repositories**. To give ArgoCD read-only access:

1. **Generate a dedicated Deploy Key pair on `master-1`:**
```bash
ssh-keygen -t ed25519 -C "argocd-k8s-cluster" -f /root/.ssh/argocd_github_key -N ""
```
2. **Add the Public Key (`/root/.ssh/argocd_github_key.pub`) to GitHub:**
   * Go to: `Repository Settings` $\longrightarrow$ `Deploy Keys` $\longrightarrow$ `Add Deploy Key`.
   * Title: `argocd-cluster` (Keep 'Allow write access' UNCHECKED for read-only security).

3. **Create the ArgoCD Repository Secret in the Cluster:**
```bash
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: private-k8s-repo-creds
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: git@github.com:AmreetPoudel/k8s.git
  sshPrivateKey: |
$(sed 's/^/    /' /root/.ssh/argocd_github_key)
EOF
```

---

### 🚀 Step 3: Apply the Master GitOps Root Application ("App of Apps")

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root-platform-app
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: git@github.com:AmreetPoudel/k8s.git
    targetRevision: HEAD
    path: manifests
    directory:
      recurse: true
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
EOF
```

---

### 🔑 Step 4: Retrieve Initial ArgoCD Admin Password:
```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
echo ""
```

---

## 🛠️ Real-World GitOps Troubleshooting & Gotchas

### 1. `metadata.annotations: Too long: may not be more than 262144 bytes`
* **Symptom**: Running `kubectl apply -f install.yaml` fails on `applicationsets.argoproj.io`.
* **Root Cause**: Client-side apply serializes the full CRD YAML into `kubectl.kubernetes.io/last-applied-configuration`, exceeding the hard 256KB annotation limit.
* **Fix**: Always use `--server-side=true`.

### 2. `Apply failed with 1 conflict: conflict with "kubectl-client-side-apply"`
* **Symptom**: Switching from client-side apply to Server-Side Apply flags a field manager conflict.
* **Fix**: Append `--force-conflicts` so Server-Side Apply takes full ownership of the fields.

### 3. `The Kubernetes API could not find metallb.io/IPAddressPool`
* **Symptom**: ArgoCD tries to sync Custom Resources (e.g. MetalLB IPAddressPool, Longhorn StorageClass) before the operator CRDs exist in the cluster.
* **Root Cause**: Custom Resources cannot be instantiated if their defining CRD is not yet registered with the API server.
* **Fix**: Install the base operator manifests (e.g. `metallb-native.yaml` or Longhorn) first, or structure ArgoCD sync waves (`argocd.argoproj.io/sync-wave`).

### 4. Non-Root `kubectl` Configuration
* **Symptom**: Non-root users (`amrit`) get `The connection to the server localhost:8080 was refused`.
* **Fix**: Copy `/etc/rancher/rke2/rke2.yaml` to `/home/amrit/.kube/config`, replace server IP with VIP `10.0.2.60`, and `chown -R amrit:amrit /home/amrit/.kube`.

---

## Part 2: Deploy `kube-prometheus-stack` (Master Observability)

### 🎯 The Commands:
```bash
# Add Prometheus Community repository
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Create custom values with Longhorn Persistent Storage for Prometheus TSDB
cat <<EOF | tee /tmp/prometheus-values.yaml
prometheus:
  prometheusSpec:
    retention: 15d
    storageSpec:
      volumeClaimTemplate:
        spec:
          storageClassName: longhorn-replicated
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 20Gi

grafana:
  adminPassword: "AdminGrafanaPassword2026!#"
  persistence:
    enabled: true
    storageClassName: longhorn-replicated
    size: 5Gi

alertmanager:
  alertmanagerSpec:
    storage:
      volumeClaimTemplate:
        spec:
          storageClassName: longhorn-replicated
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 5Gi
EOF

# Install Prometheus Stack
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  -f /tmp/prometheus-values.yaml
```

---

### 🔍 Deep Architecture: Why Persistent Storage is Mandatory for Prometheus

```
[ Prometheus TSDB Pod ]
        │
        ▼ (Writes metrics every 15s)
[ Longhorn Replicated Block Storage: 20 GiB (3 Replicas) ]
        ├── Replicated on Worker 1
        ├── Replicated on Worker 2
        └── Replicated on Worker 3
```

* **If ephemeral storage is used**: Whenever the Prometheus pod restarts or upgrades, **all historical CPU, Memory, Disk IOPS, and network metrics are permanently lost!**
* **With Longhorn Persistent Storage**: Metrics survive node reboots, pod rescheduling, and cluster upgrades seamlessly!

---

## Step 3: Expose Grafana & ArgoCD via Ingress

### 🎯 The Command:
```bash
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: platform-ingress
  namespace: monitoring
  annotations:
    ingress.kubernetes.io/ssl-redirect: "false"
spec:
  ingressClassName: nginx
  rules:
  - host: grafana.internal.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: monitoring-grafana
            port:
              number: 80
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: argocd-server-ingress
  namespace: argocd
  annotations:
    ingress.kubernetes.io/ssl-redirect: "false"
spec:
  ingressClassName: nginx
  rules:
  - host: argocd.internal.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: argocd-server
            port:
              number: 80
EOF
```

---

## ✅ Step 4: Verification of Master Production Stack

Run this single health check:

```bash
# 1. Check all pods across all namespaces
kubectl get pods -A

# 2. Check all PVCs are Bound to Longhorn
kubectl get pvc -A

# 3. Check Ingress hosts
kubectl get ingress -A
```

---

# 🏁 Summary of the Built Production Platform:
1. **HA Control Plane**: 3 Masters with automated Keepalived Unicast VIP (`10.0.2.60`) failover in $<1\text{s}$.
2. **etcd Quorum**: 3-node distributed Raft with automatic snapshots every 4 hours.
3. **Dedicated Workers**: 3 Workload nodes isolated with control-plane `NoSchedule` taints.
4. **Cloud-Native Storage**: Longhorn 3-way synchronous block mirroring over Linux `iscsid`.
5. **Bare-Metal Ingress**: MetalLB Layer-2 ARP LoadBalancer + NGINX Ingress preserving real client IPs.
6. **GitOps & Observability**: ArgoCD continuous reconciliation + Prometheus TSDB, Grafana dashboards, and Alertmanager.
