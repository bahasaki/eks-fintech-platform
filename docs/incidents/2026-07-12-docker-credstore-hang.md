# Incident: Docker Desktop credsStore hang blocks public image pulls

**Date:** 2026-07-12
**Service:** api-gateway (build step)
**Severity:** Low (blocked a local build, no cluster/service impact)

---

## Symptoms

`docker build` for `api-gateway-service:dev` failed at the very first
step — pulling the public base image `python:3.11-slim` — with:

```
ERROR: failed to solve: error getting credentials - err: exit status 1, out: ``
ERROR: image: "api-gateway-service:dev" not present locally
```

This was surprising: `python:3.11-slim` is a public image on Docker
Hub, requiring no authentication. The same build command had worked
without issue minutes earlier for `accounts` and `transactions`.

## Investigation

1. Checked `~/.docker/config.json`:
   ```json
   {
     "auths": { "774493573578.dkr.ecr.us-east-1.amazonaws.com": {} },
     "credsStore": "desktop.exe"
   }
   ```
   `credsStore` points at a Windows binary (`desktop.exe`) that Docker
   CLI invokes for *every* registry interaction — including pulls that
   don't actually need credentials.
2. Confirmed Docker Desktop itself was responsive:
   ```bash
   docker info | head -20
   ```
   returned normally — the daemon was healthy, so this wasn't a
   Docker Desktop outage.
3. Confirmed the credential helper binary was present and on PATH:
   ```bash
   which docker-credential-desktop.exe
   # -> /usr/bin/docker-credential-desktop.exe
   ```
   Ruled out a missing-binary/PATH issue — the helper existed but
   wasn't responding when invoked.

## Root Cause

`credsStore: desktop.exe` requires Docker CLI to round-trip through a
Windows-side credential helper process for every registry call, even
when pulling public images that need no authentication at all. That
helper process had become unresponsive (likely tied to the same
Docker Desktop/WSL2 instability observed elsewhere in this session —
see the late-night namespace restart incident), so every `docker
build` involving an image pull hung on the credentials step and
eventually failed.

## Fix

Backed up and temporarily stripped `credsStore` from the Docker
config, keeping only the (unused for this purpose) ECR auth entry:

```bash
cp ~/.docker/config.json ~/.docker/config.json.bak
echo '{"auths": {"774493573578.dkr.ecr.us-east-1.amazonaws.com": {}}}' > ~/.docker/config.json
```

Re-ran the build — succeeded immediately (78s, mostly `pip install`
time, no further credential errors).

## Verification

```bash
docker build -t api-gateway-service:dev -f services/api-gateway/Dockerfile services/api-gateway/
```
completed with `exporting to image` / `naming to
docker.io/library/api-gateway-service:dev` — confirmed via a clean
successful build log with no credential errors.

## Prevention

No permanent fix applied — the workaround (stripped `credsStore`) was
left in place rather than restoring the original config, since this
project never actually needs Docker Hub authentication (only public
base images) and doesn't push to ECR from this machine's Docker CLI.
If ECR push were needed again from this environment, `docker login`
would need to be run explicitly rather than relying on `credsStore`.

## Lessons Learned

- A credential-helper failure can block pulls of images that need no
  authentication at all — the failure mode isn't specific to the
  registry being private or public, it's that the CLI unconditionally
  invokes the configured helper for every registry interaction.
- `docker info` responding normally doesn't rule out a credential
  helper specifically being stuck — they're separate subsystems.
  Check the credential helper's own responsiveness directly rather
  than inferring health from the daemon being up.
- This is now a recognized failure signature for this environment:
  `error getting credentials - err: exit status 1` on a `docker build`
  that otherwise looks correct means check `credsStore`, not the
  Dockerfile or the image name.
