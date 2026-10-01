# Scenario 24 — Analytics Worker: Missing ServiceAccount (FailedCreate in ReplicaSet)

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Silent Deployment Failure)**  
> The analytics pipeline was updated and deployed, but no pods are created or running. `kubectl get pods -n lab` returns `No resources found`. The deployment exists, but 0 pods are running.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: analytics-worker
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: analytics-worker
  template:
    metadata:
      labels:
        app: analytics-worker
    spec:
      serviceAccountName: analytics-processor
      containers:
      - name: worker
        image: busybox:1.36
        command: ["sh", "-c", "echo 'Processing analytics batch...'; sleep 3600"]
        resources:
          requests:
            cpu: "50m"
            memory: "32Mi"
          limits:
            cpu: "100m"
            memory: "64Mi"
EOF
```

---

## Diagnosis

### 1. Check Pods and Deployment

```bash
kubectl get deployment,pods -n lab
```
Output:
```text
NAME                               READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/analytics-worker   0/1     0            0           2m

No resources found in lab namespace.
```
Notice that there are **NO pods** at all. You cannot run `kubectl logs` or `kubectl describe pod`.

### 2. Follow the Hierarchy: Deployment → ReplicaSet

```bash
kubectl get rs -n lab
```
Output:
```text
NAME                                DESIRED   CURRENT   READY   AGE
analytics-worker-66c964b96b         1         0         0       2m
```

### 3. Check ReplicaSet Events

```bash
kubectl describe rs analytics-worker-66c964b96b -n lab
```
Output:
```text
Events:
  Type     Reason        Age                From                   Message
  ----     ------        ----               ----                   -------
  Warning  FailedCreate  12s (x5 over 45s)  replicaset-controller  Error creating: pods "analytics-worker-66c964b96b-" is forbidden: error looking up service account lab/analytics-processor: serviceaccount "analytics-processor" not found
```

---

## Root Cause

The Pod spec defines:
```yaml
spec:
  serviceAccountName: analytics-processor
```

Before the kube-apiserver admits and creates a Pod, the admission controller verifies that the requested `ServiceAccount` exists in the target namespace.

Because `analytics-processor` was never created, the API server rejects the ReplicaSet's request to create the pod with a `403 Forbidden` (`serviceaccount "analytics-processor" not found`). The ReplicaSet controller enters back-off and retries, but no Pod is ever spawned.

---

## Solution

### Fix 1: Create the Missing ServiceAccount (Recommended)

```bash
kubectl create serviceaccount analytics-processor -n lab
```

The ReplicaSet controller will immediately detect the ServiceAccount and spawn the Pod:
```bash
kubectl get pods -n lab
```
Output:
```text
NAME                                READY   STATUS    RESTARTS   AGE
analytics-worker-66c964b96b-k8x7m   1/1     Running   0          3s
```

### Fix 2: Remove or Change `serviceAccountName` in Deployment

If a custom ServiceAccount is not required, remove `serviceAccountName: analytics-processor` so the Pod defaults to the `default` ServiceAccount.

---

## Key Takeaways

1. **The Management Chain**:
   `Deployment → ReplicaSet → Pod`
   When `kubectl get pods` shows nothing, check the layer above it: `kubectl describe rs`.
2. **Admission Validation**:
   Kubernetes verifies ServiceAccounts at Pod creation time. If the ServiceAccount does not exist, the Pod cannot even be scheduled or created.
