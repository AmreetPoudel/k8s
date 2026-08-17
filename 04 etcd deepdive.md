# Doc 04: etcd Deep Dive
## The Only Stateful Part of Your Cluster

> If etcd dies, Kubernetes dies. If etcd corrupts, Kubernetes corrupts.  
> Understanding etcd is understanding your cluster's source of truth.

---

## 4.1 What etcd Is

etcd is a **distributed key-value store** built specifically for storing distributed system configuration data.

It's not a database in the traditional sense. It:
- Stores key-value pairs (keys are paths like `/registry/pods/default/my-pod`)
- Guarantees **strong consistency** (all clients always see the same data)
- Is optimized for **small writes, lots of reads**
- Uses the **Raft consensus algorithm** to replicate across nodes

Every Kubernetes object you create ends up as a key-value entry in etcd:
```
/registry/pods/default/nginx-pod-abc123  → <serialized Pod protobuf>
/registry/services/default/kubernetes    → <serialized Service protobuf>
/registry/deployments/production/web     → <serialized Deployment protobuf>
```

💡 **Interview**: *"Where does Kubernetes store its state?"*  
→ "In etcd, a distributed key-value store. Every Kubernetes object — pods, deployments, services, secrets, configmaps — is serialized to protobuf and stored as a key under /registry/<resource-type>/<namespace>/<name>. etcd is the only stateful component in Kubernetes. The API server is the only component that directly reads/writes etcd. All other components (scheduler, controllers) read from the API server's cache (watch mechanism), not directly from etcd."

---

## 4.2 The Raft Consensus Algorithm

Raft solves a fundamental distributed systems problem: **how do multiple servers agree on the same value, even when some fail?**

### The Problem Without Raft

If you just replicate writes to 3 servers and one fails mid-write:
```
Write "x=5" to 3 servers:
  Server 1: x=5 ✓
  Server 2: x=5 ✓  
  Server 3: CRASHED during write  → x=4 (old value) or corrupted

Now what's the true value of x?
→ You don't know. Split brain.
```

### How Raft Solves It

Raft elects one **leader**. ALL writes go to the leader. The leader replicates to followers. Write is only confirmed when a **quorum** (majority) acknowledges it.

```
Write "x=5":
  1. Client → Leader (master-1)
  2. Leader appends to its log (not committed yet)
  3. Leader sends log entry to all followers
  4. Wait for quorum (2 of 3 in a 3-node cluster) to acknowledge
  5. Leader marks entry as COMMITTED
  6. Leader responds success to client
  7. Followers learn the entry is committed on next heartbeat
```

Even if master-3 crashes after step 3, steps 4-6 succeed because master-1 + master-2 = quorum.

### Raft Leader Election

```
Normal state:
  master-1: LEADER (sends heartbeats every 100ms)
  master-2: FOLLOWER (receives heartbeats)
  master-3: FOLLOWER (receives heartbeats)

master-1 crashes / network partition:
  master-2 and master-3 stop receiving heartbeats
  After election timeout (150-300ms), master-2 becomes CANDIDATE
  master-2 increments term number (say, term 2)
  master-2 requests votes from master-3
  master-3 grants vote (master-2's log is at least as up-to-date)
  master-2 now has votes from: itself + master-3 = 2 = quorum
  master-2 becomes LEADER for term 2
  master-2 starts sending heartbeats
  master-1 comes back, sees term 2, steps down to FOLLOWER
```

💡 **Interview**: *"What is Raft and why does Kubernetes use it?"*  
→ "Raft is a consensus algorithm that ensures a distributed system agrees on a single consistent view of data, even with node failures. Kubernetes uses it in etcd. etcd elects one leader node that handles all writes. Before confirming a write, the leader waits for a quorum (majority) of followers to acknowledge the entry. This means you can lose any minority of nodes without losing data. With 3 nodes, you can lose 1. With 5 nodes, you can lose 2. This is why the control plane must have an odd number of nodes — to always have a clear majority."

---

## 4.3 Quorum Math — Critical for Architecture Decisions

```
Formula: Quorum = floor(N/2) + 1
         Max failures tolerated = N - Quorum = floor(N/2)

N=1: Quorum=1, can lose 0 nodes (no HA — don't use in production)
N=2: Quorum=2, can lose 0 nodes (worse than 1! Adding a node made it fragile)
N=3: Quorum=2, can lose 1 node ← YOUR SETUP
N=4: Quorum=3, can lose 1 node (same tolerance as 3, more complexity)
N=5: Quorum=3, can lose 2 nodes (next meaningful HA improvement)
N=7: Quorum=4, can lose 3 nodes (max recommended for etcd)
```

