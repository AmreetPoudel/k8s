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

## Step 3: Deploy Longhorn via Helm

### 🎯 Run from `master-1`:
```bash
# Add official Longhorn repository
helm repo add longhorn https://charts.longhorn.io
helm repo update

# Install Longhorn in dedicated namespace with 3 replicas
helm install longhorn longhorn/longhorn \
  --namespace longhorn-system \
  --create-namespace \
  --set defaultSettings.defaultDataPath="/var/lib/longhorn" \
  --set defaultSettings.defaultReplicaCount=3 \
  --set defaultSettings.backupTarget="" \
  --set persistence.defaultClass=true \
  --set persistence.defaultClassReplicaCount=3
```

### ❓ Why `defaultReplicaCount=3`?
Longhorn writes every block synchronously to **all 3 worker nodes** simultaneously. If Worker 1 suffers a hardware crash, the application pod can be rescheduled to Worker 2 or Worker 3 and immediately reconnect to its data with **zero data loss and zero manual recovery**.

---

## Step 4: Create the Production Replicated StorageClass

### 🎯 The Command:
```bash
cat <<EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: longhorn-replicated
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: driver.longhorn.io
allowVolumeExpansion: true
reclaimPolicy: Delete
volumeBindingMode: Immediate
parameters:
  numberOfReplicas: "3"
  staleReplicaTimeout: "30"
  dataLocality: "best-effort"
EOF
```

### 🔍 Parameter Breakdown:
* `numberOfReplicas: "3"`: Guarantees 3 physical copies across the 3 worker nodes.
* `dataLocality: "best-effort"`: Longhorn attempts to place a replica on the same physical worker node where the application pod is running to ensure fast local read speeds!
* `allowVolumeExpansion: true`: Allows you to increase PVC storage dynamically (e.g. 10Gi $\rightarrow$ 50Gi) without restarting the pod.

---

## ✅ Step 5: Test Dynamic PVC Provisioning & Multi-Node Failover

Run this end-to-end storage test:

```bash
# 1. Create a Test PVC & Pod
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: storage-smoke-test-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn-replicated
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: storage-smoke-test-pod
spec:
  containers:
  - name: writer
    image: busybox
    command: ["sh", "-c", "echo 'Longhorn storage is working 100%' > /mnt/data/test.txt && sleep 3600"]
    volumeMounts:
    - name: data
      mountPath: /mnt/data
  volumes:
  - name: data
    persistentVolumeClaim:
      claimName: storage-smoke-test-pvc
EOF

# 2. Wait for Pod to run and verify content
kubectl get pvc storage-smoke-test-pvc
kubectl exec storage-smoke-test-pod -- cat /mnt/data/test.txt

# 3. Clean up test
kubectl delete pod storage-smoke-test-pod
kubectl delete pvc storage-smoke-test-pvc
```

Once Longhorn storage passes the smoke test, proceed to **[07_metallb_and_ingress.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/07_metallb_and_ingress.md)**!
