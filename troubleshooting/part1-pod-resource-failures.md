# Part 1 — Pod Resource Failures

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 1. OOMKilled (Memory Limit Exceeded)

### Symptoms
- Pod keeps restarting with exit code `137`
- `kubectl get pod` shows `OOMKilled` in the REASON column
- Restart count increments rapidly

### Diagnosis
```bash
# Confirm OOMKilled and exit code
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.status.containerStatuses[*].lastState.terminated}'

# Current memory usage vs limits
kubectl top pod <pod-name> -n <namespace> --containers

# Full describe — look at Limits and Last State
kubectl describe pod <pod-name> -n <namespace>

# Cluster-level OOM events
kubectl get events -n <namespace> --field-selector reason=OOMKilling

# On the node — kernel OOM log
sudo dmesg | grep -i "oom\|killed process" | tail -30

# Watch memory trend to detect leak
kubectl top pod <pod-name> -n <namespace> -w
```

### Fix Steps
1. **Raise the memory limit** (if usage is legitimate growth):
   ```yaml
   resources:
     requests:
       memory: "256Mi"
     limits:
       memory: "512Mi"   # ← increase this
   ```

2. **Detect memory leak** before raising limits:
   ```bash
   # Java — heap dump
   kubectl exec <pod> -- jcmd 1 VM.native_memory summary

   # Go — pprof heap
   kubectl exec <pod> -- curl -s localhost:6060/debug/pprof/heap > heap.out
   go tool pprof heap.out
   ```

3. Apply corrected manifest:
   ```bash
   kubectl apply -f deployment.yaml
   # or patch in-place
   kubectl set resources deployment/<name> -n <namespace> --limits=memory=512Mi
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# Restarts should stop; status → Running
kubectl top pod <pod-name> -n <namespace> --containers
```

---

## 2. CPU Throttling (CFS Quota / cpu.stat throttled_time)

### Symptoms
- Application latency spikes intermittently while pod stays `Running`
- Metrics show high `container_cpu_cfs_throttled_seconds_total`
- `kubectl top` shows CPU well under limit yet latency is high

### Diagnosis
```bash
# Check CPU limits on the pod
kubectl describe pod <pod-name> -n <namespace> | grep -A5 Limits

# Current CPU usage
kubectl top pod <pod-name> -n <namespace> --containers

# Get container ID on the node
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.status.containerStatuses[0].containerID}'

# Read cpu.stat on the node (cgroupv2)
# Replace <pod-uid> and <container-id> with values from above
cat /sys/fs/cgroup/kubepods.slice/kubepods-burstable.slice/pod<pod-uid>/<container-id>/cpu.stat
# Look for: nr_throttled and throttled_usec — high = actively throttled

# Prometheus query (if available)
# rate(container_cpu_cfs_throttled_seconds_total{pod="<pod-name>"}[5m])
```

### Fix Steps
1. **Raise or remove CPU limit:**
   ```yaml
   resources:
     requests:
       cpu: "250m"
     limits:
       cpu: "1000m"   # ← raise; or delete limits line for Burstable QoS
   ```

2. **Scale horizontally** instead of giving one pod more CPU:
   ```bash
   kubectl scale deployment <name> -n <namespace> --replicas=3
   ```

3. Apply:
   ```bash
   kubectl apply -f deployment.yaml
   ```

### Verify
```bash
# Watch throttled_usec drop
watch -n2 'cat /sys/fs/cgroup/kubepods.slice/.../cpu.stat | grep throttled'

# Latency should normalize; no more throttle events
```

---

## 3. CrashLoopBackOff (Exit Code Interpretation)

### Symptoms
- Pod status shows `CrashLoopBackOff`
- Restart count climbing; exponential back-off delay (10s → 20s → 40s … 300s max)
- App never reaches `Running` or immediately exits

### Diagnosis
```bash
# Get exit code of last termination
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.status.containerStatuses[0].lastState.terminated.exitCode}'

# Read logs from the crashed container
kubectl logs <pod-name> -n <namespace> --previous

# Full event timeline
kubectl describe pod <pod-name> -n <namespace>
```

**Exit code reference:**

| Code | Meaning | Action |
|------|---------|--------|
| `1` | App error | Read `--previous` logs, fix app config |
| `137` | OOMKilled (SIGKILL) | See scenario 1 |
| `139` | Segfault (SIGSEGV) | Bad binary / init logic |
| `143` | SIGTERM — app didn't handle graceful shutdown | Add trap / preStop hook |
| `126` | Permission denied on entrypoint | Fix file permissions in image |
| `127` | Command not found | Fix `command`/`args` in pod spec |

