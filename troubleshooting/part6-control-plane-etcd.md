# Part 6 — Control Plane / etcd

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 26. etcd Quorum Loss

### Symptoms
- API server returns `etcdserver: request timed out` or `context deadline exceeded`
- `kubectl` commands hang or fail
- RKE2 control-plane nodes show majority down (lost >50% of etcd members)

### Diagnosis
```bash
# SSH into a surviving control-plane node
ssh <user>@<control-plane-ip>

# Check etcd member list and health (RKE2 embedded etcd)
sudo /var/lib/rancher/rke2/bin/etcdctl \
  --endpoints https://127.0.0.1:2379 \
  --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert /var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key /var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  member list -w table

# Check endpoint health
sudo /var/lib/rancher/rke2/bin/etcdctl \
  --endpoints https://127.0.0.1:2379 \
  --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert /var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key /var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  endpoint health

# Check etcd logs
sudo journalctl -u rke2-server -n 200 --no-pager | grep -i "etcd\|raft\|quorum\|leader"

# Determine how many nodes are needed for quorum
# 3-node cluster: needs 2 alive
# 5-node cluster: needs 3 alive
```

### Fix Steps
1. **Bring back failed etcd members** — restart rke2-server on each down node:
   ```bash
   sudo systemctl start rke2-server
   # Wait for it to re-join — watch logs
   sudo journalctl -u rke2-server -f | grep -i "raft\|leader\|member"
   ```

2. **Remove a failed member permanently** (if node is dead/replaced):
   ```bash
   # Get the member ID of the dead node from member list
   MEMBER_ID=<id-from-member-list>

   sudo /var/lib/rancher/rke2/bin/etcdctl \
     --endpoints https://127.0.0.1:2379 \
     --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
     --cert /var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
     --key /var/lib/rancher/rke2/server/tls/etcd/server-client.key \
     member remove $MEMBER_ID
   ```

3. **Restore from snapshot** (last resort — quorum unrecoverable):
   ```bash
   # Stop rke2-server on all nodes first
   sudo systemctl stop rke2-server

   # On the node with the snapshot:
   sudo /var/lib/rancher/rke2/bin/etcdctl snapshot restore \
     /path/to/backup.db \
     --data-dir /var/lib/rancher/rke2/server/db/etcd \
     --name <this-node-name>

   sudo systemctl start rke2-server
   ```

### Verify
```bash
sudo /var/lib/rancher/rke2/bin/etcdctl \
  --endpoints https://127.0.0.1:2379 \
  --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert /var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key /var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  endpoint health

kubectl get nodes   # API server should respond again
```

---

## 27. etcd Disk Latency Degrading API Server

### Symptoms
- API server is slow — `kubectl` commands take 10s+ to return
- etcd logs show `took too long to execute` or `slow fdatasync`
- `etcd_disk_wal_fsync_duration_seconds` metric is high (>100ms)

### Diagnosis
```bash
# Check etcd WAL fsync latency (Prometheus query)
# etcd_disk_wal_fsync_duration_seconds_bucket

# On the node — check disk I/O directly
iostat -xz 2 5   # look for %util close to 100% on etcd disk
iotop -o         # see which process is writing most

# Check disk type and speed
cat /sys/block/<device>/queue/rotational   # 0=SSD, 1=HDD
hdparm -t /dev/<etcd-disk>

# etcd DB size (large DB = more writes = more latency)
sudo /var/lib/rancher/rke2/bin/etcdctl \
  --endpoints https://127.0.0.1:2379 \
  --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert /var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key /var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  endpoint status -w table
# Check DB SIZE column

# Check for disk sharing with other workloads
lsblk   # is etcd on the same disk as OS/logs?
```

### Fix Steps
1. **Compact and defragment etcd** (reduces DB size):
   ```bash
   # Get current revision
   REV=$(sudo /var/lib/rancher/rke2/bin/etcdctl \
     --endpoints https://127.0.0.1:2379 \
     --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
     --cert /var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
     --key /var/lib/rancher/rke2/server/tls/etcd/server-client.key \
     endpoint status -w json | jq '.[0].Status.header.revision')

   # Compact to current revision
   sudo /var/lib/rancher/rke2/bin/etcdctl \
     --endpoints https://127.0.0.1:2379 \
     --cacert ... --cert ... --key ... \
     compact $REV

   # Defragment all members
   sudo /var/lib/rancher/rke2/bin/etcdctl \
     --endpoints https://127.0.0.1:2379 \
     --cacert ... --cert ... --key ... \
     defrag --cluster
   ```

