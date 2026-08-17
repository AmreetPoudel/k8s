# Doc 10: Ingress & LoadBalancer
## Getting External Traffic to Your Pods

---

## 10.1 The Traffic Problem

Your pods have private IPs (10.42.x.x). Your users are on the internet. How does traffic get from the internet to a specific pod?

Three ways in Kubernetes:

```
Option 1: NodePort Service
  User → <worker-IP>:30080 → kube-proxy iptables → pod
  Problem: Ugly URLs with ports. No TLS. Users need to know a worker IP.

Option 2: LoadBalancer Service
  User → <external-LB-IP>:80 → LoadBalancer → worker:nodeport → pod
  Problem: On-prem/bare-metal has no cloud LB. MetalLB solves this.

Option 3: Ingress (Best)
  User → <ingress-IP>:80 → NGINX Ingress Controller → routes by hostname/path → pod
  This is what you actually use.
```

---

## 10.2 How NGINX Ingress Controller Works

RKE2 ships with NGINX Ingress Controller pre-installed as a DaemonSet.

```bash
# [M1] — check ingress controller
kubectl get pods -n kube-system | grep ingress
# rke2-ingress-nginx-controller-xxxxx   1/1   Running

kubectl get daemonset -n kube-system rke2-ingress-nginx-controller
# DaemonSet runs on every worker node

# Check what ports the ingress controller listens on
kubectl get pod -n kube-system -l app.kubernetes.io/name=rke2-ingress-nginx -o yaml \
  | grep -A 10 "hostPort"
# hostPort: 80 and hostPort: 443
# This means: the ingress controller uses the NODE's port 80/443 directly
```

### The Architecture

```
Internet
  ↓
<worker-1 public IP>:80 or :443
  ↓
NGINX Ingress Controller (hostPort, runs on every worker)
  ↓
Reads Ingress resource (routing rules)
  ↓
Proxies to Service ClusterIP
  ↓
Service → Pod
```

When you create an `Ingress` resource:
1. NGINX Ingress watches the API server for Ingress objects
2. For each Ingress rule, NGINX generates a server block in `nginx.conf`
3. Traffic matching that hostname/path is proxied to the specified Service

---

## 10.3 Creating Your First Ingress

```bash
# [M1] — deploy a test application
cat << 'EOF' | kubectl apply -f -
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hello-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: hello
  template:
    metadata:
      labels:
        app: hello
    spec:
      containers:
      - name: hello
        image: gcr.io/google-samples/hello-app:1.0
        ports:
        - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: hello-svc
spec:
  selector:
    app: hello
  ports:
  - port: 80
    targetPort: 8080
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: hello-ingress
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  rules:
  - host: hello.yourdomain.com    # ← DNS must point to a worker's IP
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: hello-svc
            port:
              number: 80
EOF

# Check ingress
kubectl get ingress hello-ingress
# Shows: ADDRESS column should show a worker node IP

# Test (add hello.yourdomain.com to /etc/hosts pointing to worker IP)
echo "10.0.2.10 hello.yourdomain.com" >> /etc/hosts
curl http://hello.yourdomain.com
# Hello, world! Version: 1.0.0 ...
```

---

## 10.4 TLS with Ingress

### Manual TLS (your own cert)

```bash
# [M1] — create TLS secret
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout hello.key \
  -out hello.crt \
  -subj "/CN=hello.yourdomain.com"

kubectl create secret tls hello-tls \
  --cert=hello.crt \
  --key=hello.key

# Update Ingress to use TLS
cat << 'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: hello-ingress
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - hello.yourdomain.com
    secretName: hello-tls
  rules:
  - host: hello.yourdomain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: hello-svc
            port:
              number: 80
EOF

curl -k https://hello.yourdomain.com
```

### Automatic TLS with cert-manager (Let's Encrypt)

```bash
# [M1] — install cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml

kubectl get pods -n cert-manager -w
# cert-manager, cert-manager-cainjector, cert-manager-webhook

# Create a ClusterIssuer for Let's Encrypt
cat << 'EOF' | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: your-email@yourdomain.com
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
    - http01:
        ingress:
          class: nginx
EOF

# Annotate Ingress to use cert-manager
kubectl annotate ingress hello-ingress \
  cert-manager.io/cluster-issuer=letsencrypt-prod

# cert-manager automatically creates the TLS secret and renews it
kubectl get certificate
# Shows: READY True when cert is issued
```

---

## 10.5 MetalLB — LoadBalancer for Bare Metal

On AWS/GCP, when you create `type: LoadBalancer`, the cloud creates an NLB/ALB automatically.  
On bare metal or private cloud (Nutanix), there's no such magic. **MetalLB** fills this gap.

