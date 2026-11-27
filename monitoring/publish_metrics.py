"""Publish the model-layer and business-layer metrics to CloudWatch (Lab 6 Task 1).

Runs on the LAPTOP after an Evidently processing job completes. The job cannot
publish its own metrics: pip-installing evidently breaks urllib3 for botocore
inside the sklearn container, and the ModelMonitorExecution role has no
CloudWatch write on purpose.

  1. Finds the drift job (latest Completed nsc-evidently-* unless --job is given),
     downloads drift_violations.json, drift_report.json and drift_report.html
     to monitoring/reports/ (the two JSON files are committed as evidence).
  2. MODEL layer:    DriftDetected (0/1), DriftViolationCount, and per-feature
                     DriftPSI / DriftKSPValue with a Feature dimension.
  3. BUSINESS layer: ChurnAlertVolume = distinct predictions in the last
                     --hours whose churn score >= --alert-threshold, read from
                     the endpoint's data capture (all variants). Also
                     PredictionVolume, the denominator.
  4. Optional: logs the drift run to the MLflow App (--mlflow-arn).

Namespace NorthStar/ChurnModel, dimension EndpointName, matching the capture
block in the handout.

    python monitoring/publish_metrics.py
    python monitoring/publish_metrics.py --dry-run
    python monitoring/publish_metrics.py --mlflow-arn arn:aws:sagemaker:...:mlflow-app/app-XXXX
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

import boto3

REGION = "us-east-1"
NAMESPACE = "NorthStar/ChurnModel"
ENDPOINT = "northstar-churn-prod"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "monitoring" / "reports"
REPORT_FILES = ["drift_violations.json", "drift_report.json", "drift_report.html"]
# CloudWatch metric names per test, so a PSI statistic and a KS p-value never
# share a metric (they move in opposite directions; see drift_direction()).
METRIC_FOR_TEST = {"psi": "DriftPSI", "ks": "DriftKSPValue", "jensenshannon": "DriftJSD"}


def latest_completed_job(sm) -> str:
    jobs = sm.list_processing_jobs(NameContains="nsc-evidently", StatusEquals="Completed",
                                   SortBy="CreationTime", SortOrder="Descending",
                                   MaxResults=1)["ProcessingJobSummaries"]
    if not jobs:
        sys.exit("FATAL: no Completed nsc-evidently job. Run monitoring/run_evidently_job.py first.")
    return jobs[0]["ProcessingJobName"]


def download_reports(s3, bucket: str, job: str) -> dict:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for name in REPORT_FILES:
        key = f"monitoring/reports/{job}/{name}"
        try:
            s3.download_file(bucket, key, str(REPORTS_DIR / name))
            print(f"downloaded s3://{bucket}/{key}")
        except Exception as e:
            if name == "drift_violations.json":
                sys.exit(f"FATAL: cannot read s3://{bucket}/{key}: {e}")
            print(f"  (skipped {name}: {e})")
    return json.loads((REPORTS_DIR / "drift_violations.json").read_text())


def churn_alert_volume(s3, bucket: str, endpoint: str, hours: int, threshold: float):
    """Count distinct captured predictions with score >= threshold.

    Byte-identical input payloads are counted once, the same rule the drift job
    uses, so the deploy script's 200 copies of sample.csv do not inflate it.
    """
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    prefix = f"datacapture/{endpoint}/"
    seen, scores = set(), []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < since or not obj["Key"].endswith(".jsonl"):
                continue
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read().decode()
            for line in body.splitlines():
                if not line.strip():
                    continue
                try:
                    cap = json.loads(line)["captureData"]
                    inp = cap["endpointInput"]["data"].strip()
                    out = cap["endpointOutput"]["data"].strip()
                except (json.JSONDecodeError, KeyError):
                    continue
                if inp in seen or "\n" in inp:  # duplicate, or a batched payload
                    continue
                seen.add(inp)
                try:
                    scores.append(float(out.split(",")[0]))
                except ValueError:
                    continue
    alerts = sum(s >= threshold for s in scores)
    return alerts, len(scores)


def put(cw, data: list, dry_run: bool) -> None:
    for d in data:
        dims = ",".join(f"{x['Name']}={x['Value']}" for x in d["Dimensions"])
        print(f"  {'(dry-run) ' if dry_run else ''}{NAMESPACE} {d['MetricName']:<20} "
              f"{d['Value']:>10.4f}  [{dims}]")
    if not dry_run:
        for i in range(0, len(data), 20):  # put_metric_data takes <= 1000, keep it small
            cw.put_metric_data(Namespace=NAMESPACE, MetricData=data[i:i + 20])


def log_to_mlflow(arn: str, job: str, s: dict) -> None:
    import mlflow  # lazy: only needed with --mlflow-arn

    mlflow.set_tracking_uri(arn)
    mlflow.set_experiment("northstar-drift")
    with mlflow.start_run(run_name=f"drift-{job}"):
        mlflow.log_params({"baseline_records": s["baseline_records"],
                           "captured_records": s["captured_records"],
                           "variant": "champion", "job": job})
        for feat, d in s["per_feature"].items():
            mlflow.log_metric(f"{d['method']}_{feat}", d["value"])
        mlflow.log_metric("violation_count", s["violation_count"])
        html = REPORTS_DIR / "drift_report.html"
        if html.exists():
            mlflow.log_artifact(str(html))
    print("logged run to MLflow experiment northstar-drift")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bucket", help="default: northstar-dev-data-<account>")
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--job", help="processing job name (default: latest Completed)")
    ap.add_argument("--alert-threshold", type=float, default=0.5,
                    help="churn score that counts as a retention alert (default 0.5)")
    ap.add_argument("--hours", type=int, default=24,
                    help="business window: captures from the last N hours (default 24 = daily)")
    ap.add_argument("--mlflow-arn", help="also log the drift run to this MLflow App")
    ap.add_argument("--dry-run", action="store_true", help="print, do not publish")
    args = ap.parse_args()

    if args.bucket:
        bucket = args.bucket
    else:
        acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
        bucket = f"northstar-dev-data-{acct}"
    sm = boto3.client("sagemaker", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    now = dt.datetime.now(dt.timezone.utc)
    ep_dim = [{"Name": "EndpointName", "Value": args.endpoint}]

    # ── model layer ──────────────────────────────────────────────────────────
    job = args.job or latest_completed_job(sm)
    print(f"drift job: {job}")
    s = download_reports(s3, bucket, job)
    print(f"captured_records={s['captured_records']}  underpowered={s['underpowered_window']}  "
          f"violations={s['violation_count']}")
    if s["underpowered_window"]:
        print("WARNING: underpowered window (<500 records). Send more traffic and rerun the "
              "job before relying on these numbers.")

    model = [
        {"MetricName": "DriftDetected", "Dimensions": ep_dim, "Timestamp": now,
         "Value": 1.0 if s["drift_detected"] else 0.0, "Unit": "Count"},
        {"MetricName": "DriftViolationCount", "Dimensions": ep_dim, "Timestamp": now,
         "Value": float(s["violation_count"]), "Unit": "Count"},
    ]
    for feat, d in s["per_feature"].items():
        name = METRIC_FOR_TEST.get(d["method"], f"Drift_{d['method']}")
        model.append({"MetricName": name, "Timestamp": now, "Value": float(d["value"]),
                      "Unit": "None",
                      "Dimensions": ep_dim + [{"Name": "Feature", "Value": feat}]})
    print("\nmodel layer:")
    put(cw, model, args.dry_run)

    # ── business layer ───────────────────────────────────────────────────────
    alerts, total = churn_alert_volume(s3, bucket, args.endpoint, args.hours,
                                       args.alert_threshold)
    print(f"\nbusiness layer: {alerts} of {total} distinct predictions in the last "
          f"{args.hours}h scored >= {args.alert_threshold}")
    if total == 0:
        sys.exit("FATAL: no captured predictions in the window. Is capture on, and has "
                 "monitoring/send_traffic.py run?")
    business = [
        {"MetricName": "ChurnAlertVolume", "Dimensions": ep_dim, "Timestamp": now,
         "Value": float(alerts), "Unit": "Count"},
        {"MetricName": "PredictionVolume", "Dimensions": ep_dim, "Timestamp": now,
         "Value": float(total), "Unit": "Count"},
    ]
    # % change vs the 7-day average of earlier daily values. The P2 alarm
    # northstar-p2-churn-alert-volume-drop fires below -30. No history -> no
    # datapoint, rather than a fake 0% that would claim the volume is normal.
    hist = cw.get_metric_statistics(
        Namespace=NAMESPACE, MetricName="ChurnAlertVolume", Dimensions=ep_dim,
        StartTime=now - dt.timedelta(days=7), EndTime=now - dt.timedelta(hours=1),
        Period=86400, Statistics=["Maximum"])["Datapoints"]
    if hist:
        avg7 = sum(d["Maximum"] for d in hist) / len(hist)
        pct = (alerts - avg7) / avg7 * 100 if avg7 else 0.0
        print(f"  7-day avg {avg7:.1f} over {len(hist)} day(s): change {pct:+.1f}%")
        put(cw, [{"MetricName": "ChurnAlertVolumeChangePct", "Dimensions": ep_dim,
                  "Timestamp": now, "Value": pct, "Unit": "Percent"}], args.dry_run)
    else:
        print("  no earlier ChurnAlertVolume history: ChurnAlertVolumeChangePct not published")
    put(cw, business, args.dry_run)

    if args.mlflow_arn and not args.dry_run:
        log_to_mlflow(args.mlflow_arn, job, s)
    print("\ndone" + (" (dry run, nothing published)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
