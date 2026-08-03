# EKS Fintech Platform

Production-grade microservices platform on AWS EKS, built with Terraform and GitOps principles.

## Architecture

```mermaid
graph TB
    Client[Client / Browser]
    
    subgraph AWS["AWS us-east-1"]
        ALB[Application Load Balancer]
        
        subgraph EKS["EKS Cluster - eks-fintech-dev"]
            NG[NGINX Ingress Controller]
            
            subgraph GW["namespace: api-gateway"]
                AGW[api-gateway x2]
            end
            
            subgraph AC["namespace: accounts"]
                ACC[accounts x2]
            end
            
            subgraph TR["namespace: transactions"]
                TRN[transactions x2-5\nHPA enabled]
            end

            subgraph MON["namespace: monitoring"]
                PROM[Prometheus]
                GRAF[Grafana]
                AM[Alertmanager]
            end
        end
        
        ECR[ECR Registry]
        ARG[ArgoCD]
    end
    
    GH[GitHub Repository]
    
    Client --> ALB
    ALB --> NG
    NG --> AGW
    AGW --> ACC
    AGW --> TRN
    ACC -.metrics.-> PROM
    TRN -.metrics.-> PROM
    AGW -.metrics.-> PROM
    PROM --> GRAF
    PROM --> AM
    GH --> ARG
    ARG --> EKS
    ECR --> EKS
```

## Tech Stack

- **Kubernetes** — EKS 1.30, kind (local)
- **Infrastructure** — Terraform (VPC, EKS modules)
- **GitOps** — ArgoCD
- **Services** — FastAPI (Python)
- **Registry** — AWS ECR
- **Ingress** — NGINX Ingress Controller + AWS ALB
- **Observability** — Prometheus, Grafana, Alertmanager (kube-prometheus-stack)
- **CI/CD** — GitHub Actions (coming)

## Services

| Service | Description | Port |
|---------|-------------|------|
| api-gateway | Single entry point, routes to services | 8002 |
| accounts | Bank account management | 8000 |
| transactions | Transaction processing with HPA | 8001 |

## Kubernetes Features

| Feature | Implementation |
|---------|---------------|
| Multi-tenancy | 3 isolated namespaces |
| Security | RBAC — ServiceAccounts, Roles, RoleBindings |
| Auto-scaling | HPA on transactions (2-5 replicas, 50% CPU) |
| High Availability | Pod Anti-Affinity across nodes |
| GitOps | ArgoCD synced to GitHub |
| Zero-downtime deploy | Rolling updates |
| Observability | Prometheus metrics, Grafana dashboards, Alertmanager rules — see below |

## Observability

All three services expose Prometheus metrics via
`prometheus-fastapi-instrumentator` (RPS, error rate, request latency
histograms), scraped by Prometheus through per-service `ServiceMonitor`
CRDs. The entire stack — Helm release, dashboards, and alert rules — is
deployed and managed through ArgoCD, not manual `helm install` /
`kubectl apply`.

