# Lab 4: XOps Assessment

## Pipeline Stage Mapping

The pipeline is AWS CodePipeline + CodeBuild, defined as one CloudFormation stack (`pipeline.yaml`), with the build steps in `buildspec.yml`. The five required phases run inside three CodePipeline stages:

| Phase | CodePipeline stage | Where it happens |
|---|---|---|
| 1. Source | `Source` | CodeStar connection pulls `main` from GitHub on every push |
| 2. Test | `Build` | `buildspec.yml` `pre_build`: `pytest tests/test_data.py` and `tests/test_features.py`. A failure stops the build here, before any training |
| 3. Build | `Build` | `buildspec.yml` `build`: `models/churn/pipeline_definition.py --upsert` creates or updates the SageMaker Pipeline, then a spot training job runs `models/churn/train_sagemaker.py`, which calls `churn_model.train()` |
| 4. Evaluate | `Build` | The promotion gates in `evaluate_model()` raise inside training, so a failing model never registers; then `pytest tests/test_model.py tests/test_fairness.py` checks the trained artifact, its metrics and the champion regression |
| 5. Register | `Build` (SageMaker `RegisterModel` step) | The model is registered in `northstar-churn-models` as `PendingManualApproval`. If a later CodeBuild test fails, `post_build` sets that package to `Rejected` |
| Human decision | `ManualApproval` | SNS-notified approval before anything is promoted |

A CodePipeline stage is a deployment boundary, not a logical step. Splitting the five phases into five stages would mean handing the model artifact between stages and running a CodeBuild project per stage, which adds cost and failure points without adding any control. The one boundary that matters, the human promotion decision, is its own stage.

**Why CodePipeline rather than GitHub Actions:**

- **No AWS credentials in GitHub.** The build runs inside AWS under IAM roles, so nothing secret is stored in this public repository or its settings.
- **Native integration** with what the pipeline uses: SageMaker Pipelines and the Model Registry, the CloudWatch `FailedBuilds` metric behind the `northstar-ci-build-failure` alarm, the SNS topic, and a built-in manual approval stage.
- **Infrastructure as code:** the whole CI setup is one CloudFormation stack, so it can be recreated or torn down with one command.
- **Trade-offs:** it ties the pipeline to AWS, its console is less convenient than GitHub's Actions tab, and build minutes are billed (a few cents per run).

## Champion-Challenger

**Criterion.** A challenger replaces the champion only if **all three** are true:

1. **AUC ≥ champion AUC + 0.02**
2. **precision@top10% ≥ champion precision@top10%**
3. **every existing promotion gate passes**: precision@top10% ≥ 0.50, recall@top10% ≥ 0.25, and the 95% bootstrap CI on lift over the recency-only baseline excludes zero.

If any condition fails, the champion stays. The current champion is v3 (AUC 0.7855).

**Why +0.02.** Re-splitting the same data moves AUC by roughly ±0.01–0.02, and the lift CI on this model is about 0.04 wide. A margin of +0.01 sits inside that noise, so it would promote a challenger that only drew a lucky split more often than a margin of +0.02. Each promotion requires a redeploy. We want to redeploy when our model is genuinely improving, but not every time the model changes by even a little. +0.02 is the smallest margin that is clearly outside split-to-split variation, so a promotion reflects a real improvement (new features, more data), not the random seed. The cost is that promotions are rare, but because the model is retrained regularly on new data anyway, we are okay with this trade-off. 

**Why precision@top10% too.** The business contacts the top 10% of customers by risk, so that list is what the model is used for. AUC scores the ranking of every customer, so a challenger could gain AUC by reordering customers who are never contacted. Requiring precision@top10% to be no worse keeps an AUC gain from coming at the expense of the contact list.

**How it relates to the regression test.** `TestChampionRegression` in `tests/test_model.py` is a safety floor, not a promotion rule: it fails the build if a new model's AUC is more than 0.02 *below* the champion. Together the two thresholds give three zones:

