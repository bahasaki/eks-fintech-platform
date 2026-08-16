# Incident: Helm release rename silently broke ServiceMonitor/PrometheusRule matching

**Date:** 2026-08-03
**Service:** accounts, transactions, api-gateway (metrics scraping);
monitoring-extras (alert rules)
**Severity:** Medium (no scraping/alerting for all three services for
the duration of the incident; no application-level impact)

---

## Symptoms

After successfully migrating the `kube-prometheus-stack` Helm release
to ArgoCD (new pods `Running`, Application `Synced`/`Healthy`), the
Grafana dashboard that had been showing live data all session suddenly
showed `No data` on every panel. Checking Prometheus directly:

```
Ctrl+F "account" on http://localhost:9090/targets -> 0/0 results
```

None of the three services appeared as scrape targets at all — not
even as `DOWN`. Only built-in kube-prometheus-stack components
(alertmanager, apiserver, coredns, grafana, kube-controller-manager,
kube-etcd) were listed.

## Investigation

1. Confirmed the ServiceMonitors and Services themselves were
   unchanged and correctly configured (label selectors, port names —
   all previously verified working before the migration).
2. Checked what selector the new Prometheus instance was actually
   using:
   ```bash
   kubectl get prometheus -n monitoring -o jsonpath='{.items[0].spec.serviceMonitorSelector}'
   # -> {"matchLabels":{"release":"kube-prometheus-stack"}}
   ```
3. Compared against the label on the existing ServiceMonitors:
   ```yaml
   labels:
     release: prometheus
   ```
   Mismatch found: `prometheus` vs `kube-prometheus-stack`.

## Root Cause

Before the ArgoCD migration, the Helm release had been installed
manually with `helm install prometheus prometheus-community/...`,
naming the release `prometheus`. kube-prometheus-stack's chart derives
its default `serviceMonitorSelector` from the release name — so
Prometheus was configured to only scrape ServiceMonitors labeled
`release: prometheus`.

Migrating to ArgoCD required uninstalling the manual release and
recreating it as a fresh Helm release managed by an ArgoCD Application
named `kube-prometheus-stack` — which changed the release name, and
therefore the label value Prometheus's operator-generated selector
expects. All three ServiceMonitors and the PrometheusRule still
carried the old `release: prometheus` label from when they were
originally created, so the selector match silently failed for all of
them — no error anywhere, just an empty Targets list and (separately)
zero alert rules loaded.

## Fix

Updated the `release` label on all four affected manifests:

```yaml
# kubernetes/accounts/servicemonitor.yaml
# kubernetes/transactions/servicemonitor.yaml
# kubernetes/api-gateway/servicemonitor.yaml
# observability/alerts/fintech-services-alerts.yaml
labels:
  release: kube-prometheus-stack  # was: prometheus
```

```bash
git add kubernetes/accounts/servicemonitor.yaml kubernetes/transactions/servicemonitor.yaml kubernetes/api-gateway/servicemonitor.yaml observability/alerts/fintech-services-alerts.yaml
git commit -m "fix: update release label to match new Helm release name"
git push
```

Then forced a sync on all four affected ArgoCD Applications
(`accounts`, `transactions`, `api-gateway`, `monitoring-extras`) to
avoid waiting for the default ~3-minute poll interval.

## Verification

```
http://localhost:9090/targets, Ctrl+F "accounts" ->
serviceMonitor/accounts/accounts/0, 2/2 up
```
Confirmed for all three services within ~90 seconds of the sync.
Grafana dashboard panels returned to showing live data once ~5 minutes
of fresh scrape history had accumulated (needed for the `rate()`-based
queries).

## Prevention

No structural change made — this coupling (Helm release name ↔
`release:` label on every ServiceMonitor/PrometheusRule) is inherent
to how kube-prometheus-stack's default selector works, not something
to engineer around for a project this size. Documented explicitly in
`observability/runbook-instrument-fastapi-service.md`'s failure-mode
table so it's the first thing checked next time a target silently
disappears after any Helm-release-level change.

## Lessons Learned

- **Renaming a Helm release isn't just a cosmetic change** — anything
  that references the release name in a label selector (which
  kube-prometheus-stack does by default for `serviceMonitorSelector`
  and `ruleSelector`) breaks silently, with no error message anywhere
  in the chain. The ServiceMonitor applies fine, Prometheus starts
  fine, there's just an empty Targets list.
- **"No data" on a previously-working dashboard after an unrelated
  infrastructure change is a strong signal to check label selectors**
  before assuming the metrics pipeline itself is broken.
- **This is a class of failure specific to any migration that touches
  a component's identity** (name, namespace, release) while other
  resources reference that identity by label rather than by a stable
  ID — worth checking for on any future Helm release rename, not just
  this one.
