# Doc 14: Senior/Staff DevOps & SRE Interview Mastery Guide
## 60+ Technical Questions, Production War Stories, and Day-to-Day Operational Wisdom

> **Purpose**: This document bridges the gap between theoretical knowledge and battle-tested production reality.  
> Use these architectural mental models, war stories (STAR format), and technical breakdowns to speak with the depth and authority of someone who has engineered and debugged Kubernetes platforms under high-stakes production conditions.

---

## 14.1 The 4 Senior Mental Models

When answering any Kubernetes question in an interview, anchor your response in these 4 principles:

1. **Reconciliation Loop Over Imperative Scripting**: Never describe Kubernetes as "deploying things"; describe it as asynchronous controllers continuously converging reality to a declarative desired state stored in etcd.
2. **Blast Radius & Failure Domain Isolation**: Senior answers always mention what happens when things fail (e.g. AZ outages, disk full, master node loss, CNI partition, pod eviction avalanches).
3. **The Abstraction Cost**: Understand what abstraction is doing under the hood (e.g. Services are iptables/IPVS, Pods are Linux namespaces + cgroups, Storage is CSI bind mounts, CNI is veth + VXLAN/eBPF).
4. **Day-2 Operations Over Day-1 Setup**: A junior engineer focuses on `helm install`; a senior engineer focuses on zero-downtime upgrades, certificate rotations, backup restoration validation, monitoring saturation metrics (USE/RED), and cost/resource efficiency.

---

## 14.2 Production Incident War Stories (STAR Format)

Interviewers love asking: *"Tell me about the hardest production outage you debugged in Kubernetes."*  
Here are 5 real-world production incident stories formatted using the **STAR** (Situation, Task, Action, Result) method. Memorize these narratives.

---

### War Story 1: The "Silent 504 Gateway Timeout" (VXLAN MTU Mismatch)

- **Situation**: Following a minor CNI plugin upgrade on a 40-node bare-metal cluster, our edge ingress controller reported intermittent `504 Gateway Timeout` errors. Small health check requests (`GET /healthz`) returned in 2ms, but large API POST payloads ($>2\text{KB}$) and PDF report downloads hung indefinitely before timing out.
- **Task**: Isolate the root cause causing selective packet drops on large payloads without rolling back the entire cluster immediately.
- **Action**:
  1. I deployed a `netshoot` diagnostic pod across different worker nodes to run layer-by-layer network diagnostics.
  2. Running `curl -v` on small payloads succeeded, but streaming large payloads stalled right after TCP window scaling (TLS handshake or data transfer phase).
  3. Suspecting an MTU fragmentation issue, I ran a non-fragmenting ICMP sweep: `ping -M do -s 1472 <remote-pod-ip>`. It failed immediately with `Frag needed and DF set`.
  4. Discovered that the physical network interface was standard MTU 1500, but Canal/Flannel's VXLAN encapsulation added 50 bytes of UDP/VTEP overhead. Because the host was setting the Don't Fragment (DF) flag, all packets exceeding 1450 bytes were dropped silently by intermediate routers.
  5. I patched the Canal ConfigMap `veth_mtu: 1450` and performed a rolling restart of the CNI DaemonSet.
- **Result**: Large payload drops dropped to zero immediately. I added automated CNI MTU validation checks to our CI/CD cluster provisioning pipelines to prevent future regressions.

---

### War Story 2: The Cascading "OOM & Node Eviction Avalanche"

- **Situation**: During a Black Friday traffic surge, one worker node ran out of memory. Within 8 minutes, 5 out of 12 worker nodes transitioned to `NotReady`, and over 60 application pods entered `CrashLoopBackOff` across the entire cluster.
- **Task**: Stabilize the cluster, halt the cascading failure, and redesign scheduling policies to prevent reoccurrence.
- **Action**:
  1. Triage revealed that several non-critical microservices were deployed without CPU/memory `requests` or `limits` (`BestEffort` QoS).
  2. When Node 1 experienced memory pressure, kubelet evicted 25 `BestEffort` pods simultaneously.
  3. The Kubernetes scheduler immediately rescheduled all 25 pods onto Node 2 and Node 3.
  4. The sudden memory influx on Node 2 and Node 3 triggered Linux kernel OOM killer, which killed system processes (`containerd` and `kubelet`), causing the nodes to report `NotReady`.
  5. To stop the bleed: I cordoned the flapping nodes, increased cluster capacity, and applied emergency `LimitRange` rules to prevent unbounded pod creation.
