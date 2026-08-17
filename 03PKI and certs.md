# Doc 03: PKI & Certificates
## How TLS Secures Your Entire Cluster

> **This is theory + reference, not hands-on yet.**  
> RKE2 generates all these certs automatically. But you need to understand them  
> so you know WHY things break and how to fix them.

---

## 3.1 Why Every Kubernetes Component Uses TLS

Kubernetes components talk to each other constantly:
- kubectl → API server
- API server → etcd
- API server → kubelet
- kubelet → API server
- Scheduler → API server
- Controller manager → API server

Every single one of these connections uses **mutual TLS (mTLS)**. Both sides present certificates. Both sides verify each other.

**Why not just a username/password?**  
1. Certs can be bound to specific IP/hostname (SANs) — a stolen cert is useless elsewhere
2. No round-trip to auth database — verification is a local crypto operation
3. Certificate can be revoked without changing passwords everywhere
4. No shared secret that could be leaked

💡 **Interview**: *"How does the API server authenticate the scheduler?"*  
→ "The scheduler has a client certificate signed by the Kubernetes CA. When it connects to the API server, it presents this cert. The API server verifies the cert's signature against the cluster CA, checks it's not expired, and reads the Common Name (CN) to determine identity — in this case `system:kube-scheduler`. RBAC then authorizes what that identity can do. This is called mTLS — both sides authenticate each other."

---

## 3.2 The CA Chain in Kubernetes

A **Certificate Authority (CA)** is a trusted entity that signs certificates. In Kubernetes, there are actually MULTIPLE CAs:

```
┌────────────────────────────────────────────────────────────────────┐
│                    RKE2 Certificate Authorities                    │
│                                                                    │
│  /var/lib/rancher/rke2/server/tls/                                 │
│  │                                                                 │
│  ├── server-ca.crt / server-ca.key                                 │
│  │   Signs: API server serving cert, kubelet serving cert          │
│  │   Verifies: HTTPS connections TO these servers                  │
│  │                                                                 │
│  ├── client-ca.crt / client-ca.key                                 │
│  │   Signs: kubectl client certs, component client certs           │
│  │   Verifies: WHO is connecting (authentication)                  │
│  │                                                                 │
│  ├── request-header-ca.crt / request-header-ca.key                 │
│  │   Signs: front-proxy client cert                                │
│  │   Verifies: aggregation layer (API extensions)                  │
│  │                                                                 │
│  ├── etcd/                                                         │
│  │   ├── server-ca.crt / server-ca.key                             │
│  │   │   Signs: etcd server certs                                  │
│  │   └── peer-ca.crt / peer-ca.key                                 │
│  │       Signs: etcd peer certs (etcd-to-etcd)                     │
│  │                                                                 │
│  └── service.key                                                   │
│      Used for ServiceAccount token signing (not a cert)            │
└────────────────────────────────────────────────────────────────────┘
```

Why multiple CAs? **Principle of least privilege.** If the etcd CA is compromised, an attacker can forge etcd certs but NOT API server or client certs. Defense in depth.

---

## 3.3 Every Certificate RKE2 Generates

### API Server Certificates

| Cert | Path | Purpose |
|------|------|---------|
| `server-ca.crt` | `tls/` | Root CA for server certs |
| `serving-kube-apiserver.crt` | `tls/` | API server's HTTPS server cert |
| `client-kube-apiserver.crt` | `tls/` | API server client cert for etcd |
| `client-controller.crt` | `tls/` | Controller manager auth |
| `client-scheduler.crt` | `tls/` | Scheduler auth |
| `client-admin.crt` | `tls/` | Admin (kubectl) auth |

### etcd Certificates

| Cert | Path | Purpose |
|------|------|---------|
| `server-ca.crt` | `tls/etcd/` | etcd server CA |
| `server-etcd.crt` | `tls/etcd/` | etcd server serving cert |
| `peer-ca.crt` | `tls/etcd/` | etcd peer CA |
| `peer-etcd.crt` | `tls/etcd/` | etcd peer-to-peer cert |
| `client-kube-apiserver-etcd.crt` | `tls/etcd/` | API server's cert to talk to etcd |

### kubelet Certificates

| Cert | Path | Purpose |
|------|------|---------|
| `serving-kubelet.crt` | node cert | kubelet's HTTPS server |
| `client-kubelet.crt` | node cert | kubelet's auth to API server |

### Where RKE2 Stores Them

```bash
ls /var/lib/rancher/rke2/server/tls/
# ca.crt  ca.key
# server-ca.crt  server-ca.key
# client-ca.crt  client-ca.key
# etcd/
# ...
```

