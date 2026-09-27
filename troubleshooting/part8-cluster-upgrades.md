# Part 8 — Cluster Upgrades

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 36. Version Skew Violation

### Symptoms
- `kubectl` commands fail with `server version (1.28) is too old for client version (1.30)`
- kubelet on a node rejects API server responses (skew > 2 minor versions)
- After upgrading control plane — some worker nodes start behaving oddly

### Diagnosis
```bash
# Check client, server, and component versions
kubectl version
kubectl version --short   # deprecated in 1.28+, use:
kubectl version -o json | jq '{client: .clientVersion.gitVersion, server: .serverVersion.gitVersion}'

# Check kubelet version on each node
kubectl get nodes -o custom-columns=\
"NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion,OS:.status.nodeInfo.osImage"

# Check component versions
kubectl get componentstatuses  # deprecated but still useful

# Identify skew violations:
# kube-apiserver should be upgraded first
# kubelet can be N-2 behind kube-apiserver (not ahead)
# kubectl client can be N+1/N-1 of server

# Check RKE2 installed version on each node
ssh <user>@<node-ip>
rke2 --version
```

### Fix Steps
1. **Upgrade kubectl client** to match or be within 1 minor version of server:
   ```bash
   # macOS
   brew upgrade kubernetes-cli

   # Linux
   curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
   sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
   ```

2. **Upgrade worker nodes** that are lagging (RKE2):
   ```bash
   # Drain node first
   kubectl drain <node-name> --ignore-daemonsets --delete-emissary-on-pod-failure

   # SSH into node and upgrade RKE2 agent
   ssh <user>@<node-ip>
   curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION=v1.29.x+rke2r1 INSTALL_RKE2_TYPE=agent sh -
   sudo systemctl restart rke2-agent

   # Back on control plane — uncordon
   kubectl uncordon <node-name>
   ```

3. **Roll back kubectl** if you accidentally went too far ahead:
   ```bash
   # Install specific version
   curl -LO "https://dl.k8s.io/release/v1.29.0/bin/linux/amd64/kubectl"
   sudo install kubectl /usr/local/bin/kubectl
   ```

### Verify
```bash
kubectl version -o json | jq '{client: .clientVersion.gitVersion, server: .serverVersion.gitVersion}'
# Should be within 1 minor version

kubectl get nodes -o custom-columns="NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion"
# All within 2 minor versions of API server
```

---

## 37. Control-Plane-First Upgrade Order

### Symptoms
- After upgrading worker nodes before control plane — API server rejects newer kubelet features
- `kubectl apply` fails with `unknown field` for new API fields
- Pods use features not yet supported by the control plane

### Diagnosis
```bash
# Check current versions across all components
kubectl version
kubectl get nodes -o custom-columns=\
"NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion,ROLE:.metadata.labels.node-role\.kubernetes\.io/control-plane"

# Check if control-plane nodes have older kubelet than workers
# workers should NEVER be newer than control plane

# Check RKE2 version on server vs agent nodes
# On control plane:
ssh <cp-node> "rke2 --version"
# On worker:
ssh <worker-node> "rke2 --version"
# Worker version should be <= control plane version

# Check for feature compatibility issues
kubectl api-versions   # list all supported API versions
```

### Fix Steps
1. **Upgrade control plane first** — always:
   ```bash
   # RKE2 control plane upgrade
   ssh <control-plane-node>

   # Stop, upgrade, restart
   sudo systemctl stop rke2-server
   curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION=v1.30.x+rke2r1 sh -
   sudo systemctl start rke2-server
   sudo journalctl -u rke2-server -f   # watch until ready

   # Verify control plane is Ready before touching workers
   kubectl get nodes
   ```

2. **Upgrade workers one at a time** after control plane:
   ```bash
   for NODE in worker1 worker2 worker3; do
     echo "=== Draining $NODE ==="
     kubectl drain $NODE --ignore-daemonsets --delete-emissary-on-pod-failure
     
     echo "=== Upgrading $NODE ==="
     ssh <user>@$NODE "curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION=v1.30.x+rke2r1 INSTALL_RKE2_TYPE=agent sh - && sudo systemctl restart rke2-agent"
     
     echo "=== Waiting for $NODE to be Ready ==="
     kubectl wait node $NODE --for=condition=Ready --timeout=120s
     
     echo "=== Uncordoning $NODE ==="
     kubectl uncordon $NODE
     sleep 30   # let pods settle before next node
   done
   ```