- **Result**: Restored cluster stability within 12 minutes. As a permanent fix, I enforced `LimitRanges`, `ResourceQuotas`, configured node-level kubelet `--kube-reserved` and `--system-reserved` memory pools, and set up `PodDisruptionBudgets` (PDBs) and PriorityClasses.

---

### War Story 3: The 3:00 AM etcd Leader-Flapping Crisis (Disk IOPS Saturation)

- **Situation**: Our monitoring alerted on high API server latency ($>5000\text{ms}$) and periodic HTTP 500 errors. `kubectl` commands returned `Error from server: etcdserver: leader changed` every 30 to 45 seconds.
- **Task**: Restore etcd leader stability without losing in-flight state or corrupting cluster metadata.
- **Action**:
  1. I checked etcd metrics in Prometheus and noticed `etcd_disk_wal_fsync_duration_seconds` p99 latency was spiking to $180\text{ms}$ (healthy is $<10\text{ms}$).
  2. Correlating system metrics using `iostat -xz 1`, I discovered that a centralized fluent-bit logging agent on Master 1 was flushing large JSON debug logs to `/var/log` on the exact same underlying NVMe disk partition as etcd's Write-Ahead Log (`/var/lib/rancher/rke2/server/db/etcd`).
  3. The heavy I/O write locks caused etcd's `fdatasync` calls to delay, causing the leader to miss the $100\text{ms}$ Raft heartbeat window. Followers initiated constant re-elections.
  4. I throttled the logging daemonset, adjusted its buffer path to a temporary in-memory ramdisk (`tmpfs`), and prioritized the etcd process using `ionice -c2 -n0`.
- **Result**: etcd p99 write latency dropped back to $2.1\text{ms}$, leadership stabilized, and API error rate dropped to 0%. I subsequently migrated etcd to physically isolated dedicated disks on all masters.

---

### War Story 4: The Expired Root Certificate Outage

- **Situation**: An air-gapped on-premises cluster suddenly became completely unreachable on Monday morning. All `kubectl` commands failed with `x509: certificate has expired or is not yet valid`. The CI/CD pipelines were paralyzed.
- **Task**: Rotate and regenerate control plane and component certificates without rebuilding the cluster from scratch.
- **Action**:
  1. I SSH'd directly into Master 1 via console and inspected the certs using `openssl x509 -in /var/lib/rancher/rke2/server/tls/serving-kube-apiserver.crt -noout -dates`. The 1-year cluster CA had expired over the weekend.
  2. Because the control plane was unresponsive, I backed up `/var/lib/rancher/rke2/server/tls/` to a secure tarball.
  3. I executed `rke2 certificate rotate` on Master 1 and restarted `rke2-server`.
  4. Distributed the updated CA and client certificates to Master 2 and Master 3, sequentially restarting their services.
  5. Restarted worker node `rke2-agent` services to pull the new CA bundle, and regenerated all user/CI/CD kubeconfigs.
- **Result**: Full recovery within 35 minutes. To prevent recurrence, I deployed Prometheus alert rules tracking `apiserver_client_certificate_expiration_seconds` with alerts triggering at 60 and 30 days before expiration.

---

### War Story 5: The "Split-Brain" VIP Blackhole (Keepalived & Cloud ARP)

- **Situation**: After a network switch firmware upgrade in our private data center, developers reported that 50% of `kubectl` requests failed with `Connection reset by peer` or SSL hostname validation errors.
- **Task**: Identify why API traffic was load-balancing erratically between master nodes instead of hitting the active master VIP.
- **Action**:
  1. Running `ip addr show` across the control plane nodes, I discovered that **both Master 1 and Master 2 had bound the Virtual IP `10.0.1.100` to their `eth0` interfaces**.
  2. A packet capture (`tcpdump -i eth0 vrrp`) revealed that the new network switch ACL had blocked IP Protocol 112 (VRRP multicast).
  3. Master 2 stopped receiving heartbeat advertisements from Master 1, concluded Master 1 was dead, and promoted itself to `MASTER`.
  4. The network switches' ARP tables were flapping between Master 1 and Master 2 MAC addresses on every packet, sending TLS handshakes to Master 1 and subsequent data packets to Master 2.
  5. I reconfigured keepalived to use **unicast VRRP** (`unicast_src_ip` / `unicast_peer`) over standard TCP/UDP ports and sent a gratuitous ARP (`arping -U`) from Master 1.
- **Result**: Master 2 transitioned cleanly to `BACKUP`, the duplicate IP was released, and API connectivity stabilized immediately.

