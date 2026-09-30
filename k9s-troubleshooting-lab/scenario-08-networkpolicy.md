# Scenario 08 — Notification Service: Complete Network Blackout

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Complete Service Blackout)**
> Email and SMS notifications have completely stopped. The `notification-service` pods show healthy and running with 0 restarts. Engineers report the service cannot reach any external API, internal microservice, or even resolve DNS. Networking appears completely broken for this service only — other pods in the same namespace work fine.

**Your job:** Triage, find root cause, fix, and verify stability.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: lab
data:
  DB_HOST: "postgres-db.lab.svc.cluster.local"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notification-service
  namespace: lab
  labels:
    app: notification-service
spec:
  replicas: 1
  selector:
    matchLabels:
      app: notification-service
  template:
    metadata:
      labels:
        app: notification-service
    spec:
      containers:
      - name: app
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "Notification service started..."
          while true; do
            echo "--- DNS check ---"
            nslookup kubernetes.default.svc.cluster.local
            sleep 10
          done
        envFrom:
        - configMapRef:
            name: app-config
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: notification-lockdown
  namespace: lab
spec:
  podSelector:
    matchLabels:
      app: notification-service
  policyTypes:
  - Ingress
  - Egress
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What does `kubectl get pods -n lab` show? Is the pod Running?
2. What does `kubectl logs -n lab -l app=notification-service` show?
3. **Critical:** The service is trying to resolve `kubernetes.default.svc.cluster.local`. This is a built-in Kubernetes service that **ALWAYS exists**. Why is even this failing?
4. What resource type is NOT visible in `kubectl get all`?
5. What is the root cause and fix?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

A `NetworkPolicy` named `notification-lockdown` was applied to the `notification-service` pod. It declares both `Ingress` and `Egress` as `policyTypes` but defines **zero rules** under either type.

**The Silent Killer Rule:** When you declare a policyType with no rules, Kubernetes blocks **100% of that traffic type**. This includes DNS queries (port 53 UDP/TCP to CoreDNS) — so the pod cannot resolve any hostname, not even built-in cluster ones.

### Smoking Gun

```bash
kubectl get networkpolicy -n lab
# notification-lockdown   notification-service   3m

kubectl describe networkpolicy notification-lockdown -n lab
```
```
Allowing ingress traffic:
  <none> (Selected pods are isolated for ingress connectivity)
Allowing egress traffic:
  <none> (Selected pods are isolated for egress connectivity)
```

`<none>` on Egress = ALL outbound traffic blocked, including DNS.

```bash
# Logs confirm:
kubectl logs -n lab -l app=notification-service
# ;; connection timed out; no servers could be reached   ← DNS blocked
```

vs after fix:
```
# NXDOMAIN = DNS works, but the domain doesn't exist
```

`timeout` = blocked by NetworkPolicy. `NXDOMAIN` = DNS works, service just doesn't exist.

### Fix — Allow DNS Egress at Minimum

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: notification-lockdown
  namespace: lab
spec:
  podSelector:
    matchLabels:
      app: notification-service
  policyTypes:
  - Ingress
  - Egress
  egress:
  - ports:
    - port: 53
      protocol: UDP
    - port: 53
      protocol: TCP
EOF
```

### NetworkPolicy Rules

| Default state | Effect |
|--------------|--------|
| No NetworkPolicy on pod | Pod can talk to everyone freely (all namespaces) |
| NetworkPolicy applied (Egress declared, no rules) | ALL outbound blocked |
| NetworkPolicy applied with explicit egress rules | Only allowed traffic passes |

### Key Lesson

`kubectl get all` does NOT show NetworkPolicies. Always check explicitly:
```bash
kubectl get networkpolicy -n <namespace>
```

When a pod has perfect health (Running, 0 restarts) but cannot reach anything, always suspect NetworkPolicy.

The error `connection timed out; no servers could be reached` for a DNS query = **NetworkPolicy or firewall is dropping the packet silently.**

</details>
