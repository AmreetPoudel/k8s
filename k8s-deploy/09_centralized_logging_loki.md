# 09. Centralized Logging Pipeline (`Grafana Loki` & `Promtail`)

> **Components**: Grafana Loki (Log Aggregation & Indexing), Promtail (Log Shipper DaemonSet)  
> **Storage Backend**: Longhorn 3-Way Synchronous Replicated Block Storage (`longhorn-replicated`)  
> **Delivery Model**: Fully Declarative GitOps via ArgoCD (`manifests/06-logging/`)  
> **Integration**: Native DataSource wiring into `kube-prometheus-stack` (Grafana)

---

## 1. The Enterprise Sysadmin ➔ Cloud-Native Logging Bridge

| Enterprise Sysadmin Concept | Modern Kubernetes Equivalent |
| :--- | :--- |
| **Log Agent (`rsyslogd` / `syslog-ng`)** | **`Promtail` DaemonSet** (Runs on every master and worker node). |
| **Syslog Receiver / SIEM (Graylog / Splunk)** | **`Grafana Loki`** (Indexes labels and compresses log chunks onto Longhorn storage). |
| **Log File Locations (`/var/log/messages`, `/var/log/nginx/`)** | **`/var/log/pods/<ns>_<pod>_<uid>/<container>/0.log`** managed by `containerd`. |
| **Search Syntax (grep, awk, sed, regex)** | **`LogQL`** (Log Query Language inside Grafana). |
| **Log Rotation (`logrotate.d`)** | **Loki Table Manager & Retention Period** (`retention_period: 168h` / 7 days). |

---

## 2. End-to-End Container Logging Architecture

```
   [ Control Plane / Master Nodes ]                  [ Workload Worker Nodes ]
 ┌──────────────────────────────────┐             ┌──────────────────────────────────┐
 │ • kube-apiserver   • etcd        │             │ • Application Pods • Ingress     │
 │ • keepalived       • rke2-server │             │ • MetalLB Speakers • Longhorn    │
 └────────────────┬─────────────────┘             └────────────────┬─────────────────┘
                  │ (stdout / stderr)                              │ (stdout / stderr)
                  ▼                                                ▼
 ┌──────────────────────────────────┐             ┌──────────────────────────────────┐
 │ Host Path: /var/log/pods/*       │             │ Host Path: /var/log/pods/*       │
 └────────────────┬─────────────────┘             └────────────────┬─────────────────┘
                  │                                                │
                  ▼                                                ▼
 ┌──────────────────────────────────┐             ┌──────────────────────────────────┐
 │ Promtail DaemonSet (Master Pods) │             │ Promtail DaemonSet (Worker Pods) │
 │ (Tolerates master/control-plane) │             │ (Auto-discovers Pod metadata)    │
 └────────────────┬─────────────────┘             └────────────────┬─────────────────┘
                  │                                                │
                  └───────────────┬────────────────────────────────┘
                                  │ HTTP POST (Port 3100 /loki/api/v1/push)
                                  ▼
                   ┌──────────────────────────────┐
                   │    Grafana Loki Service      │
                   │  (logging-stack-loki:3100)   │
                   └──────────────┬───────────────┘
                                  │
                   ┌──────────────┴───────────────┐
                   ▼                              ▼
    ┌─────────────────────────────┐┌──────────────────────────────┐
    │  Inverted Label Index       ││ Compressed Stream Chunks     │
    │  (namespace, pod, container)││ (10 GiB Longhorn PVC 3-Way)  │
    └─────────────────────────────┘└──────────────────────────────┘
                                  ▲
                                  │ LogQL Queries
                   ┌──────────────┴───────────────┐
                   │   Grafana Dashboard / UI     │
                   │  (grafana.internal.local)    │
                   └──────────────────────────────┘
```

---

## 3. Declarative GitOps Configuration

