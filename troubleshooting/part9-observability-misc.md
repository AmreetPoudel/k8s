# Part 9 — Observability & Misc

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 41. Metrics-Server Down (HPA Fails Silently)

### Symptoms
- `kubectl top pods` returns `error: Metrics API not available`
- HPA shows `<unknown>/50%` for CPU target — never scales
- `kubectl describe hpa <name>` shows `unable to fetch metrics from resource metrics API`

### Diagnosis
```bash
# Check if metrics-server is running
kubectl get pods -n kube-system | grep metrics-server
kubectl describe pod <metrics-server-pod> -n kube-system

# Check metrics-server logs
kubectl logs -n kube-system -l k8s-app=metrics-server --tail=100

# Verify the metrics API is registered
kubectl get apiservice v1beta1.metrics.k8s.io
kubectl describe apiservice v1beta1.metrics.k8s.io
# Look at: Available: True/False and Message

# Test metrics endpoint
kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes | jq
kubectl get --raw /apis/metrics.k8s.io/v1beta1/pods | jq

# Check HPA status
kubectl describe hpa <hpa-name> -n <namespace>
# AbleToScale: True/False
# ScalingActive: True/False — False means metrics not available

# Check if kubelet metrics are accessible (metrics-server scrapes kubelets)
kubectl get --raw /api/v1/nodes/<node-name>/proxy/metrics/resource
```

### Fix Steps
1. **Restart metrics-server**:
   ```bash
   kubectl rollout restart deployment metrics-server -n kube-system
   kubectl rollout status deployment metrics-server -n kube-system
   ```

2. **Fix TLS issue** — common with kubeadm/RKE2 (kubelet cert not trusted):
   ```bash
   # Add --kubelet-insecure-tls flag (dev/lab clusters only)
   kubectl edit deployment metrics-server -n kube-system
   # Add to args:
   # - --kubelet-insecure-tls
   # - --kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname
   ```

3. **Metrics-server not installed** — install it:
   ```bash
   kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
   # Or via Helm:
   helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
   helm upgrade --install metrics-server metrics-server/metrics-server \
     -n kube-system \
     --set args[0]="--kubelet-insecure-tls"
   ```

4. **HPA still not scaling after metrics restored** — check HPA min/max and cooldown:
   ```bash
   kubectl describe hpa <name> -n <namespace>
   # Check: minReplicas, maxReplicas, currentReplicas, desiredReplicas
   # If currentReplicas == maxReplicas → already at max
   ```

### Verify
```bash
kubectl top nodes
kubectl top pods --all-namespaces

kubectl get hpa -n <namespace>
# TARGETS column should show actual value, not <unknown>
# e.g., 45%/50% instead of <unknown>/50%
```

---

## 42. Log Aggregation Gaps (Loki / Fluentd Misconfig)

### Symptoms
- Logs missing from Grafana/Loki for certain namespaces or pods
- Fluentd/Fluent Bit DaemonSet pods in `Error` or `CrashLoopBackOff`
- Log gaps in Loki — some time ranges show no logs even though pods were running

### Diagnosis
```bash
# Check the log agent DaemonSet
kubectl get daemonset -n logging   # or kube-system / monitoring
kubectl get pods -n logging -o wide   # check which nodes have issues

# Check Fluent Bit / Fluentd pod logs
kubectl logs -n logging <fluentd-pod> --tail=100
kubectl logs -n logging <fluentd-pod> --previous   # if crashed

# Common log agent errors to look for:
# "Connection refused" → Loki endpoint wrong
# "authentication required" → Loki auth token missing
# "buffer full" → agent can't keep up, dropping logs
# "permission denied" → can't read /var/log/containers/

# Check Fluentd config
kubectl get configmap -n logging | grep fluentd
kubectl get configmap <config-name> -n logging -o yaml

# Check if log files exist on the node
kubectl debug node/<node-name> -it --image=ubuntu -- bash
ls /var/log/containers/ | head -20

# Check Loki ingestion
kubectl port-forward -n monitoring svc/loki 3100:3100
curl -s http://localhost:3100/metrics | grep loki_ingester_streams_total
```

