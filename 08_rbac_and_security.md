# Doc 08: RBAC & Security
## Controlling Access and Hardening Your Cluster

---

## 8.1 Why RBAC Matters

In production, you don't give everyone the admin kubeconfig. You give:
- Developers: read-only access to their namespace
- CI/CD pipelines: deploy access to specific namespaces
- Monitoring systems: read-only access to metrics
- Nothing/nobody: write access to kube-system

Without RBAC, anyone with cluster access can delete namespaces, read secrets (which contain TLS keys, passwords, API tokens), or backdoor the cluster.

**RBAC = Who can do What to Which resources.**

---

## 8.2 The RBAC Object Model

```
RBAC Model:

  Subject         Verb       Resource in Scope
  (WHO)          (WHAT)     (WHERE)

  User            get        pods in namespace "dev"
  Group           list       secrets in namespace "dev"
  ServiceAccount  create     deployments in namespace "dev"
  
  Objects that implement this:
  
  ┌────────────┐    ┌──────────────────┐
  │   Role      │    │  RoleBinding     │
  │  (rules)    │ ←─ │  (who gets role) │
  │  namespaced │    │  namespaced      │
  └────────────┘    └──────────────────┘
  
  ┌────────────┐    ┌──────────────────┐
  │ClusterRole  │    │ClusterRoleBinding│
  │  (rules)   │ ←─ │  (who gets it)   │
  │  cluster   │    │  cluster-wide    │
  └────────────┘    └──────────────────┘
  
  ClusterRole can also be used in a RoleBinding!
  → This gives cluster-level defined permissions within one namespace.
```

### Role vs ClusterRole

| | Role | ClusterRole |
|---|------|------------|
| Scope | One namespace | All namespaces + cluster-scoped resources |
| Can access | Pods, services, secrets, etc. in one ns | Nodes, PVs, ClusterRoles themselves, etc. |
| Bound by | RoleBinding (in same namespace) | ClusterRoleBinding (all ns) or RoleBinding (limits to one ns) |

💡 **Interview**: *"What is the difference between Role and ClusterRole?"*  
→ "A Role grants permissions within a single namespace. A ClusterRole grants permissions at the cluster level, including cluster-scoped resources like Nodes, PersistentVolumes, and Namespaces themselves. But ClusterRoles are also commonly used with namespace-scoped RoleBindings — this is a pattern where you define the permission set once as a ClusterRole, then bind it to different users in different namespaces separately. This avoids duplicating the same Role definition across 50 namespaces."

---

## 8.3 Built-in ClusterRoles

Kubernetes ships with useful built-in ClusterRoles:

