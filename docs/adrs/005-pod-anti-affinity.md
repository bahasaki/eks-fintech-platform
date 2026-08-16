# ADR-005: Pod Anti-Affinity on transactions for High Availability

**Status:** Accepted
**Date:** 2026-06-24

## Context

After an HPA scale-up/scale-down cycle, inspecting where pods
actually landed showed both `accounts` and `api-gateway` replicas
spread across the cluster's two worker nodes as expected — but both
`transactions` replicas had been scheduled onto the *same* node:

```bash
kubectl get pods -n transactions -o wide
# transactions-mmrsp   fintech-worker
# transactions-xqqsz   fintech-worker
```

The default Kubernetes scheduler optimizes for resource balance, not
replica spread — with only two lightly-loaded nodes, it's free to
place both replicas of a Deployment on the same node if that node has
more free capacity at scheduling time. This defeats the purpose of
running 2 replicas: if that one node fails, `transactions` goes fully
down instead of degrading gracefully.

## Decision

Added a `preferredDuringSchedulingIgnoredDuringExecution` pod
anti-affinity rule to the `transactions` Deployment, keyed on
`topologyKey: kubernetes.io/hostname`, so the scheduler prefers
placing replicas on different nodes:

```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: transactions
          topologyKey: kubernetes.io/hostname
```

## Alternatives Considered

**`requiredDuringSchedulingIgnoredDuringExecution` (hard requirement)**
— rejected for this project. A hard requirement would refuse to
schedule a second replica at all if only one node had capacity (e.g.
during a node failure or maintenance), which is worse for
availability than a soft preference in a small, 2-node cluster. In a
larger production cluster with more nodes, a hard requirement would
be reasonable.

**Applying anti-affinity to all three services, not just
transactions** — considered but not done in this pass. `transactions`
was the one observed to actually violate co-location during testing;
the same fix is a one-line addition to `accounts`/`api-gateway` if the
same issue is observed there, but wasn't added speculatively without
evidence it was needed.

## Consequences

- After applying this and forcing a redeploy
  (`kubectl rollout restart`), `transactions` replicas were confirmed
  scheduled on separate nodes.
- A single node failure no longer takes `transactions` fully offline
  — the surviving replica on the other node continues serving traffic
  while Kubernetes reschedules the lost pod.
- Soft preference means this is not a hard guarantee under all
  cluster states (e.g. if one node is completely full), which is an
  accepted trade-off for a small cluster where a hard requirement
  could instead cause a pod to become unschedulable entirely.
