# Scenario 06 — Catalog Service: Stuck at Initialization

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Pod Initialization Stalled)**
> New release of `catalog-service` deployed. Pod status shows `Init:CrashLoopBackOff`. Frontend product catalog is completely down. Engineers cannot find crash logs from the main app container.

**Your job:** Triage, find root cause, fix, and verify stability.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: catalog-service
  namespace: lab
  labels:
    app: catalog-service
spec:
  replicas: 1
  selector:
    matchLabels:
      app: catalog-service
  template:
    metadata:
      labels:
        app: catalog-service
    spec:
      initContainers:
      - name: wait-for-db
        image: busybox:1.36
        command: ['sh', '-c']
        args:
        - |
          echo "Connecting to postgres-db..."
          nslookup postgres-db.lab.svc.cluster.local > /dev/null 2>&1
          if [ $? -ne 0 ]; then
            echo "FATAL: postgres-db does not exist in cluster! Halting."
            exit 1
          fi
      containers:
      - name: catalog-app
        image: nginx:alpine
        ports:
        - containerPort: 80
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What is the pod STATUS?
2. When you run `kubectl logs -n lab -l app=catalog-service`, what error does kubectl itself throw? Why?
3. What is the correct command to see logs from the init container?
4. What is the root cause preventing `catalog-app` from starting?
5. How do you fix it without changing the deployment?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The init container `wait-for-db` performs a DNS lookup for `postgres-db.lab.svc.cluster.local`. This service does not exist in the cluster, so `nslookup` fails and the script exits with code `1`. Because the init container failed, Kubernetes never starts the main `catalog-app` container.

### Smoking Gun — Why Standard Logs Fail

```bash
kubectl logs -n lab -l app=catalog-service
```
```
Error from server (BadRequest): container "catalog-app" in pod "..." is waiting to start: PodInitializing
```

**Reason:** `kubectl logs` defaults to the main container. But the main container hasn't been created yet — init container is still running/failing. You must specify the init container by name.

### Correct Command

```bash
kubectl logs -n lab -l app=catalog-service -c wait-for-db
```
```
Connecting to postgres-db...
FATAL: postgres-db does not exist in cluster! Halting.
```

### Fix — Satisfy the Dependency

Create a Service named `postgres-db` in the `lab` namespace to satisfy the DNS check:

```bash
kubectl create service clusterip postgres-db -n lab --tcp=5432:5432
```

Watch the init container pass on its next retry:
```bash
kubectl get pods -n lab -w
```

The init container will exit `0`, and `catalog-app` will start immediately.

### Init Container Lifecycle

```
Pod scheduled
    │
    ▼
[Init Container 1] runs → exit 0 ✅ → next init or main container
[Init Container 1] runs → exit 1 ❌ → restart (CrashLoopBackOff)
                                       main container NEVER starts
```

### Production Use Cases for Init Containers

| Use Case | What it does |
|----------|-------------|
| **DB readiness check** | Wait until database DNS resolves or port is open |
| **Schema migration** | Run `alembic upgrade head` before app boots |
| **Config download** | Pull TLS certs or seed data into a shared volume |
| **Dependency ordering** | Ensure service A is up before service B starts |

### Key Lesson

`kubectl logs` without `-c` always targets the **main container**. When a pod shows `Init:*` status, you MUST use `-c <init-container-name>` to see why the init container is failing.

In sofka/k9s: highlight pod → `l` (logs) → arrow keys to switch between containers.

</details>
