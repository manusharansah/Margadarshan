# Deploying to AWS

The stack in `infra/marga.cfn.yml` runs one ECS Fargate task holding the frontend on port 3000 and the API on
localhost port 8000. It adds a private RDS PostgreSQL database, an S3 bucket for the network file, a public load
balancer that serves HTTPS only, and Secrets Manager entries for the sign-in client secret, the database password and the
optional Gemma key. Logs
go to CloudWatch for 30 days. When you pass a Gemma key, the template also sets `MARGA_REQUIRE_GEMMA=true`.

## One-time setup

1. Choose a hostname such as `marga.example.com`, and request an ACM certificate for it in the deployment region.
2. At your identity provider, create an OpenID Connect web client. For Google that is Google Cloud console, APIs and
   services, Credentials, OAuth client ID. Set the redirect URI to `https://marga.example.com/api/auth/callback`. Note the
   client id and secret. Turn on multi-factor sign-in for your users at the provider.
3. Create two ECR repositories named `marga-backend` and `marga-frontend`, and push the images, or run the Deploy to AWS
   workflow after step 7.
4. Deploy the stack:
   ```bash
   aws cloudformation deploy --stack-name marga --template-file infra/marga.cfn.yml \
     --capabilities CAPABILITY_IAM \
     --parameter-overrides VpcId=... PublicSubnetIds=subnet-a,subnet-b BackendImage=... FrontendImage=... \
       CertificateArn=arn:aws:acm:... PublicUrl=https://marga.example.com \
       OidcClientId=... OidcClientSecret=... BootstrapOwnerEmail=you@example.com \
       [OidcIssuer=...] [AllowedEmailDomains=example.com] [GemmaApiKey=...]
   ```
5. Point the DNS name at the `AlbDnsName` output.
6. Build the network file on any machine and upload it:
   `aws s3 cp backend/data/network.json.gz s3://<DataBucketName>/network.json.gz`. Then start a new deployment so the task fetches it.
7. Open the site and sign in with the bootstrap email. That creates the first organisation with you as owner. Invite the
   rest of the team from the Team page, and add warehouses on the Inventory page or by CSV.
8. In GitHub, set these repository variables: `AWS_REGION`, `AWS_ROLE_ARN`, `ECR_BACKEND`, `ECR_FRONTEND`, `ECS_CLUSTER`,
   `ECS_SERVICE`, `CFN_STACK`, `VPC_ID`, `SUBNET_IDS`. The role must trust GitHub OIDC, so no AWS keys live in GitHub.

## Size and cost

The task uses 1 vCPU and 3 GB. The graph for Kathmandu and Kavre loads in under a second. The API process measured about 400 MB
of memory with that network loaded. The database is a `db.t4g.micro`. Expect a low monthly cost in the tens of US dollars. I have not
measured it on a real account.

## Before a public launch

Move the tasks and database to private subnets behind a NAT gateway, and add AWS WAF rate rules in front of the load
balancer, because the application's own rate limits are per process. Add a schema migration tool before changing a live
database. The remaining gaps are listed in [SECURITY.md](SECURITY.md).