⚠️ **These files contain the PRIVATE KEYS of your cluster CAs.** Treat them like passwords.  
On AWS: encrypt the EBS volume or use KMS.  
In production: use external PKI (Vault, AWS ACM Private CA).

---

## 3.4 Subject Alternative Names (SANs) — The Most Common Certificate Error

### What Is a SAN?

When a TLS server presents its certificate, the client checks:  
*"Is the address I connected to listed in the certificate?"*

This check uses the **Subject Alternative Names (SANs)** field of the certificate.

If you connect to `https://10.0.1.10:6443` and the cert only lists `master-1` as a SAN, the connection fails with:
```
x509: certificate is valid for master-1, not 10.0.1.10
```

### What SANs Must the API Server Cert Have?

The API server cert needs EVERY way you might connect to it:

```
SANs required for API server certificate:
  - 127.0.0.1          (localhost, for local kubectl)
  - ::1                (IPv6 localhost)
  - 10.0.1.10          (master-1 private IP)
  - 10.0.1.11          (master-2 private IP)
  - 10.0.1.12          (master-3 private IP)
  - 10.0.1.100         (keepalived VIP)
  - master-1           (hostname)
  - master-2
  - master-3
  - master-vip
  - kubernetes          (default k8s DNS name)
  - kubernetes.default
  - kubernetes.default.svc
  - kubernetes.default.svc.cluster.local
  - 10.43.0.1          (kubernetes Service ClusterIP — always first IP in service CIDR)
  - <public IPs>        (if you access kubectl from internet)
```

**RKE2 automatically adds many of these**. But you must configure it to add your VIP and public IPs.

In RKE2 config, you set this with `tls-san`:
```yaml
# /etc/rancher/rke2/config.yaml
tls-san:
  - 10.0.1.100          # keepalived VIP
  - master-vip
  - <your-public-ip>    # if you want to kubectl from laptop
```

🔍 **What is `kubernetes.default.svc.cluster.local`?**  
Inside the cluster, pods reach the API server via the `kubernetes` Service in the `default` namespace. This service always gets the first IP in your service CIDR (default: `10.43.0.1`). Pods call `https://kubernetes.default.svc.cluster.local:443` — so the API server cert must include this DNS name AND the ClusterIP.

💡 **Interview**: *"A new developer is getting x509 errors connecting to the API server. What do you check first?"*  
→ "First, check what address they're using to connect. Then inspect the API server certificate's SANs: `openssl x509 -in /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt -noout -ext subjectAltName`. If their address isn't listed, the cert needs to be regenerated with that SAN added. In RKE2, add the address to `tls-san` in config.yaml and restart rke2-server."

---

## 3.5 How to Inspect Certificates

You will use these commands often during debugging:

```bash
# [M1] — inspect the API server certificate
openssl x509 \
  -in /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt \
  -noout \
  -text \
  | grep -A 20 "Subject Alternative Name"

# Check certificate expiry
openssl x509 \
  -in /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt \
  -noout \
  -dates
# Output:
# notBefore=Aug 17 10:00:00 2026 GMT
# notAfter=Aug 17 10:00:00 2027 GMT

# Quick expiry check for all certs
for cert in /var/lib/rancher/rke2/server/tls/*.crt; do
  echo "=== $cert ==="
  openssl x509 -in "$cert" -noout -dates 2>/dev/null
done

# Check a live TLS connection (useful for remote cert check)
openssl s_client -connect master-1:6443 -showcerts </dev/null 2>/dev/null \
  | openssl x509 -noout -text | grep -A 5 "Subject Alternative Name"
```

---

## 3.6 The kubeconfig File Explained

When you run `kubectl get pods`, kubectl reads `~/.kube/config` (or `/etc/rancher/rke2/rke2.yaml` on masters).

This file contains:
```yaml
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: <base64-encoded-CA-cert>  # to verify API server
    server: https://127.0.0.1:6443                        # where to connect
  name: default
contexts:
- context:
    cluster: default
    user: default
  name: default
current-context: default
users:
- name: default
  user:
    client-certificate-data: <base64-encoded-client-cert>  # your identity
    client-key-data: <base64-encoded-client-key>           # your private key
```

When kubectl runs:
1. Reads `server` URL
2. Opens TLS connection to that server
3. Verifies server cert against `certificate-authority-data`
4. Presents `client-certificate-data` + `client-key-data` as identity
5. API server verifies client cert against its client CA
6. API server reads CN from client cert → `system:masters` group → full access

