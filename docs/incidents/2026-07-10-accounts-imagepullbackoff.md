# Incident: accounts pod stuck in ImagePullBackOff

**Date:** 2026-07-10
**Service:** accounts
**Severity:** Low (2/3 replicas remained healthy; no user-facing impact)
**Detected during:** Phase 4 (Observability) work — unrelated to the fix itself

---

## Symptoms

- `kubectl get application -n argocd` showed the `accounts` ArgoCD Application as
  `Synced` / `Degraded`.
- `kubectl get pods -n accounts` showed one pod
  (`accounts-6dc464bd88-ch6pj`) stuck in `ImagePullBackOff`, age 3d13h.
  The other two pods were `1/1 Running`.

## Investigation

1. Confirmed cluster resources were not under pressure
   (`kubectl top nodes` — all nodes at 2–5% CPU, ~22% memory). Ruled out
   resource starvation as a cause.
2. `kubectl describe pod -n accounts accounts-6dc464bd88-ch6pj` showed the
   real error in the Events section:
   ```
   Failed to pull image "...ecr.us-east-1.amazonaws.com/accounts-service:latest":
   pull access denied, repository does not exist or may require authorization:
   authorization failed: no basic auth credentials
   ```
3. Checked pod start time — this pod was scheduled 2026-07-06, i.e. 3+ days
   before this Phase 4 session started. Confirmed via timestamps that this
   was a pre-existing issue, not something introduced by the observability
   work happening in parallel.
4. Attempted a manual fix with `kubectl set image` + `imagePullPolicy: Never`.
   This was immediately reverted — ArgoCD's `selfHeal: true` sync policy
   detected the drift from Git and recreated the old (broken) ReplicaSet
   within seconds. Confirmed via `kubectl get application accounts -n argocd
   -o jsonpath='{.spec.syncPolicy}'` → `{"automated":{"prune":true,"selfHeal":true},...}`.

## Root Cause

The `accounts` Deployment manifest in Git referenced a private AWS ECR image:
```yaml
image: 774493573578.dkr.ecr.us-east-1.amazonaws.com/accounts-service:latest
imagePullPolicy: Always
```

This works on EKS because EKS nodes authenticate to ECR automatically via
their IAM Role. It does **not** work on kind — kind nodes are plain Docker
containers with no AWS credentials and no IAM integration.

The two "healthy" pods were not actually evidence the manifest was correct —
they were running an image that happened to already be cached in
containerd on their specific nodes from an earlier manual `kind load
docker-image`. When the broken pod's sandbox was recreated
(`SandboxChanged` event), it tried to pull fresh from ECR — on a node
without that cached image — and failed.

In short: **the manifest was written for EKS and never adapted for local
kind development.** The cluster had been silently relying on stale local
image caches to mask this for days.

## Fix

1. Built a local dev image and loaded it onto all three kind nodes:
   ```bash
   docker build -t accounts-service:dev -f services/accounts/Dockerfile services/accounts/
   kind load docker-image accounts-service:dev --name fintech
   ```
2. Updated `kubernetes/accounts/deployment.yaml` in Git (not a manual
   `kubectl` patch, since ArgoCD would just revert that):
   ```yaml
   image: accounts-service:dev
   imagePullPolicy: Never
   ```
3. Committed and pushed. Forced an ArgoCD sync:
   ```bash
   kubectl patch application accounts -n argocd --type merge -p '{"operation": {"sync": {}}}'
   ```
4. Verified: `kubectl get pods -n accounts` → both replicas `Running` on the
   new ReplicaSet; `kubectl get application accounts -n argocd` → `Synced` / `Healthy`.

## Lessons Learned

- **Manifests written for EKS need explicit adaptation for kind** — at
  minimum, the `image` and `imagePullPolicy` fields. A single Deployment
  manifest that's expected to work on both environments is a trap; the
  "it's been fine" state can be an illusion caused by stale local caches,
  not correctness.
- **`kubectl` changes are pointless against `selfHeal: true`.** Git is the
  only source of truth ArgoCD respects. Any manual patch will be reverted
  within the next reconciliation loop — don't waste time debugging why a
  manual fix "isn't sticking."
- **A pod being `Running` isn't proof the manifest is correct** — it can
  just mean the node happens to have a cached image from a previous manual
  load. Don't treat partial health as validation.
- **Follow-up action:** the underlying architectural problem (one manifest,
  two environments) isn't fully solved by this fix — it's patched for kind
  specifically. A Kustomize `base` + `overlays/dev` + `overlays/prod`
  structure would let the environment be a deploy-time parameter instead of
  a manually toggled file. Tracked as a future improvement, not yet done.
