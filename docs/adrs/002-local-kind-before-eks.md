# ADR-002: Develop Locally on kind Before Deploying to EKS

**Status:** Accepted
**Date:** 2026-06-24

## Context

Building three FastAPI microservices, Kubernetes RBAC, Ingress, HPA,
and ArgoCD GitOps from scratch involves many iterations — manifests
get written wrong, images fail to build, proxy logic has bugs. Each
of those needs a fast edit → deploy → observe → fix loop.

EKS charges for the control plane the moment it exists
(~$0.10/hour) plus EC2 worker nodes, NAT Gateways, and any load
balancers — roughly $0.25-0.30/hour combined, regardless of whether
the cluster is actively used or sitting idle while a Dockerfile typo
gets fixed.

## Decision

All application and platform development (services, Kubernetes
manifests, RBAC, HPA, Ingress, ArgoCD) was built and debugged against
a local `kind` (Kubernetes-in-Docker) cluster first. EKS was only
provisioned via Terraform once every component was already working
end-to-end locally, specifically to produce a real AWS deployment for
portfolio evidence (screenshots, a live ALB endpoint), then torn down
with `terraform destroy` immediately after.

## Alternatives Considered

**Developing directly against EKS** — rejected. Every debugging cycle
(e.g. the API gateway's 307-redirect bug, or fixing a bad Dockerfile)
would have accumulated AWS cost with no added learning value — `kind`
and EKS both run the same Kubernetes API, so none of that debugging
work needed real AWS infrastructure underneath it.

**minikube instead of kind** — kind was chosen instead because it
runs nodes as plain Docker containers with no separate VM/hypervisor
layer, which matched the already-installed Docker Desktop setup and
kept the local resource footprint lower.

## Consequences

- Near-zero cost during the entire development phase — the EKS
  cluster was only live for the final deployment/verification/
  screenshot session.
- Every Kubernetes manifest, RBAC policy, and ArgoCD Application had
  already been validated on `kind` before ever touching EKS, so the
  actual EKS deployment step mostly just needed the image
  references switched from local (`imagePullPolicy: Never`) to ECR
  (`imagePullPolicy: Always`) — see incident
  `2026-07-06-eks-nodegroup-create-failed.md` for the one thing that
  *did* differ (Free-Tier instance type availability, which has no
  local equivalent to catch in advance).
- Trade-off: `kind` cannot catch AWS-specific failure modes (IAM,
  VPC networking, EKS-managed node group provisioning, Free Tier
  instance restrictions). Those were only discovered once the real
  EKS deployment ran, which is expected and acceptable — the goal of
  the local-first approach is to eliminate *Kubernetes-config*
  debugging cost, not AWS-infrastructure debugging cost.