---

## 14.3 Day-to-Day Real-Life Problems & Senior Solutions

### 1. How do you perform zero-downtime node OS patching / kernel upgrades?
**Senior Answer**:
"We never patch in-place on live workloads. We follow a strict cordon-drain-patch-uncordon workflow:
1. **PDB Verification**: Check that `PodDisruptionBudgets` exist for all critical workloads (`kubectl get pdb -A`).
2. **Cordon**: `kubectl cordon node-1` prevents new pods from scheduling.
3. **Drain**: `kubectl drain node-1 --ignore-daemonsets --delete-emptydir-data --grace-period=60`. This triggers graceful SIGTERM termination and reschedules replicas onto other nodes according to topology spread rules.
4. **Host Upgrade**: Apply OS security patches, upgrade kernel, and reboot the physical server/VM.
5. **Post-Boot Validation**: Verify systemd services (`rke2-agent`, `containerd`, `iscsid`), check disk and network interfaces.
6. **Uncordon**: `kubectl uncordon node-1`. Verify node transitions to `Ready` and pods schedule normally."

---

### 2. How do you handle "Noisy Neighbors" on shared multi-tenant clusters?
**Senior Answer**:
"We combat noisy neighbors across 4 layers:
- **Compute Isolation**: Enforce mandatory CPU/memory `requests` and `limits` via validating admission webhooks (Kyverno or OPA Gatekeeper). Enforce `LimitRange` defaults and `ResourceQuotas` per namespace.
- **Node Isolation**: Use `nodeSelector`, `taints/tolerations`, or `nodeAffinity` to isolate heavy compute/database workloads onto dedicated worker node pools.
- **Network Isolation**: Default-deny `NetworkPolicies` preventing runaway cross-namespace chatter, coupled with CNI egress bandwidth rate-limiting annotations.
- **Storage QoS**: Using dedicated storage classes with IOPS throttling or pinning high-IOPS applications to local NVMe via Local Persistent Volumes."

---

### 3. How do you manage secrets securely in production without committing base64 YAMLs to Git?
**Senior Answer**:
"We use a **GitOps-compatible secret management workflow**:
- **External Secrets Operator (ESO)**: Kubernetes secrets are synced dynamically from HashiCorp Vault or AWS Secrets Manager into native Kubernetes `Secret` objects inside the cluster. Git only stores an `ExternalSecret` custom resource containing the reference key, never the secret data.
- **Sealed Secrets (Bitnami)**: As an alternative, secrets are encrypted using asymmetric public-key cryptography and committed to Git; only the controller inside the cluster holds the private key to decrypt them.
- **At-Rest Encryption**: We configure the Kubernetes API server with an `EncryptionConfiguration` file to encrypt all Secret objects in etcd using AES-CBC or KMS envelope encryption."

---

## 14.4 60+ Deep Technical Interview Questions & Answers

### Control Plane & Architecture (Q1–Q12)

#### Q1: What is the difference between `kube-apiserver` and `kube-controller-manager`?
- **kube-apiserver**: Stateless REST gateway and data validator. The ONLY component that directly reads and writes to etcd. Exposes metrics, handles authN/authZ, and triggers admission webhooks.
- **kube-controller-manager**: A single multi-threaded binary that runs core reconciliation loops (Deployment, ReplicaSet, Node, EndpointSlice, ServiceAccount controllers). It watches the API server for changes and issues API requests to reconcile state.

#### Q2: What is the Kubelet and how does it report node health?
- Kubelet is the node-level agent. It syncs with the API server to get pod specs assigned to its node (`Pod Lifecycle Event Generator` - PLEG), interacts with containerd via CRI, mounts storage via CSI, configures networking via CNI, and sends periodic **NodeStatus heartbeats** (default every 10s) via `Lease` objects in the `kube-node-lease` namespace.

#### Q3: What happens when Kubelet stops sending heartbeats?
- If the API server doesn't receive a lease renewal within `node-monitor-grace-period` (default 40s), the Node Controller marks the node `Ready: Unknown`.
- After `pod-eviction-timeout` (default 5m), the controller marks pods for deletion and creates replacement pods on healthy nodes.

#### Q4: Why is etcd communication strictly mTLS?
- etcd contains raw, unencrypted cluster state, including RBAC keys, tokens, and secrets. Mutual TLS ensures that only verified control plane components with valid client certificates signed by the dedicated etcd CA can read or write data, preventing rogue access on ports 2379/2380.

