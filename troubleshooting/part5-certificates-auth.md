# Part 5 — Certificates & Auth

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 21. Expired Control-Plane Certs (API Server Auth Failure)

### Symptoms
- `kubectl` commands fail with `certificate has expired` or `x509: certificate has expired or is not yet valid`
- API server unreachable after ~1 year (kubeadm default) or after VM time change
- Cluster worked fine, then all commands fail simultaneously

### Diagnosis
```bash
# Check expiry of all control-plane certs (kubeadm clusters)
kubeadm certs check-expiration

# RKE2 — check certs manually
for cert in /var/lib/rancher/rke2/server/tls/*.crt; do
  echo "=== $cert ===";
  openssl x509 -noout -enddate -in "$cert" 2>/dev/null;
done

# Check specific API server cert
openssl x509 -noout -dates \
  -in /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt

# Check the kubeconfig cert
kubectl config view --raw | grep client-certificate-data | \
  awk '{print $2}' | base64 -d | openssl x509 -noout -dates

# Confirm time is correct on nodes (clock skew can cause this)
date
timedatectl status
```

### Fix Steps
1. **RKE2 — rotate certs automatically by restarting**:
   ```bash
   # RKE2 auto-rotates certs on restart if they're within 90 days of expiry
   # For expired certs, force rotation:
   sudo systemctl stop rke2-server

   # Remove old certs to force regeneration
   sudo rm -f /var/lib/rancher/rke2/server/tls/dynamic-cert.json
   sudo rm -f /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt

   sudo systemctl start rke2-server
   ```

2. **kubeadm clusters — rotate all certs**:
   ```bash
   sudo kubeadm certs renew all
   # Then restart control-plane components
   sudo crictl pods | grep -E "apiserver|scheduler|controller-manager" | \
     awk '{print $1}' | xargs -I{} sudo crictl stopp {}
   # Static pods restart automatically from /etc/kubernetes/manifests/
   ```

3. **Update kubeconfig** after cert rotation:
   ```bash
   # kubeadm
   sudo cp /etc/kubernetes/admin.conf ~/.kube/config

   # RKE2
   sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
   sudo chown $(id -u):$(id -g) ~/.kube/config
   ```

4. **Fix clock skew**:
   ```bash
   sudo timedatectl set-ntp true
   sudo chronyc makestep   # force immediate NTP sync
   ```

### Verify
```bash
kubectl get nodes
# Should work without x509 errors

# Confirm new expiry dates
openssl x509 -noout -dates \
  -in /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt
```

---

## 22. kubelet-to-API Server Cert Failure

### Symptoms
- Node shows `NotReady` even though kubelet is running
- kubelet logs show `x509: certificate signed by unknown authority` or `TLS handshake error`
- `kubectl get node` shows node as `NotReady` — kubelet can't authenticate

### Diagnosis
```bash
# SSH into the affected node
ssh <user>@<node-ip>

# Check kubelet logs for TLS errors
sudo journalctl -u rke2-agent -n 200 --no-pager | grep -i "x509\|tls\|cert\|unauthorized"

# Check serving cert the kubelet is using
sudo openssl x509 -noout -dates \
  -in /var/lib/rancher/rke2/agent/serving-kubelet.crt

# Check the CA cert the kubelet trusts
sudo openssl x509 -noout -text \
  -in /var/lib/rancher/rke2/agent/server-ca.crt | grep -E "Subject:|Issuer:|Not"

# Verify kubelet's kubeconfig points to correct server
sudo cat /var/lib/rancher/rke2/agent/kubelet.kubeconfig | grep server

# Check if bootstrap token is still valid (initial join)
kubectl get secret -n kube-system | grep bootstrap-token
```

### Fix Steps
1. **RKE2 — re-join the node** (cleanest fix for broken kubelet certs):
   ```bash
   # On the node — stop agent
   sudo systemctl stop rke2-agent

   # Remove old certs
   sudo rm -rf /var/lib/rancher/rke2/agent/serving-kubelet.crt
   sudo rm -rf /var/lib/rancher/rke2/agent/client-kubelet.crt
   sudo rm -rf /var/lib/rancher/rke2/agent/kubelet.kubeconfig

   # Restart agent — it will re-bootstrap certs from the server
   sudo systemctl start rke2-agent
   sudo journalctl -u rke2-agent -f   # watch for success
   ```

