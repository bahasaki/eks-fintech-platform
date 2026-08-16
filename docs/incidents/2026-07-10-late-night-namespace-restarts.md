# Incident: recurring late-night whole-namespace pod restarts (Docker Desktop/WSL2)

**Date:** first observed 2026-07-10, recurred multiple times through
2026-08-03
**Service:** all namespaces intermittently — most visibly `monitoring`
and `accounts`/`transactions`
**Severity:** Low (self-resolving within minutes each time; no
sustained impact)

---

## Symptoms

Recurring pattern noticed across multiple sessions, always overnight
or in the early-morning hours: every pod across several unrelated
namespaces showed a fresh `RESTARTS` count with a very similar `AGE`
(e.g. `RESTARTS: 4 (24h ago)`, `8 (24h ago)`, `12 (4m ago)` all
appearing together on unrelated pods), and Prometheus targets
`briefly` flipped to `DOWN` / `connection refused` for multiple
services simultaneously before recovering on their own.

Example, all pods in `monitoring` restarting within the same short
window despite belonging to entirely independent components
(Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics,
the operator):
```
alertmanager-...   Running   6 (12h ago)
prometheus-grafana-...   Running   12 (4m42s ago)
prometheus-kube-prometheus-operator-...   Running   11 (5m48s ago)
prometheus-kube-state-metrics-...   Running   12
prometheus-prometheus-...   Running   6 (12h ago)
prometheus-prometheus-node-exporter-...  Running   5 (12h ago)
```

## Investigation

1. Ruled out application-level or Kubernetes-scheduling causes by
   checking resource pressure at the time:
   ```bash
   kubectl top nodes
   ```
   consistently showed low CPU/memory usage (2-5% CPU, ~22% memory) —
   not a resource-starvation event.
2. Checked exact restart timestamps against wall-clock time — restarts
   consistently clustered in late-night/early-morning hours across
   multiple separate occurrences, not correlated with any deliberate
   action taken during active work sessions.
3. Checked `kubectl describe pod` `Last State` on affected pods —
   `Reason: Unknown, Exit Code: 255` was a recurring pattern: an
   uninformative code typically meaning the process inside the
   container terminated abnormally without Kubernetes capturing a
   specific cause (as opposed to `OOMKilled`, which would point to
   memory pressure).
4. Cross-referenced with a separate, related finding: ArgoCD's own
   `config-reloader` logs showed `dial tcp 127.0.0.1:9090: connect:
   connection refused` and `Reconnect to redis because error: dial
   tcp: lookup argocd-redis: i/o timeout` in the same time windows —
   pointing at DNS/networking within the cluster briefly failing, not
   any single component crashing independently.

## Root Cause

Not a Kubernetes-level or application-level issue. The consistent
pattern — multiple unrelated pods across multiple unrelated
namespaces restarting together, at times correlating with idle
overnight hours, combined with generic `Exit Code: 255` and transient
DNS/connection failures at the cluster-networking level — points to
the underlying kind cluster's host environment (Docker Desktop and/or
WSL2 on Windows) being paused, throttled, or briefly restarted (e.g.
by the laptop sleeping, Docker Desktop's own background maintenance,
or WSL2 resource reclamation). This is outside the Kubernetes cluster
itself; from kind's perspective, the "hardware" underneath it
disappeared and came back.

## Fix

No fix applied — nothing to fix at the Kubernetes or application
level. Each occurrence self-resolved within a few minutes as pods were
automatically rescheduled/restarted by Kubernetes' normal reconciliation.

## Verification

Confirmed self-resolution each time via `kubectl get pods -A` showing
all pods back to `Running` with no further restarts, and (for the
`monitoring` namespace specifically) Prometheus Targets returning to
`UP` without any manual intervention.

## Prevention

None practical from within the cluster — this is a host-environment
characteristic, not a cluster misconfiguration. The practical
prevention is procedural: recognizing the pattern quickly (see Lessons
Learned) avoids spending time debugging Kubernetes-level causes that
aren't the actual source.

## Lessons Learned

- **When multiple unrelated services/namespaces show fresh restarts
  simultaneously, suspect the underlying host (Docker Desktop/WSL2),
  not the Kubernetes objects themselves.** A single pod restarting
  is worth investigating individually; several independent components
  restarting together in the same narrow window almost never has a
  Kubernetes-level explanation.
- **`kubectl top nodes` showing low resource usage at the time rules
  out the most common single-cluster explanation (resource pressure)
  quickly** and should be the first check before going deeper.
- **This is now a recognized, named pattern for this environment** —
  documented in `observability/runbook-instrument-fastapi-service.md`'s
  failure-mode table specifically so future occurrences are identified
  in seconds rather than re-investigated from scratch each time.
