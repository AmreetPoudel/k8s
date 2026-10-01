# Scenario 21 — Web Portal: Service TargetPort Mismatch (Connection Refused)

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Service Outage)**  
> Customer portal returns `Connection refused` (502 Bad Gateway). Pods are `1/1 Running` with 0 restarts, labels match the Service selector, and Endpoints are populated. Yet no network traffic can reach the web server.

---

## Lab Setup

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-portal
  namespace: lab
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web-portal
  template:
    metadata:
      labels:
        app: web-portal
    spec:
      containers:
      - name: web
        image: busybox:1.36
        command: ["sh", "-c"]
        args:
        - |
          echo "<h1>Welcome to Customer Portal</h1>" > /index.html
          echo "Starting HTTP server on port 8080..."
          httpd -f -p 8080 -h /
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: "50m"
            memory: "32Mi"
          limits:
            cpu: "100m"
            memory: "64Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: web-portal
  namespace: lab
spec:
  type: ClusterIP
  selector:
    app: web-portal
  ports:
  - name: http
    port: 80
    targetPort: 80     # <-- BUG: TargetPort points to 80, but container listens on 8080
EOF
```

---

## Diagnosis

### 1. Pod Status and Labels

```bash
kubectl get pods -n lab -o wide
```
Output:
```text
NAME                          READY   STATUS    RESTARTS   AGE    IP           NODE
web-portal-7b5f98c6cd-lngq6   1/1     Running   0          8m     10.42.4.67   worker-3
```
Pod is healthy and running.

### 2. Check Endpoints

```bash
kubectl get endpoints -n lab
```
Output:
```text
NAME         ENDPOINTS       AGE
web-portal   10.42.4.67:80   8m
```
The Service selector is matching the Pod labels (the endpoint is not `<none>`). However, notice the endpoint target port: **`10.42.4.67:80`**.

### 3. Check What Port the App Listens On

Inspect the Deployment manifest or container processes:
```bash
kubectl get deployment web-portal -n lab -o yaml | grep -A 5 ports
```
The container exposes `containerPort: 8080`, and the command starts `httpd -p 8080`.

Because nothing inside the pod is listening on port `80`, any traffic routed to `10.42.4.67:80` receives an immediate TCP Reset (`Connection refused`).

---

## Root Cause

A Kubernetes Service has two distinct port definitions:
1. **`port`**: The port exposed by the Service itself inside the cluster (e.g. `http://web-portal:80`).
2. **`targetPort`**: The port on the **Pod** where traffic should be forwarded.

When `targetPort` does not match the port the application is actually listening on, the Endpoints object gets created with the wrong port. The connection drops with `Connection refused`.

---

## Solution

Edit or reapply the Service so that `targetPort` points to the application's actual listening port (`8080`):

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: web-portal
  namespace: lab
spec:
  type: ClusterIP
  selector:
    app: web-portal
  ports:
  - name: http
    port: 80
    targetPort: 8080    # <-- Fixed: forwards cluster port 80 to pod port 8080
EOF
```

### Verification

Check updated endpoints:
```bash
kubectl get endpoints web-portal -n lab
```
Output:
```text
NAME         ENDPOINTS         AGE
web-portal   10.42.4.67:8080   10m
```

Test HTTP connectivity:
```bash
kubectl run test-client --rm -i --tty --image=busybox:1.36 -n lab -- wget -O- -T 3 http://web-portal
```
Output:
```html
<h1>Welcome to Customer Portal</h1>
```

---

## Key Takeaways

| Field | Meaning | Example |
| :--- | :--- | :--- |
| **`port`** | Port on the Service (ClusterIP) that clients connect to | `80` |
| **`targetPort`** | Port on the Pod that the application is listening on | `8080` |
| **`containerPort`** | Informational tag in Pod spec (documents intent, does not open firewall) | `8080` |

If `targetPort` is omitted in a Service manifest, Kubernetes defaults `targetPort` to be equal to `port`. If your app is listening on `8080`, `3000`, `5000`, etc., you **must** explicitly set `targetPort`.
