# Incident: API Gateway 307 Redirect → JSONDecodeError

**Status:** Resolved
**Date:** 2026-06-25
**Severity:** Medium (blocked all api-gateway → accounts/transactions traffic)

## Symptoms

`POST /accounts` through api-gateway returned an empty response with
no error message. Direct `curl` to the accounts service worked fine;
only requests proxied through api-gateway failed.

```bash
curl -X POST http://localhost:8080/accounts \
  -H "Content-Type: application/json" \
  -d '{"owner_name": "Jane Smith", ...}'
# (empty response, no error)
```

## Investigation

Checked api-gateway pod logs:

```bash
kubectl logs -n api-gateway deployment/api-gateway
```

Found:
```
"POST /accounts HTTP/1.1" 307 Temporary Redirect
```

Retried with a trailing slash (`/accounts/`) — got `500 Internal
Server Error` instead. Checked logs again and found a full traceback
ending in:

```
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

## Root Cause

Two compounding issues:

1. FastAPI automatically issues a `307 Temporary Redirect` from
   `/accounts` to `/accounts/` (trailing slash normalization). The
   `httpx.AsyncClient` call in api-gateway's proxy function did not
   follow redirects by default, so the redirect response (empty body)
   was passed straight back to the client instead of the real
   response.
2. Once redirects were being followed, the downstream URL was still
   being built with a trailing `/accounts/accounts/` when `path` was
   empty, causing the accounts service to return an empty body that
   `response.json()` then failed to parse.

## Fix

Updated `services/api-gateway/main.py`:
- Added `follow_redirects=True` to the `httpx.AsyncClient.request()` call
- Split the route into two decorators (`/accounts` and
  `/accounts/{path:path}`) with a `path: str = ""` default, and only
  appended `/{path}` to the downstream URL when `path` was non-empty
- Wrapped the response in `JSONResponse(content=..., status_code=...)`
  instead of returning `response.json()` directly, so downstream
  status codes are preserved

Rebuilt and redeployed via `kubectl rollout restart` (zero-downtime —
old pods kept serving traffic until new pods passed readinessProbe).

## Verification

```bash
curl -X POST http://localhost:8080/accounts \
  -H "Content-Type: application/json" \
  -d '{"owner_name": "Jane Smith", "email": "jane@example.com", "account_type": "savings"}'
# {"account_id":"20859b3b-...","owner_name":"Jane Smith",...}
```

Confirmed via `kubectl logs` that requests now show `200 OK` instead
of `307`/`500`.

## Prevention

- When proxying through `httpx`, always set `follow_redirects=True`
  explicitly rather than relying on defaults — FastAPI's trailing-slash
  redirect behavior is easy to forget about until it's hit in a proxy
  context (direct browser/curl usage rarely surfaces it, since most
  HTTP clients follow redirects by default).
- Test proxied routes specifically, not just the origin service in
  isolation — a working direct call does not guarantee a working
  proxied call.

## Lessons Learned

A working direct-to-service curl gave false confidence that the API
was fully functional. The bug only appeared once a second layer
(api-gateway) was introduced between the client and the service —
a reminder that each hop in a microservices chain needs its own
verification, not just the endpoints.
