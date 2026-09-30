# Scenario 10 — Audit Logger: Forbidden on Startup

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Compliance Risk)**
> The `audit-logger` service deployed 5 minutes ago keeps crashing immediately on startup. Security team reports all audit logs for user actions have completely stopped. Compliance team is escalating.

**Your job:** Triage, find root cause, fix, and verify logs are flowing.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: audit-logger-sa
  namespace: lab
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: audit-logger
  namespace: lab
  labels:
    app: audit-logger
spec:
  replicas: 1
  selector:
    matchLabels:
      app: audit-logger
  template:
    metadata:
      labels:
        app: audit-logger
    spec:
      serviceAccountName: audit-logger-sa
      containers:
      - name: logger
        image: bitnami/kubectl:latest
        command: ["sh", "-c"]
        args:
        - |
          echo "Audit Logger starting..."
          echo "Fetching pod list for audit..."
          kubectl get pods -n lab
          echo "Fetching events for audit..."
          kubectl get events -n lab
          echo "Audit complete. Sleeping..."
          sleep 3600
        resources:
          requests:
            cpu: "50m"
            memory: "64Mi"
          limits:
            cpu: "100m"
            memory: "128Mi"
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What does `kubectl logs -n lab -l app=audit-logger` show? What specific error message?
2. The error mentions `system:serviceaccount:lab:audit-logger-sa` — what is this identity?
3. What three Kubernetes objects form the RBAC system?
4. What is the difference between `Role` and `ClusterRole`?
5. Write the fix (Role + RoleBinding) before looking at the solution.

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The pod uses `serviceAccountName: audit-logger-sa`. This ServiceAccount exists but has **no permissions** — no Role or RoleBinding has been created for it. When the container tries to call the Kubernetes API (`kubectl get pods`), the API server checks RBAC and denies the request.

### Smoking Gun

```bash
kubectl logs -n lab -l app=audit-logger
```
```
Audit Logger starting...
Fetching pod list for audit...
Error from server (Forbidden): pods is forbidden:
  User "system:serviceaccount:lab:audit-logger-sa"
  cannot list resource "pods" in API group "" in the namespace "lab"
```

The API server tells you exactly:
- **WHO:** `system:serviceaccount:lab:audit-logger-sa`
- **WHAT:** tried to `list` `pods`
- **WHERE:** in namespace `lab`
- **RESULT:** `Forbidden`

### Fix — Create Role + RoleBinding

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: audit-logger-role
  namespace: lab
rules:
- apiGroups: [""]
  resources: ["pods", "events"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: audit-logger-binding
  namespace: lab
subjects:
- kind: ServiceAccount
  name: audit-logger-sa
  namespace: lab
roleRef:
  kind: Role
  name: audit-logger-role
  apiGroup: rbac.authorization.k8s.io
EOF
```

Restart the deployment to pick up new permissions:
```bash
kubectl rollout restart deployment/audit-logger -n lab
```

### RBAC Structure

```
ServiceAccount (audit-logger-sa)
       │
       │ (connected by)
       ▼
RoleBinding (audit-logger-binding)
       │
       │ (points to)
       ▼
Role (audit-logger-role)
  rules:
  - resources: [pods, events]
    verbs: [get, list, watch]
```

### Role vs ClusterRole

| Combination | Scope | Use Case |
|-------------|-------|----------|
| `Role` + `RoleBinding` | Single namespace | App needs access in one namespace |
| `ClusterRole` + `ClusterRoleBinding` | All namespaces | Monitoring tool, cluster-admin |
| `ClusterRole` + `RoleBinding` | Single namespace | Reuse role definition across multiple namespaces |
| `Role` + `ClusterRoleBinding` | ❌ **INVALID** | Cannot bind a namespace-scoped role cluster-wide |

### Quick RBAC Debug Command

```bash
# Can this SA list pods in lab namespace?
kubectl auth can-i list pods -n lab \
  --as=system:serviceaccount:lab:audit-logger-sa
# yes ✅ or no ❌
```

Use this before reading long Role YAML to quickly verify permissions.

</details>