2. **Move etcd to a dedicated SSD**:
   ```bash
   # Stop rke2-server
   sudo systemctl stop rke2-server

   # Move data dir to faster disk
   sudo mv /var/lib/rancher/rke2/server/db /mnt/fast-ssd/rke2-db
   sudo ln -s /mnt/fast-ssd/rke2-db /var/lib/rancher/rke2/server/db

   sudo systemctl start rke2-server
   ```

3. **Tune etcd for disk** — raise heartbeat interval on slow disks:
   ```yaml
   # Not directly configurable in RKE2 yet — use etcd-arg flag
   # in /etc/rancher/rke2/config.yaml (if supported by version)
   etcd-arg:
   - "heartbeat-interval=200"
   - "election-timeout=2000"
   ```

4. **Reduce API server write pressure** — audit and limit frequent updates:
   ```bash
   # Find objects being updated most frequently
   kubectl get events --all-namespaces --sort-by=lastTimestamp | tail -30
   ```

### Verify
```bash
# Watch wal fsync drop
iostat -xz 1 | grep <etcd-disk>

# etcd should report healthy with low latency
sudo /var/lib/rancher/rke2/bin/etcdctl ... endpoint status -w table
kubectl get nodes   # should respond quickly
```

---

## 28. API Server Unavailable (HA Failover)

### Symptoms
- `kubectl` returns `dial tcp: connect: connection refused` or times out
- VIP/load balancer not forwarding to healthy API server
- One control-plane node is down in an HA setup

### Diagnosis
```bash
# Check API server health on each control plane directly
for ip in <cp1-ip> <cp2-ip> <cp3-ip>; do
  echo "=== $ip ===";
  curl -sk https://$ip:6443/healthz;
  echo;
done

# Check which node holds the VIP (if using keepalived)
ip addr show | grep <vip>
# Only the MASTER keepalived node should show this

# Check keepalived status
sudo systemctl status keepalived
sudo journalctl -u keepalived -n 50 --no-pager

# Check API server static pod (kubeadm) or rke2-server process
sudo systemctl status rke2-server
sudo crictl pods --name kube-apiserver

# Check if it's a leader election issue (API server multi-instance)
kubectl get lease -n kube-system kube-apiserver-legacy-service-account-token-cleaner

# Check load balancer health if using external LB
# curl directly to each backend
```

### Fix Steps
1. **Restart RKE2 server on the failed node**:
   ```bash
   sudo systemctl restart rke2-server
   sudo journalctl -u rke2-server -f
   # Wait for "Node registered" or similar message
   ```

2. **Fix keepalived VIP failover**:
   ```bash
   # Check priority on each node
   sudo grep priority /etc/keepalived/keepalived.conf

   # Manually trigger failover on current master
   sudo systemctl stop keepalived   # drops VIP
   sudo systemctl start keepalived  # re-elects with correct priority
   ```

3. **If API server certificate doesn't include new VIP** — add SAN and rotate (see scenario 23).

4. **External load balancer** — remove the unhealthy backend from the LB target group until it recovers.

### Verify
```bash
curl -sk https://<vip>:6443/healthz
# {"status":"ok"}

kubectl get nodes
# All nodes visible and Ready
```

---

## 29. Scheduler Not Scheduling (Leader Election Stuck)

### Symptoms
- New pods stay `Pending` indefinitely
- Existing pods unaffected
- No scheduling events in `kubectl describe pod`
- `kubectl get pods -n kube-system | grep scheduler` shows scheduler pod `Running` but nothing is scheduled