| ClusterRole | What It Can Do |
|-------------|---------------|
| `cluster-admin` | Everything. Equivalent to root. |
| `admin` | Full control within a namespace (can't create namespaces or nodes) |
| `edit` | Read/write most resources. Can't read Secrets in some configs. |
| `view` | Read-only on most resources. Can't see Secrets. |
| `system:masters` | Group that maps to cluster-admin (your admin cert) |

```bash
# [M1] — view the rules in the edit role
kubectl get clusterrole edit -o yaml

# View all built-in clusterroles
kubectl get clusterroles | grep -v "system:"
```

---

## 8.4 Creating a Developer User (Read Access to One Namespace)

Kubernetes doesn't have "User" objects. Users are authenticated by their client certificates.  
To create a developer user:
1. Generate a client cert signed by the cluster CA
2. Create a RoleBinding giving that cert permissions

```bash
# [M1] — generate developer certificate

# Step 1: Create private key for the dev user
openssl genrsa -out dev-user.key 2048

# Step 2: Create Certificate Signing Request
# CN = username, O = group
openssl req -new \
  -key dev-user.key \
  -out dev-user.csr \
  -subj "/CN=dev-alice/O=developers"

# Step 3: Sign with cluster CA
openssl x509 -req \
  -in dev-user.csr \
  -CA /var/lib/rancher/rke2/server/tls/client-ca.crt \
  -CAkey /var/lib/rancher/rke2/server/tls/client-ca.key \
  -CAcreateserial \
  -out dev-user.crt \
  -days 365

# Step 4: Create namespace for dev team
kubectl create namespace dev

# Step 5: Give dev-alice view access to dev namespace
kubectl create rolebinding dev-alice-view \
  --clusterrole=view \
  --user=dev-alice \
  --namespace=dev

# Step 6: Build kubeconfig for dev-alice
kubectl config set-cluster rke2-cluster \
  --server=https://10.0.1.100:6443 \
  --certificate-authority=/var/lib/rancher/rke2/server/tls/server-ca.crt \
  --embed-certs=true \
  --kubeconfig=dev-alice.kubeconfig

kubectl config set-credentials dev-alice \
  --client-certificate=dev-user.crt \
  --client-key=dev-user.key \
  --embed-certs=true \
  --kubeconfig=dev-alice.kubeconfig

kubectl config set-context dev-alice-context \
  --cluster=rke2-cluster \
  --user=dev-alice \
  --namespace=dev \
  --kubeconfig=dev-alice.kubeconfig

kubectl config use-context dev-alice-context --kubeconfig=dev-alice.kubeconfig

# Test — give this kubeconfig to Alice
KUBECONFIG=dev-alice.kubeconfig kubectl get pods -n dev   # ✓ works
KUBECONFIG=dev-alice.kubeconfig kubectl get pods -n kube-system   # ✗ forbidden
KUBECONFIG=dev-alice.kubeconfig kubectl delete pod some-pod -n dev  # ✗ forbidden (view only)
```

---

## 8.5 ServiceAccounts — How Pods Authenticate

Every pod runs with a ServiceAccount. By default, it's the `default` ServiceAccount in its namespace.

```bash
# [M1] — see the default ServiceAccount
kubectl get serviceaccount default -n default -o yaml
# Has a secret containing a JWT token

# See what token is mounted in a pod
kubectl exec some-pod -- cat /var/run/secrets/kubernetes.io/serviceaccount/token
# This is the JWT the pod uses to call the API server
```

### Create a ServiceAccount With Specific Permissions

Example: a CI/CD pipeline that can deploy to the `production` namespace.

```bash
# [M1]
# Create namespace and ServiceAccount
kubectl create namespace production
kubectl create serviceaccount cicd-deployer -n production

# Create Role allowing deployment management
cat << 'EOF' | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: deployer
  namespace: production
rules:
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
  - apiGroups: [""]
    resources: ["pods", "services", "configmaps"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list"]   # read only for secrets
EOF

# Bind the Role to the ServiceAccount
cat << 'EOF' | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: cicd-deployer-binding
  namespace: production
subjects:
  - kind: ServiceAccount
    name: cicd-deployer
    namespace: production
roleRef:
  kind: Role
  name: deployer
  apiGroup: rbac.authorization.k8s.io
EOF

# Get the ServiceAccount token for CI/CD pipeline use
kubectl create token cicd-deployer -n production --duration=8760h
# This creates a time-limited token the pipeline can use
```

---

## 8.6 Understanding API Server Authorization Flow

```
Request arrives at API server:

1. AUTHENTICATION: Who are you?
   - Client cert → CN becomes username, O becomes group
   - Bearer token (ServiceAccount JWT) → decoded, subject becomes SA identity
   - No credential → anonymous (if allowed)

2. AUTHORIZATION: What can you do?
   - RBAC check: does this user/group/SA have permission for this verb+resource?
   - Also: Node authorizer (for kubelet), ABAC (legacy, not used in RKE2)

3. ADMISSION CONTROL: Is this request valid?
   - Mutating webhooks (can modify the request)
   - Validating webhooks (can reject the request)
   - Built-in admission plugins (ResourceQuota, LimitRanger, PodSecurity, etc.)

4. etcd WRITE: Store the object
```

💡 **Interview**: *"A pod is getting 403 Forbidden when calling the API server. How do you debug?"*  
→ "First, find which ServiceAccount the pod is using: `kubectl get pod <name> -o jsonpath='{.spec.serviceAccountName}'`. Then check what that SA can do: `kubectl auth can-i list pods --as=system:serviceaccount:<namespace>:<sa-name>`. Check if a RoleBinding exists binding this SA to appropriate roles: `kubectl get rolebindings -n <namespace> -o yaml | grep <sa-name>`. Also check ClusterRoleBindings: `kubectl get clusterrolebindings -o yaml | grep <sa-name>`. The most common cause is a missing RoleBinding or the SA in the wrong namespace."

---

## 8.7 Audit Logging

Kubernetes audit logs record every API request — who did what, when.

```bash
# [M1] — RKE2 audit log location
ls /var/lib/rancher/rke2/server/logs/
# audit.log (if audit policy configured)

# Enable audit logging in RKE2 config
cat >> /etc/rancher/rke2/config.yaml << 'EOF'
kube-apiserver-arg:
  - "audit-log-path=/var/lib/rancher/rke2/server/logs/audit.log"
  - "audit-log-maxage=30"        # days to keep old logs
  - "audit-log-maxbackup=5"      # number of backup files
  - "audit-log-maxsize=100"      # max size in MB per file
  - "audit-policy-file=/etc/rancher/rke2/audit-policy.yaml"
EOF

# Create audit policy
cat > /etc/rancher/rke2/audit-policy.yaml << 'EOF'
apiVersion: audit.k8s.io/v1
kind: Policy
rules:
  # Log all secret accesses (security critical)
  - level: Metadata
    resources:
    - group: ""
      resources: ["secrets"]
  
  # Log privileged pod creation
  - level: Request
    verbs: ["create", "update", "patch"]
    resources:
    - group: ""
      resources: ["pods"]
    
  # Log RBAC changes
  - level: RequestResponse
    resources:
    - group: "rbac.authorization.k8s.io"
      resources: ["clusterroles", "clusterrolebindings", "roles", "rolebindings"]

  # Skip noisy read-only requests
  - level: None
    verbs: ["get", "list", "watch"]
    resources:
    - group: ""
      resources: ["events"]
  
  # Default: log metadata only
  - level: Metadata
EOF

# Restart RKE2 to apply
systemctl restart rke2-server

# View audit logs
tail -f /var/lib/rancher/rke2/server/logs/audit.log | jq .
```

---

## 8.8 Pod Security Standards

Kubernetes 1.25+ includes **Pod Security Admission** replacing the old PodSecurityPolicy.  
Three levels:

| Level | What it blocks |
|-------|---------------|
| `privileged` | No restrictions |
| `baseline` | Blocks most dangerous settings (privileged containers, host network, etc.) |
| `restricted` | Strict — requires non-root, no privilege escalation, read-only root FS |

```bash
# [M1] — label a namespace to enforce restricted pods
kubectl label namespace production \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/audit=restricted

# Now try to create a privileged pod in production:
cat << 'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: bad-pod
  namespace: production
spec:
  containers:
  - name: bad
    image: nginx
    securityContext:
      privileged: true   # ← violates 'restricted' policy
EOF
# → Error: pods "bad-pod" is forbidden: violates PodSecurity "restricted:latest"
```

---

## 8.9 CIS Kubernetes Benchmark (What RKE2 Does by Default)

CIS (Center for Internet Security) publishes a benchmark for Kubernetes security. RKE2 applies many of these by default:

```bash
# RKE2 enables by default:
# - API server: --anonymous-auth=false (no anonymous access)
# - API server: --audit-log-* (audit logging)
# - etcd: TLS encrypted
# - kubelet: --read-only-port=0 (disabled insecure port)
# - RBAC enabled by default
# - ServiceAccount tokens signed and rotated

# Check what API server flags RKE2 uses:
ps aux | grep kube-apiserver | tr ' ' '\n' | grep -E "\-\-"
```

Key CIS checks you should verify:

```bash
# [M1]
# 1. Verify RBAC is enabled
kubectl api-versions | grep rbac
# Should show: rbac.authorization.k8s.io/v1

# 2. Verify anonymous auth is disabled
curl -sk https://127.0.0.1:6443/api -o /dev/null -w "%{http_code}"
# Should return 403 (not 200) — anonymous is rejected

# 3. Verify etcd only listens on localhost (not public)
ss -tlnp | grep 2379
# Should show: 127.0.0.1:2379 and 10.0.1.10:2379 (private IPs only)

# 4. Verify kubelet read-only port is closed
ss -tlnp | grep 10255
# Should show nothing — port 10255 should be closed

# 5. Verify no privileged containers in kube-system (should be none)
kubectl get pods -n kube-system -o json | \
  jq -r '.items[] | select(.spec.containers[].securityContext.privileged == true) | .metadata.name'
```

---

## 8.10 Summary

You now understand:
- ✅ Role vs ClusterRole, RoleBinding vs ClusterRoleBinding
- ✅ How to create developer users with cert-based auth
- ✅ ServiceAccounts and how pods authenticate to the API server
- ✅ The full authentication → authorization → admission flow
- ✅ Audit logging configuration
- ✅ Pod Security Standards
- ✅ CIS benchmark basics

**Next**: [Doc 09 - Storage & Longhorn →](./09-storage-longhorn.md)  
Persistent storage in Kubernetes — how PVs, PVCs, StorageClasses work, and deploying Longhorn for distributed block storage.

---

*Doc 08 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
