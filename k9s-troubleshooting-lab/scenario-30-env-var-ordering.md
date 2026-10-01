# Scenario 30 (Grand Finale) — Checkout Backend: Environment Variable Ordering Dependency

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Database Connection Failure)**  
> New release of `checkout-backend` crashes immediately in `CrashLoopBackOff`. Logs show literal unexpanded strings: `postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):5432/$(DB_NAME)`. All database connections fail.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: checkout-backend
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: checkout-backend
  template:
    metadata:
      labels:
        app: checkout-backend
    spec:
      containers:
      - name: backend
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting checkout-backend..."
          echo "Connecting to database at: $DATABASE_URL"
          if echo "$DATABASE_URL" | grep -q '\$('; then
            echo "FATAL: Variable expansion failed! Unresolved variable in DATABASE_URL: $DATABASE_URL"
            exit 1
          fi
          echo "Connected to database successfully!"
          sleep 3600
        env:
        - name: DATABASE_URL
          value: "postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):5432/$(DB_NAME)"
        - name: DB_USER
          value: "checkout_app"
        - name: DB_PASSWORD
          value: "VaultPassword99!"
        - name: DB_HOST
          value: "postgres-cluster.db.svc"
        - name: DB_NAME
          value: "orders_db"
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

### 1. Check Pod Status

```bash
kubectl get pods -n lab
```
Output:
```text
NAME                                READY   STATUS             RESTARTS      AGE
checkout-backend-59cddb8cdd-wds9g   0/1     CrashLoopBackOff   2 (20s ago)   40s
```

### 2. Check Logs

```bash
kubectl logs -n lab -l app=checkout-backend
```
Output:
```text
Starting checkout-backend...
Connecting to database at: postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):5432/$(DB_NAME)
FATAL: Variable expansion failed! Unresolved variable in DATABASE_URL: postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):5432/$(DB_NAME)
```

---

## Root Cause

In Kubernetes, dependent environment variable expansion (`$(VAR_NAME)`) is evaluated **strictly in order, from top to bottom**:

1. When the kubelet evaluates the first variable:
   ```yaml
   - name: DATABASE_URL
     value: "postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):5432/$(DB_NAME)"
   ```
2. The referenced variables (`DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_NAME`) **do not exist yet** in the environment table because they are defined further down in the YAML list!
3. In Kubernetes syntax, if a referenced variable is undefined, Kubernetes does not throw an error—it leaves the **literal string** `$(DB_USER)` unexpanded.
4. When the application boots, it attempts to connect to `$(DB_HOST)` as a hostname, which fails DNS resolution and causes an immediate crash.

---

## Solution: Place Dependent Variables at the Bottom

Move `DATABASE_URL` below all of its prerequisite dependencies:

### Fixed Manifest

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: checkout-backend
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: checkout-backend
  template:
    metadata:
      labels:
        app: checkout-backend
    spec:
      containers:
      - name: backend
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Starting checkout-backend..."
          echo "Connecting to database at: $DATABASE_URL"
          if echo "$DATABASE_URL" | grep -q '\$('; then
            echo "FATAL: Variable expansion failed! Unresolved variable in DATABASE_URL: $DATABASE_URL"
            exit 1
          fi
          echo "Connected to database successfully!"
          sleep 3600
        env:
        - name: DB_USER
          value: "checkout_app"
        - name: DB_PASSWORD
          value: "VaultPassword99!"
        - name: DB_HOST
          value: "postgres-cluster.db.svc"
        - name: DB_NAME
          value: "orders_db"
        - name: DATABASE_URL               # <-- Defined AFTER its prerequisite variables!
          value: "postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):5432/$(DB_NAME)"
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

Check pod status:
```bash
kubectl get pods -n lab
```
Output:
```text
NAME                                READY   STATUS    RESTARTS   AGE
checkout-backend-67df98c564-9k2zl   1/1     Running   0          5s
```

Check logs:
```bash
kubectl logs -n lab -l app=checkout-backend
```
Output:
```text
Starting checkout-backend...
Connecting to database at: postgresql://checkout_app:VaultPassword99!@postgres-cluster.db.svc:5432/orders_db
Connected to database successfully!
```

---

## Key Takeaways

1. **Sequential Evaluation in `env`:**
   - Any variable referencing `$(ANOTHER_VAR)` must appear **after** `ANOTHER_VAR` in the YAML list.
2. **Beware of Auto-Formatters:**
   - Linter plugins or YAML alphabetizers that sort `env` alphabetically will move `DATABASE_URL` (starts with D) before `POSTGRES_HOST` (starts with P), silently breaking production deployments!
