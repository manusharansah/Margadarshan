# Deploying to AWS

Stack (`infra/marga.cfn.yml`): one ECS Fargate task (frontend :3000 + API on localhost:8000), RDS PostgreSQL (private),
S3 bucket for the network artifact, public ALB (HTTP, or HTTPS when you pass `CertificateArn`), Secrets Manager for the
admin key / DB password / optional Gemma key, 30-day CloudWatch logs.

## One-time

1. Create two ECR repos: `aws ecr create-repository --repository-name marga-backend` and `marga-frontend`.
2. Build/push images (or run the **Deploy to AWS** workflow after step 5).
3. Deploy: `aws cloudformation deploy --stack-name marga --template-file infra/marga.cfn.yml --capabilities CAPABILITY_IAM
   --parameter-overrides VpcId=… PublicSubnetIds=subnet-a,subnet-b BackendImage=… FrontendImage=… [GemmaApiKey=…] [CertificateArn=…]`
4. Upload the artifact built from **real** data: `aws s3 cp backend/data/network.json.gz s3://<DataBucketName>/network.json.gz`
   then force a new deployment so the task fetches it.
5. Load inventory with `POST /warehouses/import` (needs the admin key from Secrets Manager; the API is reachable
   from inside the VPC / via a bastion or by temporarily running the CLI as an ECS task — it is deliberately not
   exposed through the ALB).
6. GitHub → Settings → Variables: `AWS_REGION, AWS_ROLE_ARN, ECR_BACKEND, ECR_FRONTEND, ECS_CLUSTER, ECS_SERVICE,
   CFN_STACK, VPC_ID, SUBNET_IDS`; the role trusts GitHub OIDC (no stored keys).

## Cost & sizing
Fargate 1 vCPU / 3 GB + `db.t4g.micro` + ALB ≈ low tens of USD/month. Memory is dominated by the graph (≈ 14k nodes ≈ 100 MB).

## Hardening to do before public launch
Use private subnets + NAT for tasks/DB; add WAF/rate-limiting on `/api/optimize-route`; restrict `CORS_ORIGINS`.
