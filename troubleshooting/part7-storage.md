# Part 7 — Storage

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 31. PVC Stuck Pending (No Matching StorageClass)

### Symptoms
- PersistentVolumeClaim stays in `Pending` state
- Pod stays `Pending` waiting for the PVC
- `kubectl describe pvc` shows `no persistent volumes available` or `no StorageClass found`

### Diagnosis
```bash
# Check PVC status and events
kubectl describe pvc <pvc-name> -n <namespace>
# Look at Events section — error message tells you exactly what's wrong

# List available StorageClasses
kubectl get storageclass

# Check if a default StorageClass exists
kubectl get storageclass -o jsonpath=\
'{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.storageclass\.kubernetes\.io/is-default-class}{"\n"}{end}'

# Check what StorageClass the PVC is requesting
kubectl get pvc <pvc-name> -n <namespace> \
  -o jsonpath='{.spec.storageClassName}'

# Check if there are matching PVs (for static provisioning)
kubectl get pv
kubectl describe pv <pv-name>   # check storageClassName and accessModes match
```

### Fix Steps
1. **StorageClass name mismatch** — patch PVC or fix the spec:
   ```yaml
   # PVC spec — ensure storageClassName matches exactly
   spec:
     storageClassName: "longhorn"    # ← must match kubectl get sc output exactly
     accessModes: [ReadWriteOnce]
     resources:
       requests:
         storage: 10Gi
   ```

2. **No default StorageClass** — set one as default:
   ```bash
   kubectl patch storageclass <sc-name> \
     -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
   ```

3. **Dynamic provisioner not running** — check the CSI driver pods:
   ```bash
   kubectl get pods -n kube-system | grep -E "csi|provisioner|longhorn"
   kubectl logs -n kube-system <provisioner-pod> --tail=50
   # Restart if failing
   kubectl rollout restart deployment <csi-provisioner> -n kube-system
   ```

4. **Access mode mismatch** — PV exists but wrong access mode:
   ```yaml
   spec:
     accessModes:
     - ReadWriteOnce    # Must match PV's accessModes
   ```

### Verify
```bash
kubectl get pvc <pvc-name> -n <namespace>
# STATUS should change from Pending → Bound

kubectl get pv
# Corresponding PV should show CLAIM: <namespace>/<pvc-name>
```

---

## 32. Volume Attach / Detach Failure

### Symptoms
- Pod stuck in `ContainerCreating` with event `Multi-Attach error` or `Unable to attach or mount volumes`
- `kubectl describe pod` shows `FailedAttachVolume` or `FailedMount`
- Pod was rescheduled to a new node but volume still attached to old node

### Diagnosis
```bash
# Describe the pod for attachment errors
kubectl describe pod <pod-name> -n <namespace> | grep -A10 Events
# Look for: "FailedAttachVolume", "VolumeNotFound", "TooManyRequests"

# Check the PVC and PV status
kubectl get pvc <pvc-name> -n <namespace>
kubectl describe pv <pv-name>

# Check which node the volume is attached to
kubectl get volumeattachment
kubectl describe volumeattachment <va-name>

# Check CSI driver pods for errors
kubectl get pods -n kube-system -l app=csi-node
kubectl logs -n kube-system <csi-node-pod> -c <csi-driver-container> --tail=50

# Check node where the volume is stuck (if old node is unhealthy)
kubectl get node <old-node>
```

### Fix Steps
1. **Force detach stuck VolumeAttachment** (when old node is gone):
   ```bash
   # Get the VolumeAttachment name
   kubectl get volumeattachment | grep <pv-name>

   # Remove the finalizer so it can be deleted
   kubectl patch volumeattachment <va-name> \
     -p '{"metadata":{"finalizers":[]}}' --type=merge

   # Delete the VolumeAttachment
   kubectl delete volumeattachment <va-name>
   ```

2. **Restart the CSI node plugin** on the affected node:
   ```bash
   kubectl delete pod -n kube-system <csi-node-pod-on-node>
   ```

3. **Node force-delete PV attachment** (cloud provider specific — e.g., AWS EBS):
   ```bash
   # AWS: Force detach via CLI if stuck
   aws ec2 detach-volume --volume-id <vol-id> --force
   ```

