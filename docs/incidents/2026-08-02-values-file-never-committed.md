# Incident: values-kind.yaml worked locally but was never committed to Git

**Date:** 2026-08-02 (discovered during ArgoCD migration; file had existed
since the start of Phase 4, 2026-07-07)
**Service:** kube-prometheus-stack (monitoring)
**Severity:** Medium (blocked the ArgoCD migration; no impact on the
already-running manually-installed stack)

---

## Symptoms

Migrating the monitoring Helm release to an ArgoCD Application failed
during manifest generation:

```
Failed to load target state: failed to generate manifest for source
1 of 2: rpc error: code = Unknown desc = Manifest generation error
(cached): failed to execute helm template command: failed running
helm: `helm template . ... --values <path to cached source>/observability/values-kind.yaml
... --include-crds` failed exit status 1: Error: open
<path to cached source>/observability/values-kind.yaml:
no such file or directory
```

The file existed and was actively working locally — it had been used
for the original `helm install` three weeks earlier, at the very start
of Phase 4, and every `helm upgrade`/manual apply since.

## Investigation

1. Confirmed the file was present on disk: `cat
   observability/values-kind.yaml` returned its full content, no
   issue.
2. Checked whether ArgoCD's resolved `$values` reference pointed to
   the right relative path — the repo-server logs showed:
   ```
   "resolved value files: [/tmp/_argocd-repo/.../observability/values-kind.yaml]"
   ```
   The path resolution itself was correct.
3. Checked `.gitignore` for any pattern that might exclude the file —
   nothing matched (`*.tfvars`, `*-secret.yaml`, etc. — none of these
   apply to `values-kind.yaml`).
4. Checked Git directly, rather than assuming:
   ```bash
   git log --all --full-history -- observability/values-kind.yaml
   # -> empty output
   git show HEAD:observability/values-kind.yaml
   # -> fatal: path 'observability/values-kind.yaml' exists on disk, but not in 'HEAD'
   ```
   This confirmed it definitively: the file had never been committed,
   despite having been in active local use for weeks.

## Root Cause

`helm install` and `docker build` both read files from the local
filesystem — they have no concept of Git state. This file was created
at the very beginning of Phase 4, before every subsequent change had
settled into a strict "create → immediately commit" habit, and it was
simply missed. Every manual command that used it worked correctly,
which meant there was no functional signal that anything was wrong —
the gap only became visible once a tool that clones from GitHub
(ArgoCD's repo-server) tried to use the same path.

A related instance of the same root cause was found in the same
`git status` check: `services/accounts/main.py` and
`requirements.txt` also had the `Instrumentator()` code only on disk,
not in the commit that was believed to have included it
(`7321ed3`, "add ServiceMonitor for accounts") — that commit had only
included the Kubernetes manifests, not the application code change it
depended on.

## Fix

```bash
git add observability/values-kind.yaml services/accounts/main.py services/accounts/requirements.txt
git commit -m "fix: commit files that existed on disk but were never in Git"
git push
```
Followed by a hard refresh on the ArgoCD Application to bypass any
cached (stale) source state:
```bash
kubectl patch application kube-prometheus-stack -n argocd --type merge \
  -p '{"metadata": {"annotations": {"argocd.argoproj.io/refresh": "hard"}}}'
```

## Verification

```bash
kubectl get application kube-prometheus-stack -n argocd -o jsonpath='{.status.conditions}'
```
returned empty (no error conditions), and `kubectl get pods -n
monitoring` showed the full stack coming up under the new
ArgoCD-managed release.

## Prevention

No tooling change made (e.g. a pre-commit hook or CI check for
untracked-but-referenced files) — noted as a manual discipline item
instead: after any multi-file change, run `git status` before moving
on, not just after the files you *remember* changing.

## Lessons Learned

- **A file working correctly locally is not evidence it's in Git.**
  `helm install`, `docker build`, and any tool reading from the local
  filesystem will happily use an uncommitted file for weeks with zero
  indication anything is wrong.
- **The failure only surfaces when something clones from the remote**
  — in this case, ArgoCD's repo-server. This means any GitOps
  migration is also, incidentally, an audit of whether local state and
  Git state actually match. That's a feature, not just an annoyance —
  it caught two real gaps (the values file and the accounts app code)
  that had been silently diverged for weeks.
- **`git show HEAD:<path>` is the authoritative way to check**  whether
  something is really committed — `cat` and `ls` only tell you about
  the filesystem, not Git's actual tracked state.