3. **If workers are already ahead of control plane** — temporarily restrict to old features:
   ```bash
   # This is a bad state — upgrade control plane ASAP
   # Don't use new API fields until control plane is upgraded
   ```

### Verify
```bash
kubectl get nodes -o custom-columns=\
"NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion"
# All versions within 2 minor versions of each other — control plane highest
```

---

## 38. Drain / Cordon Sequencing Mistakes

### Symptoms
- Pods not migrating off nodes before maintenance
- Stuck Terminating pods blocking drain
- `kubectl drain` errors out or takes too long
- PodDisruptionBudgets blocking drain

### Diagnosis
```bash
# Check drain command output carefully
kubectl drain <node-name> --ignore-daemonsets --dry-run
# Use --dry-run first to see what would be evicted

# Find blocking pods
kubectl get pods --all-namespaces -o wide | grep <node-name>
kubectl describe pod <blocking-pod> -n <namespace>

# Check PodDisruptionBudgets
kubectl get pdb --all-namespaces
kubectl describe pdb <pdb-name> -n <namespace>
# disruptionsAllowed: 0 = drain will block

# Check for pods with no owner (will block drain without --force)
kubectl get pods --all-namespaces -o wide | grep <node-name> | \
  awk '{print $1, $2}' | while read ns pod; do
    kubectl get pod $pod -n $ns -o jsonpath='{.metadata.ownerReferences[0].kind}' | grep -q "" && \
    echo "Orphan pod: $ns/$pod"
  done

# Check terminating pods stuck
kubectl get pods --all-namespaces | grep Terminating
```

### Fix Steps
1. **Correct drain sequence**:
   ```bash
   # Step 1: Cordon first (no new pods scheduled)
   kubectl cordon <node-name>

   # Step 2: Drain with options
   kubectl drain <node-name> \
     --ignore-daemonsets \
     --delete-emissary-on-pod-failure \
     --grace-period=60 \
     --timeout=300s

   # Do your maintenance

   # Step 3: Uncordon after maintenance
   kubectl uncordon <node-name>
   ```

2. **PDB blocking drain** — temporarily scale up the deployment first:
   ```bash
   kubectl scale deployment <name> -n <namespace> --replicas=3
   # Now PDB has room — retry drain
   kubectl drain <node-name> --ignore-daemonsets
   ```

3. **Force drain** (pods without PDB that are stuck):
   ```bash
   kubectl drain <node-name> --ignore-daemonsets --force --grace-period=0
   ```

4. **Manually delete stuck Terminating pods**:
   ```bash
   kubectl delete pod <pod-name> -n <namespace> --force --grace-period=0
   ```

### Verify
```bash
# Node should have no user pods
kubectl get pods --all-namespaces -o wide | grep <node-name> | grep -v kube-system

# Node is cordoned
kubectl get node <node-name>
# STATUS: Ready,SchedulingDisabled

# After maintenance — uncordon and verify pods come back
kubectl uncordon <node-name>
kubectl get pods --all-namespaces -o wide | grep <node-name>
```

---

## 39. Upgrade Rollback Mid-Failure

### Symptoms
- Upgrade in progress — control plane upgraded but workers failing
- New pods failing to start on upgraded nodes
- Need to roll back to previous version

### Diagnosis
```bash
# Check current state of the upgrade
kubectl get nodes -o custom-columns=\
"NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion,STATUS:.status.conditions[-1].type"

# Check what went wrong
kubectl get events --all-namespaces --sort-by=lastTimestamp | tail -30

# Check pod failures on upgraded nodes
kubectl get pods --all-namespaces -o wide | grep <upgraded-node>
kubectl describe pod <failing-pod> -n <namespace>

# Check for API deprecation breaking existing manifests
kubectl get events --all-namespaces | grep -i "not found\|deprecated\|unknown field"

# Check RKE2 version running
ssh <node-ip> "rke2 --version"
```

### Fix Steps
1. **Roll back worker node** to previous RKE2 version:
   ```bash
   # Drain the node
   kubectl drain <node-name> --ignore-daemonsets --force

   # SSH and downgrade
   ssh <user>@<node-ip>
   sudo systemctl stop rke2-agent

   # Install old version
   curl -sfL https://get.rke2.io | INSTALL_RKE2_VERSION=v1.29.x+rke2r1 INSTALL_RKE2_TYPE=agent sh -
   sudo systemctl start rke2-agent
   sudo journalctl -u rke2-agent -f

   # Uncordon
   kubectl uncordon <node-name>
   ```