### Fix Steps
1. **Fix Loki endpoint in Fluentd/Fluent Bit config**:
   ```yaml
   # Fluent Bit output section
   [OUTPUT]
     Name          loki
     Match         *
     Host          loki.monitoring.svc.cluster.local   # ← correct service name
     Port          3100
     Labels        job=fluentbit
   ```
   ```bash
   kubectl apply -f fluent-bit-configmap.yaml
   kubectl rollout restart daemonset fluent-bit -n logging
   ```

2. **Buffer overflow — Fluentd can't ship logs fast enough**:
   ```yaml
   # Increase buffer size in Fluentd config
   <buffer>
     @type file
     path /var/log/fluentd-buffers
     chunk_limit_size 8MB
     total_limit_size 1GB     # ← increase if disk allows
     overflow_action drop_oldest_chunk
   </buffer>
   ```

3. **Permission issue reading /var/log/containers**:
   ```bash
   # Check securityContext in DaemonSet
   kubectl describe daemonset <fluentd-ds> -n logging | grep -A10 "Security Context"
   # May need: runAsUser: 0 (root) or specific capabilities
   ```

4. **Namespace filter excluding pods** — review and fix filter rules:
   ```yaml
   # Fluent Bit filter — ensure your target namespace is not excluded
   [FILTER]
     Name    grep
     Match   *
     Exclude $kubernetes['namespace_name'] kube-system  # remove if you need kube-system
   ```

### Verify
```bash
kubectl logs -n logging <fluentd-pod> --tail=20
# No errors — should show "flushed" or "sent" messages

# Query Loki directly
kubectl port-forward -n monitoring svc/loki 3100:3100
curl -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="<your-namespace>"}' \
  --data-urlencode 'start=1h' | jq '.data.result | length'
# Should return > 0
```

---

## 43. Resource Quota Exhaustion

### Symptoms
- New pods/deployments fail with `exceeded quota: ... resource ... is over the limit`
- `kubectl apply` returns `forbidden: exceeded quota`
- Namespace-level operations fail even though cluster has free resources

### Diagnosis
```bash
# Check resource quota status in the namespace
kubectl describe resourcequota -n <namespace>
# Look at: Used vs Hard columns

# Get all quotas
kubectl get resourcequota -n <namespace> -o yaml

# See what's consuming the quota
kubectl top pods -n <namespace> --containers | sort -k4 -rn
kubectl get pods -n <namespace>

# Check specific resource counts
kubectl get pods -n <namespace> | wc -l          # pod count
kubectl get pvc -n <namespace> | grep Bound | wc -l  # PVC count

# Find quota violations in events
kubectl get events -n <namespace> | grep -i "quota\|forbidden\|exceeded"

# Check LimitRange defaults (affects even pods without explicit requests)
kubectl describe limitrange -n <namespace>
```

### Fix Steps
1. **Clean up unused resources**:
   ```bash
   # Delete completed/failed pods
   kubectl delete pods -n <namespace> \
     --field-selector=status.phase=Succeeded
   kubectl delete pods -n <namespace> \
     --field-selector=status.phase=Failed

   # Delete unused ConfigMaps/Secrets
   kubectl get cm -n <namespace>
   kubectl delete cm <unused-cm> -n <namespace>
   ```

2. **Raise the quota limit** (if legitimately needed):
   ```bash
   kubectl edit resourcequota <quota-name> -n <namespace>
   # Increase specific limits:
   # e.g., requests.cpu: "10", limits.memory: 20Gi
   ```
   Or apply a new quota:
   ```yaml
   apiVersion: v1
   kind: ResourceQuota
   metadata:
     name: ns-quota
     namespace: <namespace>
   spec:
     hard:
       pods: "50"
       requests.cpu: "10"
       requests.memory: 20Gi
       limits.cpu: "20"
       limits.memory: 40Gi
       persistentvolumeclaims: "20"
   ```

3. **Fix pods missing resource requests** (quota counts even 0 as needing limits):
   ```yaml
   resources:
     requests:
       cpu: "100m"
       memory: "128Mi"
   # Without this, LimitRange defaults apply and may consume quota unexpectedly
   ```

