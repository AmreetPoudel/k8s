# Scenario 25 — Database Migrator: Job Failure (BackoffLimitExceeded)

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Database Migration Failed)**  
> Nightly database schema migration Job failed. Release pipeline is blocked. Multiple pods were spawned and died with status `Error`. The Job controller has given up retrying.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrator
  namespace: lab
spec:
  backoffLimit: 2
  template:
    metadata:
      labels:
        app: db-migrator
    spec:
      restartPolicy: Never
      containers:
      - name: migrator
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting schema migration for release v2.4.0..."
          echo "Connecting to primary database at postgres.prod.svc..."
          sleep 2
          echo "Executing migration script: 20261001_add_billing_indexes.sql..."
          echo "FATAL: relation 'billing_accounts' does not exist"
          exit 1
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

### 1. Check Job Status

```bash
kubectl get jobs -n lab
```
Output:
```text
NAME          COMPLETIONS   DURATION   AGE
db-migrator   0/1           45s        45s
```

### 2. Check Pods Spawned by the Job

```bash
kubectl get pods -n lab
```
Output:
```text
NAME                READY   STATUS   RESTARTS   AGE
db-migrator-62n7b   0/1     Error    0          40s
db-migrator-p8vzk   0/1     Error    0          28s
db-migrator-q9hnx   0/1     Error    0          12s
```
Notice there are **3 pods**!
- Attempt 1: Failed
- Retry 1: Failed
- Retry 2: Failed
- Total failures: 3 (exceeded `backoffLimit: 2`).

### 3. Check Job Conditions & Events

```bash
kubectl describe job db-migrator -n lab
```
Output:
```text
Conditions:
  Type    Status  Reason
  ----    ------  ------
  Failed  True    BackoffLimitExceeded

Events:
  Type     Reason                Age   From            Message
  ----     ------                ----  ----            -------
  Normal   SuccessfulCreate      48s   job-controller  Created pod: db-migrator-62n7b
  Normal   SuccessfulCreate      36s   job-controller  Created pod: db-migrator-p8vzk
  Normal   SuccessfulCreate      20s   job-controller  Created pod: db-migrator-q9hnx
  Warning  BackoffLimitExceeded  10s   job-controller  Job has reached the specified backoff limit
```

### 4. Check Pod Logs for Root Cause

```bash
kubectl logs -n lab db-migrator-q9hnx
```
Output:
```text
Starting schema migration for release v2.4.0...
Connecting to primary database at postgres.prod.svc...
Executing migration script: 20261001_add_billing_indexes.sql...
FATAL: relation 'billing_accounts' does not exist
```
The migration script failed with exit code 1 because a required database table (`billing_accounts`) does not exist yet.

---

## Root Cause

1. The container script encountered a fatal error and exited with code `1`.
2. Because `restartPolicy: Never` is set, Kubernetes does not restart the container in the same pod; instead, the Job controller creates a fresh pod for each retry.
3. The Job spec specifies `backoffLimit: 2`. Once retries exceed 2, the Job controller marks the Job condition as `Failed (BackoffLimitExceeded)` and stops retrying.

---

## Solution & Critical Kubernetes Rule: Job Spec Immutability

In Kubernetes, **Job pod templates are immutable**. If you simply edit `job.yaml` and run `kubectl apply`, Kubernetes will reject the update:
```text
The Job "db-migrator" is invalid: spec.template: Invalid value: ... field is immutable
```

To re-run a Job:
1. Delete the failed Job (or use `replace --force`).
2. Apply the corrected Job manifest.

### Fixed Manifest & Re-run

```bash
# 1. Delete the failed job and its error pods
kubectl delete job db-migrator -n lab

# 2. Apply the fixed job
kubectl apply -n lab -f - <<'EOF'
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrator
  namespace: lab
spec:
  backoffLimit: 2
  template:
    metadata:
      labels:
        app: db-migrator
    spec:
      restartPolicy: Never
      containers:
      - name: migrator
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting schema migration for release v2.4.0..."
          echo "Connecting to primary database at postgres.prod.svc..."
          sleep 2
          echo "Creating prerequisite table: billing_accounts..."
          echo "Executing migration script: 20261001_add_billing_indexes.sql..."
          echo "SUCCESS: Migration completed without errors."
          exit 0
        resources:
          requests:
            cpu: "50m"
            memory: "32Mi"
          limits:
            cpu: "100m"
            memory: "64Mi"
EOF
```

### Verification

```bash
kubectl get jobs,pods -n lab
```
Output:
```text
NAME                  COMPLETIONS   DURATION   AGE
job.batch/db-migrator 1/1           4s         6s

NAME                    READY   STATUS      RESTARTS   AGE
pod/db-migrator-5xlqm   0/1     Completed   0          6s
```

Check the log:
```bash
kubectl logs -n lab -l app=db-migrator
```
Output:
```text
SUCCESS: Migration completed without errors.
```

---

## Key Takeaways

1. **`Deployment` vs `Job`:**
   - Deployments keep long-running services alive indefinitely (restarts forever).
   - Jobs run a task to completion (`exit 0`).
2. **`backoffLimit`:**
   - Controls how many times the Job controller will retry a failed task (default is 6).
   - After reaching the limit, the Job stops and sets `BackoffLimitExceeded`.
3. **`restartPolicy: Never` vs `OnFailure`:**
   - `Never`: Spawns a new pod for each retry (keeps all failed pods for post-mortem debugging).
   - `OnFailure`: Restarts the container inside the same pod.
4. **Immutability:**
   - You cannot update an existing Job's `spec.template`. You must delete and recreate it.
