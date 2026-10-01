# Scenario 28 — Order Cleanup: Pod Stuck in Terminating (Finalizers)

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Pod Stuck in Terminating)**  
> CI/CD pipeline tried to decommission pod `order-cleanup`, but the pipeline is hung indefinitely. The pod remains in `Terminating` status. Even `kubectl delete pod --force` cannot remove it from the cluster.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: order-cleanup
  namespace: lab
  finalizers:
  - storage.security.io/backup-lock
spec:
  containers:
  - name: cleanup
    image: busybox:1.36
    command: ["sh", "-c", "echo 'Cleanup active'; sleep 3600"]
EOF
```

Initiate deletion:
```bash
kubectl delete pod order-cleanup -n lab --now &
```

---

## Diagnosis

### 1. Check Pod Status

```bash
kubectl get pods -n lab
```
Output:
```text
NAME            READY   STATUS        RESTARTS   AGE
order-cleanup   0/1     Terminating   0          3m
```
The pod sits in `Terminating` status and never disappears.

### 2. Check Container Logs

```bash
kubectl logs order-cleanup -n lab
```
Output:
```text
unable to retrieve container logs for containerd://...
```
This proves that the container process on the worker node is **already dead and stopped**. The kubelet has already terminated the container.

### 3. Inspect Pod Metadata for Finalizers

```bash
kubectl get pod order-cleanup -n lab -o jsonpath='{.metadata.finalizers}'
```
Output:
```text
["storage.security.io/backup-lock"]
```

Or view the YAML:
```bash
kubectl get pod order-cleanup -n lab -o yaml | grep -A 3 finalizers
```

---

## Root Cause

### What is a Finalizer?

A **Finalizer** is a safeguard in Kubernetes metadata (`metadata.finalizers`). 

When a resource with a finalizer is deleted:
1. Kubernetes sets `metadata.deletionTimestamp`.
2. The resource status displays as **`Terminating`**.
3. The kubelet stops the container on the node.
4. **BUT the API Server refuses to delete the object record from etcd** until the controller responsible for that finalizer performs its cleanup and removes the finalizer string.

If the controller that owns that finalizer is broken, uninstalled, or does not exist, Kubernetes waits forever. The pod will remain in `Terminating` for days or weeks.

This applies not only to Pods, but also to:
- Namespaces stuck in `Terminating`
- PVCs / PVs stuck in `Terminating`
- Custom Resources (CRDs) stuck in `Terminating`

---

## Solution: Patch Out the Finalizer

To free any object stuck in `Terminating`, manually remove the finalizers list:

```bash
kubectl patch pod order-cleanup -n lab -p '{"metadata":{"finalizers":null}}' --type=merge
```

### Verification

```bash
kubectl get pods -n lab
```
Output:
```text
No resources found in lab namespace.
```
The pod is instantly deleted from etcd and disappears!

---

## Key Takeaways

1. **`Terminating` does not always mean the container is running:**
   - The container on the node is usually already terminated.
   - It is `etcd` and the API server waiting for finalizers.
2. **Standard Pattern for Any Stuck Resource:**
   - If a Pod, PVC, or Namespace is stuck in `Terminating`, always run:
     ```bash
     kubectl get <resource> <name> -o yaml | grep -A 5 finalizers
     ```
   - Patching `finalizers: null` immediately deletes the record.