### Verify
```bash
kubectl describe resourcequota -n <namespace>
# Used < Hard for all resources

kubectl apply -f <new-resource.yaml>
# Should succeed without quota error
```

---

## 44. PodDisruptionBudget Blocking Drains

### Symptoms
- `kubectl drain` hangs or returns `Cannot evict pod as it would violate the pod's disruption budget`
- Node maintenance is blocked
- Rolling update stalls because pods can't be evicted

### Diagnosis
```bash
# Find which PDB is blocking
kubectl get pdb --all-namespaces
kubectl describe pdb <pdb-name> -n <namespace>

# Key fields in PDB:
# minAvailable: N    → drain blocked if current available = N
# maxUnavailable: N  → drain blocked if unavailable >= N
# disruptionsAllowed: 0 → NO eviction allowed right now

# Check how many pods are running vs required
kubectl get pods -n <namespace> -l <pdb-selector> | grep Running | wc -l

# Find which pods on the draining node are covered by PDB
kubectl get pods -n <namespace> -o wide | grep <draining-node>
kubectl get pods -n <namespace> -l <pdb-label-selector> -o wide

# Check if pods can be rescheduled elsewhere
kubectl get nodes   # are other nodes available and not cordoned?
```

### Fix Steps
1. **Scale up deployment before draining** — give PDB room to evict:
   ```bash
   kubectl scale deployment <name> -n <namespace> --replicas=3
   # Wait for new pods to be Running and Ready
   kubectl rollout status deployment <name> -n <namespace>
   # Now drain should work
   kubectl drain <node-name> --ignore-daemonsets
   ```

2. **Temporarily patch the PDB** to allow disruptions:
   ```bash
   # Change minAvailable to lower value
   kubectl patch pdb <pdb-name> -n <namespace> \
     -p '{"spec":{"minAvailable": 0}}'

   # Perform the drain
   kubectl drain <node-name> --ignore-daemonsets

   # Restore PDB after drain
   kubectl patch pdb <pdb-name> -n <namespace> \
     -p '{"spec":{"minAvailable": 1}}'
   ```

3. **Force eviction** (last resort — risks violating availability SLA):
   ```bash
   kubectl drain <node-name> --ignore-daemonsets --disable-eviction
   # This bypasses PDB entirely — use with caution
   ```

4. **Fix the PDB definition** if it's too strict for your cluster size:
   ```yaml
   spec:
     # For 2-replica deployment, minAvailable:2 makes drain IMPOSSIBLE
     # Fix:
     maxUnavailable: 1   # allows 1 pod down = drain works
     selector:
       matchLabels:
         app: myapp
   ```

### Verify
```bash
kubectl drain <node-name> --ignore-daemonsets --dry-run
# Should succeed without PDB violation error

kubectl get pdb --all-namespaces
# disruptionsAllowed > 0 on all PDBs during maintenance

kubectl get node <node-name>
# SchedulingDisabled (cordoned) = drain successful
```

---

## 45. Cluster Autoscaler Not Scaling

### Symptoms
- Pods stuck in `Pending` but cluster autoscaler doesn't add nodes
- `kubectl describe pod` shows `Insufficient cpu/memory` but no new nodes appear
- Or: cluster autoscaler adds nodes but pods still don't schedule

### Diagnosis
```bash
# Check cluster autoscaler pod
kubectl get pods -n kube-system | grep cluster-autoscaler
kubectl logs -n kube-system <autoscaler-pod> --tail=200

# Common log messages:
# "Pod ... is unschedulable"       → autoscaler sees it
# "Scale up in group X"            → scaling triggered
# "max node provision time reached"→ node took too long
# "No candidates for scale-up"     → pods don't match any node group
# "Waiting for instances to warm"  → scaling in progress

# Check autoscaler events
kubectl get events -n kube-system | grep -i autoscal

# Check cluster autoscaler configmap (if using)
kubectl get configmap cluster-autoscaler-status -n kube-system -o yaml | \
  grep -A20 "Status\|ScaleUp\|ScaleDown"

# Verify node groups / autoscaling groups are configured
kubectl describe deployment cluster-autoscaler -n kube-system | grep -A10 args

# Check if pod has resource requests (autoscaler won't scale for pods without requests)
kubectl get pod <pending-pod> -n <namespace> \
  -o jsonpath='{.spec.containers[*].resources}'

# Check if node group has capacity left (cloud provider limits)
# AWS: check ASG desired/max in AWS console
```

