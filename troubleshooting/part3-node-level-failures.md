# Part 3 — Node-Level Failures

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 11. NotReady Node (kubelet Down)

### Symptoms
- `kubectl get nodes` shows `NotReady` for one or more nodes
- Pods on that node eventually get evicted (after ~5m by default)
- `kubectl describe node` shows `KubeletNotReady` or stale heartbeat

### Diagnosis
```bash
# Identify NotReady nodes
kubectl get nodes

# Describe the node — look at Conditions section
kubectl describe node <node-name> | grep -A30 Conditions

# Check last heartbeat time
kubectl get node <node-name> \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].lastHeartbeatTime}'

# SSH into the node
ssh <node-user>@<node-ip>

# Check kubelet service status (RKE2)
sudo systemctl status rke2-agent   # worker node
sudo systemctl status rke2-server  # control-plane node

# Read kubelet logs
sudo journalctl -u rke2-agent -n 100 --no-pager
# or
sudo journalctl -u rke2-server -n 100 --no-pager

# Check kubelet is listening
sudo netstat -tlnp | grep 10250
```

### Fix Steps
1. **Restart kubelet (RKE2 agent/server)**:
   ```bash
   # Worker node
   sudo systemctl restart rke2-agent

   # Control-plane node
   sudo systemctl restart rke2-server

   # Verify it comes up
   sudo systemctl status rke2-agent
   ```

2. **Check and fix common kubelet failure causes**:
   ```bash
   # Disk full — clear up space
   df -h
   sudo journalctl --vacuum-size=500M
   sudo crictl rmi --prune   # remove unused images

   # Certificate expired — see Part 5 scenario 21
   sudo ls -la /var/lib/rancher/rke2/agent/serving-kubelet.crt

   # kubelet config corrupt
   sudo cat /var/lib/rancher/rke2/agent/kubelet.kubeconfig
   ```

3. **Node completely unreachable** — reboot if safe:
   ```bash
   sudo reboot
   # After reboot, RKE2 services start automatically if enabled
   ```

### Verify
```bash
kubectl get nodes -w
# NotReady → Ready (takes ~30-60s after kubelet restarts)
kubectl get pods -n kube-system -o wide | grep <node-name>
```

---

## 12. DiskPressure Eviction

### Symptoms
- Node condition shows `DiskPressure=True`
- Pods evicted with reason `Evicted` and message `disk usage exceeds eviction threshold`
- New pods can't be scheduled on this node

### Diagnosis
```bash
# Confirm DiskPressure condition
kubectl describe node <node-name> | grep -A5 DiskPressure

# SSH into node
ssh <user>@<node-ip>

# Check disk usage
df -h
du -sh /var/lib/rancher/rke2/*   # RKE2 data
du -sh /var/log/*
du -sh /tmp/*

# Check container runtime storage usage
sudo crictl images | sort -k4 -h
sudo crictl ps -a | grep -v Running  # stopped containers

# Check ephemeral storage per pod
kubectl get pods -n <namespace> \
  -o custom-columns="NAME:.metadata.name,EPHEMERAL:.status.containerStatuses[0].allocatedResources"
```

### Fix Steps
1. **Clean up unused container images**:
   ```bash
   sudo crictl rmi --prune
   # Or using RKE2's built-in garbage collection (triggered automatically)
   ```

2. **Clean up logs and temp files**:
   ```bash
   sudo journalctl --vacuum-size=500M --vacuum-time=7d
   sudo find /tmp -mtime +7 -delete
   sudo find /var/log -name "*.gz" -mtime +7 -delete
   ```

3. **Evict and reschedule problem pods** with high ephemeral storage:
   ```bash
   kubectl drain <node-name> --ignore-daemonsets --delete-emissary-on-pod-failure
   ```

4. **Long-term fix** — set ephemeral storage limits on pods:
   ```yaml
   resources:
     limits:
       ephemeral-storage: "1Gi"
   ```

5. **Expand disk** if it's genuinely undersized (cloud volume resize or attach additional disk).

### Verify
```bash
# On node
df -h   # Usage should be below eviction threshold (default 85%)

# From cluster
kubectl describe node <node-name> | grep DiskPressure
# DiskPressure: False

kubectl get node <node-name>
# STATUS: Ready
```

---

## 13. MemoryPressure Eviction

### Symptoms
- Node condition `MemoryPressure=True`
- Pods getting evicted with `The node was low on resource: memory`
- `kubectl get events` shows `Evicting` events from kubelet

### Diagnosis
```bash
# Confirm MemoryPressure
kubectl describe node <node-name> | grep -A5 MemoryPressure

# SSH into node
ssh <user>@<node-ip>

# Check memory usage
free -h
cat /proc/meminfo | grep -E "MemTotal|MemAvailable|MemFree|Cached|SwapTotal"

# Which processes are using the most memory
ps aux --sort=-%mem | head -20

# Check kubelet eviction thresholds (RKE2 default: memory.available < 100Mi)
sudo cat /var/lib/rancher/rke2/agent/etc/config.yaml 2>/dev/null | grep eviction
# or
sudo ps aux | grep kubelet | grep eviction

# Per-pod memory usage on this node
kubectl top pods --all-namespaces --sort-by=memory | grep <node-name>
```

### Fix Steps
1. **Immediate — cordon node and drain** to stop new pods scheduling:
   ```bash
   kubectl cordon <node-name>
   kubectl drain <node-name> --ignore-daemonsets --grace-period=30
   ```

