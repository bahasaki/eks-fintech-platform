# ADR-003: Three Separate Services (accounts, transactions, api-gateway) Rather Than a Monolith

**Status:** Accepted
**Date:** 2026-06-24

## Context

The platform needed to demonstrate realistic Kubernetes platform
patterns — namespace-level isolation, RBAC, independent scaling — for
a portfolio targeting Platform/DevOps Engineer roles. A single
monolithic FastAPI service could implement the same account/
transaction business logic with far less code and no inter-service
networking to debug.

## Decision

Split the platform into three independently deployed services, each
in its own namespace:

- **accounts** — account creation/lookup, stable read-heavy load
- **transactions** — transaction processing, spiky load (paydays,
  end-of-month), the only service with HPA configured
- **api-gateway** — single entry point, proxies to the other two so
  clients never call accounts/transactions directly

## Alternatives Considered

**Single monolithic service** — rejected. It would have been faster
to build, but would not exercise or demonstrate namespace-per-service
RBAC, Kubernetes DNS-based service-to-service communication, per-
service HPA, or Pod Anti-Affinity in any meaningful way — all core
Platform Engineering skills this project exists to practice and show.

**More than three services (e.g. separate notification, audit
services)** — rejected as unnecessary for the current scope. Real
systems grow to dozens of services over years of organic team/domain
splits, not by pre-emptively slicing a new project as thin as
possible; three services with real distinct scaling/ownership
boundaries is enough to demonstrate the pattern without inflating
AWS cost or operational surface for no added portfolio value.

## Consequences

- Each service can be deployed, scaled (see `transactions-hpa`), and
  RBAC-restricted independently — a change to `transactions` does not
  require touching `accounts`.
- Real inter-service networking had to be solved (Kubernetes DNS:
  `accounts.accounts.svc.cluster.local`), which surfaced a real bug
  (see incident `2026-06-25-api-gateway-307-redirect.md`) that a
  monolith would never have exposed — the debugging experience itself
  is part of the portfolio value.
- Slight added complexity: three Dockerfiles, three sets of
  Kubernetes manifests, three RBAC configs instead of one. Accepted
  as the cost of demonstrating patterns that only exist in a
  multi-service system.