### Diagnosis
```bash
# Check scheduler pod
kubectl get pods -n kube-system | grep scheduler
kubectl logs -n kube-system <scheduler-pod> --tail=100

# Check scheduler leader election lease
kubectl get lease kube-scheduler -n kube-system
kubectl describe lease kube-scheduler -n kube-system
# Look at acquireTime and renewTime — if stale, leader is stuck

# Check if scheduler is actually healthy
kubectl get componentstatus   # deprecated but useful
# Or:
curl -sk https://<api-server>:10259/healthz   # scheduler health

# RKE2 — check the scheduler static pod
sudo crictl pods --name kube-scheduler
sudo crictl logs <scheduler-container-id> 2>&1 | tail -50
```

### Fix Steps
1. **Restart the scheduler pod** (static pod — delete and let kubelet recreate):
   ```bash
   # Static pod — can't kubectl delete it permanently
   # For kubeadm:
   sudo mv /etc/kubernetes/manifests/kube-scheduler.yaml /tmp/
   sleep 5
   sudo mv /tmp/kube-scheduler.yaml /etc/kubernetes/manifests/

   # For RKE2:
   sudo systemctl restart rke2-server
   ```

2. **Force leader lease release**:
   ```bash
   kubectl delete lease kube-scheduler -n kube-system
   # New leader will be elected automatically
   ```

3. **Check for resource constraints on the scheduler pod itself**:
   ```bash
   kubectl describe pod <scheduler-pod> -n kube-system | grep -A5 Limits
   # If throttled — static pod resources may need tuning (kubeadm only, edit manifest)
   ```

### Verify
```bash
kubectl get lease kube-scheduler -n kube-system
# renewTime should be updating every few seconds

# Create a test pod
kubectl run test-sched --image=nginx --restart=Never -n default
kubectl get pod test-sched -w
# Should move to Running quickly

kubectl delete pod test-sched
```

---

## 30. Controller-Manager Stuck

### Symptoms
- Deployments not scaling (replica count wrong)
- ReplicaSets not creating pods
- Jobs/CronJobs not triggering
- `kubectl rollout status` hangs

### Diagnosis
```bash
# Check controller-manager pod
kubectl get pods -n kube-system | grep controller-manager
kubectl logs -n kube-system <controller-manager-pod> --tail=100 | grep -E "error|Error|panic"

# Check leader election
kubectl get lease kube-controller-manager -n kube-system
kubectl describe lease kube-controller-manager -n kube-system
# renewTime should update every few seconds

# Check if specific controllers are broken
kubectl get events --all-namespaces --sort-by=lastTimestamp | \
  grep -i "controller\|failed\|error" | tail -30

# Check API server connectivity from controller-manager
kubectl logs -n kube-system <controller-manager-pod> | grep -i "timeout\|refused\|context"

# Check if it's a specific reconciliation loop that's stuck
kubectl logs -n kube-system <controller-manager-pod> | grep "reconcile\|sync" | tail -30
```

### Fix Steps
1. **Restart the controller-manager**:
   ```bash
   # Static pod (kubeadm):
   sudo mv /etc/kubernetes/manifests/kube-controller-manager.yaml /tmp/
   sleep 5
   sudo mv /tmp/kube-controller-manager.yaml /etc/kubernetes/manifests/

   # RKE2:
   sudo systemctl restart rke2-server
   ```

2. **Release stuck leader lease**:
   ```bash
   kubectl delete lease kube-controller-manager -n kube-system
   ```

3. **Fix API server connectivity issues** — if controller-manager can't reach API server:
   ```bash
   # Check kubeconfig used by controller-manager
   # kubeadm:
   sudo cat /etc/kubernetes/controller-manager.conf | grep server

   # Ensure API server is healthy
   curl -sk https://127.0.0.1:6443/healthz
   ```

4. **Resource quota causing deployment reconciliation to fail**:
   ```bash
   kubectl describe resourcequota -n <namespace>
   # Controller can't create pods if quota is hit
   kubectl get events -n <namespace> | grep quota
   ```

### Verify
```bash
kubectl get lease kube-controller-manager -n kube-system
# renewTime updating continuously

# Test reconciliation
kubectl scale deployment <name> -n <namespace> --replicas=3
kubectl get pods -n <namespace> -w
# 3 pods should appear
```

---

*Previous → [Part 5 — Certificates & Auth](./part5-certificates-auth.md)*  
*Next → [Part 7 — Storage](./part7-storage.md)*
