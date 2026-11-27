"""Export the Evidently drift baseline from the Lab 2 feature set (Lab 6 Task 1).

Reads the Parquet the Glue feature job wrote to s3://<bucket>/features/customers/,
keeps exactly the 11 features the endpoint receives (in FEATURE_COLUMNS order),
writes monitoring/baseline/baseline.csv, and uploads it to
s3://<bucket>/monitoring/baseline/baseline.csv where the drift job reads it.

Run from the repo root AFTER the Glue feature-engineer job has succeeded:

    python monitoring/export_baseline.py
    python monitoring/export_baseline.py --bucket northstar-dev-data-<acct> --no-upload
"""
import argparse
import io
import pathlib
import re
import sys

import boto3
import pandas as pd

REGION = "us-east-1"
FEATURES_PREFIX = "features/customers/"
BASELINE_KEY = "monitoring/baseline/baseline.csv"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL_OUT = REPO_ROOT / BASELINE_KEY

# Must match FEATURE_COLUMNS in monitoring/evidently_drift.py (checked below).
# Copied rather than imported because that module imports evidently at the top,
# which only needs to exist inside the processing job, not on this laptop.
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


def check_matches_drift_script() -> None:
    """Fail if this list has drifted from the one the drift job uses."""
    src = (REPO_ROOT / "monitoring" / "evidently_drift.py").read_text(encoding="utf-8")
    block = re.search(r"FEATURE_COLUMNS = \[(.*?)\]", src, re.S)
    theirs = re.findall(r'"([^"]+)"', block.group(1)) if block else []
    if theirs != FEATURE_COLUMNS:
        sys.exit(f"FATAL: FEATURE_COLUMNS differs from evidently_drift.py:\n"
                 f"  here:  {FEATURE_COLUMNS}\n  there: {theirs}")


def read_features(s3, bucket: str) -> pd.DataFrame:
    """Read every Parquet part file under features/customers/ into one frame."""
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=FEATURES_PREFIX):
        keys += [o["Key"] for o in page.get("Contents", [])
                 if o["Key"].endswith(".parquet")]
    if not keys:
        sys.exit(f"FATAL: no Parquet under s3://{bucket}/{FEATURES_PREFIX}. "
                 "Has the Glue feature-engineer job succeeded?")
    print(f"reading {len(keys)} parquet part(s) from s3://{bucket}/{FEATURES_PREFIX}")
    frames = [pd.read_parquet(io.BytesIO(s3.get_object(Bucket=bucket, Key=k)["Body"].read()))
              for k in keys]
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bucket", help="default: northstar-dev-data-<account>")
    ap.add_argument("--no-upload", action="store_true",
                    help="write the local CSV only, skip the S3 upload")
    args = ap.parse_args()

    check_matches_drift_script()

    if args.bucket:
        bucket = args.bucket
    else:
        acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
        bucket = f"northstar-dev-data-{acct}"
    s3 = boto3.client("s3", region_name=REGION)

    df = read_features(s3, bucket)
    print(f"feature set: {len(df):,} rows x {len(df.columns)} columns")

    missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
    if missing:
        sys.exit(f"FATAL: feature set is missing {missing}")
    dropped = [c for c in df.columns if c not in FEATURE_COLUMNS]
    print(f"dropping non-input columns: {dropped}")

    base = df[FEATURE_COLUMNS]
    nulls = base.isna().sum()
    if nulls.any():
        sys.exit(f"FATAL: nulls in baseline:\n{nulls[nulls > 0]}")
    assert list(base.columns) == FEATURE_COLUMNS and len(base.columns) == 11

    LOCAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    base.to_csv(LOCAL_OUT, index=False, lineterminator="\n")
    print(f"wrote {LOCAL_OUT.relative_to(REPO_ROOT)}: {len(base):,} rows x 11 features")

    if args.no_upload:
        print("--no-upload: skipped S3")
        return
    s3.upload_file(str(LOCAL_OUT), bucket, BASELINE_KEY)
    print(f"uploaded s3://{bucket}/{BASELINE_KEY}")


if __name__ == "__main__":
    main()
