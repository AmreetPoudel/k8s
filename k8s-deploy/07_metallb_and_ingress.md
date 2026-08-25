# 07. MetalLB Layer-2 LoadBalancer & NGINX Ingress Controller

> **Technology**: MetalLB (Layer-2 ARP Mode) + NGINX Ingress Controller  
> **IP Pool**: `10.0.1.200 - 10.0.1.220` (21 External Floating Load Balancer IPs)  
> **Execution Location**: Run commands from `master-1` using `kubectl` and `helm`.

---

## Step 1: Deploy MetalLB via Helm

### 🎯 The Commands:
```bash
# Add official MetalLB repository
helm repo add metallb https://metallb.github.io/metallb
helm repo update

# Install MetalLB
helm install metallb metallb/metallb \
  --namespace metallb-system \
  --create-namespace
```

### ❓ Why are we doing this?
In bare-metal and on-premise Nutanix environments, Kubernetes does **NOT** have AWS NLB or Google Cloud LoadBalancer integrations. Services of `type: LoadBalancer` get stuck permanently in `<pending>` without an external IP. MetalLB solves this by responding to Layer-2 ARP requests on your physical enterprise network, giving bare-metal Kubernetes true cloud-native LoadBalancer capability.

---

## Step 2: Configure MetalLB IP Address Pool & Layer-2 Advertisement

### 🎯 The Command:
```bash
cat <<EOF | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: production-public-pool
  namespace: metallb-system
spec:
  addresses:
    - 10.0.1.200-10.0.1.220
  autoAssign: true
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: l2-advertisement
  namespace: metallb-system
spec:
  ipAddressPools:
    - production-public-pool
EOF
```

### 🔍 How MetalLB Layer-2 ARP Works:
1. When a `LoadBalancer` service is created, MetalLB assigns an IP from the pool (e.g. `10.0.1.200`).
2. One of the worker nodes acts as the **Speaker** and sends Gratuitous ARP packets to the physical network switch:  
   *"IP `10.0.1.200` has MAC address `50:6b:8d:xx:yy:zz` (Worker 1)"*.
3. External traffic hitting `10.0.1.200` is delivered directly to Worker 1's physical NIC.

---

## Step 3: Deploy NGINX Ingress Controller

### 🎯 The Commands:
```bash
# Add NGINX Ingress repository
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

# Deploy NGINX Ingress with Client-IP Preservation
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.replicaCount=2 \
  --set controller.service.externalTrafficPolicy=Local \
  --set controller.metrics.enabled=true \
  --set controller.service.annotations."metallb\.universe\.tf/address-pool"=production-public-pool
```

---

### ⚠️ Critical Architecture: Why `externalTrafficPolicy=Local` is Mandatory

```
[ CLIENT: 192.168.1.50 ]
           │
           ▼
[ WORKER 1: Ingress Pod Running ] ──► (Traffic served locally) ──► Client IP PRESERVED (192.168.1.50)!
           │
           │ (If externalTrafficPolicy was 'Cluster')
           ▼
[ WORKER 2: Cross-Node SNAT ] ──► Client IP replaced with Worker-1 Node IP (10.0.2.10) ❌ (Security Logs Ruined!)
```

* **`externalTrafficPolicy: Cluster` (Default)**: Kubernetes performs Source NAT (SNAT), replacing the real client's IP address with the worker node's internal IP. Your access logs and rate-limiting rules will never see real user IPs!
* **`externalTrafficPolicy: Local` (Production)**: Bypasses SNAT completely, preserving the true client IP address for security, audit compliance, and geo-blocking!

---

## ✅ Step 4: Verification & Test Ingress Application

```bash
# 1. Verify Ingress Controller acquired an IP from MetalLB (e.g. 10.0.1.200)
kubectl get svc -n ingress-nginx ingress-nginx-controller

# 2. Deploy a Test App with Ingress
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-web-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: demo-web
  template:
    metadata:
      labels:
        app: demo-web
    spec:
      containers:
      - name: web
        image: hashicorp/http-echo
        args: ["-text=Kubernetes Ingress & MetalLB is working perfectly!"]
        ports:
        - containerPort: 5678
---
apiVersion: v1
kind: Service
metadata:
  name: demo-web-svc
spec:
  selector:
    app: demo-web
  ports:
  - port: 80
    targetPort: 5678
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: demo-web-ingress
  annotations:
    ingress.kubernetes.io/ssl-redirect: "false"
spec:
  ingressClassName: nginx
  rules:
  - host: demo.internal.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: demo-web-svc
            port:
              number: 80
EOF

# 3. Test HTTP routing through MetalLB VIP
INGRESS_IP=$(kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl -H "Host: demo.internal.local" http://$INGRESS_IP/

# Output MUST print:
# "Kubernetes Ingress & MetalLB is working perfectly!"
```

Once Ingress routing is verified, proceed to **[08_monitoring_and_gitops.md](file:///Users/amritpoudel/k8s-rke2/k8s-deploy/08_monitoring_and_gitops.md)**!
