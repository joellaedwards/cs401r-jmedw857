"""Your Lab 3 churn model, packaged for the Lab 4 pipeline.

YOU WRITE THIS FILE. The pipeline's training job runs train_sagemaker.py
(beside this file; do not edit it), which imports this module and calls
train(). Everything the job needs from you goes through the three names below.

Contract (train_sagemaker.py checks it in the first seconds of the job):

    FEATURE_COLUMNS   list[str]   model input columns, in order
    LABEL             str         "churn_label"
    train(df, seed=42) -> (xgboost.Booster, dict)

`df` is the full labeled feature table: every column of your Lab 2 output,
including `loyalty_tier` for slice evaluation. It is already loaded. Do not
query Athena or the Feature Store from here; the training container stages
the data for you and has no business making service calls for its input.

The metrics dict must include the four numbers the CI gate in
tests/test_model.py reads. Your Lab 3 evaluate_model() already returns them:

    precision_top10, recall_top10      gated at 0.50 / 0.25
    lift_ci_low, lift_ci_high          bootstrap 95% CI on AUC lift over the
                                       recency-only baseline; lift_ci_low > 0

Include anything else you reported in Lab 3 (auc_roc, baseline_auc,
positive_rate_val, slice results). It is all written to evaluation_metrics.json
and attached to the registered model.

What runs in the container: the SageMaker XGBoost 1.7-1 image ships pandas,
numpy, scikit-learn, xgboost and boto3. It does NOT ship mlflow or sagemaker,
so do not import them at the top of this file. MLflow tracking stays in your
Lab 3 script.

Only this directory (models/churn/) is uploaded to the training job, so the
code must live here. `import churn_training_skeleton` will fail inside the
container even though it works on your laptop. Copy the functions you need.

Test it locally before pushing:

    python models/churn/train_sagemaker.py \
        --train-channel <dir containing your features CSV or Parquet> \
        --model-dir /tmp/model --output-data-dir /tmp/out
"""

# Keeps Lab 3 annotations like `-> tuple[np.ndarray, np.ndarray]` from being
# evaluated, whatever Python the training container runs.
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import xgboost as xgb
from sklearn.metrics import roc_auc_score  # type: ignore[import-untyped]
from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]


# HYPERPARAMS

HYPERPARAMS = {
    "max_depth": 4,
    "eta": 0.05,
    "num_round": 200,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}


# Must match the columns your Lab 3 model was trained on, in the same order.
FEATURE_COLUMNS = [
    "days_since_last_purchase",
    "customer_tenure_days",
    "purchase_frequency_30d",
    "purchase_frequency_90d",
    "purchase_frequency_180d",
    "avg_order_value",
    "total_spend_90d",
    "total_lifetime_value",
    "avg_basket_size_6m",
    "category_diversity_score",
    "online_to_store_ratio",
]

LABEL = "churn_label"

EVAL_THRESHOLDS = {
    "precision_top10":   0.50,   # Minimum precision @ top 10% scored customers
    "recall_top10":      0.25,   # Minimum recall @ top 10% scored customers
    # Note the recall ceiling: with ~22% positives, targeting the top 10% of
    # customers caps achievable recall near 0.45. Hitting 0.25 means capturing
    # roughly half of what is reachable within that contact budget.
}
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42
BASELINE_FEATURE = "days_since_last_purchase"
SLICE_COLUMN = "loyalty_tier"
SMALL_SLICE_POSITIVES = 30  # not enough churners. we can't decide anything 
RECALL_GAP = 0.10  
LABEL_COLUMN = "churn_label"


# ── Feature Preprocessing ──────────────────────────────────────────────────────

def preprocess_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.Series]:
    """
    Clean and prepare features for XGBoost training.
    Returns (X, y) arrays.
    """
    print(f"Preprocessing {len(df):,} samples...")

    # Drop rows where label is missing
    df = df.dropna(subset=[LABEL_COLUMN])

    # Handle missing values in features
    # XGBoost can handle NaN natively, but document your imputation strategy.
    # For features with business-meaningful nulls (e.g., clickstream for non-web customers),
    # consider median imputation or a sentinel value.
    X = df[FEATURE_COLUMNS].fillna(-1).values  # Sentinel -1 for missing
    y = df[LABEL_COLUMN].values.astype(int)
    if SLICE_COLUMN in df.columns:
        slice_values = df[SLICE_COLUMN].astype(str)
    else:
        slice_values = pd.Series("all", index=df.index)

    print(f"  Positive class rate: {y.mean():.1%}")
    print(f"  Feature matrix shape: {X.shape}")

    return X, y, slice_values


# ── Model Training ─────────────────────────────────────────────────────────────