#### Q5: What is the role of `kube-scheduler`? Can you bypass it?
- The scheduler selects the optimal node for unscheduled pods using Filtering (predicates: resource checks, taints, affinities) and Scoring (priorities: spread, image locality).
- **Yes, you can bypass it**: By explicitly setting `spec.nodeName: worker-1` in the Pod spec, kubelet directly starts the pod without scheduler involvement.

#### Q6: How does Kubernetes handle leader election for `kube-controller-manager` and `kube-scheduler`?
- Both components use **Lease-based leader election**. Multiple instances run across master nodes in an active-passive setup. They compete to acquire a `Lease` object in `kube-system`. The leader renews its lease periodically; if it fails, another instance acquires the lease and takes over.

#### Q7: What is an Admission Controller? Mutating vs. Validating?
- Code plugins that intercept API server requests *after* authentication and authorization, but *before* object persistence in etcd.
- **Mutating**: Executes first; can modify objects (e.g. inject sidecar proxies, add default security contexts).
- **Validating**: Executes second; accepts or rejects objects based on business rules (e.g. enforcing image registry whitelists).

#### Q8: What are CRDs and Operators?
- **Custom Resource Definition (CRD)**: Extends the Kubernetes API with user-defined resource kinds (e.g. `Prometheus`, `VaultSecret`, `PostgresCluster`).
- **Operator**: A pattern combining a CRD with a custom Controller that automates human operational knowledge (backups, failover, upgrades) for complex stateful applications.

#### Q9: What is PLEG (Pod Lifecycle Event Generator)?
- A module inside Kubelet that periodically inspects the container runtime (containerd) to detect container state changes (started, stopped, exited). If PLEG takes longer than 3 minutes to inspect containers (e.g., due to containerd lockup or D-state I/O hangs), Kubelet reports `PLEG is not healthy` and the node becomes `NotReady`.

#### Q10: How does RKE2 handle control plane High Availability?
- RKE2 embeds etcd and runs `rke2-server` across 3 master nodes. The initial master boots an etcd cluster, and subsequent masters join using a shared cluster secret token over port 9345. A floating keepalived VIP or TCP load balancer distributes client/worker traffic to `:6443` across healthy masters.

#### Q11: What is the difference between Container Runtime Interface (CRI) and OCI?
- **OCI (Open Container Initiative)**: Standards for container image formats and runtime specifications (`runc`).
- **CRI (Container Runtime Interface)**: A gRPC API allowing Kubernetes Kubelet to talk to different container runtimes (`containerd`, `CRI-O`) without vendor-specific code.

#### Q12: How do you gracefully restart a Kubernetes master node without interrupting cluster operations?
- Verify etcd is healthy: `etcdctl endpoint health --cluster`.
- Stop the master's RKE2 server: `systemctl stop rke2-server`.
- The remaining 2 masters maintain quorum ($2 > \lfloor 3/2 \rfloor$). Keepalived fails over the VIP.
- Perform maintenance, then start `rke2-server`. Verify rejoin via `etcdctl member list`.

---

### Networking & CNI (Q13–Q24)

#### Q13: What are the 3 mandatory requirements of the Kubernetes Network Model?
1. All pods can communicate with all other pods on any node without NAT.
2. All agents on a node (kubelet, daemons) can communicate with all pods on that node.
3. The IP that a pod sees as its own address is the same IP that other pods see it as.

#### Q14: How does Calico/Flannel (Canal) assign IPs to Pods?
- Flannel acts as the **IPAM** (IP Address Management) driver. It subdivides the global `cluster-cidr` (`10.42.0.0/16`) into `/24` host-local subnets per node. When a pod starts, the CNI plugin assigns an unused IP from the node's local `/24` pool and records it in host state.

#### Q15: What is a `veth` pair?
- A virtual Ethernet cable with two endpoints. One endpoint is placed inside the Pod's isolated network namespace as `eth0`, and the other endpoint remains in the host network namespace (named `caliXXXX` or `vethXXXX`), bridging pod traffic to the host routing table.

#### Q16: What is the difference between `ClusterIP`, `NodePort`, and `LoadBalancer`?
- **ClusterIP**: Virtual internal IP accessible only from within the cluster. Implemented via iptables/IPVS.
- **NodePort**: Allocates a static port (30000-32767) on every node's physical IP, forwarding traffic to the ClusterIP.
- **LoadBalancer**: Provisions an external load balancer (cloud AWS NLB or bare-metal MetalLB) that routes external traffic directly into the NodePort/ClusterIP.