4. **Restart the pod** after forcing detach:
   ```bash
   kubectl delete pod <pod-name> -n <namespace>
   # New pod will trigger fresh attach
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# ContainerCreating → Running

kubectl get volumeattachment
# Old stuck attachment should be gone
```

---

## 33. Multi-Attach Error (RWO Conflict)

### Symptoms
- Pod can't start: `Multi-Attach error for volume — Volume is already exclusively attached to one node`
- Happens during node failure, rolling update, or when a pod is rescheduled
- `kubectl get pvc` shows Bound — the volume is fine but can't be claimed by two nodes

### Diagnosis
```bash
# Confirm the error type
kubectl describe pod <pod-name> -n <namespace> | grep -i "multi-attach\|already attached"

# Find where the volume is currently attached
kubectl get volumeattachment -o wide
kubectl describe volumeattachment <va-name> | grep "Node\|Volume"

# Check old pod — is it still running on the old node?
kubectl get pods -n <namespace> -o wide | grep <deployment-name>

# Check if old node is actually dead
kubectl get node <old-node>
# If NotReady — volume is still marked attached to dead node

# Check access mode
kubectl get pvc <pvc-name> -n <namespace> \
  -o jsonpath='{.spec.accessModes}'
# RWO = ReadWriteOnce = only one node at a time
```

### Fix Steps
1. **Old pod not terminating** — force delete it:
   ```bash
   # Standard delete first
   kubectl delete pod <old-pod> -n <namespace> --grace-period=30

   # If it stays Terminating:
   kubectl delete pod <old-pod> -n <namespace> --force --grace-period=0
   ```

2. **Old node is dead** — force node deletion and clean up VolumeAttachment:
   ```bash
   # Delete the dead node object
   kubectl delete node <dead-node>

   # Remove stuck VolumeAttachment (same as scenario 32 step 1)
   kubectl patch volumeattachment <va-name> \
     -p '{"metadata":{"finalizers":[]}}' --type=merge
   kubectl delete volumeattachment <va-name>
   ```

3. **Architectural fix** — if your workload needs multi-node access, use RWX:
   ```yaml
   spec:
     accessModes:
     - ReadWriteMany   # requires an NFS or CephFS StorageClass
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# Should move to Running with volume attached

kubectl get volumeattachment
# Only one attachment for this volume now
```

---

## 34. StatefulSet Pod Stuck on Volume Mount

### Symptoms
- StatefulSet pod stuck in `ContainerCreating` or `Init:0/1`
- `kubectl describe pod` shows `Unable to mount volumes`
- Usually happens on pod restart, node failure, or StatefulSet reschedule

### Diagnosis
```bash
# Describe pod for mount errors
kubectl describe pod <statefulset-pod> -n <namespace> | grep -A15 Events
# Common errors:
# "MountVolume.SetUp failed"
# "couldn't propagate object: ... not attached"
# "Orphaned pod ... found, but volume paths are still present"

# Check the specific PVC for this StatefulSet pod
kubectl get pvc -n <namespace> | grep <pod-name>
kubectl describe pvc <pvc-name> -n <namespace>

# Check kubelet logs on the node where pod is scheduled
kubectl get pod <pod-name> -n <namespace> -o wide   # which node?
ssh <user>@<node-ip>
sudo journalctl -u rke2-agent -n 200 --no-pager | grep -i "mount\|volume\|orphan"

# Check for orphaned mount points on the node
sudo ls /var/lib/kubelet/pods/<pod-uid>/volumes/

# Check CSI node driver
sudo /var/lib/rancher/rke2/bin/crictl ps | grep csi
```

### Fix Steps
1. **Clean up orphaned mount directories** on the node:
   ```bash
   # SSH into node
   ssh <user>@<node-ip>

   # Find the pod UID
   POD_UID=$(kubectl get pod <pod-name> -n <namespace> \
     -o jsonpath='{.metadata.uid}')

   # Check for orphaned dirs
   sudo ls /var/lib/kubelet/pods/$POD_UID/volumes/

   # Unmount stuck mountpoints
   sudo umount -f /var/lib/kubelet/pods/$POD_UID/volumes/kubernetes.io~csi/<pv-name>/mount

   # Remove orphaned dir
   sudo rm -rf /var/lib/kubelet/pods/$POD_UID/
   ```

