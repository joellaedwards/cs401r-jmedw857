"""Send realistic, monitorable inference traffic to the endpoint (Lab 6 Task 1).

The drift job needs >= 500 DISTINCT captured requests on the champion variant.
canary_deploy_realtime.py only sends one sample.csv row 200 times, and the drift
script drops those duplicates, so that leaves ~1 usable record. This script
sends N different customers, ONE CSV row per request (batched rows are captured
as a single string and would count as one record).

Rows come from monitoring/baseline/baseline.csv (run export_baseline.py first),
sampled without replacement. At the 90/10 split, 700 requests gives ~630 on
champion.

    python monitoring/send_traffic.py              # 700 requests, 90/10 split
    python monitoring/send_traffic.py --n 900
"""
import argparse
import collections
import pathlib
import sys
import time

import boto3
import pandas as pd

REGION = "us-east-1"
ENDPOINT = "northstar-churn-prod"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "monitoring" / "baseline" / "baseline.csv"
MIN_CHAMPION = 500  # evidently_drift.py marks the window underpowered below this


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=700, help="number of requests (default 700)")
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--source", default=str(BASELINE), help="CSV with a header row of the 11 features")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    df = pd.read_csv(args.source)
    if len(df.columns) != 11:
        sys.exit(f"FATAL: {args.source} has {len(df.columns)} columns, expected 11")
    df = df.drop_duplicates()
    if len(df) < args.n:
        sys.exit(f"FATAL: only {len(df)} distinct rows in {args.source}, asked for {args.n}")
    rows = df.sample(n=args.n, random_state=args.seed)

    rt = boto3.client("sagemaker-runtime", region_name=REGION)
    variants = collections.Counter()
    scores = []
    errors = 0
    t0 = time.time()
    for i, row in enumerate(rows.itertuples(index=False), 1):
        payload = ",".join(str(v) for v in row)  # one row, no header, FEATURE_COLUMNS order
        try:
            resp = rt.invoke_endpoint(EndpointName=args.endpoint,
                                      ContentType="text/csv", Body=payload)
        except Exception as e:  # keep going; a few errors are themselves a signal
            errors += 1
            print(f"  request {i} failed: {e}")
            continue
        variants[resp["InvokedProductionVariant"]] += 1
        scores.append(float(resp["Body"].read().decode().strip().split(",")[0]))
        if i % 100 == 0:
            print(f"  {i}/{args.n} sent ({time.time() - t0:.0f}s) {dict(variants)}")

    s = pd.Series(scores)
    print(f"\nsent {args.n} distinct rows in {time.time() - t0:.0f}s, errors: {errors}")
    print(f"variant split: {dict(variants)}")
    if len(s):
        print(f"score: mean {s.mean():.3f}  p50 {s.median():.3f}  p90 {s.quantile(0.9):.3f}  "
              f"max {s.max():.3f}")
    champ = variants.get("champion", 0)
    if champ < MIN_CHAMPION:
        print(f"WARNING: champion got {champ} < {MIN_CHAMPION}. Send more before the drift job.")
    print("capture is written asynchronously: wait ~5-10 min, then check\n"
          f"  aws s3 ls s3://<bucket>/datacapture/{args.endpoint}/champion/ --recursive | wc -l")


if __name__ == "__main__":
    main()
