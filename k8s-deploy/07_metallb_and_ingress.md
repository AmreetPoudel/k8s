# 07. MetalLB Layer-2 LoadBalancer & NGINX Ingress Controller

> **Technology**: MetalLB (Layer-2 ARP Mode) + NGINX Ingress Controller  
> **IP Pool**: `10.0.2.56 - 10.0.2.59` (Floating Load Balancer IPs)  
> **Execution Location**: Run commands from `master-1` using `kubectl` and `helm`.

---

## Step 1: Deploy MetalLB Native Manifests

### 🎯 The Command:
```bash
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.8/config/manifests/metallb-native.yaml
```

---

## Step 2: Apply Declarative IP Pool & Layer-2 Advertisement

### 🎯 The Commands:
```bash
kubectl apply -f manifests/02-metallb/01-ipaddresspool.yaml
kubectl apply -f manifests/02-metallb/02-l2advertisement.yaml
```

---

## 🧠 Physical Networking Deep Dive: Layer-2 (MAC/ARP) vs Layer-3 (BGP)

### 1. The Fundamental Law of Ethernet: Why MAC is Mandatory
On any physical wire, fiber cable, or virtual switch (Nutanix AHV OVS), **a network switch does NOT understand IP addresses—it ONLY understands MAC addresses.**
* **Layer 3 (IP)**: Used by global internet routers across the world to get packets to your datacenter gateway.
* **Layer 2 (MAC)**: The mandatory physical envelope used by the local datacenter switch to deliver electrical bits to the exact physical network port.

### 2. The 2 Actors of MetalLB:
* **The Controller (1 Pod on Master)**: The Bookkeeper. Watches for `type: LoadBalancer` services and assigns an unused IP from `production-public-pool` (`10.0.2.56`).
* **The Speakers (DaemonSet on all Workers)**: The Shouting Guards. Run with `hostNetwork: true`. When a client or gateway broadcasts an ARP request (`"Who has 10.0.2.56?"`), the elected worker node's speaker responds: `"10.0.2.56 is at MY physical MAC address!"`

### 3. Why `L2Advertisement` is Mandatory:
* `IPAddressPool` only defines **WHAT** IPs exist.
* `L2Advertisement` is the **PERMISSION SWITCH** that authorizes worker node speakers to start answering ARP requests on the switch. It restricts announcements strictly to worker nodes (`node-role.kubernetes.io/worker: worker`), keeping control plane master nodes completely free from application data traffic.

### 4. Layer-2 (L2) Mode vs. BGP Mode:
* **Layer-2 Mode (What we use)**: Standard single-subnet ARP announcements. Zero router configuration needed. Failover occurs via Gratuitous ARP (GARP) in $<0.2\text{s}$.
* **BGP Mode (Enterprise Core)**: Worker nodes peer with upstream core firewalls (FortiGate/Cisco) via BGP. Enables **Equal-Cost Multi-Path (ECMP)** active-active hardware line-speed load balancing across all workers simultaneously.

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
