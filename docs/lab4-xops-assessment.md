# Lab 4: XOps Assessment

## Pipeline Stage Mapping

<!-- TODO: map the five phases (Source, Test, Build, Evaluate, Register) to CodePipeline stages and give the rationale for CodePipeline. -->

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
