# Scenario 14 — Frontend App: Stuck Mid-Rollout

## 🚨 Incident Alert (PagerDuty)

> **SEV-1 (Partial Outage)**
> Release v3.1.0 of `frontend-app` was deployed 8 minutes ago. 50% of users are getting HTTP 200, the other 50% are getting connection errors. The deployment appears stuck mid-rollout. Engineers cannot push a hotfix — another rollout is already in progress.

**Your job:** Triage, explain why some users are still served, find root cause, and restore 100% service.

---

## Lab

```bash
kubectl apply -n lab -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend-app
  namespace: lab
  labels:
    app: frontend-app
  annotations:
    kubernetes.io/change-cause: "Release v3.1.0 - new homepage redesign"
spec:
  replicas: 4
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      app: frontend-app
  template:
    metadata:
      labels:
        app: frontend-app
    spec:
      containers:
      - name: frontend
        image: nginx:this-tag-does-not-exist
        resources:
          requests:
            cpu: "50m"
            memory: "64Mi"
          limits:
            cpu: "100m"
            memory: "128Mi"
EOF
```

---

## Questions to Answer Before Looking at Solution

1. What is the pod STATUS for the new pods?
2. Run `kubectl rollout status deployment/frontend-app -n lab`. What does it say?
3. Why are SOME users still getting HTTP 200? (Hint: look at `maxUnavailable: 0`)
4. What does `maxUnavailable: 0` protect you from?
5. What does `maxSurge: 1` mean?
6. What are the two ways to restore service?

---

## Solution

<details>
<summary>Click to reveal solution</summary>

### Root Cause

The new image `nginx:this-tag-does-not-exist` does not exist on Docker Hub. The container runtime cannot pull it, so new pods stay in `ImagePullBackOff`. Because `maxUnavailable: 0`, Kubernetes refuses to kill any old pods until new pods are Ready — so the rollout is completely frozen.

### Smoking Gun

```bash
kubectl get pods -n lab
```
```
NAME                           READY   STATUS             RESTARTS
frontend-app-OLD-xxx           1/1     Running            0         ← old pods still alive
frontend-app-OLD-xxx           1/1     Running            0
frontend-app-OLD-xxx           1/1     Running            0
frontend-app-OLD-xxx           1/1     Running            0
frontend-app-NEW-xxx           0/1     ImagePullBackOff   0         ← new pod stuck
```

```bash
kubectl rollout status deployment/frontend-app -n lab
```
```
Waiting for deployment "frontend-app" rollout to finish: 1 out of 4 new replicas have been updated...
```

```bash
kubectl describe pod <new-pod> -n lab
```
```
Events:
  Warning  Failed  kubelet  Failed to pull image "nginx:this-tag-does-not-exist":
  not found
```

### Why Some Users Still Get HTTP 200

`maxUnavailable: 0` means **zero old pods can be killed** until a replacement is Ready. The new pod is stuck in `ImagePullBackOff` and never becomes Ready. So all 4 old pods keep running and serving traffic.

In a real scenario (where you had a previous working version deployed), this configuration **protected you from a complete outage** during a bad release.

### maxUnavailable and maxSurge Explained

```
Desired: 4 replicas
maxUnavailable: 0  →  Zero old pods can be removed until new pod is Ready
maxSurge: 1        →  1 extra pod allowed above 4 (so temporarily 5 pods total)

Timeline:
Old: [Pod1] [Pod2] [Pod3] [Pod4]         (4 serving)
New pod created (surge):
     [Pod1] [Pod2] [Pod3] [Pod4] [NewX]  (5 total, NewX stuck ImagePullBackOff)
maxUnavailable: 0 → cannot kill Pod1 yet
FROZEN: rollout cannot proceed or reverse
```

### Fix Option 1 — Rollback (Fastest at 2 AM)

```bash
kubectl rollout undo deployment/frontend-app -n lab
```

This instantly reverts to the previous working ReplicaSet. Check history first:
```bash
kubectl rollout history deployment/frontend-app -n lab
```

### Fix Option 2 — Fix Forward (If you know the correct image)

```bash
kubectl set image deployment/frontend-app frontend=nginx:alpine -n lab
```

Rollout resumes with the corrected image.

### Rollout Strategy Reference

| Strategy | Description | Use Case |
|----------|-------------|----------|
| `maxUnavailable: 0, maxSurge: 1` | Zero downtime, one pod at a time | Production, stateless apps |
| `maxUnavailable: 1, maxSurge: 0` | Replace one at a time, no extra capacity | Resource-constrained environments |
| `maxUnavailable: 25%, maxSurge: 25%` | Default Kubernetes | General purpose |
| `Recreate` | Kill all, then start all | Stateful apps that can't run multiple versions |

### Key Lesson

When a rollout is stuck:
1. Check new pod status first (`ImagePullBackOff`, `CrashLoopBackOff`, `Pending`)
2. The old pods surviving = `maxUnavailable: 0` protecting you
3. Fastest fix = `kubectl rollout undo` — don't waste time diagnosing at 2 AM, restore service first, investigate after

</details>
