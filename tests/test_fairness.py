"""
NorthStar Retail — Fairness Check by Loyalty Tier
CS 401R Lab 4, Task 1

Manual slice evaluation (no SageMaker Clarify). churn_model.train() runs
evaluate_slices() on the validation split and stores the per-tier results in
evaluation_metrics.json under "slices":

    {"Bronze": {"n": ..., "positives": ..., "auc": ..., "recall_top10": ...,
                "too_small": ..., "flagged_low_recall": ...}, ...}

Policy: FLAG, DO NOT BLOCK. If recall@top10% for the highest loyalty tier
(Platinum) and the lowest (Bronze) differ by more than 10 percentage points,
the test emits a warning. It still passes, so the build continues, but the
buildspec runs this file with -rw, so the warning lands in the build log's
warnings summary where the human approver sees it before approving.

TLDR: makes sure the model works well on high and low loyalty tiers.

Run locally:
    EVAL_METRICS_PATH=/tmp/out/evaluation_metrics.json pytest tests/test_fairness.py -v -rw
"""

import json
import os
import warnings
from pathlib import Path

import pytest

TIERS = ["Bronze", "Silver", "Gold", "Platinum"]   # lowest -> highest loyalty
RECALL_GAP_LIMIT = 0.10                             # 10 percentage points


@pytest.fixture(scope="module")
def slices():
    metrics_path = os.environ.get("EVAL_METRICS_PATH", "/tmp/eval_metrics.json")
    if not Path(metrics_path).exists():
        pytest.skip(f"Evaluation metrics not found at {metrics_path}.")
    with open(metrics_path) as f:
        metrics = json.load(f)
    assert "slices" in metrics, (
        "evaluation_metrics.json has no 'slices' - churn_model.train() must put "
        "evaluate_slices() results into metrics['slices']"
    )
    return metrics["slices"]


class TestFairnessSlices:

    def test_every_tier_has_slice_results(self, slices):
        missing = [t for t in TIERS if t not in slices]
        assert not missing, f"No slice results for tiers: {missing}"
        for tier in TIERS:
            for key in ("n", "positives", "recall_top10"):
                assert key in slices[tier], f"{tier} slice is missing '{key}'"

    def test_slice_results_are_valid(self, slices):
        """Counts must be consistent and recall must be a proportion."""
        for tier, s in slices.items():
            assert s["n"] > 0, f"{tier} slice is empty"
            assert 0 <= s["positives"] <= s["n"], f"{tier}: positives outside [0, n]"
            if s["recall_top10"] is not None:
                assert 0.0 <= s["recall_top10"] <= 1.0, f"{tier}: recall_top10 outside [0, 1]"

    def test_recall_gap_highest_vs_lowest_tier(self, slices):
        """Flag (do not block) a recall gap > 10pp between Platinum and Bronze."""
        lowest, highest = TIERS[0], TIERS[-1]
        r_low = slices[lowest]["recall_top10"]
        r_high = slices[highest]["recall_top10"]
        assert r_low is not None and r_high is not None, (
            f"Cannot compare tiers: {lowest} or {highest} has no churners in the validation split"
        )

        gap = abs(r_high - r_low)
        if gap > RECALL_GAP_LIMIT:
            per_tier = ", ".join(
                f"{t} {slices[t]['recall_top10']:.3f}"
                + (" (small slice)" if slices[t].get("too_small") else "")
                for t in TIERS if slices[t]["recall_top10"] is not None
            )
            warnings.warn(
                f"FAIRNESS FLAG: recall@top10% gap between {highest} ({r_high:.3f}) and "
                f"{lowest} ({r_low:.3f}) is {gap * 100:.1f}pp, above the "
                f"{RECALL_GAP_LIMIT * 100:.0f}pp limit. Per tier: {per_tier}. "
                f"Not blocking - review before approving this model.",
                UserWarning,
            )