def train_model(X_train: np.ndarray,
                y_train: np.ndarray,
                X_val: np.ndarray,
                y_val: np.ndarray,
) -> xgb.Booster:
    """Train the XGBoost churn model."""

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_COLUMNS)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_COLUMNS)

    # Class imbalance, computed from the training split rather than hardcoded.
    # On the reference dataset this lands at 3.545.
    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))

    # Record the RESOLVED value so track_run() can log what was actually used.
    # Logging HYPERPARAMS.scale_pos_weight instead would log None on every run that
    # let it compute itself -- which is most of them -- and your MLflow
    # comparison would be missing the one parameter most likely to explain a
    # precision/recall difference between runs.

    params = {
        # eval_metric ORDER MATTERS. XGBoost early-stops on the LAST metric in
        # this list, not the first. This used to read ["auc", "logloss"], which
        # silently made logloss the early-stopping criterion: under
        # scale_pos_weight the reweighted logloss keeps improving long after
        # ranking quality peaks, so training ran the full 200 rounds instead of
        # stopping near round 31 and validation AUC came out 0.7603 instead of
        # 0.7822. Measured across 50 splits, that cost the model the promotion
        # gate on 28% of them -- a student doing everything right was told their
        # features did not beat recency. Keep "auc" last (defect 49).
        "objective": "binary:logistic",
        "eval_metric": ["logloss", "auc"],
        "max_depth": HYPERPARAMS["max_depth"],
        "eta": HYPERPARAMS["eta"],
        "min_child_weight": HYPERPARAMS["min_child_weight"],
        "subsample": HYPERPARAMS["subsample"],
        "colsample_bytree": HYPERPARAMS["colsample_bytree"],
        "scale_pos_weight": spw,
        "seed": 42,
        "verbosity": 1,
    }

    print(f"\nTraining XGBoost with params: {json.dumps(params, indent=2)}")

    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=HYPERPARAMS["num_round"],
        evals=[(dtrain, "train"), (dval, "validation")],
        early_stopping_rounds=20,
        verbose_eval=25,
    )

    return model





def train_recency_baseline(X_train: np.ndarray,
                           y_train: np.ndarray,
                           X_val: np.ndarray) -> np.ndarray:
    """Train the recency-only baseline and score the validation split.

    The baseline is a model over `days_since_last_purchase` ALONE, trained on
    the same training rows and scored on the same validation rows as the full
    model. That is what makes the comparison fair.

    It is deliberately NOT a constant predictor. A constant prediction has an
    AUC of exactly 0.5 by construction, so "beating" it proves only that your
    model is better than a coin flip. The question Lab 3 asks is whether your
    feature engineering beats the rule the business already has for free:
    "contact whoever has not purchased in a while."
    """
    i = FEATURE_COLUMNS.index(BASELINE_FEATURE)
    dtr = xgb.DMatrix(X_train[:, [i]], label=y_train, feature_names=[BASELINE_FEATURE])
    dva = xgb.DMatrix(X_val[:, [i]], feature_names=[BASELINE_FEATURE])
    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))
    params = {
        "objective": "binary:logistic", "eval_metric": "auc",
        "max_depth": 4, "eta": 0.1, "subsample": 0.9,
        "colsample_bytree": 0.9, "scale_pos_weight": spw, "seed": 42,
    }
    return xgb.train(params, dtr, num_boost_round=200).predict(dva)