2. **Identify and fix the memory hog** (see Part 1 scenario 1 for OOM details):
   ```bash
   kubectl top pods --all-namespaces --sort-by=memory
   # Kill/restart the biggest consumers
   kubectl delete pod <pod-name> -n <namespace>
   ```

3. **Adjust kubelet eviction threshold** (RKE2 — add to kubelet args):
   ```yaml
   # /etc/rancher/rke2/config.yaml on the node
   kubelet-arg:
   - "eviction-hard=memory.available<200Mi"
   - "eviction-soft=memory.available<500Mi"
   - "eviction-soft-grace-period=memory.available=60s"
   ```
   Then restart: `sudo systemctl restart rke2-agent`

4. **Add swap** as a safety buffer (Kubernetes 1.28+ supports swap with feature gate):
   ```bash
   sudo fallocate -l 4G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

### Verify
```bash
kubectl describe node <node-name> | grep MemoryPressure
# MemoryPressure: False

kubectl uncordon <node-name>
kubectl get nodes
```

---

## 14. PIDPressure Eviction

### Symptoms
- Node condition `PIDPressure=True`
- New pods fail to start with fork errors
- Kubelet logs show `pid pressure`

### Diagnosis
```bash
# Confirm PIDPressure condition
kubectl describe node <node-name> | grep -A5 PIDPressure

# SSH into node
ssh <user>@<node-ip>

# Check current PID count
cat /proc/sys/kernel/pid_max       # system max
ps aux | wc -l                     # current count

# Find PID-heavy processes
ps aux | awk '{print $1}' | sort | uniq -c | sort -rn | head -20

# Check per-container PID count (cgroupv2)
cat /sys/fs/cgroup/kubepods.slice/kubepods-burstable.slice/pod<uid>/pids.current

# Kubelet's pid eviction threshold (default: pid.available < 1000)
sudo journalctl -u rke2-agent | grep -i pid
```

### Fix Steps
1. **Find the PID-leaking pod and kill/restart it**:
   ```bash
   # Identify which pod is forking excessively
   kubectl top pods --all-namespaces
   # Or check crictl
   sudo crictl ps | head -20
   kubectl delete pod <leaking-pod> -n <namespace>
   ```

2. **Set PID limit per pod**:
   ```yaml
   spec:
     containers:
     - name: app
       # No native PID limit in pod spec — use kubelet config
   ```

   Set per-node PID limit via kubelet (RKE2):
   ```yaml
   # /etc/rancher/rke2/config.yaml
   kubelet-arg:
   - "pod-max-pids=1000"
   ```

3. **Increase system PID limit** (short-term):
   ```bash
   sudo sysctl -w kernel.pid_max=4194304
   sudo sysctl -w kernel.threads-max=4194304
   # Persist:
   echo "kernel.pid_max=4194304" | sudo tee -a /etc/sysctl.d/99-k8s.conf
   sudo sysctl -p /etc/sysctl.d/99-k8s.conf
   ```

### Verify
```bash
kubectl describe node <node-name> | grep PIDPressure
# PIDPressure: False
ps aux | wc -l   # PID count should be reasonable
kubectl get nodes
```

---

## 15. containerd / CRI-O Runtime Crash

### Symptoms
- Pods stuck in `ContainerCreating` on a specific node
- `kubectl describe pod` shows `runtime: rpc error`
- kubelet logs show CRI endpoint errors

### Diagnosis
```bash
# SSH into affected node
ssh <user>@<node-ip>

# Check containerd status (RKE2 bundles its own)
sudo systemctl status rke2-agent
sudo /var/lib/rancher/rke2/bin/crictl --runtime-endpoint \
  unix:///run/k3s/containerd/containerd.sock ps

# RKE2 uses embedded containerd — check its socket
ls -la /run/k3s/containerd/containerd.sock

# containerd logs
sudo journalctl -u rke2-agent -n 200 --no-pager | grep -i "containerd\|cri\|error"

# Test CRI directly
sudo crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock info

# Check for disk issues (containerd metadata DB)
du -sh /var/lib/rancher/rke2/agent/containerd/

# Check for corrupted metadata
sudo /var/lib/rancher/rke2/bin/ctr --address /run/k3s/containerd/containerd.sock \
  namespaces list
```

### Fix Steps
1. **Restart the runtime via RKE2 agent restart**:
   ```bash
   sudo systemctl restart rke2-agent
   sudo systemctl status rke2-agent
   ```

2. **containerd metadata corrupted** — clear and restart:
   ```bash
   # CAUTION: This removes all container state on the node — drain first!
   kubectl drain <node-name> --ignore-daemonsets --force

   sudo systemctl stop rke2-agent
   sudo rm -rf /var/lib/rancher/rke2/agent/containerd/io.containerd.metadata.v1.bolt/meta.db
   sudo systemctl start rke2-agent
   ```

3. **Disk full causing containerd write failure** — free disk space (see scenario 12), then restart.

4. **Socket permission issue**:
   ```bash
   ls -la /run/k3s/containerd/containerd.sock
   # Should be owned by root
   sudo chown root:root /run/k3s/containerd/containerd.sock
   ```

### Verify
```bash
# On node
sudo crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock ps

# From cluster
kubectl get pods -n kube-system -o wide | grep <node-name>
# Pods should move out of ContainerCreating

kubectl uncordon <node-name>
kubectl get nodes
```

---

*Previous → [Part 2 — Pod Scheduling & Health Checks](./part2-pod-scheduling-health-checks.md)*  
*Next → [Part 4 — Networking](./part4-networking.md)*
