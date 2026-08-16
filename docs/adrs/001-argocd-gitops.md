# ADR-001: ArgoCD (GitOps) Over Manual `kubectl apply` / CI-Driven Deploys

**Status:** Accepted
**Date:** 2026-06-24

## Context

Every service and platform component in this project (accounts,
transactions, api-gateway, RBAC, Ingress, and later the
kube-prometheus-stack monitoring release) needs a deployment
mechanism. The simplest option is what was used throughout early
development: run `kubectl apply -f kubernetes/...` directly against
the cluster whenever a manifest changes.

That works fine for a single developer iterating locally, but it has
no answer for a few questions that matter in a real team/production
setting:

- If someone runs `kubectl patch` or `kubectl edit` directly against
  the cluster to fix something urgently, what un-does that once the
  urgency passes — does it silently drift from what's in Git forever?
- If a deploy needs to happen from CI, does the CI pipeline need
  cluster-admin credentials to run `kubectl apply` itself?
- Is there an audit trail of *what* was deployed and *when*, beyond
  scrolling through `kubectl rollout history`?

## Decision

Installed ArgoCD inside the cluster and defined one `Application`
resource per component (`accounts`, `transactions`, `api-gateway`,
plus later `kube-prometheus-stack` and `monitoring-extras`), each
pointing at a specific path in the GitHub repository with:

```yaml
syncPolicy:
  automated:
    prune: true
    selfHeal: true
  syncOptions:
    - CreateNamespace=true
```

Git becomes the single source of truth. Deploying a change means
`git push`, not `kubectl apply` — ArgoCD polls the repository and
reconciles the cluster to match automatically.

## Alternatives Considered

**Plain `kubectl apply` from a CI pipeline** — rejected. This just
moves the manual-apply problem into CI: the pipeline would need
broad cluster-write credentials, there's still no `selfHeal` against
manual drift, and there's no equivalent of ArgoCD's diff/sync UI to
see what's about to change before it happens.

**Flux (alternative GitOps tool)** — not evaluated in depth; ArgoCD
was chosen primarily for its web UI (useful for demonstrating
Sync/Health status visually — see README screenshots) and because it
is the more commonly requested GitOps tool in the DevOps/Platform
Engineering job postings this portfolio targets.

**No automation — deploy manually as needed** — this was, in effect,
the state of the project before ArgoCD was introduced. Rejected for
anything beyond initial local iteration, for the reasons in Context
above.

## Consequences

- Any manual `kubectl edit`/`kubectl patch` against a resource managed
  by an ArgoCD Application gets automatically reverted back to what's
  in Git (`selfHeal: true`) — this was directly observed and had to be
  worked around once, when a fix needed to go through Git instead of a
  quick manual patch, during the observability work (see
  `docs/adrs/004-observability-stack.md` and its linked incidents).
- `prune: true` means deleting a manifest file from Git and pushing
  is enough to remove the corresponding resource from the cluster —
  no separate `kubectl delete` step.
- Adds one more moving part to operate and understand (ArgoCD itself
  needs to be running and healthy for deploys to take effect), which
  is a reasonable cost given the project's goal of demonstrating
  production-representative GitOps practice, not just the fastest
  path to a running pod.