2. **kubelet cert rotation is disabled** — enable it:
   ```yaml
   # /etc/rancher/rke2/config.yaml on node
   kubelet-arg:
   - "rotate-certificates=true"
   - "rotate-server-certificates=true"
   ```

3. **CA bundle mismatch** — if control plane CA was rotated:
   ```bash
   # Copy fresh CA from control plane to node
   scp <control-plane>:/var/lib/rancher/rke2/server/tls/server-ca.crt \
     /var/lib/rancher/rke2/agent/server-ca.crt
   sudo systemctl restart rke2-agent
   ```

### Verify
```bash
kubectl get nodes
# Node should return to Ready

sudo journalctl -u rke2-agent -n 50 --no-pager | grep -i "registered\|ready"
```

---

## 23. Manual Cert Rotation (RKE2)

### Symptoms
- Certs approaching expiry (within 90 days)
- Planned cert rotation for compliance
- After cluster restore — need fresh certs

### Diagnosis
```bash
# Check all cert expiry dates on control plane
for cert in /var/lib/rancher/rke2/server/tls/*.crt; do
  echo "=== $(basename $cert) ===";
  openssl x509 -noout -enddate -in "$cert" 2>/dev/null || echo "  Not a cert";
done

# Check ETcd certs
for cert in /var/lib/rancher/rke2/server/tls/etcd/*.crt; do
  echo "=== $(basename $cert) ===";
  openssl x509 -noout -enddate -in "$cert" 2>/dev/null;
done

# Check client-facing API server cert SANs
openssl x509 -noout -text \
  -in /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt | grep -A5 "Subject Alternative"
```

### Fix Steps
1. **RKE2 built-in cert rotation** (cleanest method):
   ```bash
   # Rotate all certificates
   sudo rke2 certificate rotate

   # Or rotate specific cert
   sudo rke2 certificate rotate --service api-server
   sudo rke2 certificate rotate --service etcd

   # Restart server after rotation
   sudo systemctl restart rke2-server
   ```

2. **Add new SANs to API server cert** (e.g., adding new VIP or load balancer IP):
   ```yaml
   # /etc/rancher/rke2/config.yaml
   tls-san:
   - 192.168.1.100          # VIP
   - k8s-api.example.com    # FQDN
   - 10.0.0.10              # new LB IP
   ```
   Then: `sudo rke2 certificate rotate --service api-server && sudo systemctl restart rke2-server`

3. **Update kubeconfig after rotation**:
   ```bash
   sudo cp /etc/rancher/rke2/rke2.yaml ~/.kube/config
   sudo chown $(id -u):$(id -g) ~/.kube/config

   # If using a custom kubeconfig with embedded certs:
   kubectl config set-cluster <cluster> \
     --certificate-authority=/var/lib/rancher/rke2/server/tls/server-ca.crt
   ```

4. **Restart all agents** to pick up new CA:
   ```bash
   # On each worker node
   sudo systemctl restart rke2-agent
   ```

### Verify
```bash
kubectl get nodes
# All Ready

for cert in /var/lib/rancher/rke2/server/tls/*.crt; do
  echo "=== $(basename $cert) ===";
  openssl x509 -noout -enddate -in "$cert" 2>/dev/null;
done
# All dates should be fresh (1 year from now)
```

---

## 24. ServiceAccount Token Issues

### Symptoms
- Pod gets `401 Unauthorized` when calling the Kubernetes API
- Application logs show `couldn't get current server API group list: the server has asked for the client to provide credentials`
- After K8s 1.24+ upgrade — old long-lived tokens stopped working

### Diagnosis
```bash
# Check the ServiceAccount the pod uses
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.spec.serviceAccountName}'

# Check the ServiceAccount exists
kubectl get serviceaccount <sa-name> -n <namespace>

# Check if an auto-mounted token exists in the pod
kubectl exec <pod-name> -n <namespace> -- \
  ls /var/run/secrets/kubernetes.io/serviceaccount/
# Should contain: ca.crt, namespace, token

# Verify the token is valid
TOKEN=$(kubectl exec <pod-name> -n <namespace> -- \
  cat /var/run/secrets/kubernetes.io/serviceaccount/token)
kubectl get --token=$TOKEN pods -n <namespace>

# Check token expiry (K8s 1.21+ tokens expire)
kubectl exec <pod-name> -n <namespace> -- \
  cat /var/run/secrets/kubernetes.io/serviceaccount/token | \
  cut -d. -f2 | base64 -d 2>/dev/null | jq '.exp | todate'

# Check if automountServiceAccountToken is false
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.spec.automountServiceAccountToken}'
```

