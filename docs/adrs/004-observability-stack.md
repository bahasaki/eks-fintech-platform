# ADR-004: Observability Stack Architecture (Phase 4)

**Status:** Accepted
**Date:** 2026-08-03

---

## Context

The platform (three FastAPI microservices — accounts, transactions,
api-gateway — running on a local kind cluster, managed via ArgoCD)
had no visibility into runtime behavior beyond `kubectl logs` and manual
`kubectl get pods`. There was no way to answer basic operational
questions without direct cluster access: What's the current request
rate? Is any service returning errors? Is latency degrading? Is a pod
stuck in a restart loop?

This needed to be closed before the platform could be described as
production-grade, and it's also one of the most commonly assessed
skills in DevOps/Cloud Engineer interviews.

## Decision

Deployed the [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts)
Helm chart (Prometheus + Grafana + Alertmanager + Node Exporter +
kube-state-metrics + Prometheus Operator), instrumented all three
FastAPI services with `prometheus-fastapi-instrumentator`, and built
a custom Grafana dashboard and three PrometheusRule alerts on top.

Key sub-decisions:

**1. kube-prometheus-stack over a manually-assembled Prometheus Operator setup**
The chart bundles the operator, CRDs (ServiceMonitor, PrometheusRule),
and sensible defaults for cluster-level metrics (node health, pod
state) in one install, rather than wiring each component by hand.

**2. `retention: 2d` for Prometheus**
This is a local dev cluster, not a long-lived production environment.
Two days of history is enough to debug a recent issue or demo a
dashboard; anything longer just consumes disk for data that will never
be looked at again. A production deployment would size retention
against actual incident-investigation needs (typically 15-30 days) or
use remote_write to a long-term store (Thanos, Cortex, Amazon Managed
Prometheus) instead of relying on local retention at all.

**3. Dashboards and alerts as code (ConfigMap + PrometheusRule), not
   configured via UI**
Grafana's dashboard sidecar auto-loads any ConfigMap labeled
`grafana_dashboard: "1"` in the cluster. Combined with PrometheusRule
CRDs for alerts, this means the entire observability configuration —
not just the infrastructure — lives in Git and goes through the same
review/rollback path as everything else. A dashboard built by clicking
in the Grafana UI would be invisible to Git, unreviewable, and lost on
any full stack recreation (which happened once during this phase, and
the ConfigMap/PrometheusRule approach meant nothing had to be manually
rebuilt).

**4. `prometheus-fastapi-instrumentator` over hand-rolled metrics**
The library adds standard HTTP metrics (`http_requests_total`,
`http_request_duration_seconds` histograms) via middleware with three
lines of code per service, using Prometheus's own histogram bucket
convention. It doesn't cover outbound calls made by api-gateway to
accounts/transactions — that would need explicit httpx client
instrumentation, which wasn't added (see Consequences).

**5. Everything deployed through ArgoCD, not `helm install` / `kubectl apply`**
Initially installed manually to validate the stack worked before adding
GitOps management on top — deliberately separating "does this
configuration work" from "does this work through ArgoCD" to keep
failure domains isolated while debugging. Once confirmed working, the
Helm release and the dashboard/alert manifests were migrated to two
separate ArgoCD Applications (see Alternatives Considered for why two,
not one).

## Alternatives Considered

**Single ArgoCD Application for everything (Helm chart + dashboard +
alerts combined)**
Rejected in favor of two separate Applications:
`kube-prometheus-stack` (the Helm release itself) and
`monitoring-extras` (the ConfigMap/PrometheusRule manifests). The Helm
chart is an external dependency with its own release cadence; the
dashboard and alerts are project-specific content that changes far more
often and independently. Bundling them would mean every dashboard tweak
gets reconciled alongside chart-level Helm state, and any future chart
version bump would need to account for custom content in the same
sync — worse separation of concerns for no real benefit at this scale.

**Prometheus Operator assembled from individual manifests, instead of
the Helm chart**
Would give a more granular understanding of what each CRD does, but at
the cost of significant setup time better spent building the actual
service metrics and alerting logic. The Helm chart is also what most
teams actually run in production, so it's the more realistic skill to
practice.

**Custom exporter / manual `/metrics` implementation over
`prometheus-fastapi-instrumentator`**
Would allow full control (e.g. in-flight request tracking, which the
library's default configuration doesn't export — confirmed by checking
`/metrics` output directly rather than assuming). Rejected because the
library covers the metrics that actually matter for this project's
current traffic levels (RPS, error rate, latency percentiles) with
minimal code, and hand-rolling metrics collection is solving a problem
the ecosystem has already solved well.

## Consequences

**Positive:**
- Full request-level visibility (RPS, error rate, p50/p95/p99 latency)
  across all three services, backed by a tested alerting pipeline —
  not just "alerts exist" but a verified `INACTIVE → PENDING → FIRING
  → INACTIVE` cycle using deliberate fault injection (see
  `docs/incidents/2026-07-14-alert-validation-podrestartloop.md`).
- Recreating the entire monitoring stack (which happened once, when
  migrating to ArgoCD required deleting the manual Helm release first)
  required zero manual dashboard/alert rebuilding — everything came
  back from Git automatically.
- Two unrelated pre-existing production issues (stale ECR image
  references causing `ImagePullBackOff` on `accounts` and later
  `api-gateway`) were discovered as a side effect of this work, not
  because observability was already running — a reminder that
  observability tooling only helps once it's actually watching the
  right things.

**Negative / accepted trade-offs:**
- `api-gateway`'s outbound calls to accounts/transactions aren't
  separately instrumented — if the gateway is slow, the dashboard can't
  currently distinguish "gateway is slow" from "gateway is slow because
  a downstream call is slow." Acceptable for now; would need explicit
  httpx client instrumentation to close this gap.
- The `values-kind.yaml` used since the start of this phase existed
  only on local disk for an extended period before being committed —
  it worked locally (`helm install` reads from disk) but wasn't
  visible to ArgoCD (which clones from GitHub) until the gap was
  caught during the ArgoCD migration. This is now a known failure mode
  to check for explicitly: local file existing and working is not the
  same as it being in Git.
- ServiceMonitor and PrometheusRule objects reference the Helm release
  name via a `release: <name>` label, which Prometheus's
  `serviceMonitorSelector` matches against. Renaming the Helm release
  (which happened when switching from a manual `helm install prometheus`
  to an ArgoCD Application named `kube-prometheus-stack`) silently broke
  scraping for all three services and the alert rules, with no error —
  just an empty Targets list. This coupling between release name and
  label selectors is easy to miss and worth calling out explicitly in
  the runbook for future reference.
- Chart version is unpinned (`targetRevision: "*"` in the ArgoCD
  Application), which means the next sync could pull a newer chart
  version with breaking changes without warning. Fine for a dev
  cluster; would pin to an explicit version in production.