💡 **Interview**: *"How does kubectl authenticate to the API server?"*  
→ "By default, kubectl uses client certificate authentication. The kubeconfig contains a base64-encoded client certificate and private key. When making a request, kubectl establishes a TLS connection and presents this certificate. The API server verifies it against the cluster's client CA. The certificate's Common Name (CN) and Organization (O) fields map to Kubernetes user and group. The `admin` cert has CN=`admin` and O=`system:masters`, which matches a built-in ClusterRoleBinding giving full access."

---

## 3.7 Certificate Rotation and Expiry

### Default Expiry

RKE2 certificates expire in **1 year** by default (365 days).

⚠️ **In production, expired certs = cluster completely inaccessible.** There's no graceful degradation. The API server rejects all connections.

### RKE2 Auto-Rotation

RKE2 automatically rotates certs if:
1. The rke2-server service restarts (it checks cert expiry and rotates if <90 days left)
2. You manually trigger rotation

```bash
# [M1] — manually trigger cert rotation
systemctl stop rke2-server

# RKE2 will rotate certs on next start if <90 days remain
systemctl start rke2-server

# Or force rotation (RKE2 1.25+)
rke2 certificate rotate
```

### What Breaks When Certs Expire

```
API server cert expires:
  → kubectl stops working: x509: certificate has expired
  → All kubelet → API server connections fail
  → Pods still run (kubelet cached state)
  → No new pods can be scheduled
  → Can't exec into pods, can't read logs

etcd peer cert expires:
  → etcd nodes can't replicate
  → If leader dies, no new leader can be elected (peers can't communicate)
  → Cluster becomes read-only (API server can read but not write to etcd)
  → kubectl apply fails: "etcdserver: leader changed"

kubelet cert expires:
  → API server can't reach kubelet
  → kubectl exec, kubectl logs fail for pods on that node
  → Node shows NotReady (API server can't health-check kubelet)
  → But pods on that node keep running

client-ca cert expires:
  → No new kubeconfigs work
  → Existing connections may continue (session resumption) briefly
  → Complete lockout after connections reset
```

💡 **Interview**: *"Our cluster certs expired. How do you recover?"*  
→ "First, verify the exact cert that expired using `openssl x509 -in <cert> -noout -dates`. For RKE2, the recovery path is: stop rke2-server on all masters, remove or backup the expired certs, force rotation with `rke2 certificate rotate` or delete the cert files and restart rke2-server (it regenerates). For kubeadm clusters, use `kubeadm certs renew all`. The trickiest part is accessing the node — if your kubectl certs expired too, you need direct SSH access. After rotation, distribute new kubeconfigs to all users."

---

## 3.8 ServiceAccount Tokens — A Different Auth Mechanism

ServiceAccounts use tokens, not certs. This is how pods authenticate to the API server.

```
Pod wants to call API server:
  1. kubelet mounts /var/run/secrets/kubernetes.io/serviceaccount/token
  2. Pod reads this file (JWT token)
  3. Pod sends Bearer token in HTTP header to API server
  4. API server verifies JWT signature using service.key (the SA signing key)
  5. API server reads the token's subject → determines ServiceAccount identity
  6. RBAC determines what that ServiceAccount can do
```

```bash
# [M1] — look at the SA signing key
ls /var/lib/rancher/rke2/server/tls/service.key
# This key signs all ServiceAccount tokens in your cluster
# If this key rotates, all existing SA tokens immediately become invalid
```

⚠️ **If you accidentally delete `service.key`, all ServiceAccount tokens become invalid cluster-wide.** Every pod that calls the API server (Prometheus, ingress controllers, your apps) will fail with 401 Unauthorized.

---

## 3.9 Summary

You now understand:
- ✅ Why every Kubernetes connection uses mTLS
- ✅ The multiple CA hierarchy and why it exists
- ✅ What SANs are and why wrong SANs cause x509 errors
- ✅ How to inspect certificates with openssl
- ✅ What the kubeconfig file is and how kubectl uses it
- ✅ What breaks when each cert type expires
- ✅ How ServiceAccount tokens differ from cert-based auth

**Next**: [Doc 04 - etcd Deep Dive →](./04-etcd-deep-dive.md)  
Understanding etcd is understanding your cluster's brain. You'll learn Raft consensus, why quorum matters, how to back up etcd, and how to recover from etcd disasters.

---

*Doc 03 of 14 | RKE2 Kubernetes: From Zero to Interview-Ready*