### Fix Steps
1. **Debug interactively** — override entrypoint:
   ```bash
   kubectl run debug-pod --image=<same-image> -it --rm \
     --command -- /bin/sh
   ```

2. **Fix bad entrypoint** (exit 126/127):
   ```yaml
   spec:
     containers:
     - command: ["/bin/sh", "-c"]
       args: ["exec /app/start.sh"]
   ```

3. **Handle SIGTERM** (exit 143):
   ```yaml
   lifecycle:
     preStop:
       exec:
         command: ["/bin/sh", "-c", "sleep 5"]
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
kubectl logs <pod-name> -n <namespace> -f
```

---

## 4. ImagePullBackOff (Registry Auth / Tag / Rate Limits)

### Symptoms
- Pod status shows `ImagePullBackOff` or `ErrImagePull`
- `kubectl describe` shows `Failed to pull image` in Events

### Diagnosis
```bash
# See exact pull error
kubectl describe pod <pod-name> -n <namespace> | grep -A15 Events

# Common messages:
# "unauthorized"          → auth issue
# "manifest unknown"      → bad tag or image doesn't exist
# "toomanyrequests"       → Docker Hub rate limit
# "connection refused"    → private registry unreachable

# Check the image reference in use
kubectl get pod <pod-name> -n <namespace> \
  -o jsonpath='{.spec.containers[*].image}'

# Inspect pull secret
kubectl get secret <secret-name> -n <namespace> \
  -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq

# Test registry reachability from a node
kubectl debug node/<node-name> -it --image=alpine -- \
  wget -qO- https://<registry-host>/v2/
```

### Fix Steps
1. **Auth failure** — create imagePullSecret:
   ```bash
   kubectl create secret docker-registry regcred \
     --docker-server=<registry> \
     --docker-username=<user> \
     --docker-password=<token> \
     -n <namespace>

   # Attach to deployment
   kubectl patch deployment <name> -n <namespace> \
     -p '{"spec":{"template":{"spec":{"imagePullSecrets":[{"name":"regcred"}]}}}}'
   ```

2. **Wrong tag** — fix and redeploy:
   ```bash
   kubectl set image deployment/<name> <container>=<image>:<correct-tag> -n <namespace>
   ```

3. **Docker Hub rate limit** — add auth for public images too:
   ```bash
   kubectl create secret docker-registry dockerhub-creds \
     --docker-server=https://index.docker.io/v1/ \
     --docker-username=<user> \
     --docker-password=<token> \
     -n <namespace>
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# Pending → ContainerCreating → Running
```

---

## 5. Pending Pod — Insufficient Resources vs Scheduling Constraints

### Symptoms
- Pod stuck in `Pending` indefinitely
- No node assigned in `kubectl get pod -o wide`

### Diagnosis
```bash
# First stop — describe pod, read events
kubectl describe pod <pod-name> -n <namespace> | tail -40

# "Insufficient cpu/memory"  → resource exhaustion
# "0/N nodes are available"  → nothing schedulable

# Check all nodes resource usage
kubectl top nodes

# Check allocatable vs requested per node
kubectl describe nodes | grep -A8 "Allocated resources"

# Check for cordoned nodes
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,STATUS:.status.conditions[-1].type,UNSCHEDULABLE:.spec.unschedulable

# Check namespace ResourceQuota
kubectl describe resourcequota -n <namespace>

# Check LimitRange defaults
kubectl describe limitrange -n <namespace>
```

### Fix Steps
1. **Resource exhaustion** — reduce requests or add node:
   ```bash
   # Reduce over-provisioned requests
   kubectl set resources deployment/<name> -n <namespace> \
     --requests=cpu=100m,memory=128Mi

   # RKE2 — add a worker node by running agent join on new VM
   ```

2. **Quota exhausted** — clean up or raise quota:
   ```bash
   # Find and delete unused pods
   kubectl get pods -n <namespace> --field-selector=status.phase=Succeeded
   kubectl delete pods --field-selector=status.phase=Succeeded -n <namespace>

   # Raise quota (edit the ResourceQuota object)
   kubectl edit resourcequota <name> -n <namespace>
   ```

3. **Node cordoned** — uncordon:
   ```bash
   kubectl uncordon <node-name>
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# Pending → ContainerCreating → Running
kubectl get pod <pod-name> -n <namespace> -o wide
# Node field should now be populated
```

---

*Next → [Part 2 — Pod Scheduling & Health Checks](./part2-pod-scheduling-health-checks.md)*
