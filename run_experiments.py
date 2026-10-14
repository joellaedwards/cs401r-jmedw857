#!/usr/bin/env python3
"""
Run the three Lab 3 Task 1 experiment runs and write one labeled summary file.

    python scripts/run_experiments.py

Per run, output goes to its own folder (nothing gets overwritten):
    output/<run>/train.log               full console output
    output/<run>/evaluation_metrics.json
    output/<run>/slice-evaluation.csv
    output/<run>/*.png
Combined summary (commit this one, it's your report cheat-sheet):
    docs/lab3-experiment-log.md

Needs MLFLOW_APP_ARN exported so runs 2 and 3 log to MLflow.
Run 1 is a re-run for this log only: it skips MLflow so you don't get a
duplicate (the original run 1 already exists there under the same name).

These are EXPERIMENT runs. Nothing is registered. The graded run is separate.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RUNS = [
    {
        "key": "run1",
        "name": "xgb-depth6-eta0.1",
        "title": "Baseline configuration",
        "purpose": ("Reference run: default hyperparameters (max_depth 6, eta 0.1), "
                    "the 11 engineered features, no churn_risk_score."),
        "args": [],
        "settings": {"max_depth": "6", "eta": "0.1",
                     "features": "11 (no churn_risk_score)"},
        "mlflow": False,
        "mlflow_note": "not logged (re-run for this file; the original run 1 is already in MLflow)",
    },
    {
        "key": "run2",
        "name": "xgb-depth4-eta0.05",
        "title": "Shallower trees, slower learning",
        "purpose": ("Hyperparameter variation: max_depth 6 -> 4 and eta 0.1 -> 0.05. "
                    "Tests whether a simpler, more conservative model generalizes "
                    "better or loses signal. Features unchanged."),
        "args": ["--max-depth", "4", "--eta", "0.05"],
        "settings": {"max_depth": "4", "eta": "0.05",
                     "features": "11 (no churn_risk_score)"},
        "mlflow": True,
        "mlflow_note": "logged",
    },
    {
        "key": "run3",
        "name": "xgb-with-churn-risk-score",
        "title": "Leakage check: add churn_risk_score",
        "purpose": ("Feature ablation: same hyperparameters as run 1, but "
                    "churn_risk_score (a pure recency heuristic) is added as a "
                    "12th feature. Compare with run 1 to show what that feature "
                    "contributes; this is the with/without comparison the "
                    "report requires."),
        "args": ["--include-churn-risk-score"],
        "settings": {"max_depth": "6", "eta": "0.1",
                     "features": "12 (WITH churn_risk_score)"},
        "mlflow": True,
        "mlflow_note": "logged",
    },
]

METRIC_ROWS = [
    ("auc_roc", "AUC-ROC"),
    ("baseline_auc", "Recency-only baseline AUC"),
    ("auc_vs_baseline", "AUC lift vs baseline"),
    ("lift_ci_low", "Lift 95% CI low"),
    ("lift_ci_high", "Lift 95% CI high"),
    ("precision_top10", "Precision @ top 10%  (gate >= 0.50)"),
    ("recall_top10", "Recall @ top 10%  (gate >= 0.25)"),
    ("positive_rate_val", "Positive rate (validation)"),
    ("n_val_samples", "Validation rows"),
]


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def run_one(run, args, env):
    out_dir = Path("output") / run["key"]
    model_dir = Path("model") / run["key"]
    out_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "train.log"

    cmd = [sys.executable, args.script,
           "--artifacts-bucket", args.bucket,
           "--run-name", run["name"],
           "--output-data-dir", str(out_dir),
           "--model-dir", str(model_dir)] + run["args"]
    if not run["mlflow"]:
        cmd += ["--mlflow-arn", ""]   # empty string => skip MLflow

    banner = f"\n{'=' * 70}\n {run['key'].upper()}: {run['title']}  ({run['name']})\n{'=' * 70}"
    print(banner, flush=True)
    print("$ " + " ".join(cmd), flush=True)

    lines = []
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n\n")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env)
        for line in proc.stdout:
            print(line, end="", flush=True)
            log.write(line)
            lines.append(line.rstrip("\n"))
        proc.wait()

    result = {"run": run, "returncode": proc.returncode, "log_path": log_path,
              "lines": lines, "metrics": None, "slices": None,
              "athena_id": None, "mlflow_url": None, "last_round": None,
              "gate_lines": [], "xgb_version": None, "out_dir": out_dir}

    mpath = out_dir / "evaluation_metrics.json"
    if mpath.exists():
        result["metrics"] = json.loads(mpath.read_text())
    spath = out_dir / "slice-evaluation.csv"
    if spath.exists():
        with open(spath, newline="") as f:
            result["slices"] = list(csv.DictReader(f))

    for ln in lines:
        m = re.search(r"Athena QueryExecutionId:\s*(\S+)", ln)
        if m:
            result["athena_id"] = m.group(1)
        m = re.search(r"View run .* at:\s*(\S+)", ln)
        if m:
            result["mlflow_url"] = m.group(1)
        m = re.search(r"XGBoost\s+([\d.]+)", ln)
        if m and not result["xgb_version"]:
            result["xgb_version"] = m.group(1)
        if re.match(r"^\[\d+\]\s+train-", ln):
            result["last_round"] = ln
        if "PASS" in ln or "FAIL" in ln:
            result["gate_lines"].append(ln.strip())
    return result


def write_summary(results, args, path):
    ok = [r for r in results if r["metrics"]]
    ver = next((r["xgb_version"] for r in results if r["xgb_version"]), "unknown")
    L = []
    L.append("# Lab 3 Task 1: Experiment Log")
    L.append("")
    L.append(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
             f"XGBoost {ver} | bucket `{args.bucket}`")
    L.append("")
    L.append("These are **experiment runs**, not the graded run. Report numbers must "
             "come from the final `--register` run (`docs/lab3-evidence/"
             "evaluation_metrics.json`), not from this file. Use this file to explain "
             "*what changed and what it did*.")
    L.append("")

    L.append("## What differs between the runs")
    L.append("")
    L.append(md_table(
        ["Run", "MLflow name", "max_depth", "eta", "Features", "MLflow"],
        [[r["run"]["key"], f"`{r['run']['name']}`", r["run"]["settings"]["max_depth"],
          r["run"]["settings"]["eta"], r["run"]["settings"]["features"],
          r["run"]["mlflow_note"]] for r in results]))
    L.append("")
    for r in results:
        L.append(f"- **{r['run']['key']}: {r['run']['title']}.** {r['run']['purpose']}")
    L.append("")
    L.append("Everything else is identical across runs: same data pull, same "
             "70/30 split (seed 42), same early stopping, same recency-only baseline.")
    L.append("")

    L.append("## Metrics side by side")
    L.append("")
    if ok:
        header = ["Metric"] + [r["run"]["key"] for r in results]
        rows = []
        for key, label in METRIC_ROWS:
            rows.append([label] + [
                (r["metrics"].get(key, "n/a") if r["metrics"] else "FAILED")
                for r in results])
        L.append(md_table(header, rows))
        L.append("")

        base = next((r for r in results if r["run"]["key"] == "run1" and r["metrics"]), None)
        if base:
            L.append("### Change vs run 1")
            L.append("")
            drows = []
            for r in results:
                if r is base or not r["metrics"]:
                    continue
                d = lambda k: f"{r['metrics'][k] - base['metrics'][k]:+.4f}"
                drows.append([r["run"]["key"], d("auc_roc"), d("precision_top10"),
                              d("recall_top10"), d("auc_vs_baseline")])
            if drows:
                L.append(md_table(["Run", "AUC-ROC", "Precision@10%", "Recall@10%",
                                   "AUC lift vs baseline"], drows))
                L.append("")
    else:
        L.append("_No run produced metrics. See the per-run sections below._")
        L.append("")

    for r in results:
        run = r["run"]
        L.append(f"## {run['key']}: {run['title']}")
        L.append("")
        L.append(f"- **MLflow run name:** `{run['name']}` ({run['mlflow_note']})")
        L.append(f"- **Exit status:** {'OK' if r['returncode'] == 0 else 'FAILED (code ' + str(r['returncode']) + ')'}")
        if r["athena_id"]:
            L.append(f"- **Athena QueryExecutionId:** `{r['athena_id']}`")
        if r["mlflow_url"]:
            L.append(f"- **MLflow run:** {r['mlflow_url']}")
        if r["last_round"]:
            last = re.sub(r"\s+", " ", r["last_round"])
            L.append(f"- **Last boosting round before early stopping:** `{last}`")
        L.append(f"- **Full console log:** `{r['log_path'].as_posix()}`")
        L.append("")
        if r["gate_lines"]:
            L.append("**Gate results**")
            L.append("")
            L.append("```")
            L.extend(r["gate_lines"])
            L.append("```")
            L.append("")
        if r["slices"]:
            L.append("**Slice evaluation by loyalty tier**")
            L.append("")
            L.append(md_table(
                ["Tier", "n", "Positives", "AUC", "Recall@10%", "Too small", "Low recall"],
                [[s["tier"], s["n"], s["positives"], s["auc"], s["recall_top10"],
                  s["too_small"], s["flagged_low_recall"]] for s in r["slices"]]))
            L.append("")
        if r["returncode"] != 0:
            L.append("**Run failed. Last lines of output:**")
            L.append("")
            L.append("```")
            L.extend(r["lines"][-15:])
            L.append("```")
            L.append("")

    L.append("## Notes for the report")
    L.append("")
    L.append("- Cite the run you actually graded; point at this file only for the "
             "run-to-run comparison.")
    L.append("- For the leakage section: compare run 3 to run 1. If AUC barely moves "
             "or the lift over baseline shrinks, say why (the score is a recency "
             "heuristic, so it duplicates `days_since_last_purchase`).")
    L.append("- For the slice section: report n and positives for every tier, name "
             "the weakest tier, and say which tiers are too small to conclude from.")
    L.append("")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(L), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", default="northstar-dev-data-860366539138")
    p.add_argument("--script", default="churn_training_skeleton.py")
    p.add_argument("--summary", default="docs/lab3-experiment-log.md")
    args = p.parse_args()

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["SAGEMAKER_SUPPRESS_V2_WARNING"] = "1"
    if not env.get("MLFLOW_APP_ARN"):
        print("WARNING: MLFLOW_APP_ARN is not set; runs 2 and 3 will NOT log to MLflow.\n"
              "  export MLFLOW_APP_ARN=arn:aws:sagemaker:us-east-1:<account>:mlflow-app/app-XXXX")

    results = [run_one(run, args, env) for run in RUNS]
    write_summary(results, args, args.summary)
    print(f"\n{'=' * 70}\nWrote summary: {args.summary}\n{'=' * 70}")
    sys.exit(0 if all(r["returncode"] == 0 for r in results) else 1)


if __name__ == "__main__":
    main()
