# Part 2 — Pod Scheduling & Health Checks

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 6. Pending — Taints / Tolerations Mismatch

### Symptoms
- Pod stuck in `Pending`
- `kubectl describe` events say `N node(s) had untolerated taint`

### Diagnosis
```bash
# See the exact taint rejection message
kubectl describe pod <pod-name> -n <namespace> | grep -A20 Events

# List all node taints across the cluster
kubectl get nodes -o custom-columns=\
"NAME:.metadata.name,TAINTS:.spec.taints"

# Or per node
kubectl describe node <node-name> | grep Taints

# Check what tolerations the pod has
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.spec.tolerations}' | jq
```

### Fix Steps
1. **Add the missing toleration** to the pod spec / deployment:
   ```yaml
   spec:
     tolerations:
     - key: "node-role.kubernetes.io/control-plane"
       operator: "Exists"
       effect: "NoSchedule"
     # Or for a custom taint:
     - key: "dedicated"
       value: "gpu"
       operator: "Equal"
       effect: "NoSchedule"
   ```

2. **Remove the taint** from the node (if the taint was added by mistake):
   ```bash
   # Taint format: key=value:effect-   (trailing dash removes it)
   kubectl taint node <node-name> dedicated=gpu:NoSchedule-
   ```

3. **Apply toleration to all pods** via namespace default — add to a MutatingWebhook or patch the deployment directly.

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
kubectl get pod <pod-name> -n <namespace> -o wide
# Node should be assigned
```

---

## 7. Pending — Node Affinity / Anti-Affinity Conflicts

### Symptoms
- Pod stuck `Pending`
- Events say `didn't match Pod's node affinity/selector`
- Or: `pod has unbound immediate PersistentVolumeClaims` combined with zone mismatch

### Diagnosis
```bash
# Read events — look for affinity message
kubectl describe pod <pod-name> -n <namespace> | tail -30

# Check the pod's affinity rules
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.spec.affinity}' | jq

# List node labels to compare with requiredDuringScheduling rules
kubectl get nodes --show-labels

# Check if any node matches the required label selector
kubectl get nodes -l <key>=<value>
# Should return at least one node; if 0 → no match → Pending

# For anti-affinity: check which nodes already host the conflicting pods
kubectl get pods -n <namespace> -o wide | grep <conflicting-app-label>
```

### Fix Steps
1. **Label a node** to satisfy `requiredDuringSchedulingIgnoredDuringExecution`:
   ```bash
   kubectl label node <node-name> disktype=ssd
   ```

2. **Relax required affinity to preferred** (less strict):
   ```yaml
   affinity:
     nodeAffinity:
       # Change from requiredDuringScheduling... to:
       preferredDuringSchedulingIgnoredDuringExecution:
       - weight: 100
         preference:
           matchExpressions:
           - key: disktype
             operator: In
             values: ["ssd"]
   ```

3. **Fix anti-affinity spreading** — if pods can't spread because there are fewer nodes than replicas:
   ```yaml
   topologySpreadConstraints:
   - maxSkew: 1
     topologyKey: kubernetes.io/hostname
     whenUnsatisfiable: ScheduleAnyway   # relax from DoNotSchedule
     labelSelector:
       matchLabels:
         app: myapp
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
kubectl describe pod <pod-name> -n <namespace> | grep -i affinity
```

---

## 8. Failed Liveness Probe (False Restarts)

### Symptoms
- Pod keeps restarting even though the app is fine
- Exit code is `137` (killed by probe) or `0`
- Restarts correlate with traffic spikes or GC pauses

### Diagnosis
```bash
# Confirm liveness probe is causing restarts
kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Liveness"
# Look for: "Liveness probe failed" in Events

# Check probe config — timeoutSeconds, failureThreshold, initialDelaySeconds
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.spec.containers[0].livenessProbe}' | jq

# Manually simulate the probe from inside the pod
kubectl exec <pod-name> -n <namespace> -- \
  curl -sf http://localhost:<port><path>
# or for exec probe:
kubectl exec <pod-name> -n <namespace> -- <probe-command>

