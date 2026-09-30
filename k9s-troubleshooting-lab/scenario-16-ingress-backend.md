# Scenario 16 — API Gateway: Ingress 404 After Domain Migration

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Complete Traffic Loss)**
> All external traffic has stopped reaching the cluster. `api-gateway` Ingress was updated 5 minutes ago during a domain migration. Browser shows `404 Not Found` from the Ingress controller. Pods and Services appear healthy.

**Your job:** Triage, find the broken link in the traffic chain, fix it.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
  namespace: lab
spec:
  replicas: 2
  selector:
    matchLabels:
      app: api-gateway
  template:
    metadata:
      labels:
        app: api-gateway
    spec:
      containers:
      - name: api
        image: hashicorp/http-echo:latest
        args: ["-text=API Gateway Response", "-listen=:8080"]
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: "50m"
            memory: "64Mi"
          limits:
            cpu: "100m"
            memory: "128Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: api-gateway-svc
  namespace: lab
spec:
  selector:
    app: api-gateway
  ports:
  - port: 80
    targetPort: 8080
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: api-gateway-ingress
  namespace: lab
spec:
  rules:
  - host: api.company.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: api-gateway-wrong-svc
            port:
              number: 80
EOF
```

---

## Questions to Answer Before Looking at Solution

1. Run `kubectl get all -n lab` — pods and service healthy?
2. `kubectl get all` does NOT show Ingress. What command shows it?
3. Run `kubectl describe ingress api-gateway-ingress -n lab`. Look at **Rules → Backends**. What do you see?
4. Compare the backend service name vs what exists in `kubectl get svc -n lab`. What is different?
5. What is an Ingress Controller and how is it different from an Ingress resource?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The Ingress backend points to `api-gateway-wrong-svc` but the actual Service is named `api-gateway-svc`. The Ingress controller cannot find the backend service, so it returns 404 for all requests.

### Smoking Gun

```bash
kubectl describe ingress api-gateway-ingress -n lab
```
```
Rules:
  Host             Path  Backends
  ----             ----  --------
  api.company.com
                   /   api-gateway-wrong-svc:80 (<none>)
```

`<none>` in the Backends column = Ingress controller cannot find the service. Endpoint IPs are empty.

After fix it shows:
```
api-gateway-svc:80 (10.42.4.59:8080, 10.42.3.49:8080)
```

Real Pod IPs populated = traffic can flow.

### Fix

```bash
kubectl patch ingress api-gateway-ingress -n lab --type=json \
  -p='[{"op":"replace","path":"/spec/rules/0/http/paths/0/backend/service/name","value":"api-gateway-svc"}]'
```

Or `kubectl edit ingress api-gateway-ingress -n lab` and fix the service name.

### The Full Traffic Chain

```
Browser → api.company.com
              │
              ▼
     Ingress Controller (nginx pod running in cluster)
              │  reads Ingress rules from API server
              │  host: api.company.com → backend: api-gateway-svc:80
              ▼
     Service: api-gateway-svc (ClusterIP)
              │
              ▼
     Endpoints: 10.42.4.59:8080, 10.42.3.49:8080
              │
              ▼
     Pods: api-gateway containers
```

Break any single link → entire chain fails.

### Ingress Resource vs Ingress Controller

| | Ingress Resource | Ingress Controller |
|-|-----------------|-------------------|
| **What it is** | A Kubernetes config object (routing rules) | A pod running nginx/Traefik that reads rules |
| **Is it a CRD?** | No — built into `networking.k8s.io/v1` | It's a Deployment/DaemonSet |
| **Without the other?** | Rules exist but nothing implements them | Nothing to read or route |
| **Examples** | `kubectl apply -f ingress.yaml` | nginx-ingress, Traefik, HAProxy |

### Key Lesson

When Ingress returns 404:
1. Check pods → healthy?
2. Check service → exists?
3. Check endpoints → populated?
4. `kubectl describe ingress` → look at Backends column — is it `<none>` or showing real IPs?
5. Compare backend service name in Ingress vs actual service names in namespace

`kubectl get all` does NOT show Ingress. Always check:
```bash
kubectl get ingress -n <namespace>
kubectl describe ingress <name> -n <namespace>
```

</details>