#### Q17: What is `externalTrafficPolicy: Local` vs `Cluster`?
- **Cluster (Default)**: Traffic hitting a node's NodePort can be forwarded via overlay network to a pod on a different node. Introduces an extra network hop and obscures the client's original Source IP (SNAT).
- **Local**: Traffic is only routed to local pods on that exact node. Preserves the client's original Source IP and eliminates the extra hop. If no local pod exists, traffic is dropped.

#### Q18: What is CoreDNS and how does it scale?
- CoreDNS provides internal cluster DNS resolution. It watches the Kubernetes API for Services and Endpoints and builds in-memory DNS records. It scales horizontally via HPA or `cluster-proportional-autoscaler` based on the number of nodes and cores in the cluster.

#### Q19: What is eBPF and why is Cilium replacing iptables?
- eBPF allows sandboxed programs to execute directly within the Linux kernel in response to socket/network events. Unlike iptables, which evaluates sequential $O(N)$ rule lists that degrade at scale, eBPF uses $O(1)$ hash map lookups, achieving line-rate packet processing, native security observability (Hubble), and L7 policy enforcement.

#### Q20: How does NetworkPolicy work under the hood?
- NetworkPolicy is a declarative firewall specification. The CNI agent (Calico Felix) translates NetworkPolicy YAML specs into kernel `ipset` collections and `iptables` chains in the `FORWARD` filter table on each worker node, dropping unauthorized traffic at the packet level.

#### Q21: What is an Ingress Controller vs an Ingress Resource?
- **Ingress Resource**: A declarative YAML defining L7 routing rules (hostnames, paths, TLS secrets).
- **Ingress Controller**: The actual reverse proxy (NGINX, Traefik, HAProxy) running as a DaemonSet or Deployment that reads Ingress resources and dynamically updates its configuration to route HTTP traffic.

#### Q22: What is the purpose of the `pause` container?
- The pause container is the "infrastructure pod container". When a pod is initialized, the pause container starts first, creates the network namespace, and binds the IP. All other application containers in the pod join the pause container's network namespace using the `container:<id>` sharing model.

#### Q23: How does MetalLB work in Layer 2 mode vs BGP mode?
- **Layer 2 Mode**: Elects one node via leader election to respond to ARP requests for the service IP using the node's MAC address. Simple, requires no specialized router config, but limited to single-node bandwidth.
- **BGP Mode**: All nodes establish BGP peering sessions with upstream physical routers and announce `/32` routes to the Service IP, achieving true multi-node ECMP (Equal-Cost Multi-Path) load balancing.

#### Q24: What happens when two Services have overlapping selector labels?
- The Endpoints/EndpointSlice controller matches pods based on labels. If two services have identical selectors, both Services will populate the same pod IPs in their Endpoints lists. Traffic sent to either Service IP will load-balance across the same shared pool of pods.

---

### Storage, Scheduling & Workloads (Q25–Q40)

#### Q25: What is the difference between Deployment, StatefulSet, and DaemonSet?
- **Deployment**: For stateless applications. Pods have random hashes (`web-7d4f9b8-x9z2`), scale in parallel, and share or lack persistent identity.
- **StatefulSet**: For stateful clustered apps (databases). Pods have deterministic ordinal names (`mysql-0`, `mysql-1`), start/stop in strict sequence, and get dedicated PVs via `volumeClaimTemplates`.
- **DaemonSet**: Ensures exactly one replica runs on every node (or matching subset), used for storage, logging, and monitoring agents.

#### Q26: Explain the PV, PVC, and StorageClass abstraction.
- **StorageClass**: Defines the dynamic provisioner (e.g. Longhorn, AWS EBS) and volume parameters.
- **PVC (Claim)**: A user's namespace-scoped request for storage (size, access mode).
- **PV (Volume)**: The actual cluster-scoped storage resource provisioned by the CSI driver and bound 1:1 to a PVC.

#### Q27: What is VolumeBindingMode: `WaitForFirstConsumer`?
- By default (`Immediate`), a PV is provisioned as soon as a PVC is created, which might place the disk in Availability Zone A. If the Pod is later scheduled in Zone B due to compute constraints, it cannot attach the volume. `WaitForFirstConsumer` delays PV creation until the Pod is scheduled, ensuring the volume is created in the exact zone/node where the pod will run.

#### Q28: How does Longhorn achieve distributed storage replication?
- Longhorn runs a controller and engine on nodes. When a block write occurs, the Longhorn engine replicates the raw block data across 3 distinct worker node disks via TCP. If one worker crashes, the volume continues serving reads/writes from the remaining 2 healthy replicas.