⚠️ **NEVER run 2 etcd nodes.** It's more fragile than 1. Both must be up for any write to succeed — you get zero fault tolerance AND the cost of 2 nodes.

⚠️ **NEVER run 4 etcd nodes.** Same fault tolerance as 3 but more complex and more to fail. If you want to tolerate 2 failures, run 5.

💡 **Interview**: *"Why do we run 3 control plane nodes and not 2 or 4?"*  
→ "Because of etcd quorum requirements. With N nodes, you need a majority (floor(N/2)+1) to elect a leader and commit writes. With 2 nodes, you need both online — zero fault tolerance despite having two nodes. With 3 nodes, you need 2 — you can lose 1 node. With 4 nodes, you still only tolerate losing 1 node. So 4 nodes gives you the same fault tolerance as 3 nodes, with more cost and complexity. The next meaningful improvement is 5 nodes, which tolerates losing 2."

---

## 4.4 etcd Data Storage Internals

### The Write-Ahead Log (WAL)

etcd doesn't write directly to its data store. Every write first goes to the **Write-Ahead Log (WAL)**:

```
Write request:
  1. Append entry to WAL file on disk (sequential write — fast)
  2. fdatasync (flush to disk — wait for confirmation)
  3. Replicate to followers via Raft
  4. Get quorum acknowledgment
  5. Apply to in-memory B-tree
  6. Periodically snapshot to disk (compact WAL)
```

The WAL is sequential, so it's fast even on spinning disks. But `fdatasync` forces the disk write to complete before continuing — this is why **disk latency is critical for etcd**. High disk latency = slow writes = Raft timeouts = leader elections.

### Storage Location in RKE2

```bash
# [M1] — etcd data directory
ls /var/lib/rancher/rke2/server/db/etcd/
# member/
#   wal/         ← Write-Ahead Log files
#   snap/        ← Snapshots (compacted WAL)

# Check etcd DB size
du -sh /var/lib/rancher/rke2/server/db/etcd/
```

### etcd DB Size Limits

By default, etcd has a **2GB database size limit** (`--quota-backend-bytes`). When exceeded:
- etcd enters **alarm mode** (read-only)
- API server can't write → kubectl apply fails → `etcdserver: mvcc: database space exceeded`
- Cluster is effectively down

Fix: compact + defragment etcd, then clear the alarm:
```bash
# [M1] — compact and defragment (covered in 4.7)
ETCDCTL_ENDPOINTS=https://127.0.0.1:2379 \
etcdctl --cacert /var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
        --cert /var/lib/rancher/rke2/server/tls/etcd/client.crt \
        --key /var/lib/rancher/rke2/server/tls/etcd/client.key \
        alarm disarm
```

---

## 4.5 Using etcdctl

`etcdctl` is the CLI for etcd. In RKE2, it's bundled at `/var/lib/rancher/rke2/bin/etcdctl`.

### Setup etcdctl Environment

```bash
# [M1] — set up convenience alias
export PATH=$PATH:/var/lib/rancher/rke2/bin
export ETCDCTL_API=3  # Always use v3 API

# etcd requires TLS — create a helper function
etcdctl_cmd() {
  etcdctl \
    --endpoints=https://127.0.0.1:2379 \
    --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
    --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
    --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
    "$@"
}

# Test connection
etcdctl_cmd endpoint health
# Output: https://127.0.0.1:2379 is healthy: ...
```

### Essential etcdctl Commands

```bash
# Check cluster member list
etcdctl_cmd member list
# Output shows: id, status, name, peer URL, client URL, isLearner
# Example:
# abc123, started, master-1, https://10.0.1.10:2380, https://10.0.1.10:2379, false
# def456, started, master-2, https://10.0.1.11:2380, https://10.0.1.11:2379, false

# Check cluster status (which is the leader)
etcdctl_cmd endpoint status --cluster -w table
# Shows leader column — only one should say "true"

# Check endpoint health across all members
etcdctl_cmd endpoint health --cluster
# Output:
# https://10.0.1.10:2379 is healthy
# https://10.0.1.11:2379 is healthy
# https://10.0.1.12:2379 is healthy

# List all Kubernetes keys (the actual data)
etcdctl_cmd get /registry --prefix --keys-only | head -50
# Shows ALL Kubernetes objects stored in etcd

# Get a specific pod's raw data
etcdctl_cmd get /registry/pods/default/nginx-pod --print-value-only | strings | head -30

# Watch for changes (real-time)
etcdctl_cmd watch /registry/pods --prefix
# In another terminal, create a pod — you'll see the entry appear here
```