**Dashboard** — a custom Grafana dashboard ("Fintech Services
Overview") shows request rate, 5xx error rate, and p50/p95/p99 latency
per service. Deployed as a `ConfigMap` (dashboards-as-code), picked up
automatically by Grafana's sidecar.

**Alerts** — three `PrometheusRule` alerts: `HighErrorRate` (>5% 5xx
over 5m), `PodRestartLoop` (>3 restarts in 15m), `HighLatencyP95` (p95 >1s
over 5m). `PodRestartLoop` was validated end-to-end by deliberately
crash-looping a pod and confirming the alert transitioned
`INACTIVE → PENDING → FIRING → INACTIVE` and reached Alertmanager (see
`docs/incidents/2026-07-14-alert-validation-podrestartloop.md`).

**Design decisions and trade-offs** — full context in
[`docs/adrs/004-observability-stack.md`](docs/adrs/004-observability-stack.md),
including why kube-prometheus-stack, why 2-day retention, why
dashboards/alerts are code rather than UI-configured, and known gaps
(e.g. api-gateway's outbound calls aren't separately instrumented).

**Runbook** — a reusable checklist for instrumenting a new FastAPI
service with Prometheus metrics lives at
[`observability/runbook-instrument-fastapi-service.md`](observability/runbook-instrument-fastapi-service.md).

### Accessing the dashboards locally

```bash
# Grafana (credentials: admin / see command below)
kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80
kubectl get secret -n monitoring kube-prometheus-stack-grafana \
  -o jsonpath="{.data.admin-password}" | base64 --decode; echo

# Prometheus
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090

# Alertmanager
kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 9093:9093
```

## Infrastructure

**Worker Nodes:** 2x t3.small EC2 (Auto Scaling: 1-3)

## Local Development

```bash
# Start local cluster
kind create cluster --name fintech --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
EOF

# Deploy all services
kubectl apply -f kubernetes/namespaces/
kubectl apply -f kubernetes/accounts/
kubectl apply -f kubernetes/transactions/
kubectl apply -f kubernetes/api-gateway/
kubectl apply -f kubernetes/rbac/
kubectl apply -f kubernetes/ingress/

# Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl apply -f argocd/

# ArgoCD manages the monitoring stack too — kube-prometheus-stack Helm
# release + custom dashboards/alerts both sync automatically once
# argocd/kube-prometheus-stack-app.yaml and
# argocd/monitoring-extras-app.yaml are applied (included in argocd/ above).
```

## AWS Deployment

```bash
# Bootstrap Terraform backend
aws s3 mb s3://tfstate-eks-fintech-<account-id> --region us-east-1
aws dynamodb create-table \
  --table-name tfstate-eks-fintech-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

# Deploy EKS
cd terraform/environments/dev
terraform init
terraform apply

# Configure kubectl
aws eks update-kubeconfig --name eks-fintech-dev --region us-east-1

# Push images to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS \
  --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

docker build -t accounts-service:local services/accounts/
docker tag accounts-service:local <account-id>.dkr.ecr.us-east-1.amazonaws.com/accounts-service:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/accounts-service:latest

# Deploy to EKS
kubectl apply -f kubernetes/
kubectl apply -f argocd/

# Destroy when done
terraform destroy
```

> **Note:** `kubernetes/*/deployment.yaml` files currently point at
> local dev images (`imagePullPolicy: Never`) for kind development —
> see `docs/incidents/2026-07-10-accounts-imagepullbackoff.md`. Before
> deploying to EKS, these need to be switched back to the ECR image
> references shown above.

## API Endpoints

```bash
# Health check
curl http://<ALB-DNS>/api/health

# Create account
curl -X POST http://<ALB-DNS>/api/accounts \
  -H "Content-Type: application/json" \
  -d '{"owner_name": "John Doe", "email": "john@example.com", "account_type": "checking"}'

# List accounts
curl http://<ALB-DNS>/api/accounts

# Create transaction
curl -X POST http://<ALB-DNS>/api/transactions \
  -H "Content-Type: application/json" \
  -d '{"account_id": "<id>", "amount": 500.00, "transaction_type": "deposit"}'
```

## Key Architectural Decisions

**Why microservices?**
Accounts and transactions have different scaling requirements. Transactions experience load spikes (paydays, holidays) requiring HPA, while accounts remain stable.

**Why ArgoCD over kubectl apply in CI/CD?**
GitOps ensures Git is the single source of truth. Any manual cluster changes are automatically reverted (selfHeal: true). Full audit trail via Git history.

**Why Pod Anti-Affinity?**
Ensures replicas are distributed across different nodes. If one node fails, the service remains available on the second node — critical for fintech availability requirements.

**Why private subnets for EKS nodes?**
Worker nodes are not directly accessible from the internet. All traffic flows through ALB → Ingress Controller → services. Reduces attack surface.

**Why kube-prometheus-stack for observability, and why is it split into two ArgoCD Applications?**
See [`docs/adrs/004-observability-stack.md`](docs/adrs/004-observability-stack.md) for full reasoning — short version: the Helm chart bundles the operator and CRDs needed for ServiceMonitor/PrometheusRule, and splitting the chart itself from the custom dashboards/alerts keeps an external dependency's release cycle separate from project-specific, frequently-changing content.

## Incident Scenarios Documented

| Incident | Cause | Resolution |
|----------|-------|------------|
| CrashLoopBackOff | Wrong resource limits | kubectl describe → logs → fix limits |
| 307 Redirect loop | FastAPI trailing slash | Added follow_redirects=True in httpx |
| Node group CREATE_FAILED | t3.medium not available | Changed to t3.small |
| Docker socket permission | User not in docker group | usermod -aG docker $USER |
| ImagePullBackOff (accounts, api-gateway) | Deployment manifests referenced ECR images unreachable from kind (no IAM auth on kind nodes) | Built local `:dev` images, `kind load docker-image`, switched manifests to `imagePullPolicy: Never` — [details](docs/incidents/2026-07-10-accounts-imagepullbackoff.md) |
| ServiceMonitor/PrometheusRule silently not scraped after ArgoCD migration | `release` label on ServiceMonitor/PrometheusRule didn't match the new Helm release name (`prometheus` → `kube-prometheus-stack`) — Prometheus's selector match failed silently, no error | Updated `release` label across all ServiceMonitors and the PrometheusRule to match the new release name |
| ArgoCD Application stuck comparing Helm chart + Git values file | `values-kind.yaml` existed locally (used for `helm install`) but was never actually committed to Git — ArgoCD clones from GitHub and had no access to it | Committed the file; confirmed with `git show HEAD:<path>` before assuming any file is actually tracked |

Full alert-validation writeup (deliberately triggering `PodRestartLoop`
end-to-end): [`docs/incidents/2026-07-14-alert-validation-podrestartloop.md`](docs/incidents/2026-07-14-alert-validation-podrestartloop.md)

## Screenshots

### EKS Cluster - Worker Nodes
![nodes](docs/screenshots/nodes.png)

### All Pods Running on EKS
![pods](docs/screenshots/pods.png)

### HPA - Autoscaling Configuration
![hpa](docs/screenshots/hpa.png)

### Live API on AWS ALB
![api](docs/screenshots/api.png)

## Cost

| Resource | Cost/hour | Notes |
|----------|-----------|-------|
| EKS Control Plane | $0.10 | |
| EC2 t3.small x2 | $0.046 | |
| NAT Gateway x2 | $0.09 | |
| ALB | $0.008 | |
| **Total** | **~$0.25/hr** | Destroy after demo |

## Author

**Baha** — Cloud/DevOps Engineer  
[GitHub](https://github.com/bahasaki) | [LinkedIn](#)
