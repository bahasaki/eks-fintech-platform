# Incident: `terraform destroy` Blocked by Orphaned Load Balancer (VPC DependencyViolation)

**Status:** Resolved
**Date:** 2026-07-06
**Severity:** Medium (blocked clean teardown, risked ongoing AWS cost if left unresolved)

## Symptoms

`terraform destroy` ran for ~20 minutes then failed on multiple
resources in sequence:

```
Error: deleting EC2 Subnet (subnet-...): DependencyViolation:
The subnet 'subnet-...' has dependencies and cannot be deleted.

Error: deleting EC2 Internet Gateway (igw-...): DependencyViolation:
Network vpc-... has some mapped public address(es). Please unmap
those public address(es) before detaching the gateway.

Error: deleting EC2 VPC (vpc-...): DependencyViolation:
The vpc 'vpc-...' has dependencies and cannot be deleted.
```

Re-running `terraform destroy` repeatedly produced the same errors.

## Investigation

Terraform only manages resources it created directly. The
`ingress-nginx` Helm chart, installed with
`controller.service.type=LoadBalancer`, had provisioned a classic AWS
ELB *outside* of Terraform's state — Kubernetes' AWS cloud-controller
created it on demand when the Service was applied. Terraform had no
knowledge of it and therefore no way to delete it, but the ELB's
network interfaces were still attached to the VPC's subnets, blocking
every downstream deletion (subnet → IGW → VPC).

By the time this was diagnosed, the EKS cluster itself had already
been deleted (either by Terraform or in a prior partial run), so
`kubectl delete service ingress-nginx-controller` was no longer
possible — the API server was gone:

```
Unable to connect to the server: dial tcp: lookup ...eks.amazonaws.com
on 10.255.255.254:53: no such host
```

Found the orphaned load balancer directly via the AWS CLI:

```bash
aws elb describe-load-balancers --region us-east-1 \
  --query 'LoadBalancerDescriptions[*].[LoadBalancerName,DNSName]' \
  --output text
# af8d3236c67754c87b327d8a6f63bb79   af8d3236...elb.amazonaws.com
```

## Root Cause

Any Kubernetes `Service` of `type: LoadBalancer` on EKS creates a
cloud resource (classic ELB or NLB/ALB via the AWS Load Balancer
Controller) that lives *outside* Terraform's state. Destroying the
EKS cluster and VPC via Terraform without first deleting that Service
leaves the load balancer's ENIs attached to the VPC's subnets, which
AWS refuses to delete out from under a live network interface.

## Fix

1. Deleted the orphaned classic ELB directly via AWS CLI (the EKS API
   server was no longer reachable to do it through `kubectl`):
   ```bash
   aws elb delete-load-balancer \
     --load-balancer-name af8d3236c67754c87b327d8a6f63bb79 \
     --region us-east-1
   ```
2. `terraform destroy` still failed on the VPC itself afterward —
   deleted the VPC manually through the AWS Console (**VPC → Your
   VPCs → Actions → Delete VPC**), which surfaces and clears any
   remaining dependencies in one step.
3. Terraform's state still referenced the now-manually-deleted VPC:
   ```bash
   terraform state list
   # module.vpc.aws_vpc.main
   terraform state rm module.vpc.aws_vpc.main
   ```
4. Confirmed a clean slate:
   ```bash
   aws eks list-clusters --region us-east-1        # []
   aws ec2 describe-vpcs --filters "Name=tag:Project,..." # []
   aws ec2 describe-nat-gateways --filter "Name=state,Values=available" # (empty)
   aws ec2 describe-addresses --query 'Addresses[*].[AllocationId,AssociationId]' # (empty)
   ```

## Verification

All four AWS CLI checks above returned empty — no EKS cluster, no
VPC, no NAT Gateways, no unattached Elastic IPs. No ongoing AWS spend
left behind.

## Prevention

- **Always delete `LoadBalancer`-type Kubernetes Services (and
  anything installed via Helm that provisions cloud infrastructure)
  before running `terraform destroy`** — anything Kubernetes
  provisions on the cloud provider's side is invisible to Terraform's
  state and will block VPC teardown.
- A safer teardown order for this stack going forward:
  `kubectl delete svc ingress-nginx-controller` (and any other
  `LoadBalancer` services) → confirm the ELB/ALB is gone via AWS CLI →
  *then* `terraform destroy`.
- When `terraform destroy` fails with `DependencyViolation`, check for
  cloud resources created by Kubernetes controllers first (load
  balancers, EBS volumes from PVCs, ENIs from CNI) before assuming the
  Terraform configuration itself is at fault.

## Lessons Learned

Terraform's state is not the full picture of what exists in an
account — controllers running *inside* a Terraform-provisioned
cluster (the AWS cloud-controller-manager, the EBS CSI driver, etc.)
can create their own cloud resources that Terraform never sees and
therefore cannot clean up. Teardown order matters: application-layer
resources that provision cloud infrastructure need to be removed
before the infrastructure layer that hosts them.
