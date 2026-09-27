# Part 4 — Networking

> **Lab Environment:** RKE2 cluster · Shared with Amreet for live debugging  
> **Format:** Symptoms → Diagnosis commands → Fix steps → Verify

---

## 16. CNI Plugin Failure (Pod Stuck ContainerCreating)

### Symptoms
- Pod stuck in `ContainerCreating` indefinitely
- `kubectl describe pod` shows `network plugin returned error` or `cni0 not found`
- Other pods on the same node may also be stuck

### Diagnosis
```bash
# Describe pod — look at Events section
kubectl describe pod <pod-name> -n <namespace> | grep -A20 Events
# Look for: "cni config uninitialized", "failed to set up pod network"

# Check CNI pod health (RKE2 uses Canal/Calico/Cilium)
kubectl get pods -n kube-system | grep -E "canal|calico|cilium|flannel"

# Describe the CNI pod on the affected node
kubectl get pods -n kube-system -o wide | grep <node-name>
kubectl describe pod <cni-pod-name> -n kube-system

# SSH into node — check CNI config files
ls -la /etc/cni/net.d/
cat /etc/cni/net.d/<cni-config>.conflist

# Check CNI binary
ls -la /opt/cni/bin/

# Check kubelet logs for CNI errors
sudo journalctl -u rke2-agent -n 100 --no-pager | grep -i cni

# Test network between pods manually
kubectl exec -n <namespace> <working-pod> -- ping <stuck-pod-ip>
```

### Fix Steps
1. **Restart the CNI DaemonSet pod** on the affected node:
   ```bash
   kubectl delete pod -n kube-system <cni-pod-on-node>
   # DaemonSet will recreate it automatically
   ```

2. **CNI config missing** — repair by restarting the node agent:
   ```bash
   sudo systemctl restart rke2-agent
   # RKE2 will re-deploy CNI config on agent start
   ```

3. **CNI binary missing or corrupt**:
   ```bash
   # RKE2 — binaries live here
   ls /var/lib/rancher/rke2/data/current/bin/

   # Force re-extract by restarting rke2-agent
   sudo systemctl stop rke2-agent
   sudo rm -rf /var/lib/rancher/rke2/data/current/bin/
   sudo systemctl start rke2-agent
   ```

4. **Node network interface issue**:
   ```bash
   ip link show
   # Ensure the node's primary interface is up
   # For canal: check flannel.1 and cali* interfaces exist
   ip link show flannel.1
   ```

### Verify
```bash
kubectl get pod <pod-name> -n <namespace> -w
# ContainerCreating → Running

# Confirm pod has an IP
kubectl get pod <pod-name> -n <namespace> -o wide
```

---

## 17. CoreDNS Resolution Failure

### Symptoms
- Pods can't resolve service names (e.g., `myservice.namespace.svc.cluster.local`)
- `nslookup` or `dig` inside pod returns `SERVFAIL` or hangs
- Intermittent DNS failures under load

### Diagnosis
```bash
# Check CoreDNS pod health
kubectl get pods -n kube-system -l k8s-app=kube-dns
kubectl describe pod <coredns-pod> -n kube-system

# Check CoreDNS logs
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=100

# Check CoreDNS ConfigMap
kubectl get configmap coredns -n kube-system -o yaml

# Test DNS from a debug pod
kubectl run dns-test --image=busybox -it --rm --restart=Never -- \
  nslookup kubernetes.default.svc.cluster.local

# Test from an existing pod
kubectl exec -n <namespace> <pod-name> -- \
  nslookup <service-name>.<namespace>.svc.cluster.local

# Check /etc/resolv.conf inside pod
kubectl exec -n <namespace> <pod-name> -- cat /etc/resolv.conf

# Check CoreDNS service and its endpoints
kubectl get svc kube-dns -n kube-system
kubectl get endpoints kube-dns -n kube-system

# Check if CoreDNS service IP matches pods' nameserver
# resolv.conf nameserver should match kube-dns ClusterIP
```

### Fix Steps
1. **CoreDNS pods crashing** — restart them:
   ```bash
   kubectl rollout restart deployment coredns -n kube-system
   ```

