# 07. MetalLB Layer-2 LoadBalancer & NGINX Ingress Controller

> **Technology**: MetalLB (Layer-2 ARP Mode) + NGINX Ingress Controller  
> **IP Pool**: `10.0.2.56 - 10.0.2.59` (Floating Load Balancer IPs)  
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
    - 10.0.2.56-10.0.2.59
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

## ✅ Step 4: Verification & Test Ingress Application

```bash
# 1. Verify Ingress Controller acquired an IP from MetalLB (e.g. 10.0.2.56)
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
