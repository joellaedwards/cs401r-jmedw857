# Lab 5 Security & Privacy Assessment: NorthStar Churn Model

**Scope.** This covers the churn model as deployed in Lab 5: the data bucket `northstar-dev-data-<account>` (versioned, public access blocked, SSE-S3 by default), the Glue pipeline, the Feature Store, the Model Registry, and the endpoint `northstar-churn-prod` with data capture enabled.

**Least-privilege boundary (Lab 2 roles).**

| Bucket prefix | DataEngineer | MLEngineer | ModelMonitor |
|---|---|---|---|
| `raw/`, `processed/` | read/write | none | none |
| `features/` | read/write | read/write | none |
| `artifacts/` | read `artifacts/glue/` only | read/write | read-only |
| `datacapture/` | none | write only | none |

## Task 3a: STRIDE threat model

| # | Threat | STRIDE category | Likelihood | Impact | Mitigation | AWS control |
|---|---|---|---|---|---|---|
| 1 | Leaked AWS access keys (e.g. committed to this public repo) are used to invoke the endpoint or change its variant weights. | Spoofing | M | H | Keep long-lived keys out of the repo, block commits that contain them, and require MFA for human users so a leaked key alone is not enough. | `scripts/check-secrets.sh` on every commit; `.gitignore` for `.env` / `*.tfvars`; IAM MFA and IAM Identity Center short-lived credentials; CloudTrail to detect use of a leaked key. |
| 2 | Poisoned training data: rows in `raw/` or `processed/` are altered so the next retrain learns the wrong customers to target. | Tampering | L | H | **Blast radius contained by the DataEngineer role:** only DataEngineer can write `raw/` and `processed/`, so a compromised MLEngineer or ModelMonitor cannot change source data. The containment is partial: both DataEngineer and MLEngineer can write `features/`. Data tests gate every retrain. | IAM policies scoping `s3:PutObject` by prefix (`raw/*`, `processed/*` to DataEngineer only); S3 Versioning to restore prior objects; `tests/test_data.py` and `tests/test_features.py` in the CodeBuild `pre_build` phase. |
| 3 | A model is approved, or canary weights are changed, and nobody can later show who did it or that it was reviewed. | Repudiation | M | M | **Gap the Lab 2 roles do not close:** model approval, deployment and rollback all run under the engineer's own administrator credentials, outside all three roles. The same person trained, approved and deployed v7 and v8, so there is no separation of duties. Add a dedicated approver role that cannot train or deploy. | CloudTrail management events for `UpdateModelPackage` and `UpdateEndpointWeightsAndCapacities`; a separate IAM approver role allowed only `sagemaker:UpdateModelPackage`; `--approval-description` recorded on each package. |
| 4 | Membership inference: an attacker sends crafted feature rows to the endpoint to learn whether a specific customer was in the training data. | Information disclosure | L | H | Only internal callers may invoke the endpoint, the response is a single probability with no model internals, and call volume per caller is limited. | IAM `sagemaker:InvokeEndpoint` scoped to the `northstar-churn-prod` ARN (SigV4-signed calls only); SageMaker runtime through a VPC interface endpoint (AWS PrivateLink) so it is unreachable from the internet. |
| 5 | Inference logs in `datacapture/` (every request's features and score) are read by someone who should not see them. | Information disclosure | M | M | Restrict who can decrypt capture data, keep it only as long as monitoring needs it, and permanently delete old versions. | SSE-KMS with a customer-managed key whose key policy allows only the endpoint role to encrypt and the monitoring role to decrypt; S3 lifecycle rule `expire-datacapture` (7 days) with `noncurrent_version_expiration` (1 day). |
| 6 | An invocation flood overwhelms the endpoint during the Monday scoring run or runs up the hourly bill. | Denial of service | L | M | Cap capacity so cost cannot run away, put a throttle in front of the endpoint, and alert on spend. | Application Auto Scaling target tracking with `MaxCapacity` 2 (`deployment/configs/scalable-target.json`); API Gateway usage-plan throttling or an AWS WAF rate-based rule; AWS Budgets alert. |

## Task 3b: Data classification

**Encryption rule used below.** SSE-KMS is chosen when the data is personal or reveals customer behavior, so access to the key is a second permission check on top of S3 and every decrypt is logged in CloudTrail. SSE-S3 is enough when the data carries no personal information and the S3 bucket policy alone is adequate control. Today the whole bucket uses SSE-S3 (AES256). The SSE-KMS rows are the target state, and the difference is a known gap.

| Asset | Tier | Encryption | Reason for encryption choice | IAM access | Third-party vendor? |
|---|---|---|---|---|---|
| Customer PII (`customers.csv`: hashed email, zip code, state, age band) | Restricted | SSE-KMS (customer-managed key) | Personal data under GDPR and CCPA. A dedicated key allows access to be revoked and every decrypt to be audited. | DataEngineer (read/write `raw/`, `processed/`) only | No |
| Transaction history (`raw/customers/northstar-raw-sample.csv`) | Confidential | SSE-KMS | Purchase records linked to `customer_id` are personal data, and they are the main training input. | DataEngineer (read/write `raw/`, `processed/`) | No, unless aggregated and de-identified |
| Behavioral clickstream | Confidential (Restricted until `search_query` is masked) | SSE-KMS | `search_query` sometimes contains customer email addresses (schema note), and browsing is linked to `customer_id`. | DataEngineer when ingested; not yet in this platform | No |
| Product catalog | Internal | SSE-S3 | No personal data. Prices and inventory are business information, but S3 bucket access control is enough. | DataEngineer (read/write `raw/`) | Yes |
| Model weights (`artifacts/.../model.tar.gz`) | Confidential | SSE-KMS | Proprietary IP, and a model can leak training membership (threat 4), so who can download it should be audited. | MLEngineer (read/write `artifacts/`); ModelMonitor (read-only) | No |
| Inference logs (`datacapture/`) | Confidential | SSE-KMS | Each record holds a customer's 11 features and churn score. The records carry no `customer_id`, but they can be linked back to customers through the calling system. | MLEngineer as endpoint execution role (write only). ModelMonitor has no read access yet, which needs fixing for Lab 6 | No |
| Feature Store records (`northstar-dev-customer-features`: online store + offline store in `features/`) | Confidential | SSE-KMS (online store and offline store) | Per-customer behavioral aggregates keyed by `customer_id`, served to the model. | DataEngineer and MLEngineer (read/write `features/`, Feature Store API) | No |

## Task 4: Privacy impact assessment

### 1. Personal data inventory

The churn model processes customers' purchase behavior, linked by `customer_id`. No names, emails or payment details enter this pipeline. The CRM file `customers.csv` (hashed email, zip code, state, age band) exists in the NorthStar schema but is not ingested by this platform.

| Pipeline stage | Location | Personal data |
|---|---|---|
| Raw ingest | `raw/customers/northstar-raw-sample.csv` | One row per transaction: `customer_id`, `purchase_date`, `order_value`, `num_items`, `payment_method`, `channel`, `store_id`, `product_category` |
| Transform (Glue) | `processed/customers/` | The same transactions, cleaned and typed |
| Feature engineering (Glue) | `features/customers/`, Feature Store `northstar-dev-customer-features` (online store + offline store in `features/offline-store/`) | One row per customer: `customer_id`, 11 behavioral aggregates, `loyalty_tier`, `churn_label`, `event_time` |
| Training | SageMaker training job reads `features/customers/` | The same feature rows; the model artifact in `artifacts/` holds learned weights, not rows |
| Evaluation | `artifacts/evaluations/` | Aggregate metrics only (AUC, per-tier recall), no per-customer data |
| Inference | `northstar-churn-prod` endpoint | 11 features in, one churn probability out. No `customer_id` is sent |
| Inference logs | `datacapture/northstar-churn-prod/` | Each request's 11 features and the returned score, kept for 7 days (+1 day for old versions) |

### 2. Lawful basis

**Legitimate interests (GDPR Art. 6(1)(f)).**

- **Purpose:** NorthStar has a real business interest in keeping existing customers. 18% annual churn costs about $128.5M a year, and the model only selects which customers receive a retention offer.
- **Necessity:** a customer's own purchase history is the most direct signal that they are drifting away, and the model uses aggregates of it rather than raw records.
- **Balancing:** the processing is low-intrusion. These are existing customers, the data is their own transactions with NorthStar, no special-category data is used, and the outcome is an offer, not a decision with legal effect (so Art. 22 automated decision-making does not apply). Customers can reasonably expect a retailer to use their purchase history to send them offers. Customers who object under Art. 21 are excluded from scoring.

**Why not the alternatives.** *Contractual necessity* does not fit: NorthStar can deliver what a customer bought without ever predicting churn. *Consent* would work legally but is fragile here. A model trained on consented data must drop customers whenever consent is withdrawn, and an opt-in would leave out exactly the disengaged customers the model is meant to find.

### 3. Data minimization

The model uses 11 behavioral features (`FEATURE_COLUMNS` in `models/churn/churn_model.py`). Data that was available and deliberately excluded from training:

- **Demographics and location** (`age_band`, `zip_code`, `state` in `customers.csv`): not ingested. They add privacy risk and potential for proxy discrimination, and retention timing is driven by behavior, not demographics.
- **Hashed email:** not needed; `customer_id` is the only join key.
- **Raw transaction detail** (`transaction_id`, `store_id`, `payment_method`, individual `product_category` values): reduced to aggregates. `product_category` becomes `category_diversity_score` and `channel` becomes `online_to_store_ratio`, so individual purchases cannot be read back from the features.
- **`loyalty_tier`:** kept in the feature rows only to evaluate fairness by tier, and not used as a model input.
- **`churn_risk_score`:** a rule-based score that the Glue feature job computes from the same purchase data. It is excluded because it repeats information the model already has.
- **`customer_id` at inference:** the endpoint receives only the 11 features, so the inference logs contain no direct identifier.

### 4. Right-to-erasure workflow (`customer_id = C00123456`)

NorthStar has one month to respond (GDPR Art. 12(3)). Steps run in this order, because each store is rebuilt from the one before it.

#### (a) Raw S3 data

1. Download `raw/customers/northstar-raw-sample.csv`, remove every row where `customer_id = C00123456`, and upload the filtered file to the same key.
2. The bucket is versioned, so the old file still exists as a previous version. List it with `aws s3api list-object-versions --bucket <bucket> --prefix raw/customers/` and permanently delete each older version with `aws s3api delete-object --version-id <id>`.
3. Re-run the Glue `northstar-dev-transform` job so `processed/customers/` is rebuilt without the customer, and delete the previous versions of the old `processed/` objects the same way.

#### (b) Feature Store: online and offline stores

- **Online store** (current value, used for low-latency lookup): `aws sagemaker-featurestore-runtime delete-record --feature-group-name northstar-dev-customer-features --record-identifier-value-as-string C00123456 --event-time <now> --deletion-mode HardDelete`. This removes the record immediately.
- **Offline store** (`features/offline-store/`, Parquet history): `DeleteRecord` does **not** remove history here. It only appends a new row marked `is_deleted = true`, and every earlier row for C00123456 stays in the old Parquet files. Erasing it means rewriting those files: a Glue or Athena job reads each partition, filters out the customer, writes new files, and then the old objects and their versions are deleted.
- Re-run the Glue `northstar-dev-feature-engineer` job so `features/customers/` is rebuilt from the cleaned `processed/` data.

#### (c) Model training data

1. The training input (`features/customers/`) is already clean after step (b).
2. The deployed models v7 and v8 were trained on data that included C00123456. Retrain through `northstar-churn-pipeline` to register a new version, approve it against the same gates, and deploy it through the canary process.
3. Reject v7 and v8 in the Model Registry and permanently delete their `model.tar.gz` artifacts (including old versions) from `artifacts/training/`.

#### (d) Inference logs

The capture records carry no `customer_id`, so they cannot be searched by ID. They expire automatically: the `expire-datacapture` lifecycle rule hides objects after 7 days and permanently deletes them 1 day later, so any capture of C00123456 is gone within about 8 days, well inside the one-month deadline. For an immediate deletion, the calling system's request log would identify the matching requests. A future improvement is to send a hashed customer ID as the SageMaker `InferenceId`, so capture records can be found directly.

#### Hardest step and why

**The trained model (c).** Every other store holds rows that can be found and removed. A model holds weights learned from all its training rows at once, with no row for C00123456 to delete. The only reliable way to remove that customer's influence is to **retrain without them** and retire the old model, which means a full train → approve → canary → teardown cycle per request. Batching deletion requests into a scheduled retrain (for example monthly, inside the one-month deadline) keeps that practical.

The **offline Feature Store** is a close second. It is append-only by design, so "delete" adds a deletion marker rather than removing anything, and true erasure needs a custom rewrite of the Parquet history plus clean-up of old S3 versions.