MetalLB operates in two modes:
- **Layer 2 mode**: MetalLB responds to ARP requests for the LoadBalancer IP from one node
- **BGP mode**: MetalLB announces routes to an upstream router

For your setup (no BGP router), use **Layer 2 mode**.

```bash
# [M1] — install MetalLB
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.3/config/manifests/metallb-native.yaml

kubectl get pods -n metallb-system -w
# controller-XXXX   Running
# speaker-XXXX      Running (one per node — responds to ARP)

# Configure an IP pool for LoadBalancer Services
# These IPs must be from your node network (same subnet as nodes)
# and MUST NOT be assigned to any existing interface
cat << 'EOF' | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: my-ip-pool
  namespace: metallb-system
spec:
  addresses:
  - 10.0.1.200-10.0.1.210   # range of unused IPs in master subnet
  # OR use worker subnet if workers are external-facing:
  # - 10.0.2.200-10.0.2.210
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: my-l2-advertisement
  namespace: metallb-system
spec:
  ipAddressPools:
  - my-ip-pool
EOF
```

### Test LoadBalancer Service

```bash
# [M1] — create a LoadBalancer service
cat << 'EOF' | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: hello-lb
spec:
  type: LoadBalancer
  selector:
    app: hello
  ports:
  - port: 80
    targetPort: 8080
EOF

# Watch for external IP assignment
kubectl get svc hello-lb -w
# EXTERNAL-IP changes from <pending> to 10.0.1.200 (MetalLB assigns it)

# Test (from any machine in the same network)
curl http://10.0.1.200
# Hello, world!
```

🔍 **How MetalLB Layer 2 works:**
1. MetalLB assigns IP `10.0.1.200` from your pool to the Service
2. The `speaker` DaemonSet on one worker node (elected by MetalLB) "owns" this IP
3. When anything on the network sends ARP "who has 10.0.1.200?", MetalLB speaker responds with the worker's MAC address
4. Traffic flows to that worker, then kube-proxy routes it to the actual pod
5. If that worker node fails, MetalLB elects a new speaker and starts responding to ARP from a new node (gratuitous ARP)

💡 **Interview**: *"How do you expose Kubernetes services externally on-premises without a cloud load balancer?"*  
→ "Two main options: MetalLB or Ingress with NodePort. MetalLB is a software load balancer that assigns real IP addresses from a configured pool to LoadBalancer Services. It works in Layer 2 mode (ARP-based, works on any LAN) or BGP mode (for integration with physical routers). The speaker component responds to ARP requests for the assigned IPs, directing traffic to a worker node. Failover happens via gratuitous ARP when the speaker node changes. For HTTP(S), you'd typically combine this with an Ingress controller — MetalLB gives the Ingress controller a stable external IP, and Ingress routes to backends by hostname/path."

---

## 10.6 Ingress Annotations Reference

Key NGINX Ingress annotations:

```yaml
annotations:
  # Rate limiting
  nginx.ingress.kubernetes.io/limit-rps: "10"
  
  # Timeout
  nginx.ingress.kubernetes.io/proxy-connect-timeout: "30"
  nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
  
  # Body size limit (for file uploads)
  nginx.ingress.kubernetes.io/proxy-body-size: "50m"
  
  # CORS
  nginx.ingress.kubernetes.io/enable-cors: "true"
  nginx.ingress.kubernetes.io/cors-allow-origin: "*"
  
  # Sticky sessions (session affinity)
  nginx.ingress.kubernetes.io/affinity: "cookie"
  nginx.ingress.kubernetes.io/session-cookie-name: "sticky"
  
  # Basic auth
  nginx.ingress.kubernetes.io/auth-type: basic
  nginx.ingress.kubernetes.io/auth-secret: basic-auth-secret
  
  # Force HTTPS redirect
  nginx.ingress.kubernetes.io/ssl-redirect: "true"
  nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
  
  # Whitelist IPs
  nginx.ingress.kubernetes.io/whitelist-source-range: "10.0.0.0/8,192.168.1.0/24"
```

---

## 10.7 Summary

You now understand:
- ✅ Three ways to expose services (NodePort, LoadBalancer, Ingress)
- ✅ How NGINX Ingress Controller works (hostPort, nginx.conf generation)
- ✅ Creating Ingress resources with TLS
- ✅ cert-manager for automatic Let's Encrypt certificates
- ✅ MetalLB for bare-metal LoadBalancer Services (L2 ARP mode)
- ✅ Key Ingress annotations

**Next**: [Doc 11 - Helm & Workloads →](./11-helm-and-workloads.md)  
Deploying real applications with Helm, understanding Helm chart structure, and namespace/resource strategies.

---

*Doc 10 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
