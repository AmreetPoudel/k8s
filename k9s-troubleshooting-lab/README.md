# K9s / Kubectl Troubleshooting War Room Labs

> **Rules:**
> 1. Read the **Scenario** (PagerDuty alert).
> 2. Apply the **Lab YAML** to your cluster.
> 3. Triage and fix it yourself using `kubectl` + `sofka`/`k9s`.
> 4. Only look at **Solution** after you have found the root cause.

---

## Scenarios

| # | Title | Concept |
|---|-------|---------|
| [01](./scenario-01-liveness-probe.md) | Auth service users cannot login | Liveness Probe Misconfiguration |
| [02](./scenario-02-missing-binary.md) | Order processor completely down | Exit Code 127 — Missing Binary |
| [03](./scenario-03-node-affinity.md) | Checkout gateway stuck, no pods | Node Affinity — No Matching Nodes |
| [04](./scenario-04-service-selector.md) | Payment API returning 503 | Service Selector Mismatch / Empty Endpoints |
| [05](./scenario-05-readiness-probe.md) | Inventory API traffic black-holed | Readiness Probe Misconfiguration |
| [06](./scenario-06-init-container.md) | Catalog service stalled at init | Init Container Failure |
| [07](./scenario-07-oomkilled.md) | Report generator keeps crashing | OOMKilled — Memory Leak |
| [08](./scenario-08-networkpolicy.md) | Notification service network dead | NetworkPolicy Blocking All Egress |
| [09](./scenario-09-hpa-no-requests.md) | HPA not scaling under load | HPA Missing CPU Requests |
| [10](./scenario-10-rbac.md) | Audit logger crashing on startup | RBAC — Missing Role/RoleBinding |
| [11](./scenario-11-pvc-storageclass.md) | Postgres database stuck pending | PVC — StorageClass Not Found |
| [12](./scenario-12-resource-quota.md) | Payment worker deployment blocked | ResourceQuota Exhaustion |
| [13](./scenario-13-missing-secret.md) | Billing API crashing at boot | Missing Secret / CreateContainerConfigError |
| [14](./scenario-14-rolling-update.md) | Frontend stuck mid-rollout | Rolling Update Stuck — Bad Image |

---

## Tools Required
- `kubectl` configured to your cluster
- `sofka` or `k9s` (TUI)
- Namespace `lab` created: `kubectl create namespace lab`

## Mental Model (Memorize This)

```
Alert: "Service X is down"
           │
           ▼
kubectl get pods -n <ns>   →  Do pods exist? What STATUS? What RESTARTS?
           │
     ┌─────┴──────┐
  Pods EXIST    Pods MISSING
     │              │
  check LOGS    check DEPLOYMENT / REPLICASET events
  check DESCRIBE POD
           │
     ┌─────┴──────────────┐
  App crashed          Pending / not starting
     │                     │
  Exit Code?           Scheduler issue?
  --previous logs?     Node affinity? Taint? Resources?
```
