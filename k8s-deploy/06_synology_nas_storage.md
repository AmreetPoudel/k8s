# 06. Synology Enterprise NAS Storage Integration (`NFS Dynamic Provisioner`)

> **Storage Appliance**: 3-Node Synology Enterprise NAS Cluster (`10.0.0.250`)  
> **Exported Share Path**: `/volume1/k8s-lab-storage`  
> **Storage Technology**: NFSv4.1 Dynamic Sub-Directory Provisioner (`nfs-subdir-external-provisioner`)  
> **Access Modes Supported**: `ReadWriteMany` (RWX) & `ReadWriteOnce` (RWO)  
> **Delivery Model**: Fully Declarative GitOps via ArgoCD (`manifests/01-storage/`)

---

## 1. Enterprise Architecture: Why Dedicated NAS Beats Longhorn HCI

In traditional Hyperconverged Infrastructure (HCI) software like Longhorn, every write is multiplied 3 times across compute worker nodes over virtual NICs, consuming CPU, RAM, and tripling VM disk consumption.

By offloading storage to your dedicated **3-Node Synology Enterprise NAS Cluster**:
1. **Zero Worker Tax**: Worker VMs dedicate 100% of their CPU, RAM, and network bandwidth to application containers instead of storage replica engines.
2. **True `ReadWriteMany` (RWX)**: Multiple pods across different worker nodes can concurrently mount and write to the same shared directory (impossible with standard Longhorn block storage).
3. **Enterprise Hardware Reliability**: Storage reliability is handled by dedicated Synology hardware RAID (Btrfs, dual power supplies, automated hardware snapshots, offsite replication).

```
   [ Worker Node 1 ]       [ Worker Node 2 ]       [ Worker Node 3 ]
 ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
 │ Pod A (PVC #1)   │    │ Pod B (PVC #1)   │    │ Pod C (PVC #2)   │
 └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
          │ (NFS Mount)           │ (NFS Mount)           │ (NFS Mount)
          └───────────────┬───────┴───────────────────────┘
                          │ (TCP Port 2049)
                          ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │      3-Node Synology Enterprise NAS Cluster (10.0.0.250)         │
 │      Export: /volume1/k8s-lab-storage                            │
 │                                                                  │
 │   ├── /default-synology-nfs-smoke-test-pvc-pvc-... (Subfolder)   │
 │   ├── /monitoring-prometheus-kube-prometheus-... (Subfolder)     │
 │   ├── /monitoring-grafana-pvc-...               (Subfolder)     │
 │   └── /logging-loki-pvc-...                     (Subfolder)     │
 └──────────────────────────────────────────────────────────────────┘
```

---

## 2. Synology DSM & Firewall Prerequisites Checklist

### A. Synology DSM Configuration
1. **Enable NFS Service**:
   * Navigate to: *Control Panel* $\longrightarrow$ *File Services* $\longrightarrow$ *NFS*.
   * Check **Enable NFS service** (Set maximum protocol to **NFSv4.1**).
2. **Configure NFS Permissions on the Shared Folder**:
   * Navigate to: *Control Panel* $\longrightarrow$ *Shared Folder*.
   * Select `/volume1/k8s-lab-storage` $\longrightarrow$ Click **Edit** $\longrightarrow$ **NFS Permissions** tab $\longrightarrow$ Click **Create**:
     * **Hostname or IP**: `10.0.2.0/24` (or your Kubernetes nodes subnet).
     * **Privilege**: `Read / Write`.
     * **Squash**: `No mapping` *(CRITICAL: allows Linux UIDs like Grafana UID 472 and Prometheus UID 65534 to retain ownership without permission errors)*.
     * **Security**: `sys`.
     * ✅ Check **"Allow connections from non-privileged ports (ports higher than 1024)"** *(MANDATORY for Kubernetes NFS clients!)*.
     * ✅ Check **"Allow users to access mounted subfolders"**.

### B. Network & Firewall Rules (FortiGate / Physical Switch)
Ensure firewall policies allow the following ports between Kubernetes Nodes (`10.0.2.0/24`) and Synology NAS (`10.0.0.250`):
* **Port `2049` (TCP/UDP)**: NFS Storage Traffic.
* **Port `111` (TCP/UDP)**: RPC Portmapper.

### C. Worker Node OS Verification
Run on all worker nodes (`worker-1`, `worker-2`, `worker-3`):
```bash
# Verify NFS client mount helper is installed
which mount.nfs || sudo apt-get install -y nfs-common
```

---

## 3. Declarative GitOps Storage Configuration

All storage resources are managed declaratively under `manifests/01-storage/`:

### `manifests/01-storage/00-nfs-provisioner-app.yaml`
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: nfs-subdir-external-provisioner
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/
    chart: nfs-subdir-external-provisioner
    targetRevision: 4.0.18
    helm:
      values: |
        nfs:
          server: "10.0.0.250"
          path: "/volume1/k8s-lab-storage"
          mountOptions:
            - nfsvers=4.1
            - rsize=1048576
            - wsize=1048576
            - hard
            - timeo=600
            - retrans=2
            - noatime

        storageClass:
          name: synology-nfs
          defaultClass: true
          reclaimPolicy: Delete
          allowVolumeExpansion: true
          archiveOnDelete: false
          accessModes: ReadWriteMany

        nodeSelector:
          node-role.kubernetes.io/worker: worker
  destination:
    server: https://kubernetes.default.svc
    namespace: nfs-provisioner
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

---

## 4. Verification & Dynamic Provisioning Smoke Test

Apply the dynamic smoke-test manifest:
```bash
kubectl apply -f manifests/01-storage/01-nfs-smoke-test.yaml
```

Inspect the test results:
```bash
# 1. Verify PVC is dynamically provisioned and Bound
kubectl get pvc synology-nfs-smoke-test-pvc

# 2. Check the Job execution logs
kubectl logs -l job-name=synology-nfs-smoke-test-job

# Expected output:
# 1. Checking dynamic NFS volume mount on Synology NAS (10.0.0.250)...
# 2. Writing test payload to Synology NFS share...
# 3. Reading back payload from Synology NAS disk...
# SYNOLOGY_NFS_VERIFIED_1788789000
# 4. SUCCESS: Dynamic NFS sub-directory created, mounted as ReadWriteMany, and verified!
```

---

## 5. Real-World Troubleshooting & Gotchas

### 1. `mount.nfs: access denied by server while mounting`
* **Root Cause**: The Kubernetes worker node IP is not listed in the Synology NFS Permissions tab, or "Allow connections from non-privileged ports" is unchecked.
* **Fix**: In DSM, verify the IP subnet (`10.0.2.0/24`) and ensure the non-privileged ports box is checked.

### 2. `Permission denied (errno 13)` inside container
* **Root Cause**: Synology squashed the container root user to `nobody` or `admin`.
* **Fix**: In DSM, set **Squash** to **"No mapping"**.

### 3. `Stale file handle` during pod restarts
* **Fix**: Ensure mount options include `hard`, `timeo=600`, and `retrans=2` as configured in our Helm values.