2. **Roll back control plane** (more complex — do only if necessary):
   ```bash
   # WARNING: Downgrading etcd data format may not be reversible
   # Always take etcd snapshot before upgrading

   # If etcd snapshot exists:
   sudo rke2 etcd-snapshot restore --name <snapshot-name>
   ```

3. **Fix the root cause** before re-attempting upgrade:
   ```bash
   # Common causes:
   # - Deprecated API in manifests → fix manifests first
   # - PodSecurityPolicy removed in 1.25 → migrate to PSA
   # - Old admission webhooks rejecting new objects → update webhook
   ```

4. **Use system-upgrade-controller for safer upgrades** (RKE2 compatible):
   ```bash
   # Staged upgrade with automatic rollback on failure
   kubectl apply -f https://github.com/rancher/system-upgrade-controller/releases/latest/download/system-upgrade-controller.yaml
   ```

### Verify
```bash
kubectl get nodes -o custom-columns=\
"NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion"

kubectl get pods --all-namespaces | grep -v Running | grep -v Completed
# Should be empty after rollback
```

---

## 40. CRD / API Deprecation Breaking After Upgrade

### Symptoms
- After upgrade — existing resources fail to apply or are rejected
- `kubectl apply` returns `no matches for kind "X" in version "Y"`
- Helm releases fail with `resource mapping not found`
- Admission webhooks returning errors for valid-looking objects

### Diagnosis
```bash
# Check which APIs were removed in the new version
kubectl api-versions
# Compare against your manifests

# Find resources using deprecated API versions
kubectl get --raw /apis | jq '.groups[].preferredVersion'

# Use kubectl convert to check
kubectl convert -f <manifest.yaml> --output-version <new-api-version>

# Check for CRD version issues
kubectl get crds
kubectl describe crd <crd-name> | grep -A5 "Versions"

# Check which Helm charts use removed APIs (if using Helm)
helm list --all-namespaces
helm template <release> <chart> | grep "apiVersion:" | sort -u

# Tool: pluto (detect deprecated APIs)
pluto detect-files -d . --target-versions k8s=v1.30.0
# or
pluto detect-helm --target-versions k8s=v1.30.0
```

### Fix Steps
1. **Migrate manifests to new API versions**:
   ```bash
   # Common migrations for 1.25:
   # extensions/v1beta1/Ingress → networking.k8s.io/v1
   # policy/v1beta1/PodDisruptionBudget → policy/v1
   # policy/v1beta1/PodSecurityPolicy → REMOVED (migrate to PSA)

   # Use kubectl convert (requires convert plugin)
   kubectl convert -f old-ingress.yaml --output-version networking.k8s.io/v1 > new-ingress.yaml
   kubectl apply -f new-ingress.yaml
   ```

2. **Update Helm chart version** to one that uses new APIs:
   ```bash
   helm repo update
   helm upgrade <release> <repo/chart> --version <compatible-version> -n <namespace>
   ```

3. **Fix stored CRD versions** — if CRD has served versions removed:
   ```bash
   # Edit CRD to add new version and mark old as non-storage
   kubectl edit crd <crd-name>
   # Change storage: true to storage: false on old version
   # Add new version with storage: true
   ```

4. **Update admission webhooks** that reference removed API groups:
   ```bash
   kubectl get validatingwebhookconfigurations
   kubectl get mutatingwebhookconfigurations
   # Update rules[].apiVersions in the webhook config
   kubectl edit validatingwebhookconfiguration <name>
   ```

5. **PodSecurityPolicy removal** (1.25) — migrate to PodSecurityAdmission:
   ```bash
   # Label namespaces with PSA policy
   kubectl label namespace <ns> pod-security.kubernetes.io/enforce=restricted
   kubectl label namespace <ns> pod-security.kubernetes.io/warn=restricted
   ```

### Verify
```bash
kubectl apply -f <manifest.yaml>
# Should succeed without apiVersion errors

kubectl api-resources | grep <your-resource>
# Resource should appear under correct API group

helm list --all-namespaces
# All releases should show STATUS: deployed
```

---

*Previous → [Part 7 — Storage](./part7-storage.md)*  
*Next → [Part 9 — Observability & Misc](./part9-observability-misc.md)*
