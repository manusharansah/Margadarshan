# Deploying to AWS

The stack in `infra/marga.cfn.yml` runs one ECS Fargate task holding the frontend on port 3000 and the API on
localhost port 8000. It adds a private RDS PostgreSQL database, an S3 bucket for the network file, a public load
balancer, and Secrets Manager entries for the admin key, the database password and the optional Gemma key. Logs
go to CloudWatch for 30 days. When you pass a Gemma key, the template also sets `MARGA_REQUIRE_GEMMA=true`.

## One-time setup

1. Create two ECR repositories named `marga-backend` and `marga-frontend`.
2. Push the images, or run the Deploy to AWS workflow after step 6.
3. Deploy the stack:
   ```bash
   aws cloudformation deploy --stack-name marga --template-file infra/marga.cfn.yml \
     --capabilities CAPABILITY_IAM \
     --parameter-overrides VpcId=... PublicSubnetIds=subnet-a,subnet-b \
       BackendImage=... FrontendImage=... GemmaApiKey=... CertificateArn=...
   ```
   `GemmaApiKey` and `CertificateArn` are optional.
4. Build the network file on any machine and upload it:
   `aws s3 cp backend/data/network.json.gz s3://<DataBucketName>/network.json.gz`. Then start a new deployment so the task fetches it.
5. Load inventory with `POST /warehouses/import`. The load balancer does not expose that endpoint. Call it from inside the
   VPC, or run `python -m gis_backend.seed_warehouses` as a one-off ECS task.
6. In GitHub, set these repository variables: `AWS_REGION`, `AWS_ROLE_ARN`, `ECR_BACKEND`, `ECR_FRONTEND`, `ECS_CLUSTER`,
   `ECS_SERVICE`, `CFN_STACK`, `VPC_ID`, `SUBNET_IDS`. The role must trust GitHub OIDC, so no AWS keys live in GitHub.

## Size and cost

The task uses 1 vCPU and 3 GB. The graph for Kathmandu and Kavre loads in under a second. The API process measured about 400 MB
of memory with that network loaded. The database is a `db.t4g.micro`. Expect a low monthly cost in the tens of US dollars. I have not
measured it on a real account.

## Before a public launch

Move the tasks and database to private subnets behind a NAT gateway, add rate limiting or a WAF in front of
`/api/optimize-route` and `/api/parse-order`, and restrict `CORS_ORIGINS`.
