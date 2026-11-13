# Lab 5 Deployment Plan: NorthStar Churn Model

## 1. Deployment strategy and rationale

**Strategy:** a real-time endpoint, `northstar-churn-prod`, rolled out as a canary. `champion` (v7) gets 90% of traffic and `canary` (v8) gets 10% (`deployment/configs/endpoint-config-canary.json`).

**Why real-time.** Churn scores have two consumers. The weekly retention campaign needs every customer scored by Monday 6 AM ET. The offer system has a 2-second budget and uses `churn_probability` in its prompt, so it needs a current score on demand; a weekly batch alone would be up to 7 days stale. One endpoint serves both. Batch Transform could be added later for the cheaper weekly run.

**Why canary.** The score decides who gets retention offers, so a bad model wastes offer margin or misses churners. v8 roughly ties v7 offline (AUC 0.7845 vs. 0.7855) but is fairer, so it has to prove itself on live traffic. A 10% canary limits how many customers are exposed, and rollback is just a weight change. Blue/green switches all traffic at once, so a weak model would reach every customer.

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

**Trigger:** canary `ModelLatency` p95 above **200000 µs (200 ms)** in 3 of 5 one-minute periods. The alarm `northstar-churn-rollback-canary-latency` is defined in `deployment/configs/rollback-alarm.json`. `ModelLatency` is reported in microseconds, so 200 ms is written as 200000.

Normal latency for this model is about 4 ms, so the threshold sits roughly 50× above normal. It catches a canary that is actually failing without firing on one-off slow requests.

**Who decides:** the on-call ML Engineer makes the rollback call and does not need approval first. The alarm has no automatic action, so a person always confirms the breach before acting. The ML Lead is informed afterwards.

**Action:** `bash deployment/configs/rollback-action.sh` sets the canary's weight to 0 and the champion's to 1, then waits for `InService` (about 90 s, with no dropped requests). Rollback stops the canary from serving, but its instance keeps billing until teardown (section 7).

## 4. Monitoring window

**Production:** the canary runs for 48 h before promotion. **Lab:** this was compressed to an observed window from 20:51 UTC to <!-- TODO end time --> on 2026-11-13, and then the canary was rolled back.

**Clean means:** the rollback alarm stays `OK`, the canary returns no 5xx errors, and the observed split stays near 9:1 (200 calls gave canary 16 / champion 184).

**Observed:** canary `ModelLatency` averaged 4.6 ms (max 20.6 ms) and champion averaged 4.0 ms (max 22.7 ms). Both were far under the 200 ms threshold, and the rollback alarm stayed `OK` for the whole window.

**What the compression misses:** a short window with a few hundred synthetic calls only catches outright failures. A 48 h window would include real traffic peaks and the Monday scoring run, show whether the champion actually scales out, and collect enough canary calls to compare v8's score distribution with v7's. In production, the team would also watch `Invocation5XXErrors` and compare canary vs. champion scores from `datacapture/`.

## 5. Stakeholder notifications

| Role | Deployment start | Promotion | Rollback |
|---|---|---|---|
| ML Lead | Start time, versions, rollback threshold | Window results; signs off | Alarm state, cause |
| On-call ML Engineer | Runs it; watches alarm | Runs weight change | Runs `rollback-action.sh` |
| Retention Marketing | Heads-up: scores may shift | Which model scores Monday's run | Champion still scoring; check recent lists |
| Offer System Owner | Endpoint name unchanged | Score source changed | Brief `Updating` status, no downtime |

## 6. Post-deployment review

- **When:** within 5 business days after promotion or rollback.
- **Who:** ML Lead (chair) and the on-call ML Engineer who ran the deployment. Retention Marketing and the Offer System Owner join if their systems were affected.
- **Artifact:** `docs/reviews/<date>-churn-<version>.md`, covering the timeline, the window metrics (latency, errors, traffic split), the decision and why, and follow-up actions with owners.

## 7. Resource cleanup