# Check if it's a slow-start problem (initialDelaySeconds too short)
kubectl logs <pod-name> -n <namespace> | grep -i "started\|listening\|ready"
```

### Fix Steps
1. **Increase timeouts and thresholds** — common fix for GC pauses:
   ```yaml
   livenessProbe:
     httpGet:
       path: /healthz
       port: 8080
     initialDelaySeconds: 60     # ← give app time to start
     periodSeconds: 20
     timeoutSeconds: 10          # ← increase for slow responses
     failureThreshold: 3         # ← allow 3 failures before kill
   ```

2. **Use startupProbe** for slow-starting apps (prevents liveness from firing too early):
   ```yaml
   startupProbe:
     httpGet:
       path: /healthz
       port: 8080
     failureThreshold: 30
     periodSeconds: 10
   # liveness won't start until startupProbe succeeds
   ```

3. **Verify the probe endpoint is lightweight** — if it does DB queries, it can time out under load. Switch to a simple `/ping` endpoint.

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# Restarts should stop
kubectl describe pod <pod-name> -n <namespace> | grep -i "restart\|liveness"
```

---

## 9. Failed Readiness Probe (Traffic Sent to Unready Pod)

### Symptoms
- Users get `502 Bad Gateway` or `connection reset`
- Pod shows `0/1 Ready` but status is `Running`
- Service endpoints don't include this pod

### Diagnosis
```bash
# Check readiness probe config and failure messages
kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Readiness"

# Check pod ready condition
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.status.conditions}' | jq

# Verify pod is excluded from service endpoints
kubectl get endpoints <service-name> -n <namespace>
# The pod IP should NOT be listed here if unready — this is correct behavior

# Simulate readiness probe manually
kubectl exec <pod-name> -n <namespace> -- \
  curl -sf http://localhost:<port><readiness-path>
# Non-200 response = probe will fail

# Check what the app is actually returning
kubectl port-forward pod/<pod-name> 9090:<app-port> -n <namespace>
curl -v http://localhost:9090<readiness-path>
```

### Fix Steps
1. **Fix the probe path / port** if misconfigured:
   ```yaml
   readinessProbe:
     httpGet:
       path: /ready    # ← ensure this endpoint exists in the app
       port: 8080
     initialDelaySeconds: 10
     periodSeconds: 5
     failureThreshold: 3
   ```

2. **Fix the app** — the readiness endpoint should return `200` only when the app is truly ready (e.g., DB connection established, cache warmed):
   ```bash
   # Debug why app is returning non-200
   kubectl logs <pod-name> -n <namespace> -f
   kubectl exec <pod-name> -n <namespace> -- env | grep -i db
   ```

3. **If probe is intentionally failing during init** — use `initialDelaySeconds` or `startupProbe` to delay readiness checks.

### Verify
```bash
# Pod should become Ready
kubectl get pod <pod-name> -n <namespace> -w
# Pod IP should appear in endpoints
kubectl get endpoints <service-name> -n <namespace>
```

---

## 10. Init Container Stuck / Failing

### Symptoms
- Pod status shows `Init:0/1` or `Init:CrashLoopBackOff` or `Init:Error`
- Main container never starts
- Pod stays in initialization phase

### Diagnosis
```bash
# See init container status
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.status.initContainerStatuses}' | jq

# Logs from the init container (not main container)
kubectl logs <pod-name> -n <namespace> -c <init-container-name>
# If it crashed:
kubectl logs <pod-name> -n <namespace> -c <init-container-name> --previous

# Describe for events and exit codes
kubectl describe pod <pod-name> -n <namespace>

# Common causes:
# - Init tries to reach a dependency (DB, config service) that isn't ready
# - Wrong command / script path
# - Missing secret or configmap it depends on
# - Network policy blocking init container's outbound call

# Check what secrets/configmaps the init container uses
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.spec.initContainers[0].env}' | jq
```

### Fix Steps
1. **Dependency not ready** — add a retry loop or use a proper wait script:
   ```yaml
   initContainers:
   - name: wait-for-db
     image: busybox
     command: ['sh', '-c',
       'until nc -z db-service 5432; do echo waiting; sleep 2; done']
   ```

2. **Wrong command path** (exit 127):
   ```bash
   # Debug the init container interactively
   kubectl run init-debug --image=<init-image> -it --rm \
     --command -- /bin/sh
   # Verify the command exists in the image
   ```

3. **Missing secret causing env var failure**:
   ```bash
   # Check if the secret exists
   kubectl get secret <secret-name> -n <namespace>
   # If not: create it or fix the secretKeyRef in the pod spec
   ```

4. **Network policy blocking** — temporarily test without network policy or add an egress rule for the init container's target.

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# Init:0/1 → PodInitializing → Running

# Confirm main container is now starting
kubectl logs <pod-name> -n <namespace> -c <main-container-name> -f
```

---

*Previous → [Part 1 — Pod Resource Failures](./part1-pod-resource-failures.md)*  
*Next → [Part 3 — Node-Level Failures](./part3-node-level-failures.md)*