### Fix Steps
1. **Pod has no resource requests** — autoscaler ignores these:
   ```yaml
   resources:
     requests:
       cpu: "100m"      # ← required for autoscaler to calculate
       memory: "128Mi"
   ```

2. **Node group limits reached** — increase max node count:
   ```bash
   # AWS EKS example — update ASG max
   aws autoscaling update-auto-scaling-group \
     --auto-scaling-group-name <asg-name> \
     --max-size 10

   # RKE2 with cluster autoscaler — update Cluster resource
   kubectl edit cluster <cluster-name> -n fleet-default
   ```

3. **Node selector / affinity prevents scheduling on new nodes**:
   ```bash
   # Check if new nodes from autoscaler will have required labels
   kubectl get nodes --show-labels | grep <required-label>
   # New nodes may not have custom labels until bootstrapped
   ```

4. **Scale-down is removing nodes too aggressively** — tune scale-down:
   ```bash
   kubectl edit deployment cluster-autoscaler -n kube-system
   # Add/adjust args:
   # --scale-down-delay-after-add=10m
   # --scale-down-unneeded-time=5m
   # --skip-nodes-with-local-storage=false
   ```

5. **Autoscaler service account missing permissions**:
   ```bash
   kubectl auth can-i create nodes --as=system:serviceaccount:kube-system:cluster-autoscaler
   # If no → fix the ClusterRole
   ```

### Verify
```bash
# Watch autoscaler logs during scale-up
kubectl logs -n kube-system <autoscaler-pod> -f | grep -i "scale up\|adding"

# Watch new nodes appear
kubectl get nodes -w

# Watch pending pods get scheduled
kubectl get pods -n <namespace> -w
# Pending → ContainerCreating → Running

# Check autoscaler status
kubectl get configmap cluster-autoscaler-status -n kube-system -o yaml
```

---

*Previous → [Part 8 — Cluster Upgrades](./part8-cluster-upgrades.md)*

---

## Quick Reference Index

| Part | Topics | File |
|------|--------|------|
| 1 | OOMKilled, CPU throttling, CrashLoopBackOff, ImagePullBackOff, Pending (resources) | [part1-pod-resource-failures.md](./part1-pod-resource-failures.md) |
| 2 | Taints/tolerations, Node affinity, Liveness probe, Readiness probe, Init containers | [part2-pod-scheduling-health-checks.md](./part2-pod-scheduling-health-checks.md) |
| 3 | NotReady node, DiskPressure, MemoryPressure, PIDPressure, containerd crash | [part3-node-level-failures.md](./part3-node-level-failures.md) |
| 4 | CNI failure, CoreDNS, No endpoints, NetworkPolicy, Ingress 502s | [part4-networking.md](./part4-networking.md) |
| 5 | Expired certs, kubelet cert, Cert rotation, SA tokens, RBAC 403 | [part5-certificates-auth.md](./part5-certificates-auth.md) |
| 6 | etcd quorum, etcd disk, API server HA, Scheduler stuck, Controller-manager stuck | [part6-control-plane-etcd.md](./part6-control-plane-etcd.md) |
| 7 | PVC Pending, Volume attach, Multi-attach RWO, StatefulSet mount, CSI driver | [part7-storage.md](./part7-storage.md) |
| 8 | Version skew, Upgrade order, Drain mistakes, Rollback, CRD/API deprecation | [part8-cluster-upgrades.md](./part8-cluster-upgrades.md) |
| 9 | Metrics-server/HPA, Log gaps, Quota exhaustion, PDB blocking, Autoscaler | [part9-observability-misc.md](./part9-observability-misc.md) |
