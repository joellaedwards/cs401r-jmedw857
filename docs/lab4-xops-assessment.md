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

<!-- TODO: numeric, binary criterion -->

## Retraining Triggers

<!-- TODO: (a) scheduled trigger and (b) performance-based trigger, each with a threshold/schedule expression and the AWS service that fires it -->

## DataOps Maturity

<!-- TODO: Current level / Evidence / Gap to Level N+1 / Top priority investment -->

## MLOps Maturity

<!-- TODO: same structure -->

## Changes from the Starter Kit and the Lab 3 Model

- **Training instance: `ml.g5.xlarge` spot instead of `ml.m5.large` spot.** This account's `ml.m5.large for spot training job usage` quota is 0 (not the default of 4), and AWS Support denied the increase. The GPU spot types have a quota of 1, so `buildspec.yml` overrides the pipeline's `TrainingInstanceType` parameter to `ml.g5.xlarge`; `pipeline_definition.py` is unchanged and training is still managed spot with `max_run`/`max_wait`. XGBoost trains on the instance's CPUs, so the model is the same. The cost is a few cents per run, and the trade-off is scarcer GPU spot capacity, which is acceptable because the job trains in about two minutes and simply restarts if interrupted.
- **Monotone constraint on recency.** `models/churn/churn_model.py` constrains the model so churn risk can only rise with `days_since_last_purchase`. The first pipeline run's model (XGBoost 1.7 in the training container) dipped 0.028 between 30 and 60 days dormant and failed `test_model_monotone_on_recency`, so the package was auto-rejected. With the constraint, metrics stay within rounding of Lab 3 (locally: AUC 0.782 vs 0.783, P@10 0.729, R@10 0.333).