#### Q29: What is the difference between `emptyDir`, `hostPath`, and `PersistentVolume`?
- **emptyDir**: Ephemeral storage created when a pod is assigned to a node; deleted permanently when the pod is removed.
- **hostPath**: Mounts a file or directory from the host node's filesystem directly into the pod. Persists on that specific node, but bypasses storage abstraction.
- **PersistentVolume**: Network-attached or CSI-managed persistent storage lifecycle-decoupled from individual pods or nodes.

#### Q30: How do Taints, Tolerations, and NodeAffinity interact?
- **Taints & Tolerations**: Taints on nodes *repel* pods unless the pod explicitly has a matching toleration (used to keep user pods off masters).
- **NodeAffinity**: *Attracts* pods to specific nodes based on node labels (e.g., `gpu=true`, `zone=us-east-1a`).

#### Q31: What is a PodDisruptionBudget (PDB)?
- A policy that limits the number of pods of a replicated application that can be simultaneously down during voluntary disruptions (e.g. node drains, cluster upgrades). For example, `minAvailable: 2` ensures `kubectl drain` pauses until replacement pods are healthy.

#### Q32: What is the difference between CPU `requests` vs `limits` under Linux cgroups?
- **CPU Request**: Maps to CFS (Completely Fair Scheduler) `cpu.shares`. Used by Kubernetes scheduler for placement and guarantees minimum CPU allocation during contention.
- **CPU Limit**: Maps to CFS quota (`cpu.cfs_quota_us` and `cpu.cfs_period_us`). Enforces a hard ceiling; exceeding it results in **CPU throttling**, not termination.

#### Q33: Why does memory limit breach cause OOMKill but CPU limit breach causes throttling?
- CPU is a **compressible resource**; the kernel can delay process execution cycles without data corruption. Memory is **non-compressible**; when physical RAM is full, the kernel must invoke the Out-Of-Memory (OOM) killer to terminate processes to protect kernel stability.

#### Q34: What is the difference between `RollingUpdate` and `Recreate` deployment strategies?
- **RollingUpdate**: Gradually replaces old pods with new pods (controlled by `maxSurge` and `maxUnavailable`), ensuring zero downtime.
- **Recreate**: Terminates all existing pods simultaneously before starting new pods. Incurs downtime, but necessary for legacy applications that cannot run multiple versions concurrently against a single database schema.

#### Q35: What are Init Containers vs Sidecar Containers?
- **Init Containers**: Run to completion sequentially *before* primary application containers start. Used for database migrations, schema seeding, or waiting for external dependencies.
- **Sidecar Containers**: Run concurrently alongside the main application container within the same Pod namespace (e.g. log shippers, Envoy proxies, Vault token refreshers).

#### Q36: How does the Kubernetes Horizontal Pod Autoscaler (HPA) calculate target replicas?
$$\text{Desired Replicas} = \left\lceil \text{Current Replicas} \times \left( \frac{\text{Current Metric Value}}{\text{Target Metric Value}} \right) \right\rceil$$
It queries the metrics API (e.g., `metrics-server` or Prometheus adapter) every 15s and applies stabilization windows to prevent scaling flapping (thrashing).

#### Q37: What is the difference between `readinessProbe` and `livenessProbe`?
- **Readiness Probe**: Determines if the container is ready to accept user traffic. Failing it removes the Pod IP from Service Endpoints; the container is NOT restarted.
- **Liveness Probe**: Determines if the container is healthy and running. Failing it triggers Kubelet to kill and restart the container according to its `restartPolicy`.

#### Q38: What is a Startup Probe and why is it essential for legacy Java/Spring apps?
- Startup probes disable liveness and readiness probes until the application completes initialization. This prevents liveness probes from prematurely killing slow-booting applications (e.g., heavy JVM apps that take 90 seconds to warm up caches).

#### Q39: What is `terminationGracePeriodSeconds` and what happens during pod deletion?
- Default 30s.
1. Pod is marked `Terminating` and removed from Service endpoints.
2. `preStop` hook executes (if defined).
3. `SIGTERM` signal is sent to container PID 1.
4. Application flushes buffers, closes DB pools, and finishes active requests.
5. If process is still alive after grace period, `SIGKILL` is sent immediately.

#### Q40: What is PriorityClass and how does Pod Preemption work?
- `PriorityClass` assigns an integer priority to pods. When a high-priority pod cannot be scheduled due to resource starvation, the scheduler **preempts** (evicts) lower-priority pods from a node to free up compute capacity.

