# 06. Longhorn Distributed Block Storage Deployment

> **Target Nodes**: Worker Nodes 1, 2, 3 (`worker-1`, `worker-2`, `worker-3`)  
> **Storage Technology**: 3-Way Synchronous Replicated Block Storage over Linux iSCSI  
> **Execution Location**: Run commands from `master-1` using `kubectl` and `helm`.

---

## Step 1: Install Helm 3 on `master-1`

### 🎯 The Command:
```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### ❓ Why are we doing this?
Helm is the official Kubernetes package manager that renders templates and deploys complex microservice applications (Longhorn, MetalLB, Prometheus, ArgoCD) with versioned release management.

---

## Step 2: Verify Host Storage Prerequisites on All Worker Nodes

### 🎯 Run this pre-flight check across `worker-1`, `worker-2`, and `worker-3`:
```bash
# Verify iscsid daemon is active
systemctl is-active iscsid

# Verify NFS client utilities are installed
which mount.nfs
```

### ⚠️ What happens if `iscsid` is inactive?
When Longhorn provisions a PersistentVolume (PV), it exposes a block device on `/dev/longhorn/<vol-name>` using Linux iSCSI. If `iscsid` is not running on the worker node, the volume mount fails with `iscsiadm: can not connect to iSCSI daemon`.

---

## Step 3: Deploy Longhorn via Helm (Declarative Values)

### 🎯 Run from `master-1`:
```bash
# Add official Longhorn repository
helm repo add longhorn https://charts.longhorn.io
helm repo update

# Install Longhorn using our declarative values file
helm install longhorn longhorn/longhorn \
  --namespace longhorn-system \
  --create-namespace \
  --values manifests/01-storage/01-longhorn-values.yaml
```

---

## Step 4: Apply the Production Replicated StorageClass

### 🎯 The Command:
```bash
kubectl apply -f manifests/01-storage/02-storageclass.yaml
```

---

## ✅ Step 5: Automated Storage Smoke Test (Zero Sleep)

### ❓ What is a "Smoke Test" in Engineering?
The term originated in electrical hardware testing: when you first turn on a newly wired circuit board, you check if **"smoke comes out"** before connecting expensive appliances.

In Kubernetes infrastructure:
A **Smoke Test** is an automated, lightweight sanity test that exercises the **entire end-to-end subsystem** (PVC request $\rightarrow$ CSI Driver $\rightarrow$ iSCSI block attachment $\rightarrow$ Ext4 formatting $\rightarrow$ Write & Read verification $\rightarrow$ clean exit).

We run this test to prove that distributed storage is 100% healthy **before** deploying mission-critical databases (PostgreSQL/Redis) onto the cluster!

---

### 🎯 Run the Smoke Test Job:
```bash
# 1. Apply the automated Job and PVC
kubectl apply -f manifests/01-storage/03-storage-smoke-test.yaml

# 2. Watch the Job complete and view the logs
kubectl get pvc storage-smoke-test-pvc
kubectl wait --for=condition=complete job/storage-smoke-test-job --timeout=120s
kubectl logs job/storage-smoke-test-job

# 3. Clean up the test resources
kubectl delete -f manifests/01-storage/03-storage-smoke-test.yaml
```

Once Longhorn storage passes the smoke test, proceed to **[07_metallb_and_ingress.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/07_metallb_and_ingress.md)**!
