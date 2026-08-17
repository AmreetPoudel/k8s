# Doc 12: Monitoring
## Prometheus + Grafana for Your RKE2 Cluster

---

## 12.1 The Monitoring Stack

You need to answer: *"Is my cluster healthy? Is it about to break?"*

The standard Kubernetes monitoring stack:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Monitoring Architecture                       │
│                                                                  │
│  Every component exposes                                         │
│  /metrics endpoint:         Prometheus scrapes → stores TSDB    │
│                                                                  │
│  kubelet :10250/metrics        ──────────────→  Prometheus      │
│  kube-apiserver :6443/metrics  ──────────────→     ↓            │
│  etcd :2379/metrics            ──────────────→  Grafana         │
│  node-exporter :9100/metrics   ──────────────→  (dashboards)    │
│  kube-state-metrics /metrics   ──────────────→     ↓            │
│  your apps /metrics            ──────────────→  Alertmanager    │
│                                                    (alerts)      │
└─────────────────────────────────────────────────────────────────┘
```

Key components:
- **Prometheus**: Scrapes metrics, stores time-series, evaluates alert rules
- **Alertmanager**: Receives alerts from Prometheus, routes to Slack/PagerDuty/email
- **Grafana**: Visualization and dashboards
- **node-exporter**: Exposes Linux node metrics (CPU, mem, disk, network)
- **kube-state-metrics**: Exposes Kubernetes object state (pod counts, deployment status)

---

## 12.2 Install kube-prometheus-stack

The `kube-prometheus-stack` Helm chart installs everything at once.

```bash
# [M1]
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl create namespace monitoring

# Install the full stack
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set grafana.adminPassword=admin123 \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=longhorn \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=20Gi \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.storageClassName=longhorn \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.resources.requests.storage=5Gi

# Watch everything come up (takes 3-5 minutes)
kubectl get pods -n monitoring -w
```

### What Gets Installed

```bash
kubectl get pods -n monitoring
# prometheus-kube-prometheus-stack-prometheus-0     ← Prometheus server
# alertmanager-prometheus-kube-prometheus-stack-alertmanager-0  ← Alertmanager
# prometheus-grafana-XXXX                           ← Grafana
# prometheus-kube-state-metrics-XXXX               ← k8s object metrics
# prometheus-prometheus-node-exporter-XXXX         ← node metrics (DaemonSet)
# prometheus-kube-prometheus-stack-operator-XXXX   ← manages Prometheus config
```

### Access Grafana

```bash
# Port-forward Grafana
kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80

# Open http://localhost:3000
# Login: admin / admin123

# Pre-loaded dashboards include:
# - Kubernetes / Cluster Overview
# - Kubernetes / Nodes
# - Kubernetes / Workloads
# - etcd
# - CoreDNS
```

---

## 12.3 Critical Metrics to Monitor

### Cluster Health Metrics

```promql
# Is the API server up?
up{job="apiserver"}
# Should be 1 for all instances

# etcd leader — should always have exactly 1 leader
etcd_server_is_leader == 1

# How often etcd leadership changes (should be near 0)
increase(etcd_server_leader_changes_seen_total[1h])
# Alert if > 3

# etcd write latency (should be < 10ms p99)
histogram_quantile(0.99, rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])) * 1000
# Alert if > 10ms

# etcd DB size (alert if > 1.5GB)
etcd_mvcc_db_total_size_in_bytes
```

### Node Metrics

```promql
# Node CPU usage percentage
100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)
# Alert if > 85%