🔍 **Why is this useful?**  
When `kubectl get pods` returns no results but you're sure pods exist, checking etcd directly confirms whether the issue is in etcd vs. API server vs. kubectl. If data exists in etcd but kubectl shows nothing, the API server has a problem serving the data.

---

## 4.6 etcd Backup

**THE most important operational task.** Do this before and after every cluster change.

### Manual Backup

```bash
# [M1] — create an etcd snapshot
BACKUP_DIR=/opt/etcd-backups
mkdir -p $BACKUP_DIR

etcdctl_cmd snapshot save $BACKUP_DIR/etcd-snapshot-$(date +%Y%m%d-%H%M%S).db

# Verify the snapshot
etcdctl_cmd snapshot status $BACKUP_DIR/etcd-snapshot-*.db -w table
# Shows: hash, revision, total keys, total size
```

### What the Snapshot Contains

The snapshot is a **complete copy of etcd's B-tree database** at a point in time. It contains:
- Every Kubernetes object (pods, deployments, secrets, everything)
- RBAC rules
- ConfigMaps and Secrets
- PV/PVC bindings
- NetworkPolicies

It does NOT contain:
- Actual pod running state (that's in containerd, not etcd)
- Persistent volume data (that's on disk/Longhorn, not etcd)
- Container images

⚠️ **A backup of etcd does NOT back up your application data.** It backs up Kubernetes configuration and object state. Your database running in a pod needs its own backup strategy.

### Automated Backup Script

```bash
# [M1] — create backup script
cat > /usr/local/bin/etcd-backup.sh << 'SCRIPT'
#!/bin/bash
set -e

BACKUP_DIR=/opt/etcd-backups
RETENTION_DAYS=7
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE=$BACKUP_DIR/etcd-snapshot-$TIMESTAMP.db

mkdir -p $BACKUP_DIR

export PATH=$PATH:/var/lib/rancher/rke2/bin
export ETCDCTL_API=3

etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/client.key \
  snapshot save $BACKUP_FILE

# Verify snapshot
etcdctl snapshot status $BACKUP_FILE

# Remove backups older than retention period
find $BACKUP_DIR -name "etcd-snapshot-*.db" -mtime +$RETENTION_DAYS -delete

echo "Backup complete: $BACKUP_FILE"
ls -lh $BACKUP_DIR
SCRIPT

chmod +x /usr/local/bin/etcd-backup.sh

# Schedule with cron (daily at 2 AM)
echo "0 2 * * * root /usr/local/bin/etcd-backup.sh >> /var/log/etcd-backup.log 2>&1" \
  > /etc/cron.d/etcd-backup
```

---

## 4.7 etcd Restore — The Disaster Recovery Procedure

⚠️ **Read this before you need it. During a disaster is the wrong time to learn.**

### When Do You Restore?

- etcd data corruption (hardware failure, filesystem corruption)
- Accidentally deleted critical objects (`kubectl delete ns production`)
- etcd quorum loss (e.g., 2 of 3 masters gone and data corrupted)

### Restore Procedure

```bash
# [ALL-M] — STOP RKE2 on ALL masters first
# This is critical. If any master is running, it will overwrite the restored data.
systemctl stop rke2-server

# [ALL-M] — move aside existing etcd data
mv /var/lib/rancher/rke2/server/db/etcd \
   /var/lib/rancher/rke2/server/db/etcd.backup-$(date +%Y%m%d)

# [M1] — restore the snapshot (only on master-1 first)
export PATH=$PATH:/var/lib/rancher/rke2/bin

etcdctl snapshot restore /opt/etcd-backups/etcd-snapshot-YYYYMMDD-HHMMSS.db \
  --name master-1 \
  --data-dir /var/lib/rancher/rke2/server/db/etcd \
  --initial-cluster "master-1=https://10.0.1.10:2380,master-2=https://10.0.1.11:2380,master-3=https://10.0.1.12:2380" \
  --initial-cluster-token etcd-cluster-1 \
  --initial-advertise-peer-urls https://10.0.1.10:2380

# [M2] — restore the SAME snapshot on master-2
etcdctl snapshot restore /opt/etcd-backups/etcd-snapshot-YYYYMMDD-HHMMSS.db \
  --name master-2 \
  --data-dir /var/lib/rancher/rke2/server/db/etcd \
  --initial-cluster "master-1=https://10.0.1.10:2380,master-2=https://10.0.1.11:2380,master-3=https://10.0.1.12:2380" \
  --initial-cluster-token etcd-cluster-1 \
  --initial-advertise-peer-urls https://10.0.1.11:2380

# [M3] — restore the SAME snapshot on master-3
etcdctl snapshot restore /opt/etcd-backups/etcd-snapshot-YYYYMMDD-HHMMSS.db \
  --name master-3 \
  --data-dir /var/lib/rancher/rke2/server/db/etcd \
  --initial-cluster "master-1=https://10.0.1.10:2380,master-2=https://10.0.1.11:2380,master-3=https://10.0.1.12:2380" \
  --initial-cluster-token etcd-cluster-1 \
  --initial-advertise-peer-urls https://10.0.1.12:2380

# [ALL-M] — start RKE2 (master-1 first, then others)
# On master-1:
systemctl start rke2-server
# Wait 30 seconds for it to be ready, then:
# On master-2:
systemctl start rke2-server
# Wait 30 seconds:
# On master-3:
systemctl start rke2-server
```

🔍 **Why do you restore the same snapshot to all 3 nodes?**  
After restore, each etcd member starts fresh from the same backup point. They use `--initial-cluster` to know who else to connect to and re-form a cluster. If you only restored one node and started all three, the other two would have newer (potentially corrupted) data and win via Raft because they'd claim a higher revision. You must start all three from the same backup state.

### Verify After Restore

```bash
# [M1]
etcdctl_cmd endpoint status --cluster -w table
kubectl get nodes
kubectl get pods -A
```

💡 **Interview**: *"Walk me through an etcd disaster recovery scenario."*  
→ "First, stop all etcd/rke2-server processes on all masters. Restore the same backup snapshot to each master node using `etcdctl snapshot restore` with the correct `--initial-cluster` flags — this creates a fresh etcd member directory from the backup. Start master-1 first, let it establish itself, then join master-2 and master-3. The cluster reforms from the backup state. Any changes after the backup timestamp are lost — that's why daily or hourly backups matter. After recovery, verify cluster health with etcdctl endpoint status, then check that Kubernetes objects look correct."

---

## 4.8 etcd Compaction and Defragmentation

Over time, etcd grows because it keeps historical versions of every key (for watch operations). You must periodically compact old versions.

```bash
# [M1] — get current revision
CURRENT_REVISION=$(etcdctl_cmd endpoint status -w json | jq -r '.[0].Status.header.revision')
echo "Current revision: $CURRENT_REVISION"

# Compact everything before current revision (remove old versions)
etcdctl_cmd compact $CURRENT_REVISION

# Defragment each member (reclaim disk space after compaction)
etcdctl_cmd defrag --cluster

# Check new DB size
etcdctl_cmd endpoint status --cluster -w table | grep -i size
```

⚠️ **Defragmentation takes each etcd member offline briefly.** Do it during low-traffic periods. Defrag one member at a time if you're worried about availability.

---

## 4.9 Monitoring etcd Health

Key metrics to watch (we set up Prometheus in Doc 12, but know these now):

| Metric | What It Means | Alert Threshold |
|--------|---------------|----------------|
| `etcd_server_leader_changes_seen_total` | How often leadership changed | > 3 changes/hour = disk or network issue |
| `etcd_disk_wal_fsync_duration_seconds` | WAL write latency | p99 > 10ms = disk too slow |
| `etcd_disk_backend_commit_duration_seconds` | DB snapshot latency | p99 > 25ms = investigate |
| `etcd_mvcc_db_total_size_in_bytes` | etcd DB size | > 1.5GB = compact soon |
| `etcd_server_proposals_failed_total` | Failed Raft proposals | Any non-zero = investigate |

```bash
# [M1] — quick health check
etcdctl_cmd endpoint health --cluster
etcdctl_cmd endpoint status --cluster -w table
```

---

## 4.10 Summary

You now understand:
- ✅ etcd's role as the only stateful component
- ✅ Raft consensus — leader election, quorum commits
- ✅ Why 3 nodes (not 2, not 4)
- ✅ The WAL and why disk speed matters
- ✅ How to use etcdctl for inspection
- ✅ Full backup and restore procedures
- ✅ Compaction and defragmentation

**Next**: [Doc 05 - CNI & Networking →](./05-cni-networking.md)  
How do pods get IPs? How does traffic flow between pods on different nodes? What is VXLAN? How do Services work at the packet level?

---

*Doc 04 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
