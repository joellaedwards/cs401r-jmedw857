# Lab 3 Task 1: Experiment Log

Generated 2026-10-14 17:13 | XGBoost 3.4.1 | bucket `northstar-dev-data-860366539138`

These are **experiment runs**, not the graded run. Report numbers must come from the final `--register` run (`docs/lab3-evidence/evaluation_metrics.json`), not from this file. Use this file to explain *what changed and what it did*.

## What differs between the runs

| Run | MLflow name | max_depth | eta | Features | MLflow |
|---|---|---|---|---|---|
| run1 | `xgb-depth6-eta0.1` | 6 | 0.1 | 11 (no churn_risk_score) | not logged (re-run for this file; the original run 1 is already in MLflow) |
| run2 | `xgb-depth4-eta0.05` | 4 | 0.05 | 11 (no churn_risk_score) | logged |
| run3 | `xgb-with-churn-risk-score` | 6 | 0.1 | 12 (WITH churn_risk_score) | logged |

- **run1: Baseline configuration.** Reference run: default hyperparameters (max_depth 6, eta 0.1), the 11 engineered features, no churn_risk_score.
- **run2: Shallower trees, slower learning.** Hyperparameter variation: max_depth 6 -> 4 and eta 0.1 -> 0.05. Tests whether a simpler, more conservative model generalizes better or loses signal. Features unchanged.
- **run3: Leakage check: add churn_risk_score.** Feature ablation: same hyperparameters as run 1, but churn_risk_score (a pure recency heuristic) is added as a 12th feature. Compare with run 1 to show what that feature contributes; this is the with/without comparison the report requires.

Everything else is identical across runs: same data pull, same 70/30 split (seed 42), same early stopping, same recency-only baseline.

## Metrics side by side

| Metric | run1 | run2 | run3 |
|---|---|---|---|
| AUC-ROC | 0.7792 | 0.7829 | 0.7805 |
| Recency-only baseline AUC | 0.7226 | 0.7226 | 0.7226 |
| AUC lift vs baseline | 0.0566 | 0.0603 | 0.0579 |
| Lift 95% CI low | 0.0376 | 0.0415 | 0.0388 |
| Lift 95% CI high | 0.0754 | 0.0796 | 0.0773 |
| Precision @ top 10%  (gate >= 0.50) | 0.7033 | 0.7167 | 0.7067 |
| Recall @ top 10%  (gate >= 0.25) | 0.3197 | 0.3258 | 0.3212 |
| Positive rate (validation) | 0.22 | 0.22 | 0.22 |
| Validation rows | 3000 | 3000 | 3000 |

### Change vs run 1

| Run | AUC-ROC | Precision@10% | Recall@10% | AUC lift vs baseline |
|---|---|---|---|---|
| run2 | +0.0037 | +0.0134 | +0.0061 | +0.0037 |
| run3 | +0.0013 | +0.0034 | +0.0015 | +0.0013 |

## run1: Baseline configuration

- **MLflow run name:** `xgb-depth6-eta0.1` (not logged (re-run for this file; the original run 1 is already in MLflow))
- **Exit status:** OK
- **Athena QueryExecutionId:** `af706e8d-d511-4198-ac57-ba29c36a48a9`
- **Last boosting round before early stopping:** `[33] train-logloss:0.46614 train-auc:0.86951 validation-logloss:0.51458 validation-auc:0.77922`
- **Full console log:** `output/run1/train.log`

**Gate results**

```
precision_top10          : 0.7033  (threshold: 0.5000)  ✓ PASS
recall_top10             : 0.3197  (threshold: 0.2500)  ✓ PASS
auc_lift 95% CI          : [0.0376, 0.0754]  (must exclude 0)  ✓ PASS
```

**Slice evaluation by loyalty tier**

| Tier | n | Positives | AUC | Recall@10% | Too small | Low recall |
|---|---|---|---|---|---|---|
| Bronze | 1071 | 361 | 0.7557 | 0.4404 | False | False |
| Gold | 483 | 52 | 0.746 | 0.1346 | False | True |
| Platinum | 307 | 21 | 0.8561 | 0.0476 | True | True |
| Silver | 1139 | 226 | 0.7076 | 0.1947 | False | True |

## run2: Shallower trees, slower learning

- **MLflow run name:** `xgb-depth4-eta0.05` (logged)
- **Exit status:** OK
- **Athena QueryExecutionId:** `48cf8f1a-2275-4308-aaf0-732ce6b7eea7`
- **Last boosting round before early stopping:** `[36] train-logloss:0.53356 train-auc:0.80571 validation-logloss:0.54435 validation-auc:0.78287`
- **Full console log:** `output/run2/train.log`

**Gate results**

```
precision_top10          : 0.7167  (threshold: 0.5000)  ✓ PASS
recall_top10             : 0.3258  (threshold: 0.2500)  ✓ PASS
auc_lift 95% CI          : [0.0415, 0.0796]  (must exclude 0)  ✓ PASS
```

**Slice evaluation by loyalty tier**

| Tier | n | Positives | AUC | Recall@10% | Too small | Low recall |
|---|---|---|---|---|---|---|
| Bronze | 1071 | 361 | 0.7571 | 0.4626 | False | False |
| Gold | 483 | 52 | 0.7417 | 0.0962 | False | True |
| Platinum | 307 | 21 | 0.8891 | 0.0476 | True | True |
| Silver | 1139 | 226 | 0.7263 | 0.1858 | False | True |

## run3: Leakage check: add churn_risk_score

- **MLflow run name:** `xgb-with-churn-risk-score` (logged)
- **Exit status:** OK
- **Athena QueryExecutionId:** `ed68f6a3-9352-4bea-99e0-e78d7a06a6bd`
- **Last boosting round before early stopping:** `[26] train-logloss:0.47723 train-auc:0.86130 validation-logloss:0.51858 validation-auc:0.78051`
- **Full console log:** `output/run3/train.log`

**Gate results**

```
precision_top10          : 0.7067  (threshold: 0.5000)  ✓ PASS
recall_top10             : 0.3212  (threshold: 0.2500)  ✓ PASS
auc_lift 95% CI          : [0.0388, 0.0773]  (must exclude 0)  ✓ PASS
```

**Slice evaluation by loyalty tier**

| Tier | n | Positives | AUC | Recall@10% | Too small | Low recall |
|---|---|---|---|---|---|---|
| Bronze | 1071 | 361 | 0.756 | 0.4155 | False | False |
| Gold | 483 | 52 | 0.7402 | 0.1346 | False | True |
| Platinum | 307 | 21 | 0.8798 | 0.1905 | True | True |
| Silver | 1139 | 226 | 0.7121 | 0.2257 | False | False |

## Notes for the report

- Cite the run you actually graded; point at this file only for the run-to-run comparison.
- For the leakage section: compare run 3 to run 1. If AUC barely moves or the lift over baseline shrinks, say why (the score is a recency heuristic, so it duplicates `days_since_last_purchase`).
- For the slice section: report n and positives for every tier, name the weakest tier, and say which tiers are too small to conclude from.
