# Scenario 11 — Postgres DB: Stuck Pending, No Storage

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Data Service Down)**
> Database pod for `postgres-db` has been stuck in `Pending` state for 15 minutes since the new release. All services depending on it are failing. Engineering team is completely blocked.

**Your job:** Triage, find root cause, fix using available StorageClass.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
  namespace: lab
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: fast-nvme-storage
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres-db
  namespace: lab
  labels:
    app: postgres-db
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres-db
  template:
    metadata:
      labels:
        app: postgres-db
    spec:
      containers:
      - name: postgres
        image: postgres:15-alpine
        env:
        - name: POSTGRES_PASSWORD
          value: "supersecret"
        volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
        resources:
          requests:
            cpu: "100m"
            memory: "256Mi"
          limits:
            cpu: "500m"
            memory: "512Mi"
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: postgres-data
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What is the pod STATUS?
2. Is this a crash inside the container or something happening before it even starts?
3. What command checks the storage claim status?
4. What command lists all available StorageClasses on your cluster?
5. What is the fix?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The PVC requests StorageClass `fast-nvme-storage` which does not exist on the cluster. The `persistentvolume-controller` cannot provision storage, so the PVC stays in `Pending`. Because the pod has an `unbound PersistentVolumeClaim`, the scheduler refuses to schedule it.

### Smoking Gun

```bash
kubectl get pvc -n lab
```
```
NAME            STATUS    VOLUME   CAPACITY   STORAGECLASS       AGE
postgres-data   Pending                       fast-nvme-storage  5m
```

```bash
kubectl describe pvc postgres-data -n lab
```
```
Events:
  Warning  ProvisioningFailed  persistentvolume-controller
  storageclass.storage.k8s.io "fast-nvme-storage" not found
```

```bash
kubectl describe pod <postgres-pod> -n lab
```
```
Events:
  Warning  FailedScheduling  default-scheduler
  0/6 nodes available: pod has unbound immediate PersistentVolumeClaims
```

### The Chain Reaction

```
StorageClass missing
    → PVC stays Pending
        → Pod cannot be scheduled (unbound PVC)
            → Database never starts
                → All dependent services fail
```

### Fix — Check Available StorageClasses First

```bash
kubectl get storageclass
```

Use the correct StorageClass from your cluster (e.g. `longhorn`):

```bash
kubectl delete pvc postgres-data -n lab

kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
  namespace: lab
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 10Gi
EOF
```

Watch PVC bind:
```bash
kubectl get pvc -n lab -w
# Pending → Bound ✅
```

### Storage Hierarchy

```
StorageClass (longhorn)       ← HOW storage is provisioned
       │
       ▼ (provisioner creates)
PersistentVolume (PV)         ← The actual 10Gi disk
       │
       ▼ (PVC binds to PV)
PersistentVolumeClaim (PVC)   ← The REQUEST for storage
       │
       ▼ (Pod mounts)
Pod container at mountPath    ← App reads/writes data
```

### Key Lesson

PVCs cannot be edited in-place to change StorageClass. You must delete and recreate them. Always verify StorageClass name from `kubectl get storageclass` before writing YAML.

> **Important:** Deleting a PVC deletes the data if the StorageClass `reclaimPolicy` is `Delete` (most common). In production, always backup before deleting PVCs!

</details>
