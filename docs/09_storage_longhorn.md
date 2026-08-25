# Doc 09: Storage & Longhorn
## Persistent Storage in Kubernetes

---

## 9.1 The Storage Problem in Kubernetes

Containers are ephemeral. When a pod restarts, its filesystem is gone.  
But databases, file uploads, and configuration that needs to survive restarts require **persistent storage**.

Kubernetes solves this with three objects:
- **PersistentVolume (PV)**: A piece of actual storage (disk, NFS share, cloud volume)
- **PersistentVolumeClaim (PVC)**: A request for storage from a PV
- **StorageClass**: A template for dynamically creating PVs

```
Developer requests storage:            Admin provides storage:
  PVC (I need 10Gi, RW, fast)    ←→    PV (here's 50Gi SSD disk)
       ↑                                     ↑
  StorageClass dynamically creates PVs from a storage backend
```

💡 **Interview**: *"What is the difference between a PV and a PVC?"*  
→ "A PersistentVolume is a cluster-level storage resource — it represents actual storage like an EBS volume, NFS share, or local disk. It's provisioned either manually by an admin or dynamically by a StorageClass provisioner. A PersistentVolumeClaim is a namespace-level request for storage — a pod says 'I need 10Gi of storage that can be mounted read-write by one pod.' Kubernetes binds a PVC to a suitable PV. The binding is based on access modes, storage class, and size. Once bound, the PV is exclusively reserved for that PVC until it's released."

---

## 9.2 Access Modes

| Mode | Shorthand | Meaning |
|------|-----------|---------|
| ReadWriteOnce | RWO | One node can mount read-write. Most storage types. |
| ReadOnlyMany | ROX | Many nodes can mount read-only. |
| ReadWriteMany | RWX | Many nodes can mount read-write. Requires shared storage (NFS, Longhorn RWX mode). |
| ReadWriteOncePod | RWOP | Kubernetes 1.22+ — only ONE pod can mount, enforced by CSI |

⚠️ **RWO is per NODE, not per pod.** Multiple pods on the same node can mount an RWO volume. This confuses people. If two deployments have PVCs bound to the same PV (RWO), pods can be on the same node and both access it — this can corrupt databases.

---

## 9.3 CSI — Container Storage Interface

Like CNI for networking, CSI is the plugin interface for storage.

```
kubelet             CSI Driver
   |                    |
   |-- NodeStageVolume  |  ← Attach and format volume on node
   |-- NodePublishVolume|  ← Mount volume into pod's filesystem
   |-- NodeUnpublishVolume ← Unmount on pod deletion
   
API Server          CSI Controller
   |                    |
   |-- CreateVolume     |  ← Provision new storage (e.g., create EBS volume)
   |-- DeleteVolume     |  ← Delete storage
   |-- AttachVolume     |  ← Attach to specific node
```

RKE2 ships with a **local-path provisioner** by default. It creates directories on the node's local disk as PVs. This is great for testing but NOT for production (node-local storage doesn't survive if the node dies).

```bash
# [M1] — check the default StorageClass
kubectl get storageclasses
# NAME                   PROVISIONER             RECLAIMPOLICY   VOLUMEBINDINGMODE
# local-path (default)   rancher.io/local-path   Delete          WaitForFirstConsumer

kubectl get pods -n kube-system | grep local-path
# local-path-provisioner-XXXXX   Running
```

---

## 9.4 Quick Test with local-path

```bash
# [M1]
cat << 'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: test-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: test-storage-pod
spec:
  containers:
  - name: app
    image: busybox
    command: ["/bin/sh", "-c", "echo 'hello persistent storage' > /data/test.txt && sleep 3600"]
    volumeMounts:
    - mountPath: /data
      name: test-volume
  volumes:
  - name: test-volume
    persistentVolumeClaim:
      claimName: test-pvc
EOF

# Check PVC bound
kubectl get pvc
# STATUS should be: Bound (not Pending)

# Verify data
kubectl exec test-storage-pod -- cat /data/test.txt
# hello persistent storage

# Delete pod and recreate — data should persist
kubectl delete pod test-storage-pod

cat << 'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: test-storage-pod-2
spec:
  containers:
  - name: app
    image: busybox
    command: ["sleep", "3600"]
    volumeMounts:
    - mountPath: /data
      name: test-volume
  volumes:
  - name: test-volume
    persistentVolumeClaim:
      claimName: test-pvc
EOF

kubectl exec test-storage-pod-2 -- cat /data/test.txt
# hello persistent storage ← data survived pod restart!

# Cleanup
kubectl delete pod test-storage-pod-2
kubectl delete pvc test-pvc
```

---

## 9.5 Longhorn — Distributed Block Storage

**Why Longhorn?**  
local-path stores data on one node's disk. If that node dies, the data is gone (unless you back it up separately). **Longhorn** replicates your volume data across multiple worker nodes — so if one worker dies, the volume can still be mounted on another.

Longhorn is a Rancher project (same team as RKE2). It's a CSI driver that:
- Creates block volumes stored as files on worker nodes' disks
- Replicates each volume to N replicas across different nodes
- Provides a web UI for storage management
- Handles volume snapshots and backups to S3/NFS

### Longhorn Requirements

```bash
# [ALL-W] — workers need these packages
apt install -y open-iscsi nfs-common cryptsetup dmsetup

# Enable and start iscsid (for iSCSI backend)
systemctl enable iscsid
systemctl start iscsid

# Verify
systemctl status iscsid

# Check kernel modules
modprobe iscsi_tcp
lsmod | grep iscsi_tcp
```