def bootstrap_lift_ci(y_true: np.ndarray,
                      proba: np.ndarray,
                      baseline_proba: np.ndarray,
                      n: int = BOOTSTRAP_N,
                      seed: int = BOOTSTRAP_SEED,
                      alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI for (model AUC - baseline AUC), by resampling the val set.

    PAIRED: each replicate resamples row indices once and scores BOTH models on
    those same rows, so the interval is on the difference and the correlation
    between the two models is preserved. Resampling them independently breaks
    the pairing and inflates the interval, which would make a real improvement
    look inconclusive.

    Replicates whose resample happens to be single-class are skipped, because
    AUC is undefined there.

    The seed is fixed so the gate is reproducible: the same split must always
    produce the same verdict.
    """
    rng = np.random.default_rng(seed)
    y_true = np.asarray(y_true)
    idx = np.arange(len(y_true))
    diffs = []
    for _ in range(n):
        s = rng.choice(idx, size=len(idx), replace=True)
        ys = y_true[s]
        if len(np.unique(ys)) < 2:
            continue
        diffs.append(roc_auc_score(ys, proba[s]) - roc_auc_score(ys, baseline_proba[s]))
    if len(diffs) < n // 2:
        raise ValueError(
            "Too few usable bootstrap replicates to form a confidence interval. "
            "Your validation set is too small or too imbalanced to support this gate."
        )
    d = np.sort(diffs)
    return float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))


def evaluate_model(model: xgb.Booster,
                   X_val: np.ndarray,
                   y_val: np.ndarray,
                   baseline_proba: np.ndarray) -> dict:
    """
    Evaluate the trained model against required thresholds.
    Returns a metrics dict. Raises ValueError if thresholds are not met.
    """
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = model.predict(dval)

    auc_roc = roc_auc_score(y_val, y_pred_proba)

    # Precision and recall @ top 10%
    top10_threshold = np.percentile(y_pred_proba, 90)
    top10_mask = y_pred_proba >= top10_threshold
    precision_top10 = y_val[top10_mask].mean() if top10_mask.sum() > 0 else 0.0
    recall_top10 = y_val[top10_mask].sum() / y_val.sum() if y_val.sum() > 0 else 0.0

    # Baseline: a model trained on days_since_last_purchase ALONE.
    #
    # This used to be `np.full_like(y_pred_proba, y_val.mean())` - a constant
    # prediction, whose AUC is exactly 0.5 by definition. Beating a coin flip
    # is not evidence that your feature engineering did anything, and it is not
    # what Lab 3 asks for. The comparison that matters is against recency,
    # because recency is the rule the business already has for free.
    #
    baseline_auc = roc_auc_score(y_val, baseline_proba)
    auc_vs_baseline = auc_roc - baseline_auc

    lift_ci_low, lift_ci_high = bootstrap_lift_ci(
        y_val, y_pred_proba, baseline_proba)

    metrics = {
        "auc_roc": round(float(auc_roc), 4),
        "precision_top10": round(float(precision_top10), 4),
        "recall_top10": round(float(recall_top10), 4),
        "auc_vs_baseline": round(float(auc_vs_baseline), 4),
        "baseline_auc": round(float(baseline_auc), 4),
        "lift_ci_low": round(float(lift_ci_low), 4),
        "lift_ci_high": round(float(lift_ci_high), 4),
        "positive_rate_val": round(float(y_val.mean()), 4),
        "n_val_samples": len(y_val),
        "eval_timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }

    print("\n── Evaluation Results ──────────────────────────────")
    failures = []
    for metric, threshold in EVAL_THRESHOLDS.items():
        value = metrics[metric]
        status = "✓ PASS" if value >= threshold else "✗ FAIL"
        print(f"  {metric:25s}: {value:.4f}  (threshold: {threshold:.4f})  {status}")
        if value < threshold:
            failures.append(f"{metric}={value:.4f} < {threshold}")

    # The baseline gate: the interval must exclude zero.
    ci_status = "\u2713 PASS" if lift_ci_low > 0 else "\u2717 FAIL"
    print(f"  {'auc_lift 95% CI':25s}: [{lift_ci_low:.4f}, {lift_ci_high:.4f}]"
          f"  (must exclude 0)  {ci_status}")
    if lift_ci_low <= 0:
        failures.append(
            f"auc_lift 95% CI [{lift_ci_low:.4f}, {lift_ci_high:.4f}] includes "
            f"zero - no evidence the model beats the recency-only baseline")

    if failures:
        raise ValueError(
            f"Model failed evaluation thresholds: {'; '.join(failures)}. "
            f"Do not promote to Model Registry."
        )

    print("  All thresholds passed ✓")

    # Add slice evaluation
    # Evaluate AUC and recall separately for each loyalty_tier and flag
    # any segment where recall drops more than 10pp below aggregate recall.

    return metrics


# ── Slice Evaluation ───────────────────────────────────────────────────────────    

def evaluate_slices(model: xgb.Booster,
                    X_val: np.ndarray,
                    y_val: np.ndarray,
                    slice_column: pd.Series) -> dict:
    """
    Evaluate model performance by slice (e.g., loyalty_tier, age_band).
    Flag slices where recall@10% drops more than 10pp below aggregate.
    """
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = model.predict(dval)
    in_top10 = y_pred_proba >= np.quantile(y_pred_proba, 0.9)
    aggregate_recall = (y_val & in_top10).sum() / y_val.sum()

    slice_column = np.asarray(slice_column)
    results = {}

    for slice_value in sorted(set(slice_column)):
        mask = slice_column == slice_value
        n = int(mask.sum())
        pos = int(y_val[mask].sum())
        tier_auc = float(roc_auc_score(y_val[mask], y_pred_proba[mask])) if 0 < pos < n else None
        recall = float(y_val[mask & in_top10].sum() / pos) if pos > 0 else None
        results[slice_value] = {
            "n": n,
            "positives": pos,
            "auc": None if tier_auc is None else round(tier_auc, 4),
            "recall_top10": None if recall is None else round(recall, 4),
            "too_small": pos < SMALL_SLICE_POSITIVES,
            "flagged_low_recall": bool(recall is not None and recall < aggregate_recall - RECALL_GAP),
        }

    return results



def train(df: pd.DataFrame, seed: int = 42):

    x, y, tiers = preprocess_features(df)

    # train and validation split
    x_train, x_val, y_train, y_val, _, tiers_val = train_test_split(x, y, tiers, test_size=0.30, random_state=seed, stratify=y)
    print(f"\nTrain: {len(x_train):,} | Validation: {len(x_val):,}")

    # train
    model = train_model(x_train, y_train, x_val, y_val)

    # evaluate
    baseline_proba = train_recency_baseline(x_train, y_train, x_val)
    metrics = evaluate_model(model, x_val, y_val, baseline_proba)

    # per-tier results ride along in evaluation_metrics.json for test_fairness.py
    metrics["slices"] = evaluate_slices(model, x_val, y_val, tiers_val.to_numpy(dtype=object))

    return model, metrics

    