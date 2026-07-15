# Alert validation: PodRestartLoop end-to-end test

**Date:** 2026-07-14
**Type:** Deliberate fault injection (not a real incident) — validating
that the `PodRestartLoop` PrometheusRule actually fires and reaches
Alertmanager, not just that it loads without syntax errors.

---

## Goal

Confirm the full alerting pipeline works end-to-end:
```
condition breached → Prometheus rule evaluates true → PENDING (for: 5m)
→ FIRING → Alertmanager receives and groups it
```

Loading a `PrometheusRule` without a parse error only proves the YAML is
valid — it doesn't prove the alert would actually catch a real problem.

## Method

Deliberately crashed the main process inside a running pod to trigger
real Kubernetes container restarts (not pod recreation — a `kubectl
delete pod` would reset the restart counter on a fresh pod, which
wouldn't test the same thing):

```bash
kubectl exec -n transactions <pod-name> -- python -c "import os, signal; os.kill(1, signal.SIGTERM)"
```

`kill` wasn't available in the `python:3.11-slim` image (no coreutils),
so used Python's own `os.kill` against PID 1 (the uvicorn process)
instead. Repeated a few times in quick succession to push the pod into
`CrashLoopBackOff`, which self-perpetuates via Kubernetes' backoff retry
— no need to keep manually triggering it.

## Observations

- First attempt: spread the restarts out over ~15+ minutes while
  figuring out the right command. By the time restarts accumulated
  past the alert's threshold, the earliest ones had already aged out of
  the 15-minute `increase()` window — alert stayed `INACTIVE`. Useful
  reminder that `increase(...[15m])` is a sliding window, not a
  cumulative total since pod start.
- Second attempt: triggered several restarts back-to-back, letting
  `CrashLoopBackOff`'s own retry loop generate the rest. This produced
  a concentrated burst within one window — `increase(...) > 3` evaluated
  to `~5.17`, clearing the threshold.
- Alert transitioned `INACTIVE → PENDING` immediately once the
  condition was true, then `PENDING → FIRING` after the `for: 5m` duration
  elapsed, exactly as configured.
- Confirmed in Alertmanager UI: the firing alert appeared in the
  `namespace="transactions"` group with 1 alert, fully labeled
  (`pod`, `container`, `namespace`, `severity=warning`).
- Pod self-recovered on its own once the induced crashes stopped —
  no manual pod deletion was needed to restore service.

## Lessons Learned

- **`increase()` over a fixed window can hide a real problem if events
  are spread out.** A pod restarting 5 times over 20 minutes might never
  trigger `increase(...[15m]) > 3` if no single 15-minute slice contains
  more than 3 of them. Worth keeping in mind when tuning thresholds —
  the window size should roughly match how quickly you want to detect a
  *concentrated* burst, not a slow trickle.
- **Testing an alert means testing the trigger mechanism, not just the
  YAML.** `kubectl apply` succeeding and the rule showing up in
  `/alerts` as `INACTIVE` only proves the PromQL parses — it says nothing
  about whether the condition would actually catch the failure mode it's
  meant to catch. Actually breaking something on purpose is the only way
  to know both the query and the threshold are right.
- **`kubectl delete pod` and killing PID 1 inside a container test
  different things.** Deleting the pod gives you a fresh pod with a
  restart count of 0 — useful for testing deployment/scheduling, not
  for testing a restart-count-based alert. Killing the process in place
  is what actually increments `kube_pod_container_status_restarts_total`
  on the same pod.