2. **Corefile misconfiguration** — restore default:
   ```bash
   kubectl edit configmap coredns -n kube-system
   # Ensure the Corefile has correct forward and kubernetes blocks
   # Minimal working Corefile:
   ```
   ```
   .:53 {
     errors
     health { lameduck 5s }
     ready
     kubernetes cluster.local in-addr.arpa ip6.arpa {
       pods insecure
       fallthrough in-addr.arpa ip6.arpa
     }
     forward . /etc/resolv.conf
     cache 30
     loop
     reload
     loadbalance
   }
   ```

3. **DNS loop detected** — CoreDNS and node DNS can loop if node's `/etc/resolv.conf` points to itself:
   ```bash
   # On each node
   cat /etc/resolv.conf
   # If nameserver is 127.0.0.x — fix with systemd-resolved or static DNS
   sudo sed -i 's/nameserver 127.0.0.53/nameserver 8.8.8.8/' /etc/resolv.conf
   ```

4. **Scale CoreDNS** for under load intermittent failures:
   ```bash
   kubectl scale deployment coredns -n kube-system --replicas=3
   ```

### Verify
```bash
kubectl run dns-test --image=busybox -it --rm --restart=Never -- \
  nslookup kubernetes.default.svc.cluster.local
# Should resolve to the ClusterIP

kubectl get pods -n kube-system -l k8s-app=kube-dns
# All Running
```

---

## 18. Service Has No Endpoints

### Symptoms
- Service exists but requests return `connection refused` or `no endpoints available`
- `kubectl get endpoints <service>` shows `<none>`
- Pods exist but are not being included in the service

### Diagnosis
```bash
# Check endpoints for the service
kubectl get endpoints <service-name> -n <namespace>
# If <none> — label selector is not matching any pods

# Get the service's label selector
kubectl get svc <service-name> -n <namespace> \
  -o jsonpath='{.spec.selector}' | jq

# List pods with those labels
kubectl get pods -n <namespace> -l <key>=<value>
# If no pods returned → selector mismatch

# Compare pod labels vs service selector
kubectl get pod <pod-name> -n <namespace> --show-labels

# Check pod readiness (unready pods are excluded from endpoints)
kubectl get pod <pod-name> -n <namespace>
# READY column — should be 1/1 not 0/1

# Check service port vs pod containerPort
kubectl get svc <service-name> -n <namespace> -o yaml | grep -A5 ports
kubectl get pod <pod-name> -n <namespace> -o yaml | grep -A5 containerPort

# Check if Endpoints object was manually deleted
kubectl get endpoints -n <namespace>
```

### Fix Steps
1. **Label selector mismatch** — fix service selector or pod labels:
   ```bash
   # Option A: Fix service selector
   kubectl patch svc <service-name> -n <namespace> \
     -p '{"spec":{"selector":{"app":"correct-label-value"}}}'

   # Option B: Add correct label to deployment
   kubectl label pods -n <namespace> -l app=old-label app=correct-label
   ```

2. **Pod not ready** — fix readiness probe (see Part 2 scenario 9).

3. **Wrong namespace** — service and pods must be in the same namespace (unless using ExternalName):
   ```bash
   kubectl get pods -n <correct-namespace> -l <selector>
   ```

4. **Service port mismatch** — fix targetPort:
   ```yaml
   spec:
     ports:
     - port: 80
       targetPort: 8080   # ← must match containerPort in pod
   ```

### Verify
```bash
kubectl get endpoints <service-name> -n <namespace>
# Should list pod IPs now

# Test connectivity
kubectl run test --image=busybox -it --rm --restart=Never -- \
  wget -qO- http://<service-name>.<namespace>.svc.cluster.local:<port>
```

---

## 19. NetworkPolicy Blocking Legitimate Traffic

### Symptoms
- Traffic between pods suddenly stopped after a NetworkPolicy was applied
- `curl` between pods returns `connection refused` or times out
- No change in pod or service config — only NetworkPolicy was added

### Diagnosis
```bash
# List all NetworkPolicies in the namespace
kubectl get networkpolicy -n <namespace>

# Describe the policy — understand what it allows/denies
kubectl describe networkpolicy <policy-name> -n <namespace>

# Check pod labels (NetworkPolicy uses label selectors)
kubectl get pod <pod-name> -n <namespace> --show-labels

# Test connectivity from a debug pod
kubectl exec -n <namespace> <source-pod> -- \
  curl -v --max-time 5 http://<dest-pod-ip>:<port>

# Check if it's ingress or egress being blocked
# Ingress = traffic INTO the pod
# Egress = traffic OUT FROM the pod

# Temporarily delete the policy to confirm it's the cause
kubectl delete networkpolicy <policy-name> -n <namespace>
# If traffic restores → policy was blocking it
```