# Node memory usage
(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100
# Alert if > 85%

# Disk usage
(node_filesystem_size_bytes{mountpoint="/"} - node_filesystem_free_bytes{mountpoint="/"}) 
/ node_filesystem_size_bytes{mountpoint="/"} * 100
# Alert if > 80%

# Disk write latency (important for etcd nodes)
rate(node_disk_write_time_seconds_total[5m]) / rate(node_disk_writes_completed_total[5m]) * 1000
# Alert if > 20ms on master nodes
```

### Kubernetes Object Metrics

```promql
# Pods NOT Running (excluding Completed)
kube_pod_status_phase{phase!~"Running|Succeeded"} == 1
# Alert if any

# Deployment replicas mismatch
kube_deployment_spec_replicas != kube_deployment_status_available_replicas
# Alert if true for > 5 minutes

# OOMKilled pods
kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1
# Alert on any

# PVC pending (storage not bound)
kube_persistentvolumeclaim_status_phase{phase="Pending"} == 1

# Node NotReady
kube_node_status_condition{condition="Ready", status="true"} == 0
# Alert immediately
```

### Resource Usage vs Limits

```promql
# Pod CPU usage vs request
rate(container_cpu_usage_seconds_total{container!=""}[5m]) / 
on(pod,container) kube_pod_container_resource_requests{resource="cpu"}
# Values > 1 mean pod is using more CPU than requested → node may be overcommitted

# Pod memory usage
container_memory_working_set_bytes{container!=""} /
on(pod,container) kube_pod_container_resource_limits{resource="memory"}
# Values approaching 1 → pod about to be OOMKilled
```

---

## 12.4 Creating Alerts

```bash
# [M1] — create a custom alert rule
cat << 'EOF' | kubectl apply -f -
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: rke2-cluster-alerts
  namespace: monitoring
  labels:
    release: prometheus   # must match your Prometheus release name
spec:
  groups:
  - name: cluster.rules
    interval: 30s
    rules:
    
    # Node down
    - alert: NodeDown
      expr: up{job="node-exporter"} == 0
      for: 2m
      labels:
        severity: critical
      annotations:
        summary: "Node {{ $labels.instance }} is down"
        description: "Node exporter on {{ $labels.instance }} has been down for 2 minutes"
    
    # etcd leader lost
    - alert: EtcdNoLeader
      expr: sum(etcd_server_is_leader) == 0
      for: 1m
      labels:
        severity: critical
      annotations:
        summary: "etcd cluster has no leader"
    
    # Pod CrashLooping
    - alert: PodCrashLooping
      expr: rate(kube_pod_container_status_restarts_total[15m]) * 60 > 0.3
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "Pod {{ $labels.namespace }}/{{ $labels.pod }} is crash looping"
    
    # High memory usage
    - alert: NodeHighMemory
      expr: (node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes > 0.90
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "Node {{ $labels.instance }} memory usage above 90%"
    
    # Certificate expiring
    - alert: CertificateExpiringIn30Days
      expr: apiserver_client_certificate_expiration_seconds_bucket{le="2592000"} > 0
      labels:
        severity: warning
      annotations:
        summary: "Kubernetes client certificate expiring within 30 days"
EOF

# Verify alert rule was picked up
kubectl get prometheusrule -n monitoring
```

---

## 12.5 Configure Alertmanager (Slack)

```bash
# [M1] — configure Alertmanager to send Slack notifications
cat << 'EOF' | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: alertmanager-config
  namespace: monitoring
stringData:
  alertmanager.yaml: |
    global:
      resolve_timeout: 5m
      slack_api_url: 'https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK'
    
    route:
      group_by: ['alertname', 'namespace']
      group_wait: 10s
      group_interval: 5m
      repeat_interval: 12h
      receiver: 'slack-notifications'
      routes:
      - match:
          severity: critical
        receiver: 'slack-critical'
        repeat_interval: 1h
    
    receivers:
    - name: 'slack-notifications'
      slack_configs:
      - channel: '#k8s-alerts'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'
    
    - name: 'slack-critical'
      slack_configs:
      - channel: '#k8s-critical'
        text: '🚨 CRITICAL: {{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'
        title: '{{ .CommonLabels.alertname }}'
EOF
```

---

## 12.6 Key Grafana Dashboards

Once Grafana is running, import these dashboard IDs from grafana.com:

| Dashboard ID | What It Shows |
|-------------|--------------|
| 3119 | Kubernetes Cluster Overview |
| 6417 | Kubernetes Pods |
| 1860 | Node Exporter Full |
| 3070 | etcd |
| 7249 | CoreDNS |
| 13770 | Longhorn Storage |

```bash
# Import via Grafana UI:
# Dashboard → Import → Enter ID → Load → Select datasource (Prometheus) → Import
```

---

## 12.7 Custom Application Metrics

Any app can expose Prometheus metrics. Example in Python:

```python
from prometheus_client import Counter, Histogram, start_http_server

REQUEST_COUNT = Counter('app_requests_total', 'Total requests', ['method', 'endpoint'])
REQUEST_LATENCY = Histogram('app_request_duration_seconds', 'Request latency')

# Expose metrics on port 8000/metrics
start_http_server(8000)
```

Then tell Prometheus to scrape it:
```yaml
# ServiceMonitor — tells Prometheus Operator where to scrape
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: my-app-metrics
  namespace: production
  labels:
    release: prometheus   # must match Prometheus release label
spec:
  selector:
    matchLabels:
      app: my-app
  endpoints:
  - port: metrics          # port name in the Service
    interval: 30s
    path: /metrics
```

---

## 12.8 Summary

You now understand:
- ✅ Monitoring stack architecture (Prometheus, Grafana, node-exporter, kube-state-metrics)
- ✅ kube-prometheus-stack installation
- ✅ Critical PromQL queries for cluster health
- ✅ Creating custom alert rules with PrometheusRule
- ✅ Alertmanager for Slack notifications
- ✅ Key Grafana dashboards to import

**Next**: [Doc 13 - Troubleshooting →](./13-troubleshooting.md)  
When things break — and they will — here's the complete debugging playbook.

---

*Doc 12 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
