# infrastructure/ — Labs 1-2 Terraform

Terraform for the NorthStar ML platform. Lab 1 built the VPC, storage, IAM,
and SageMaker modules. Lab 2 extends them and adds the Glue and Feature Store
modules for the data pipeline.


```bash
cd environments/dev
terraform init
terraform fmt -check -recursive ../..   # no output = pass
terraform validate                      # exits 0
```

## Layout

```
modules/vpc/              VPC, public + private subnets, internet gateway, NAT
                          gateway + EIP, public/private route tables, security
                          group (VPC-CIDR rule + self-referencing rule for Glue)
modules/storage/          S3 bucket + public access block, versioning, SSE-S3,
                          prefixes, lifecycle configuration (5 rules)
modules/iam/              MLEngineer, DataEngineer, and ModelMonitor roles, each
                          with a policy and attachment
modules/sagemaker/        SageMaker domain (private subnet, VpcOnly) + user profile
modules/glue/             aws_glue_catalog_database, aws_glue_crawler,
                          aws_glue_connection (NETWORK), two aws_glue_job
                          (transform, feature_engineer), aws_s3_object script uploads
modules/feature_store/    aws_sagemaker_feature_group (16 definitions, online +
                          offline store)
```

Each module contains **only** its designated resources — that is graded.

## The rule that catches people

**No hardcoded names.** The rubric runs:

```bash
grep -rn '"northstar-dev"' infrastructure/modules/
```

and expects nothing. Build names from `var.project` and `var.environment`,
and give every variable a `description` — that is also graded.

The data bucket is the one name that also needs the account ID, because S3
bucket names are global and thirty students deploy this same code. Read it
from the caller inside `modules/storage`:

```hcl
data "aws_caller_identity" "current" {}

locals {
  bucket_name = "${var.project}-${var.environment}-data-${data.aws_caller_identity.current.account_id}"
}
```

The same line yields `northstar-dev-data-<your account>` on AWS and
`northstar-local-data-000000000000` on LocalStack with no special-casing.

## Lab 2 additions

**Infrastructure Changes**
- `vpc`: private subnet (10.0.1.0/24), NAT Gateway, private route table
  `enable_nat_gateway` is false when running locally
- `storage`: five lifecycle rules to take care of the data coming into the S3 buckets.  
  These rules are turned off when running locally
- `iam`: DataEngineer (Glue/Lambda/SageMaker trust) and ModelMonitor (read only)
- `sagemaker`: Domain moved to the private subnet with VpcOnly

**Data Pipeline**
```
raw/customers/ (CSV) -> Glue crawler -> catalog table `customers`
  -> transform job -> processed/customers/ (Parquet, one row per transaction)
  -> feature-engineer job -> features/customers/ (Parquet, one row per customer)
                          -> Feature Store (PutRecord)
```

## How to Run the Pipeline

From `infrastructure/environments/dev`:


```bash
terraform init
terraform apply -no-color 2>&1 | tee -a ../../../docs/lab2-extend-output.txt
```
Note: redirecting output to lab2-extend-output.txt is not required for running, just for grading. 

From the repo root:

```bash
# 1. Upload the raw data
aws s3 cp northstar-raw-sample.csv \
  s3://northstar-dev-data-$(aws sts get-caller-identity --query Account --output text)/raw/customers/

# 2. Crawl, then wait for READY
aws glue start-crawler --name northstar-dev-raw-crawler
until [ "$(aws glue get-crawler --name northstar-dev-raw-crawler --query 'Crawler.State' --output text)" = "READY" ]; do sleep 15; done

# 3. Transform (wait for SUCCEEDED), then feature engineering (wait for SUCCEEDED)
aws glue start-job-run --job-name northstar-dev-transform
# ...poll until SUCCEEDED, then:
aws glue start-job-run --job-name northstar-dev-feature-engineer

# 4. Verify (needs python3 with pandas and pyarrow)
bash scripts/verify-lab2.sh 2>&1 | tee docs/lab2-verify-output.txt
```

Run the transform job to `SUCCEEDED` before starting the feature job. The
feature job also needs the Feature Group to be `Created`.

## Teardown

```bash
bash scripts/teardown-lab2.sh
```