### Install Longhorn via Helm

```bash
# [M1] — install Helm if not available
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version

# Add Longhorn Helm repo
helm repo add longhorn https://charts.longhorn.io
helm repo update

# Create namespace
kubectl create namespace longhorn-system

# Install Longhorn
helm install longhorn longhorn/longhorn \
  --namespace longhorn-system \
  --set persistence.defaultClass=true \
  --set persistence.defaultClassReplicaCount=3 \
  --set defaultSettings.defaultReplicaCount=3 \
  --set ingress.enabled=true \
  --set ingress.host=longhorn.yourdomain.com

# Watch Longhorn pods come up (takes 3-5 minutes)
kubectl get pods -n longhorn-system -w
# Many pods will start: longhorn-manager, longhorn-driver-deployer, etc.
```

🔍 **What `replicas=3` means:**  
When you create a PVC backed by Longhorn with 3 replicas, Longhorn stores the data on 3 different worker nodes. The Longhorn engine coordinates writes — a write is only acknowledged when it's written to all replicas. If one worker node dies, the volume can still be mounted with 2 replicas available. Longhorn automatically rebuilds the third replica on another healthy node.

### Verify Longhorn

```bash
# [M1]
kubectl get storageclass
# longhorn (default) should appear

# Create a Longhorn-backed PVC
cat << 'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: longhorn-test-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 2Gi
EOF

kubectl get pvc longhorn-test-pvc
# Wait for STATUS: Bound

# Check where replicas are stored
kubectl get volumes -n longhorn-system
# Shows volume, size, replica count, health status
```

### Access Longhorn UI

```bash
# Port-forward to access the Longhorn UI locally
kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80

# Access: http://localhost:8080
# The UI shows all volumes, nodes, replicas, snapshots
```

### Volume Snapshot and Backup

```bash
# [M1] — create a volume snapshot
cat << 'EOF' | kubectl apply -f -
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: my-snapshot
spec:
  volumeSnapshotClassName: longhorn
  source:
    persistentVolumeClaimName: longhorn-test-pvc
EOF

kubectl get volumesnapshot
# Shows snapshot status — ReadyToUse: true when complete
```

---

## 9.6 StatefulSets and Storage

StatefulSets are for **stateful applications** (databases, message queues). They differ from Deployments:

| Feature | Deployment | StatefulSet |
|---------|-----------|------------|
| Pod names | random (nginx-5f8d-abc) | stable (mysql-0, mysql-1) |
| Pod start order | parallel | ordered (0, then 1, then 2) |
| Pod DNS | none | stable: `mysql-0.mysql.default.svc.cluster.local` |
| Storage | shared PVC or individual | VolumeClaimTemplate per pod |

```bash
# [M1] — example StatefulSet with per-pod storage
cat << 'EOF' | kubectl apply -f -
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mysql-test
spec:
  serviceName: mysql-test
  replicas: 3
  selector:
    matchLabels:
      app: mysql-test
  template:
    metadata:
      labels:
        app: mysql-test
    spec:
      containers:
      - name: mysql
        image: mysql:8.0
        env:
        - name: MYSQL_ROOT_PASSWORD
          value: "testpassword"
        volumeMounts:
        - mountPath: /var/lib/mysql
          name: mysql-data
  volumeClaimTemplates:    # ← creates a PVC PER POD
  - metadata:
      name: mysql-data
    spec:
      accessModes: ["ReadWriteOnce"]
      storageClassName: longhorn
      resources:
        requests:
          storage: 5Gi
EOF

# Each pod gets its own PVC:
kubectl get pvc
# mysql-data-mysql-test-0
# mysql-data-mysql-test-1
# mysql-data-mysql-test-2
```

💡 **Interview**: *"Why use a StatefulSet instead of a Deployment for a database?"*  
→ "StatefulSets provide stable, predictable identities for pods. Each pod has a stable hostname (mysql-0, mysql-1) that doesn't change across restarts, a stable DNS entry, and its own dedicated PersistentVolumeClaim. This is essential for clustered databases like MySQL, PostgreSQL, or Cassandra where nodes discover each other by hostname, and where each node must have its own isolated storage. A Deployment's random pod names and shared storage would break database clustering. StatefulSets also start and stop pods in order, which matters for primary/replica setup."

---

## 9.7 Reclaim Policies

What happens to the PV when the PVC is deleted?

| Policy | What Happens |
|--------|-------------|
| `Delete` | PV and the underlying storage are deleted. Dangerous for important data. |
| `Retain` | PV is released but NOT deleted. Data is preserved. Must manually reclaim. |
| `Recycle` | (Deprecated) Deletes data but reuses the PV. |

```bash
# [M1] — check reclaim policy of current PVs
kubectl get pv -o custom-columns=NAME:.metadata.name,RECLAIM:.spec.persistentVolumeReclaimPolicy

# Change a PV to Retain (safer for production)
kubectl patch pv <pv-name> -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
```

---

## 9.8 Summary

You now understand:
- ✅ PV/PVC/StorageClass — the storage abstraction model
- ✅ Access modes (RWO, RWX) and what they actually mean
- ✅ CSI — how storage plugins work
- ✅ local-path provisioner (default, node-local)
- ✅ Longhorn — distributed replicated storage
- ✅ StatefulSets and per-pod storage
- ✅ Reclaim policies

**Next**: [Doc 10 - Ingress & LoadBalancer →](./10_ingress_and_lb.md)  
How external traffic reaches your pods. NGINX Ingress, MetalLB for LoadBalancer Services, and TLS termination.

---

*Doc 09 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
