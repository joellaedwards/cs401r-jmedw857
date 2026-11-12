# Lab 5 Deployment Plan: NorthStar Churn Model

## 1. Deployment strategy and rationale

**Strategy:** a real-time SageMaker endpoint, `northstar-churn-prod`, rolled out as a canary. Two production variants run on one endpoint: `champion` (v7) takes 90% of traffic and `canary` (v8) takes 10% (`deployment/configs/endpoint-config-canary.json`).

**Why real-time.** NorthStar has two consumers of churn scores. The weekly retention campaign needs every active customer scored by Monday 6 AM ET. The offer generation system has a 2-second response budget and puts each customer's `churn_probability` into its prompt (`offer_generation/prompts.py`), so it needs a current score on demand. A weekly batch job alone would hand the offer system a score up to 7 days stale. One endpoint covers both consumers: the Monday job calls the same endpoint, and at about 4 ms per call that run finishes well before the deadline. Batch Transform would be cheaper for the weekly job, and it can be added later as a second consumer of the same approved model package.

**Why canary.** The churn score decides which customers get retention offers, so a bad model either misses customers who are about to churn or spends offer margin on customers who were never at risk. Offline, v8 roughly ties v7 (AUC 0.7845 vs. 0.7855) but has a smaller fairness gap. Offline metrics cannot settle that, so v8 has to prove itself on live traffic. A 10% canary limits how many customers a bad model can affect, and rollback is a weight change with no redeploy. Blue/green would run two full endpoints and switch all traffic at once.

## 2. Pre-deployment checklist

### Model approval

Only an `Approved` version in the Model Registry group `northstar-churn-models` is deployed. Training registers versions as `PendingManualApproval`.

- **Approver:** ML Lead (model owner); I acted in that role.
- **Version:** v7 (commit `0449f2e2`), approved 2026-11-12.
- **Criteria:** the Lab 4 promotion gates. With no current champion, the baseline is the recency-only model.

| Criterion | Threshold | v7 |
|---|---|---|
| AUC-ROC | > 0.7208 (baseline) | 0.7855 |
| Precision@top 10% | ≥ 0.50 | 0.74 |
| Recall@top 10% | ≥ 0.25 | 0.3364 |
| Lift 95% CI | excludes 0 | [0.0461, 0.0843] |
| `test_model.py`, `test_fairness.py` | pass | 21 pass, 1 skip |

Fairness flag accepted: recall@top 10% is 0.471 for Bronze and 0.048 for Platinum. It is tracked in Lab 6.

The ML Lead checks the package's `evaluation_metrics.json` (S3 path under `ModelMetrics` in `describe-model-package`) against the table, then approves and confirms:

```bash
aws sagemaker update-model-package --model-package-arn arn:aws:sagemaker:us-east-1:860366539138:model-package/northstar-churn-models/7 --model-approval-status Approved --approval-description "ML Lead approved v7 after metric review"
aws sagemaker list-model-packages --model-package-group-name northstar-churn-models --query "ModelPackageSummaryList[*].[ModelPackageVersion,ModelApprovalStatus]" --output table
```

If any criterion fails, the package is set to `Rejected` and deployment stops.

## 3. Rollback criteria

## 4. Monitoring window

## 5. Stakeholder notifications

## 6. Post-deployment review

## 7. Resource cleanup
