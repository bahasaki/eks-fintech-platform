# Incident: GitHub Actions CI Pipeline — Matrix Skip Logic and Git Rebase Ordering Bugs

**Status:** Resolved
**Date:** 2026-08-16
**Severity:** Medium (silently rebuilt/pushed unchanged services; second bug blocked the pipeline outright)

## Symptoms

**Bug 1 — matrix ran all services regardless of what changed.**
A commit touching only `services/accounts/main.py` triggered
`build-and-push` jobs for `accounts`, `transactions`, *and*
`api-gateway` — all three showed green checkmarks in the GitHub
Actions UI.

**Bug 2 — pipeline failed outright on the next test.**
After fixing Bug 1 and pushing a change to `services/transactions/`,
the single `build-and-push (transactions)` job ran correctly but
failed on its last step with exit code 128:
```
error: cannot pull with rebase: You have unstaged changes.
error: Please commit or stash them.
Error: Process completed with exit code 128.
```

## Investigation

**Bug 1:** The `detect-changes` job correctly identified that only
`accounts` had changed (via `dorny/paths-filter`), but the
`build-and-push` job's `strategy.matrix.service` list was still the
static `[accounts, transactions, api-gateway]`. The `if:` condition
used to skip unaffected services was written per-step
(`if: steps.check.outputs.skip != 'true'`), not on the job itself. A
skipped *step* still reports the *job* as successful — so all three
matrix entries showed green, even though `docker build`/`docker push`
never actually ran for `transactions` or `api-gateway`. Confirmed by
checking ECR directly:
```bash
aws ecr describe-images --repository-name transactions-service \
  --region us-east-1 --query 'imageDetails[*].imageTags' --output text
# latest   (only the old image from the original EKS deployment — no new tag)
```

**Bug 2:** After fixing the matrix to only include changed services,
the workflow's final steps ran in this order:
```
1. sed -i ... (rewrites kubernetes/<service>/deployment.yaml, unstaged)
2. git pull --rebase origin main   ← fails: working tree is dirty
3. git add / commit / push
```
`git rebase` refuses to run with uncommitted changes present, and
step 1 had already modified a tracked file before step 2 tried to
sync with `origin/main`.

## Root Cause

**Bug 1:** Per-step `if:` conditions inside a matrix job silently skip
individual steps while still reporting the overall job (and matrix
entry) as `success` — this is not equivalent to excluding that matrix
entry from running at all, and gives a false-positive "everything
passed" signal in the Actions UI.

**Bug 2:** `git pull --rebase` was placed *after* a file-modifying step
instead of *before* it, so the sync step always ran against a dirty
working tree.

## Fix

**Bug 1:** Replaced the static matrix with one built dynamically by
`detect-changes`, using `dorny/paths-filter` output to construct a
JSON array (via `jq`) of only the changed service names, consumed by
`build-and-push` via `strategy.matrix.service: ${{ fromJson(...) }}`.
An empty array means the `build-and-push` job doesn't get created at
all — not "created and immediately skips every step."

**Bug 2:** Reordered the final steps so `git pull --rebase` runs
immediately after checkout, before `sed` touches any file:
```
1. git pull --rebase origin main   (working tree still clean)
2. sed -i ... (now safe to modify)
3. git add / commit / push
```

## Verification

Ran three separate isolated test pushes, one per service, each
touching only that service's `services/<name>/main.py`:

| Push | Jobs that ran | Result |
|------|---------------|--------|
| `services/accounts/**` changed | `build-and-push (accounts)` only | ✅ Success |
| `services/transactions/**` changed | `build-and-push (transactions)` only | ✅ Success (after Bug 2 fix) |
| `services/api-gateway/**` changed | `build-and-push (api-gateway)` only | ✅ Success |

Confirmed via `git pull` + `grep image:` on all three
`kubernetes/*/deployment.yaml` files that each now references a real
ECR image tagged with the exact `git.sha` of the commit that triggered
its build, and cross-checked each tag exists in ECR via
`aws ecr describe-images`.

## Prevention

- When using per-matrix-entry conditional logic in GitHub Actions,
  filter the matrix itself (a dynamically-built JSON array) rather
  than conditionally skipping steps inside every entry — a skipped
  step still counts toward job success and produces a misleading
  all-green UI.
- Any workflow step that both reads and writes the same Git-tracked
  files needs `git pull --rebase` (or equivalent sync) to run *before*
  the file is modified, not after — the ordering matters even when
  each step "looks" independent in the YAML.
- Test each element of a build matrix in isolation (one small,
  targeted change per service) rather than assuming a single
  multi-service commit exercises every path — Bug 1 was invisible
  until each service was tested one at a time, because a green UI
  hid the fact that two of three builds never actually executed.

## Lessons Learned

A fully green GitHub Actions run is not proof that the intended work
happened — it only proves no step exited non-zero. Bug 1 shipped
"successfully" through CI multiple times before the gap between
"job succeeded" and "job actually built an image" was caught, purely
because per-step skips report the same status as steps that ran and
passed. Validating a CI pipeline requires checking the actual
downstream artifact (here, ECR image tags) rather than trusting the
Actions UI's checkmarks alone.
