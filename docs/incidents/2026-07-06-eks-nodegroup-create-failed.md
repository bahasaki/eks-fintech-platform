# Incident: EKS Managed Node Group CREATE_FAILED

**Status:** Resolved
**Date:** 2026-07-06
**Severity:** High (blocked all EKS deployment — no worker nodes, no way to run any workload)

## Symptoms

`terraform apply` hung for over a minute on
`module.eks.aws_eks_node_group.main: Still creating...`, then failed:

```
Error: waiting for EKS Node Group (eks-fintech-dev:eks-fintech-dev-nodes)
create: unexpected state 'CREATE_FAILED', wanted target 'ACTIVE'.
last error: AsgInstanceLaunchFailures: Could not launch On-Demand
Instances. InvalidParameterCombination - The specified instance type
is not eligible for Free Tier.
```

The EKS control plane and VPC had already been created successfully —
only the node group failed.

## Investigation

The error message itself named the root cause directly (unusual —
most Kubernetes-level failures require digging through `kubectl
describe`/logs, but this one surfaced through Terraform's own error
output from the underlying AWS Auto Scaling Group activity).

Confirmed the cluster and VPC existed but had no usable compute:

```bash
aws eks list-clusters --region us-east-1
# { "clusters": ["eks-fintech-dev"] }

aws ec2 describe-vpcs --filters "Name=tag:Project,Values=eks-fintech" \
  --region us-east-1 --query 'Vpcs[*].VpcId'
# [ "vpc-06d87d5281d21f51c" ]
```

## Root Cause

`terraform/environments/dev/variables.tf` defaulted
`instance_types` to `["t3.medium"]`. On this AWS account/region,
`t3.medium` is not covered by the Free Tier, and the launch was
rejected at the Auto Scaling Group level before any EC2 instances
came up.

## Fix

Changed the default in `variables.tf`:

```hcl
variable "instance_types" {
  default = ["t3.small"]   # was ["t3.medium"]
}
```

Re-ran `terraform plan`, which correctly identified the node group as
`tainted` (from the failed create) and proposed a destroy+recreate
with the new instance type:

```
Plan: 1 to add, 0 to change, 1 to destroy.
```

`terraform apply` completed in under 2 minutes with `t3.small`.

## Verification

```bash
kubectl get nodes
```
```
NAME                          STATUS   ROLES    AGE   VERSION
ip-10-0-1-242.ec2.internal    Ready    <none>   2m    v1.30.14-eks-7d6f6ec
ip-10-0-2-90.ec2.internal     Ready    <none>   2m    v1.30.14-eks-7d6f6ec
```

Both nodes `Ready` within minutes; all pods scheduled successfully in
the following deployment step.

## Prevention

- Check Free Tier eligibility for the target instance type *before*
  writing it as a Terraform default — `aws ec2 describe-instance-types
  --filters "Name=free-tier-eligible,Values=true"` lists what's
  actually covered, rather than assuming `t3.medium` (a common
  "small" choice in tutorials) is safe.
- Terraform's `tainted` resource tracking meant no manual cleanup was
  needed — `terraform plan` on the next run automatically proposed the
  correct destroy+recreate, rather than silently trying to update a
  broken resource in place.

## Lessons Learned

The failure surfaced at the AWS Auto Scaling Group layer, one level
below what Terraform or `kubectl` directly manage — the fix required
reading the raw `AsgInstanceLaunchFailures` message rather than
inspecting Kubernetes objects (there were none yet to inspect). Cost
and quota constraints can fail infrastructure provisioning just as
readily as configuration mistakes, and the two failure modes look
identical until you read the actual error text.