---

### Security, RBAC & PKI (Q41–Q50)

#### Q41: How do you implement the Principle of Least Privilege in Kubernetes RBAC?
- Avoid `cluster-admin` bindings.
- Restrict permissions to specific namespaces using `Roles` and `RoleBindings`.
- Limit `verbs` (e.g., grant `get`, `list` instead of `*`).
- Restrict `apiGroups` and `resources`. Never grant `secrets` access unless strictly necessary.
- Use dedicated ServiceAccounts per application with `automountServiceAccountToken: false` where API access is unnecessary.

#### Q42: What is the difference between a RoleBinding and a ClusterRoleBinding?
- **RoleBinding**: Grants permissions within a specific namespace (even when referencing a ClusterRole).
- **ClusterRoleBinding**: Grants permissions cluster-wide across all namespaces and to cluster-scoped resources (Nodes, PVs, Namespaces).

#### Q43: How does ServiceAccount token authentication work in modern Kubernetes (v1.24+)?
- Kubernetes uses the **BoundServiceAccountTokenVolume** projection. Kubelet requests a time-limited, audience-restricted, cryptographically signed OIDC JSON Web Token (JWT) from the API server and mounts it into the container at `/var/run/secrets/kubernetes.io/serviceaccount/token`. Tokens are automatically rotated.

#### Q44: What are the 3 Pod Security Standards (PSS)?
1. **Privileged**: Unrestricted execution. Allows root, host access, capabilities.
2. **Baseline**: Minimally restrictive; prevents known privilege escalations (blocks host network, host ports, raw block mounts).
3. **Restricted**: Hardened; requires non-root user, drops all default capabilities except `NET_BIND_SERVICE`, enforces read-only root filesystems.

#### Q45: How do you harden a container's `securityContext`?
```yaml
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 10001
  capabilities:
    drop:
      - ALL
```

#### Q46: What is a Certificate Signing Request (CSR) in Kubernetes?
- A Kubernetes resource (`CertificateSigningRequest`) allowing automated certificate provisioning. A client sends a PEM-encoded CSR to the API server; an administrator or automated controller validates and approves it (`kubectl certificate approve`), and the cluster CA signs the certificate.

#### Q47: How do you secure etcd from unauthorized access?
- Enforce mTLS for client and peer communication with a dedicated CA separate from the Kubernetes control plane CA.
- Bind etcd listeners strictly to private/internal network interfaces.
- Enable encrypted storage at rest for etcd data directories.
- Implement strict firewall/Security Group rules blocking etcd ports 2379/2380 from worker nodes.

#### Q48: What is Image Vulnerability Scanning and where should it occur?
- Scanning container images for CVEs. It must happen at two stages:
  1. **Shift-Left (CI/CD Pipeline)**: Scan with Trivy/Grype before pushing to the container registry. Block builds with Critical/High CVEs.
  2. **Admission Stage (Runtime)**: Admission controllers (e.g. Kyverno) verify image cryptographic signatures (Cosign/Sigstore) and block unsigned or non-scanned images from running.

#### Q49: What is the aggregation layer in kube-apiserver?
- Allows extending the Kubernetes API with custom sub-APIs (like `metrics.k8s.io` via metrics-server) served by external standalone servers while presenting a unified API gateway to clients. Uses front-proxy mutual TLS certificates.

#### Q50: How do you audit "who deleted a namespace" in Kubernetes?
- Inspect the **Kubernetes Audit Logs** (`/var/lib/rancher/rke2/server/logs/audit.log`).
- Search for entries with `verb: delete`, `resource: namespaces`, and read the `user.username`, `sourceIPs`, and `userAgent` metadata.

---

### Observability & Troubleshooting (Q51–Q60)

#### Q51: What are the 4 Golden Signals of monitoring?
1. **Latency**: Time taken to service a request (p50, p95, p99).
2. **Traffic**: Demand placed on the system (Requests Per Second - RPS).
3. **Errors**: Rate of requests that fail (HTTP 5xx, exit codes).
4. **Saturation**: How full the most constrained resource is (CPU %, memory %, disk queue depth).

#### Q52: What is the difference between Prometheus Push vs Pull architecture?
- **Pull (Prometheus standard)**: Prometheus server periodically scrapes HTTP `/metrics` endpoints exposed by applications and node-exporters. Gives central control over scrape intervals and health detection.
- **Push (Pushgateway/OTel)**: Short-lived ephemeral batch jobs push metrics to an intermediary gateway that Prometheus scrapes.