### Fix Steps
1. **Add missing ingress rule** — allow traffic from the source namespace/pod:
   ```yaml
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: allow-from-frontend
     namespace: backend
   spec:
     podSelector:
       matchLabels:
         app: backend
     ingress:
     - from:
       - namespaceSelector:
           matchLabels:
             kubernetes.io/metadata.name: frontend
         podSelector:
           matchLabels:
             app: frontend
       ports:
       - protocol: TCP
         port: 8080
   ```

2. **Add missing egress rule** — allow the source pod to call out:
   ```yaml
   spec:
     podSelector:
       matchLabels:
         app: frontend
     egress:
     - to:
       - namespaceSelector:
           matchLabels:
             kubernetes.io/metadata.name: backend
       ports:
       - protocol: TCP
         port: 8080
     - ports:
       - protocol: UDP
         port: 53   # ← always add DNS egress!
   ```

3. **Allow DNS** — a common mistake is forgetting DNS egress:
   ```yaml
   egress:
   - ports:
     - protocol: UDP
       port: 53
     - protocol: TCP
       port: 53
   ```

### Verify
```bash
kubectl exec -n <namespace> <source-pod> -- \
  curl -v --max-time 5 http://<service-name>:<port>
# Should succeed now

kubectl get networkpolicy -n <namespace>
# Policy exists — traffic flows
```

---

## 20. Ingress Controller Misrouting / 502s

### Symptoms
- `curl https://<hostname>/path` returns `502 Bad Gateway`
- Some paths work, others don't
- Ingress resource exists but traffic doesn't reach pods

### Diagnosis
```bash
# Check Ingress resource exists and is correct
kubectl get ingress -n <namespace>
kubectl describe ingress <ingress-name> -n <namespace>
# Look at Rules, Backends, and Events sections

# Check Ingress controller pods are running
kubectl get pods -n ingress-nginx   # or kube-system for RKE2
kubectl logs -n ingress-nginx <controller-pod> --tail=100 | grep -i error

# Check the backend service and its endpoints
kubectl get svc <backend-service> -n <namespace>
kubectl get endpoints <backend-service> -n <namespace>
# Endpoints must be populated

# Test the backend directly (bypass ingress)
kubectl port-forward svc/<backend-service> 9090:<service-port> -n <namespace>
curl http://localhost:9090/path   # Does this work?

# Check TLS secret (if HTTPS)
kubectl get secret <tls-secret> -n <namespace>
kubectl describe secret <tls-secret> -n <namespace>

# Check ingress class annotation
kubectl get ingress <name> -n <namespace> \
  -o jsonpath='{.metadata.annotations.kubernetes\.io/ingress\.class}'
# Or spec.ingressClassName
kubectl get ingress <name> -n <namespace> \
  -o jsonpath='{.spec.ingressClassName}'
```

### Fix Steps
1. **Backend service/endpoint missing** — fix the service (see scenario 18).

2. **Wrong ingressClassName** — patch the ingress:
   ```bash
   kubectl patch ingress <name> -n <namespace> \
     -p '{"spec":{"ingressClassName":"nginx"}}'
   ```

3. **Path mismatch** — fix path and pathType:
   ```yaml
   rules:
   - host: myapp.example.com
     http:
       paths:
       - path: /api
         pathType: Prefix       # ← use Prefix not Exact if subpaths needed
         backend:
           service:
             name: api-service
             port:
               number: 8080
   ```

4. **TLS certificate error causing 502**:
   ```bash
   # Check cert validity
   kubectl get secret <tls-secret> -n <namespace> \
     -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates

   # Renew cert or re-create secret
   kubectl create secret tls <tls-secret> \
     --cert=tls.crt --key=tls.key -n <namespace> \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

5. **Reload ingress controller**:
   ```bash
   kubectl rollout restart deployment <ingress-controller> -n ingress-nginx
   ```

### Verify
```bash
curl -v https://<hostname>/path
# 200 OK — no 502

kubectl logs -n ingress-nginx <controller-pod> --tail=50
# No upstream errors
```

---

*Previous → [Part 3 — Node-Level Failures](./part3-node-level-failures.md)*  
*Next → [Part 5 — Certificates & Auth](./part5-certificates-auth.md)*
