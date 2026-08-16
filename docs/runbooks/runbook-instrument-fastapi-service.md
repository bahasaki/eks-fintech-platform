# Runbook: instrumenting a FastAPI service with Prometheus metrics

Reusable procedure for exposing `/metrics` on a FastAPI service running on
the `fintech` kind cluster and wiring it into the kube-prometheus-stack
already deployed in the `monitoring` namespace.

**Status: complete for all three services** — `accounts` (2026-07-10),
`transactions` and `api-gateway` (2026-07-11/12). All three confirmed
`UP` in Prometheus Targets. This runbook is now a reference for future
services (e.g. anything added in later phases), not an active checklist.

## Prerequisites

- kube-prometheus-stack is already running in `monitoring` namespace
  (Helm release `prometheus`).
- Service uses the `kind-fintech` context — always verify first:
  ```bash
  kubectl config current-context
  ```

## Steps

### 1. Add the dependency

In `services/<service-name>/requirements.txt`, add:
```
prometheus-fastapi-instrumentator==7.0.0
```

### 2. Instrument the app

In `services/<service-name>/main.py`, immediately after `app = FastAPI(...)`:
```python
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="...")
Instrumentator().instrument(app).expose(app)
```

Don't touch anything else — this wraps all existing routes automatically
via middleware. No per-route changes needed.

### 3. Name the Service port

In `kubernetes/<service-name>/service.yaml`, add `name: http` to the port
block (ServiceMonitor references ports by name, not number):
```yaml
ports:
  - name: http
    protocol: TCP
    port: 80          # or whatever this service uses
    targetPort: 8000  # or whatever this service uses
```

### 4. Create the ServiceMonitor

New file `kubernetes/<service-name>/servicemonitor.yaml`:
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: <service-name>
  namespace: <service-name>
  labels:
    release: prometheus   # REQUIRED — kube-prometheus-stack's Prometheus
                           # Operator only picks up ServiceMonitors with
                           # this label. Without it, Prometheus silently
                           # ignores the object — no error, just never
                           # shows up as a target.
spec:
  selector:
    matchLabels:
      app: <service-name>   # must match the Service's label
  endpoints:
    - port: http
      path: /metrics
      interval: 15s
```

### 5. Build and load the dev image

```bash
docker build -t <service-name>-service:dev -f services/<service-name>/Dockerfile services/<service-name>/
kind load docker-image <service-name>-service:dev --name fintech
```

Confirm the image landed on all nodes (should say "loading..." for each of
`fintech-worker`, `fintech-worker2`, `fintech-control-plane`).

### 6. Check the Deployment manifest's image reference

Before committing — check `kubernetes/<service-name>/deployment.yaml`. If it
still points at ECR (`...ecr.us-east-1.amazonaws.com/...`), this will
reproduce the ImagePullBackOff incident from 2026-07-10. Update to:
```yaml
image: <service-name>-service:dev
imagePullPolicy: Never
```

### 7. Commit, push, sync

```bash
git add services/<service-name>/ kubernetes/<service-name>/
git commit -m "feat: instrument <service-name> with Prometheus metrics"
git push
kubectl patch application <service-name> -n argocd --type merge -p '{"operation": {"sync": {}}}'
```

### 8. Verify

```bash
kubectl get application <service-name> -n argocd
kubectl get servicemonitor -n <service-name>
```

Then check Prometheus directly:
```bash
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
```
Open `http://localhost:9090/targets` — look for
`serviceMonitor/<service-name>/<service-name>/0` with all replicas `UP`.

## Common failure points

| Symptom | Cause | Fix |
|---|---|---|
| ArgoCD shows old revision hash in `describe` | Sync hasn't polled yet (default ~3 min interval) | Force sync manually (step 7's `kubectl patch`) |
| ServiceMonitor exists in Git but not in cluster | Same as above — ArgoCD hasn't synced | Same fix |
| ServiceMonitor exists in cluster but target never appears in Prometheus | Missing `release: prometheus` label | Add the label, commit, sync |
| Target appears but shows `DOWN` | Port name mismatch between Service and ServiceMonitor | Confirm `port: http` in ServiceMonitor matches `name: http` in Service |
| Pod `ImagePullBackOff` after rebuild | Deployment still references ECR image | See step 6 — this recurred independently on `api-gateway` (pre-existing, same root cause as the accounts incident) |
| `docker build` fails with `error getting credentials - err: exit status 1` pulling a *public* base image | Docker Desktop's `credsStore` (`desktop.exe` on Windows/WSL2) hung and can't be reached, even though no auth is actually needed for public images | Temporarily strip `credsStore` from `~/.docker/config.json` (back it up first): `echo '{"auths": {}}' > ~/.docker/config.json`. Restart Docker Desktop if this doesn't help. |
| Target briefly shows `DOWN` / `connect: connection refused` for services that were previously `UP`, across multiple unrelated services at once | Not a config issue — this matches a recurring pattern of the whole `monitoring` and app namespaces restarting together, seen a few times late at night (Docker Desktop/WSL2 related, not Kubernetes-level). Self-resolves within a minute or two. | Re-check `kubectl get pods -n <namespace>` for recent `RESTARTS` timestamps before debugging further. If pods are `Running` and restarts are recent, just wait and refresh. |
| `main.py` missing the `Instrumentator().instrument(app).expose(app)` line even though the import was added | Easy to miss when copy-pasting — the import alone doesn't do anything | Always `cat` the file back after editing and confirm the line is present before building the image |