#### Q53: How does kube-state-metrics differ from metrics-server?
- **metrics-server**: Lightweight, in-memory CPU and memory aggregator used strictly for core autoscaling (HPA, VPA) and `kubectl top`. Does not store historical metrics.
- **kube-state-metrics**: Listens to the API server and generates time-series metrics about Kubernetes object states (e.g. deployment desired vs available replicas, pod restart counts, certificate expiry). Scraped and stored by Prometheus.

#### Q54: What is the RED method vs USE method?
- **RED Method (Services & APIs)**: Rate, Errors, Duration. Best for request-driven architectures.
- **USE Method (Infrastructure & Hardware)**: Utilization, Saturation, Errors. Best for physical nodes, disks, and network interfaces.

#### Q55: How do you debug a pod that is crashing on startup before you can run `kubectl exec`?
1. `kubectl logs <pod-name> --previous` to inspect the exit logs of the dead container.
2. `kubectl describe pod <pod-name>` to check exit codes and termination reasons.
3. Override the entrypoint temporarily: change `command: ["sleep", "3600"]` in the deployment YAML to keep the container alive, then `kubectl exec` inside to inspect files and environment variables.

#### Q56: What does the PromQL query `rate(http_requests_total[5m])` compute?
- Computes the per-second average rate of increase of the counter metric `http_requests_total` over the last 5-minute sliding window, automatically handling counter resets (e.g. process restarts).

#### Q57: How do you capture raw network packets from a Pod without installing `tcpdump` inside its image?
- **Option A (Ephemeral Debug Container)**:
  `kubectl debug -it <pod-name> --image=nicolaka/netshoot --target=<container-name>`
- **Option B (Host Network Namespace)**:
  SSH to the worker node, find the container's PID via `crictl inspect <id>`, and run:
  `nsenter -t <PID> -n tcpdump -i eth0 -nn -w capture.pcap`

#### Q58: What is Distributed Tracing and why is it needed in microservices?
- Propagates a unique trace/span ID (e.g., OpenTelemetry, Jaeger) across HTTP/gRPC headers through multiple microservice hops. Allows pinpointing exact latency bottlenecks and identifying which downstream service caused an error in a distributed call graph.

#### Q59: Why should you avoid alert fatigue in SRE teams?
- Excessive, non-actionable, or noisy warning alerts condition engineers to ignore notifications, leading to missed critical P0 incidents. Every alert that wakes an on-call engineer must be **actionable**, linked to a **clear runbook**, and indicate real user-facing degradation.

#### Q60: How do you verify an etcd snapshot backup is valid without restoring it to production?
- Run `etcdctl snapshot status <backup.db> -w table` to verify the cryptographic hash, revision number, and total key count.
- In an isolated test environment (or temporary VM/container), execute `etcdctl snapshot restore <backup.db> --data-dir=/tmp/test-etcd`, start a standalone etcd instance, and query keys with `etcdctl get / --prefix --keys-only` to ensure the B-tree database is uncorrupted.

---

## 14.5 "Red Flag" vs "Green Flag" Interview Answers

| Question | 🚩 Junior / Red Flag Answer | 🟢 Senior / Green Flag Answer |
|---|---|---|
| *How do you scale a deployment?* | "I run `kubectl scale deployment --replicas=10`." | "For production, we avoid manual imperative commands. We define HPA resources based on CPU/memory or custom business metrics (SQS queue depth) and commit changes declaratively via GitOps pipelines." |
| *How do you give a developer access?* | "I send them the admin kubeconfig file." | "We never share admin certs. We integrate with an OIDC provider (Okta, Keycloak, Dex) or issue user client certs, and bind them to specific namespaces using RBAC RoleBindings with minimal required verbs." |
| *What do you do if a node fails?* | "I restart the node or delete the pods." | "First observe conditions (`kubectl describe node`), check if Kubelet lease is updating. If physical hardware is lost, the node controller handles pod eviction after the grace period. Once stabilized, we drain, repair, and validate host metrics before uncordoning." |
| *Where do logs go?* | "I check `kubectl logs`." | "`kubectl logs` is for real-time debugging. In production, logs written to stdout/stderr are collected by a DaemonSet (Fluent-bit/Vector) from `/var/log/pods/`, enriched with Kubernetes metadata, and shipped to centralized storage (Elasticsearch/Loki) with index retention policies." |

---

*Doc 14 of 14 | Complete RKE2 Kubernetes Architecture & SRE Mastery Series*
