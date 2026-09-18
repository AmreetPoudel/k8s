#!/usr/bin/env bash
# ==============================================================================
# Enterprise & CKA-Grade Kubectl Aliases & Shortcuts
# Compatible with both Bash (~/.bashrc) and Zsh (~/.zshrc)
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. CORE & AUTOCOMPLETION
# ------------------------------------------------------------------------------
alias k='kubectl'

# Enable tab completion for the single-letter 'k' alias
if [ -n "$BASH_VERSION" ]; then
    source <(kubectl completion bash 2>/dev/null)
    complete -o default -F __start_kubectl k
elif [ -n "$ZSH_VERSION" ]; then
    source <(kubectl completion zsh 2>/dev/null)
    compdef __start_kubectl k
fi

# Quick namespace switcher (switch active namespace permanently for session)
# Usage: kns microservices
alias kns='kubectl config set-context --current --namespace'
alias kctx='kubectl config use-context'
alias kctxs='kubectl config get-contexts'

# ------------------------------------------------------------------------------
# 2. GET RESOURCES (kg...)
# Pattern: kg = get, p = pod, d = deployment, s = svc, etc.
# ------------------------------------------------------------------------------
# Pods
alias kgp='kubectl get pods'
alias kgpw='kubectl get pods -o wide'
alias kgpa='kubectl get pods -A'
alias kgpaw='kubectl get pods -A -o wide'
alias kgpy='kubectl get pods -o yaml'

# Deployments & DaemonSets & StatefulSets
alias kgd='kubectl get deployments'
alias kgda='kubectl get deployments -A'
alias kgdw='kubectl get deployments -o wide'
alias kgds='kubectl get daemonset'
alias kgsts='kubectl get statefulset'

# Services & Networking
alias kgs='kubectl get svc'
alias kgsa='kubectl get svc -A'
alias kgsw='kubectl get svc -o wide'
alias kgi='kubectl get ingress'
alias kgia='kubectl get ingress -A'
alias kgep='kubectl get endpoints'

# Cluster Infrastructure
alias kgn='kubectl get nodes -o wide'
alias kgns='kubectl get namespaces'
alias kgall='kubectl get all'
alias kgalla='kubectl get all -A'

# Configuration & Secrets
alias kgcm='kubectl get configmap'
alias kgcma='kubectl get configmap -A'
alias kgsec='kubectl get secret'
alias kgseca='kubectl get secret -A'

# Storage
alias kgpvc='kubectl get pvc'
alias kgpvca='kubectl get pvc -A'
alias kgpv='kubectl get pv'
alias kgsc='kubectl get sc'

# External Secrets & GitOps
alias kges='kubectl get externalsecrets -A'
alias kgcss='kubectl get clustersecretstore'
alias kgapp='kubectl get applications -n argocd'

# Events (Sorted chronologically by newest)
alias kgev='kubectl get events -A --sort-by=.metadata.creationTimestamp'

# ------------------------------------------------------------------------------
# 3. DESCRIBE RESOURCES (kd...)
# ------------------------------------------------------------------------------
alias kdp='kubectl describe pod'
alias kdd='kubectl describe deployment'
alias kds='kubectl describe svc'
alias kdi='kubectl describe ingress'
alias kdn='kubectl describe node'
alias kdpvc='kubectl describe pvc'
alias kdpv='kubectl describe pv'
alias kdsec='kubectl describe secret'
alias kdcm='kubectl describe configmap'
alias kdes='kubectl describe externalsecret'
alias kdcss='kubectl describe clustersecretstore'

# ------------------------------------------------------------------------------
# 4. LOGS & DEBUGGING (kl... / ke...)
# ------------------------------------------------------------------------------
alias kl='kubectl logs'
alias klf='kubectl logs -f'
alias klf50='kubectl logs -f --tail=50'
alias klf100='kubectl logs -f --tail=100'
alias klp='kubectl logs -p'                     # Previous terminated container logs (post-crash)
alias ke='kubectl exec -it'                     # Interactive exec into container

# ------------------------------------------------------------------------------
# 5. ROLLING UPDATES & LIFECYCLE (kr...)
# ------------------------------------------------------------------------------
alias krr='kubectl rollout restart deployment'
alias krs='kubectl rollout status deployment'
alias krh='kubectl rollout history deployment'
alias kru='kubectl rollout undo deployment'

# ------------------------------------------------------------------------------
# 6. APPLY, EDIT & DELETE (ka... / kdel...)
# ------------------------------------------------------------------------------
alias kaf='kubectl apply -f'
alias kdf='kubectl delete -f'
alias kdel='kubectl delete'
alias kdelp='kubectl delete pod'
alias kdelforce='kubectl delete pod --grace-period=0 --force' # Emergency force kill
alias kedit='kubectl edit'

# ------------------------------------------------------------------------------
# 7. RAPID PROTOTYPING & CKA SHORTCUTS (Dry-Run Generator)
# ------------------------------------------------------------------------------
# Generate YAML without creating resources
alias kdr='kubectl --dry-run=client -o yaml'

# Quick temporary debugging pod (curl / alpine / netshoot)
alias kdebug='kubectl run test-debug --rm -it --image=nicolaka/netshoot -- /bin/bash'
alias kalpine='kubectl run alpine-debug --rm -it --image=alpine -- sh'