| Challenger AUC vs champion | Outcome |
|---|---|
| below −0.02 | regression: the build fails and the package is auto-rejected |
| −0.02 to +0.02 | acceptable but not better: registered as a candidate, champion is kept |
| +0.02 or more (plus conditions 2 and 3) | promoted to champion |

**Example.** The best Lab 4 MLflow run (`xgb-depth6-eta0.03-mcw10`, `docs/lab4-mlflow-runs.txt`) reached AUC 0.7846 against 0.7829 for the production settings, +0.0017. Under this rule it does not replace the champion, which is the right call: the difference is well inside the noise.

## Retraining Triggers

Both triggers are designed, not deployed. Both start the same path a code push does: fresh features, then `northstar-churn-pipeline` (train → gates → register as `PendingManualApproval`). A retrain never replaces production on its own; the new model still has to pass every gate, meet the champion-challenger rule above, and be approved by a person.

### (a) Scheduled: monthly

- **Schedule:** `cron(0 6 1 * ? *)`, 06:00 UTC on the 1st of every month.
- **Fired by:** Amazon EventBridge Scheduler, starting a two-step run: the `northstar-dev-feature-engineer` Glue job with the cutoff moved forward to `T = run date − 90 days` (the latest cutoff whose 90-day holdout has fully played out, so every label is known), then `StartPipelineExecution` on `northstar-churn-pipeline` once the Glue job succeeds (an EventBridge rule on the Glue job's `SUCCEEDED` state event).
- **Why monthly:** each month, `T` moves forward by a month. Customers who were still inside their 90-day holdout at the last retrain now have known outcomes, so every monthly retrain trains on one more month of labeled behavior, and its features reflect purchases a month more recent. Monthly is frequent enough to keep the top-10% contact list current, and infrequent enough that each retrain adds a meaningful amount of new data. A retrain at the start of November means the list going into the holiday season reflects the most recent labeled behavior. Retraining *during* the holiday spike is deliberately avoided, since holiday shopping is temporary and a model fit to it would rank customers poorly in January.
- **Prerequisite:** `FEATURE_CUTOFF` and `SNAPSHOT` are currently hard-coded in `glue-scripts/feature_engineer.py`. They have to become Glue job arguments before a schedule can advance them.

### (b) Performance-based: production AUC drops 0.03 below the current model's approval AUC

- **Threshold:** the trigger is relative to **how the current production model performed when it was approved**, not a fixed bar. Retrain when production AUC falls more than 0.03 below that starting point:

  `retrain if production AUC < (current model's validation AUC at approval − 0.03)`

  For the current champion, v3, the starting AUC was **0.7855**, so the threshold is **AUC < 0.7555**, measured on the most recent cohort whose 90-day churn outcomes are known. AUC moves roughly ±0.01–0.02 between splits, so −0.03 is the smallest drop that is clearly a real decline rather than noise. When a new model is promoted, the threshold resets to that model's own approval AUC minus 0.03.
- **Fired by:** SageMaker Model Monitor (model quality), comparing the production model's predictions against ground-truth churn labels once they arrive. It publishes AUC as a CloudWatch metric; a CloudWatch alarm on `AUC < 0.7555` triggers an EventBridge rule that calls `StartPipelineExecution` on `northstar-churn-pipeline`.
- **How production AUC is measured:** each month the model scores every customer. 90 days later we know which of those customers actually churned. Model Monitor compares the old scores with those real outcomes and computes AUC: how often the model ranked a customer who really left above one who stayed. If that number has fallen 0.03 below where the model started, the model has gotten worse at its job and is retrained.
- **Limitation:** because real outcomes take 90 days to arrive, this trigger catches a decline after it has happened, not before. As an early warning (an alert to a person, not a retrain), Model Monitor data-quality checks can flag when inputs like `days_since_last_purchase` start to look different from the training data, before the outcomes are in.

## DataOps Maturity

**Current level: 1 (Repeatable)**

**Evidence:** The pipeline exists and is reproducible: Terraform defines the S3 bucket, crawler, Glue jobs and Feature Group (`infrastructure/modules/glue/main.tf`, `infrastructure/modules/feature_store/main.tf`), and the jobs (`glue-scripts/transform.py`, `glue-scripts/feature_engineer.py`) turn raw CSV into versioned customer features. The Feature Store offline store keeps every record by `event_time`. Data quality is checked: `tests/test_data.py` covers schema, grain, leakage, post-cutoff data and 48-hour freshness, and `tests/test_features.py` unit-tests the feature logic ported to `data/feature_engineering.py` (`28 passed` and `19 passed` in `docs/lab4-build-green.log`). But the pipeline is **manually triggered**: each session the crawler, transform job and feature job were started by hand with `aws glue start-crawler` / `start-job-run`, which is the Level 1 definition.

**Gap to Level 2:** the data pipeline does not trigger automatically, and its monitoring is not systematic. The data tests only run when code is pushed, never when new data lands, and `FEATURE_CUTOFF`/`SNAPSHOT` are hard-coded in `feature_engineer.py`, so even a scheduled run could not advance the observation window.

**Top priority investment:** an AWS Glue Workflow (crawler → transform → feature job) started by an Amazon EventBridge rule on new objects in `raw/customers/`, with the cutoff passed in as a Glue job argument and the `test_data.py` checks run as the workflow's final step.

## MLOps Maturity

**Current level: 2 (Automated)**

**Evidence:** Every push to `main` runs the pipeline automatically (`pipeline.yaml`, `buildspec.yml`): tests, spot training through `models/churn/pipeline_definition.py`, evaluation gates, and registration (`docs/lab4-pipeline-runs.txt`; `Pipeline status: Succeeded` and `Registered: .../northstar-churn-models/6` in `docs/lab4-build-green.log`). The pipeline is monitored: a failed build fires the `northstar-ci-build-failure` alarm and an email (`docs/lab4-failure-demo.txt`, `docs/lab4-alarm-email.png`). Some Level 3 elements are already in place. Policy is enforced: the deliberate failure stopped at `PRE_BUILD` with no new model version, failing packages are auto-rejected (v2 and v4 in `docs/lab4-model-registry.json`), and `TestChampionRegression` compares every candidate to the approved champion (`champion v3 auc=0.7855` in the green log). There is an audit trail: commit SHA, training data URI and metrics on v6 (`docs/lab4-model-package.json`), plus experiment history in `docs/lab4-mlflow-runs.txt`.

**Gap to Level 3:** no automated retraining on drift signals. Nothing monitors the model after registration, and the retraining triggers are designed above but not deployed. Furthermore, although we have an audit trail, it has been created manually, not by the pipeline (v6 lineage), 

**Top priority investment:** deploy the performance trigger designed above: SageMaker Model Monitor (model quality) publishing production AUC to a CloudWatch alarm at the current model's approval AUC − 0.03, which fires an EventBridge rule that calls `StartPipelineExecution`. In the same change, move the `update-model-package` lineage step into `buildspec.yml` so every package records its own lineage.

## Changes from the Starter Kit and the Lab 3 Model

- **Training instance: `ml.g5.xlarge` spot instead of `ml.m5.large` spot.** This account's `ml.m5.large for spot training job usage` quota is 0 (not the default of 4), and AWS Support denied the increase (looking into that now if there's a way to resend the request). The GPU spot types have a quota of 1, so `buildspec.yml` overrides the pipeline's `TrainingInstanceType` parameter to `ml.g5.xlarge`; `pipeline_definition.py` is unchanged and training is still managed spot with `max_run`/`max_wait`. XGBoost trains on the instance's CPUs, so the model is the same. The cost is a few cents per run, and the trade-off is scarcer GPU spot capacity, which is acceptable because the job trains in about two minutes and simply restarts if interrupted.
- **Monotone constraint on recency.** `models/churn/churn_model.py` constrains the model so churn risk can only rise with `days_since_last_purchase`. The first pipeline run's model (XGBoost 1.7 in the training container) dipped 0.028 between 30 and 60 days dormant and failed `test_model_monotone_on_recency`, so the package was auto-rejected. With the constraint, metrics stay within rounding of Lab 3 (locally: AUC 0.782 vs 0.783, P@10 0.729, R@10 0.333).