All logging resources are managed declaratively under `manifests/06-logging/00-logging-stack-app.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: logging-stack
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://grafana.github.io/helm-charts
    chart: loki-stack
    targetRevision: 2.10.2
    helm:
      values: |
        loki:
          enabled: true
          persistence:
            enabled: true
            storageClassName: longhorn-replicated
            accessModes:
              - ReadWriteOnce
            size: 10Gi
          config:
            table_manager:
              retention_deletes_enabled: true
              retention_period: 168h # 7 days retention
            limits_config:
              retention_period: 168h
              max_query_length: 721h
              max_entries_limit_per_query: 5000
          nodeSelector:
            node-role.kubernetes.io/worker: worker

        promtail:
          enabled: true
          config:
            clients:
              - url: http://logging-stack-loki.logging.svc.cluster.local:3100/loki/api/v1/push
          tolerations:
            - key: node-role.kubernetes.io/control-plane
              operator: Exists
              effect: NoSchedule
            - key: node-role.kubernetes.io/master
              operator: Exists
              effect: NoSchedule

        grafana:
          enabled: false # Managed in 04-monitoring

        prometheus:
          enabled: false # Managed in 04-monitoring
  destination:
    server: https://kubernetes.default.svc
    namespace: logging
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

---

## 4. Key Architectural Decisions Explained

### A. Why Promtail Needs Tolerations
In Kubernetes, control-plane nodes have taints (`node-role.kubernetes.io/control-plane:NoSchedule` and `node-role.kubernetes.io/master:NoSchedule`) preventing regular workloads from running on them. By adding matching **tolerations** to Promtail:
* Promtail is scheduled on **all 6 nodes** (3 masters + 3 workers).
* Logs from core cluster infrastructure (`kube-apiserver`, `etcd`, `rke2-server`, `keepalived`) are collected alongside application workloads.

### B. Longhorn 3-Way Synchronous Persistence
* Loki's TSDB index and compressed log chunks are written to a `10Gi` PersistentVolume.
* Backed by `longhorn-replicated`, every log chunk written by Loki is synchronously replicated to 3 distinct physical worker disks. If the worker hosting Loki crashes, Kubernetes recreates the pod on another worker, reattaches the volume, and zero historical log data is lost.

### C. Native Grafana Data Source Wiring
In `manifests/04-monitoring/00-monitoring-app.yaml`, Loki is automatically registered in Grafana via `additionalDataSources`:
```yaml
additionalDataSources:
  - name: Loki
    type: loki
    access: proxy
    url: http://logging-stack-loki.logging.svc.cluster.local:3100
    isDefault: false
    jsonData:
      maxLines: 1000
```

---

## 5. LogQL Practical Query Playbook

Once logged into Grafana (`http://grafana.internal.local`), navigate to **Explore** and select the **Loki** data source:

### 1. View Logs by Namespace
```logql
{namespace="kube-system"}
```

### 2. Stream Specific Pod Logs
```logql
{namespace="argocd", container="argocd-server"}
```

### 3. Case-Insensitive Filter for Errors
```logql
{namespace="ingress-nginx"} (?i)error
```

### 4. Exclude Healthcheck Noise
```logql
{namespace="ingress-nginx"} != "GET /healthz" != "kube-probe"
```

### 5. Parse JSON Structured Logs
```logql
{namespace="production"} | json | status >= 500
```

### 6. Calculate Log Error Rate per Minute (Metric Query)
```logql
sum(rate({namespace="ingress-nginx"} |= "502"[1m])) by (pod)
```

---

## 6. Cluster Verification Commands

```bash
# 1. Check all logging pods are running (1 Loki + 6 Promtail DaemonSet pods)
kubectl get pods -n logging -o wide

# 2. Check Longhorn PVC is Bound
kubectl get pvc -n logging

# 3. Check Promtail logs to ensure HTTP 200 pushes to Loki
kubectl logs -n logging -l app.kubernetes.io/name=promtail --tail=50

# 4. Check Loki logs to ensure chunks are flushing to storage
kubectl logs -n logging -l app.kubernetes.io/name=loki --tail=50
```

---

## 7. Real-World Troubleshooting & Gotchas

### 1. `Entry out of order` / `Entry too far behind`
* **Symptom**: Promtail fails to push logs with `400 Bad Request: entry out of order`.
* **Root Cause**: NTP clock skew between cluster nodes, or log lines arriving with historical timestamps older than the configured buffer.
* **Fix**: Ensure `chrony` or `systemd-timesyncd` is synchronized across all bare-metal VMs, and set `reject_old_samples_max_age: 168h` in Loki limits.

### 2. `Max query length exceeded`
* **Symptom**: Grafana returns `query exceeds maximum configured query length`.
* **Fix**: Increase `max_query_length` in Loki `limits_config` (e.g., `721h` / 30 days).

### 3. `Permission denied on /var/log/pods`
* **Symptom**: Promtail cannot read log files on worker nodes.
* **Root Cause**: Linux file permissions or SELinux/AppArmor restricting hostPath volume mounts.
* **Fix**: Ensure Promtail container runs with `securityContext.privileged: true` or `readOnlyRootFilesystem: false` on systems with strict mandatory access controls.