2. **Restart the CSI node driver** on the affected node:
   ```bash
   kubectl delete pod -n kube-system \
     $(kubectl get pods -n kube-system -o wide | grep csi | grep <node-name> | awk '{print $1}')
   ```

3. **Delete and recreate the pod** — StatefulSet will recreate with same PVC:
   ```bash
   kubectl delete pod <statefulset-pod> -n <namespace>
   # StatefulSet controller recreates it — same PVC, same identity
   ```

4. **If PVC is stuck Terminating** — remove finalizer:
   ```bash
   kubectl patch pvc <pvc-name> -n <namespace> \
     -p '{"metadata":{"finalizers":[]}}' --type=merge
   ```

### Verify
```bash
kubectl get pod <statefulset-pod> -n <namespace> -w
# Init or ContainerCreating → Running

kubectl exec <statefulset-pod> -n <namespace> -- df -h
# Mounted volume should appear
```

---

## 35. CSI Driver Failure

### Symptoms
- All PVCs in `Pending` or all pods can't mount volumes
- CSI driver pods in `CrashLoopBackOff` or `Error`
- `kubectl describe pvc` shows CSI provisioner errors

### Diagnosis
```bash
# Check all CSI-related pods
kubectl get pods --all-namespaces | grep -E "csi|provisioner|attacher|snapshotter|resizer"

# Check CSI driver DaemonSet (node plugin) — runs on every node
kubectl get daemonset -A | grep csi
kubectl get pods -n kube-system -l app=csi-node -o wide

# Logs from the provisioner (controller)
kubectl logs -n kube-system <csi-controller-pod> -c <provisioner-container> --tail=100

# Logs from the node plugin (on the failing node)
kubectl logs -n kube-system <csi-node-pod> -c <node-driver-container> --tail=100

# Check CSIDriver object
kubectl get csidriver
kubectl describe csidriver <driver-name>

# Check StorageClass referencing the driver
kubectl describe storageclass <sc-name>
# provisioner: field should match the CSIDriver name

# Check if driver socket exists on the node
ssh <user>@<node-ip>
ls -la /var/lib/kubelet/plugins/<csi-driver-name>/csi.sock
```

### Fix Steps
1. **Restart CSI controller and node pods**:
   ```bash
   kubectl rollout restart deployment <csi-controller> -n kube-system
   kubectl rollout restart daemonset <csi-node> -n kube-system
   ```

2. **CSI driver socket missing** — delete the node plugin pod to recreate:
   ```bash
   kubectl delete pod -n kube-system <csi-node-pod-on-node>
   ```

3. **CSI driver version mismatch with Kubernetes** — check compatibility matrix:
   ```bash
   kubectl version --short
   kubectl get deployment <csi-controller> -n kube-system \
     -o jsonpath='{.spec.template.spec.containers[*].image}'
   # Check driver release notes for K8s version compatibility
   ```

4. **Driver missing required RBAC** — apply RBAC manifests from the driver's official install:
   ```bash
   # Example for Longhorn
   kubectl apply -f https://raw.githubusercontent.com/longhorn/longhorn/master/deploy/longhorn.yaml
   ```

5. **Node kernel modules missing** (e.g., iSCSI for some CSI drivers):
   ```bash
   sudo modprobe iscsi_tcp
   sudo modprobe dm_crypt
   # Make persistent:
   echo iscsi_tcp | sudo tee /etc/modules-load.d/iscsi.conf
   ```

### Verify
```bash
kubectl get pods -n kube-system | grep csi
# All Running

kubectl get pvc -n <namespace>
# Stuck PVCs should move to Bound

# Test end-to-end: create a test PVC
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: csi-test
  namespace: default
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: <your-sc>
  resources:
    requests:
      storage: 1Gi
EOF
kubectl get pvc csi-test -w   # should go Pending → Bound
kubectl delete pvc csi-test
```

---

*Previous → [Part 6 — Control Plane / etcd](./part6-control-plane-etcd.md)*  
*Next → [Part 8 — Cluster Upgrades](./part8-cluster-upgrades.md)*