### Fix Steps
1. **Token not mounted** — ensure `automountServiceAccountToken: true`:
   ```yaml
   spec:
     automountServiceAccountToken: true
   ```
   Or on the ServiceAccount:
   ```bash
   kubectl patch serviceaccount <sa-name> -n <namespace> \
     -p '{"automountServiceAccountToken": true}'
   ```

2. **Create a long-lived token** (K8s 1.24+ — must be explicit):
   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: <sa-name>-token
     namespace: <namespace>
     annotations:
       kubernetes.io/service-account.name: <sa-name>
   type: kubernetes.io/service-account-token
   ```
   ```bash
   kubectl apply -f sa-token.yaml
   kubectl get secret <sa-name>-token -n <namespace> \
     -o jsonpath='{.data.token}' | base64 -d
   ```

3. **Wrong RBAC** — the SA token is valid but has no permissions (see scenario 25).

### Verify
```bash
kubectl exec <pod-name> -n <namespace> -- \
  curl -sk -H "Authorization: Bearer $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
  https://kubernetes.default.svc/api/v1/namespaces/<namespace>/pods
# Should return pod list, not 401
```

---

## 25. RBAC Misconfiguration (403 Forbidden)

### Symptoms
- Application or user gets `403 Forbidden` when calling the Kubernetes API
- `kubectl` commands fail with `Error from server (Forbidden): pods is forbidden`
- ServiceAccount has a valid token but no permissions

### Diagnosis
```bash
# Test what a ServiceAccount can do
kubectl auth can-i list pods -n <namespace> \
  --as=system:serviceaccount:<namespace>:<sa-name>
# Output: yes or no

# Check all permissions for the SA
kubectl auth can-i --list -n <namespace> \
  --as=system:serviceaccount:<namespace>:<sa-name>

# List RoleBindings in the namespace
kubectl get rolebinding -n <namespace>
kubectl describe rolebinding <rb-name> -n <namespace>

# List ClusterRoleBindings
kubectl get clusterrolebinding | grep <sa-name>

# Check what Role/ClusterRole is bound
kubectl describe role <role-name> -n <namespace>
kubectl describe clusterrole <clusterrole-name>

# Check if the SA name or namespace is wrong in the binding
kubectl get rolebinding <rb-name> -n <namespace> \
  -o jsonpath='{.subjects}' | jq
```

### Fix Steps
1. **Create a Role and RoleBinding** (namespace-scoped):
   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: Role
   metadata:
     name: pod-reader
     namespace: <namespace>
   rules:
   - apiGroups: [""]
     resources: ["pods", "pods/log"]
     verbs: ["get", "list", "watch"]
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: RoleBinding
   metadata:
     name: pod-reader-binding
     namespace: <namespace>
   subjects:
   - kind: ServiceAccount
     name: <sa-name>
     namespace: <namespace>
   roleRef:
     kind: Role
     name: pod-reader
     apiGroup: rbac.authorization.k8s.io
   ```
   ```bash
   kubectl apply -f rbac.yaml
   ```

2. **Fix wrong subject** in existing RoleBinding:
   ```bash
   kubectl edit rolebinding <rb-name> -n <namespace>
   # Correct the subjects[].namespace or subjects[].name
   ```

3. **Grant cluster-wide access** (use sparingly):
   ```bash
   kubectl create clusterrolebinding <name> \
     --clusterrole=view \
     --serviceaccount=<namespace>:<sa-name>
   ```

### Verify
```bash
kubectl auth can-i list pods -n <namespace> \
  --as=system:serviceaccount:<namespace>:<sa-name>
# yes

kubectl auth can-i --list -n <namespace> \
  --as=system:serviceaccount:<namespace>:<sa-name>
# Should show the granted verbs
```

---

*Previous → [Part 4 — Networking](./part4-networking.md)*  
*Next → [Part 6 — Control Plane / etcd](./part6-control-plane-etcd.md)*
