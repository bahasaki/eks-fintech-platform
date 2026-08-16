# Runbook: Provisioning and Safely Tearing Down the EKS Cluster

Use this checklist any time the EKS cluster needs to be stood up for
a demo/verification session and torn down afterward to avoid ongoing
AWS cost.

## Provisioning

1. **Bootstrap the Terraform backend (one-time only)**
   ```bash
   aws s3 mb s3://tfstate-eks-fintech-<account-id> --region us-east-1
   aws dynamodb create-table \
     --table-name tfstate-eks-fintech-lock \
     --attribute-definitions AttributeName=LockID,AttributeType=S \
     --key-schema AttributeName=LockID,KeyType=HASH \
     --billing-mode PAY_PER_REQUEST
   ```

2. **Apply Terraform**
   ```bash
   cd terraform/environments/dev
   terraform init
   terraform plan     # confirm resource count looks right before applying
   terraform apply
   ```
   Expect ~10-15 minutes. If the node group fails with
   `CREATE_FAILED` / `InvalidParameterCombination... not eligible for
   Free Tier`, see incident `2026-07-06-eks-nodegroup-create-failed.md`
   — switch `instance_types` to a Free-Tier-eligible type (e.g.
   `t3.small`) and re-apply; Terraform will correctly detect the
   node group as tainted and replace only that resource.

3. **Connect kubectl**
   ```bash
   aws eks update-kubeconfig --name eks-fintech-dev --region us-east-1
   kubectl get nodes    # confirm both nodes Ready before continuing
   ```

4. **Push images to ECR** (skip if already pushed and unchanged)
   ```bash
   aws ecr get-login-password --region us-east-1 | \
     docker login --username AWS \
     --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

   for svc in accounts transactions api-gateway; do
     docker build -t ${svc}-service:local services/${svc}/
     docker tag ${svc}-service:local <account-id>.dkr.ecr.us-east-1.amazonaws.com/${svc}-service:latest
     docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/${svc}-service:latest
   done
   ```
   Confirm `kubernetes/*/deployment.yaml` reference the ECR image URIs
   with `imagePullPolicy: Always`, not the local `:local` /
   `imagePullPolicy: Never` values used for `kind`.

5. **Deploy application manifests**
   ```bash
   kubectl apply -f kubernetes/namespaces/
   kubectl apply -f kubernetes/accounts/
   kubectl apply -f kubernetes/transactions/
   kubectl apply -f kubernetes/api-gateway/
   kubectl apply -f kubernetes/rbac/
   kubectl get pods --all-namespaces   # confirm everything Running before continuing
   ```

6. **Install the Ingress Controller and wait for the ALB/ELB**
   ```bash
   helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
   helm repo update
   helm install ingress-nginx ingress-nginx/ingress-nginx \
     --namespace ingress-nginx --create-namespace \
     --set controller.service.type=LoadBalancer

   kubectl get service ingress-nginx-controller -n ingress-nginx --watch
   # wait for EXTERNAL-IP to populate (2-3 min), then Ctrl+C
   ```

7. **Apply Ingress and verify the live endpoint**
   ```bash
   kubectl apply -f kubernetes/ingress/ingress.yaml
   ALB=$(kubectl get service ingress-nginx-controller -n ingress-nginx \
     -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
   curl http://$ALB/api/health
   ```

## Teardown (do this every time — do not leave the cluster running)

**Order matters.** Kubernetes-provisioned cloud resources (anything
created by a `LoadBalancer`-type Service) live outside Terraform's
state and will block VPC teardown if not removed first — see incident
`2026-07-06-terraform-destroy-vpc-dependency.md`.

1. **Delete the Ingress Controller's LoadBalancer Service first, while
   the cluster is still reachable**
   ```bash
   kubectl delete service ingress-nginx-controller -n ingress-nginx
   ```

2. **Confirm the AWS load balancer is actually gone before proceeding**
   ```bash
   aws elb describe-load-balancers --region us-east-1 \
     --query 'LoadBalancerDescriptions[*].LoadBalancerName' --output text
   aws elbv2 describe-load-balancers --region us-east-1 \
     --query 'LoadBalancers[*].LoadBalancerArn' --output text
   ```
   Both should return empty. If a classic ELB is still listed, delete
   it directly and wait ~30s before continuing:
   ```bash
   aws elb delete-load-balancer --load-balancer-name <name> --region us-east-1
   ```

3. **Run terraform destroy**
   ```bash
   cd terraform/environments/dev
   terraform destroy
   ```
   If this still fails with `DependencyViolation` on subnets/VPC,
   delete the VPC manually via the AWS Console (**VPC → Your VPCs →
   Actions → Delete VPC**, which surfaces and clears remaining
   dependencies in one step), then remove the now-stale resource from
   state and re-run:
   ```bash
   terraform state list
   terraform state rm module.vpc.aws_vpc.main   # or whatever remains
   terraform destroy
   ```

4. **Confirm a clean slate — no ongoing cost left behind**
   ```bash
   aws eks list-clusters --region us-east-1
   aws ec2 describe-vpcs --filters "Name=tag:Project,Values=eks-fintech" \
     --region us-east-1 --query 'Vpcs[*].VpcId'
   aws ec2 describe-nat-gateways --filter "Name=state,Values=available" \
     --region us-east-1 --query 'NatGateways[*].NatGatewayId'
   aws ec2 describe-addresses --region us-east-1 \
     --query 'Addresses[*].[AllocationId,AssociationId]'
   ```
   All four should return empty/`[]`.

## Rough Cost Reference

| Resource | Cost/hour |
|---|---|
| EKS control plane | $0.10 |
| EC2 t3.small x2 | $0.046 |
| NAT Gateway x2 | $0.09 |
| ALB/ELB | ~$0.008 |
| **Total while running** | **~$0.25/hr (~$6/day)** |

Leaving the cluster up overnight by accident costs roughly $6 — not
catastrophic, but there's no reason to pay it. Always run the
teardown steps at the end of a session.
