# Lab 6 Runbook: NorthStar Churn Model

## Task 2: Drift Detection Plan

### 1. Drift types most likely for NorthStar

**Data drift is the most likely, and it happens on a schedule.** NorthStar sells outdoor gear, apparel and home goods, all seasonal. Purchase frequency and basket size rise in the summer camping season and during holiday promotions (Black Friday through December), then fall in January. Those are the exact inputs the model relies on most: `purchase_frequency_30d`, `purchase_frequency_180d` and `days_since_last_purchase`. Other planned changes move the inputs too:
- **Catalog refreshes:** NorthStar has 12,000 active SKUs and the product catalog updates daily, so category diversity and order value can shift when lines are added or dropped.
- **Store-level promotions and closures:** these are entered manually in the Store Operations System, so a closure can suddenly push a region's customers from stores to online, which moves `online_to_store_ratio` and order value ($87 in store vs. $112 online).

Most of this drift is expected and temporary, so a seasonal alert means investigate, not automatically retrain.

**Concept drift is the second most likely, and it is harder to see.** Here, the same behavior starts leading to a different outcome:
- **Economic shifts:** in a downturn, a customer who buys at the same pace as before may still be about to cut spending on non-essential outdoor gear. A competitor's sale can pull away customers whose history looks loyal.
- **NorthStar's own retention offers:** the model flags the top 10% for a retention offer every week. If the offer works, those customers don't churn, and over time the link between "looks like a churner" and "actually churns" weakens. The model's success changes the thing it predicts.

**Model degradation is the result, not a separate cause.** It is the drop in Recall@10% that follows when data or concept drift goes uncorrected. It is the least directly observable of the three because the churn label takes 90 days to arrive. That's why it is tracked through the weekly quality SLO and the proxy in part 3 rather than detected directly.

### 2. How each feature is checked for drift

The drift job monitors five of the 11 endpoint features, using two statistical tests:

- **PSI (Population Stability Index)** sorts a feature's values into buckets and compares how much of the baseline vs. the live data lands in each one. It is 0 when the two distributions match and grows as they move apart, so drift means **PSI > 0.2**.
- **Kolmogorov-Smirnov (KS) test** measures the largest gap between the two distributions and reports a **p-value**: how likely a gap that big would be by chance alone. A small p-value means the difference is real, so drift means **p-value < 0.05**. It moves in the opposite direction from PSI.

The three features checked with PSI are the model's top three by XGBoost gain in the Lab 3 feature importance plot. If these move, the predictions move the most. The two features checked with the Kolmogorov-Smirnov test are the next two by gain.

| Feature | Lab 3 gain | Test | Threshold | Drift when |
|---|---|---|---|---|
| purchase_frequency_180d | ~130 | PSI | 0.2 | PSI > 0.2 |
| days_since_last_purchase | ~130 | PSI | 0.2 | PSI > 0.2 |
| purchase_frequency_30d | ~75 | PSI | 0.2 | PSI > 0.2 |
| purchase_frequency_90d | ~71 | Kolmogorov-Smirnov | 0.05 | p-value < 0.05 |
| avg_basket_size_6m | ~52 | Kolmogorov-Smirnov | 0.05 | p-value < 0.05 |

**Baseline window:** the full Lab 2 training feature set (`features/customers/`, all customers), computed from purchases on or before the feature cutoff of 2026-04-01. Only the 11 endpoint input features are included, not `churn_label` or `churn_risk_score`.

**Comparison window:** captured inference requests on the champion variant, at least 500 records, with duplicate test payloads removed.

These thresholds match `DRIFT_PLAN` in `monitoring/evidently_drift.py`.

### 3. Concept drift proxy

The churn label only exists after the 90-day window, so a prediction made today can't be checked until three months from now. Two earlier signals stand in for it.

**Primary proxy: shift in the model's score distribution. Lag: about 1 week.**
Every week, compare the distribution of churn scores the model produced against the score distribution on the training data, using PSI with the same 0.2 threshold. It is available as soon as the weekly scoring run finishes. A score shift while the input features show no drift is the warning sign for concept drift: the inputs look the same but the model is reacting to them differently, or a combination of small shifts is adding up. `ChurnAlertVolume` (scores ≥ 0.5) is the daily version of the same signal, and its P2 alert fires on a > 30% drop vs. the 7-day average.

Limit: a score shift says the model's *output* moved, not whether it is *wrong*. That is why there is a second proxy.

**Secondary proxy: 30-day early-inactivity rate. Lag: 30 days.**
For each weekly scoring cohort, check after 30 days what fraction of customers have made no purchase since they were scored, then compare high-risk customers (score ≥ 0.5) with low-risk ones. That 30-day silence is an early, partial version of the 90-day churn label, and it is available two months sooner. If the model still separates churners well, high-risk customers should go silent at a much higher rate than low-risk ones. If that gap shrinks for two cohorts in a row, the model's ranking is getting worse even before the 90-day label confirms it. Customers who received a retention offer are excluded, because the offer itself changes whether they go silent.

**When the true label arrives (90 days),** Recall@10% on that cohort is measured against the prediction-quality SLO (≥ 0.25), which confirms or clears what the proxies suggested.

## Task 4: SLOs

Budgets are per 30-day month (43,200 minutes, about 4.3 weekly samples). Request-based budgets are given as a percentage plus an example at 100,000 requests/month, because the budget scales with traffic.

| SLO | Target | SLI (good / total) | Error budget | Deployment freeze trigger |
|---|---|---|---|---|
| **Availability** | 99.5% | Successful predictions / total requests | **0.5% of requests ≈ 216 min/month** of full outage (500 failed requests per 100,000) | Freeze when **≥ 50% of the monthly budget (108 min or 250 failed requests per 100,000) is used in any rolling 7 days**, or **100% is used in the month**. Lifts when the rolling 30-day budget used drops below 50%. |
| **Latency (p95)** | < 20 ms (`ModelLatency` ≤ `20000` µs) | Requests completing < 20 ms / total requests | **5% of requests may exceed 20 ms** (5,000 per 100,000) | Freeze when **more than 2.5% of requests in any rolling 7 days are slower than 20,000 µs** (half the budget), or **p95 stays above 20,000 µs for 3 consecutive hours outside a deployment window**. |
| **Prediction quality** | Recall@10% ≥ 0.25 on the weekly sample | Weekly samples passing / total weekly samples | **1 failing week per rolling 4 weeks** (75% of weeks pass) | Freeze when **2 consecutive weekly samples score below 0.25**, or **any single week scores below 0.20**. Only a rollback to the last passing model version or a retrain fix may deploy. |
| **Fairness** | Recall gap across loyalty tiers ≤ 10pp | Weeks within the fairness threshold / total weeks | **1 week over the gap per quarter** (13 weeks, about 92%) | Freeze when **any week's gap exceeds 10pp a second time in the same quarter**, or **any week exceeds 15pp**. Only a model version whose measured gap is smaller than the current one may deploy. |

**How deployments spend the budget.** Each deploy's cold start (~24,000 µs on the first invocations) breaches the 20 ms latency SLO, so every deploy spends a small amount of the latency budget. That's why the latency freeze ignores deployment windows for the "3 consecutive hours" condition but still counts their slow requests in the 7-day total.

**Quality and fairness lag 90 days.** Both are measured on the weekly cohort whose 90-day churn label has just matured, so a failing week reflects the model as it was about three months ago. Part 3 of the drift plan covers the earlier warning signals.

**Fairness is in breach at launch.** The Lab 5 models measured a 34.8pp (v8) and 42.3pp (v7) recall gap across loyalty tiers, far over 10pp. The 10pp target is a fairness requirement, not a number tuned to the current model, so it stays. The freeze trigger means any new model has to *reduce* the gap before it can ship, which makes closing the gap the priority for the next retrain.

## Task 5: Runbooks

## Runbook: Data Drift on days_since_last_purchase (Scenario A)

### Detection
- **Alert:** `northstar-p3-model-drift` (P3, Slack + ticket). It fires when the drift job's `DriftPSI` for `days_since_last_purchase` is **> 0.2**, which publishes `DriftDetected = 1` in `NorthStar/ChurnModel`.
- **Secondary signals that confirm it:**
  - `drift_violations.json` for the same job lists `days_since_last_purchase` with `underpowered_window: false` (≥ 500 captured records). A small window can manufacture drift.
  - The other features checked with PSI (`purchase_frequency_30d`, `purchase_frequency_180d`) moved in the same direction. Recency and frequency usually shift together.
  - `ChurnAlertVolume` moved by more than ~20% against its 7-day average. Recency is a top-2 feature, so a real shift shows up in the scores.

### Triage (< 5 minutes)
- **Step 1:** Open `drift_report.html` (MLflow run `drift-<job>` in `northstar-drift`, or `s3://<bucket>/monitoring/reports/<job>/`) and check the direction. Higher recency means customers are buying less often; lower means a promotion is pulling them back.
- **Step 2:** Check whether the shift is explained by a known event. Is `northstar-maintenance-window` in ALARM (retraining or deploy)? Is there a holiday or seasonal promotion, a catalog refresh, or a store closure in the Store Operations System this week?
- **Step 3:** Check that it isn't a pipeline bug. Did the last Glue `northstar-dev-feature-engineer` run succeed (no `northstar-p3-glue-job-failed` event)? Is `days_since_last_purchase` still ≥ 0 in the captured payloads? A broken cutoff date or a missing day of POS data shifts recency instantly.

### Containment Options
- **Option A: Monitor and annotate (explained, seasonal drift).** If triage ties the shift to a known promotion or season, record it on the ticket, keep serving the current model, and re-run the drift job daily. Choose this when the shift has a known cause and `ChurnAlertVolume` is still within 30% of its 7-day average.
- **Option B: Graceful degradation to the recency baseline.** If the shift is unexplained, or `ChurnAlertVolume` dropped more than 30%, stop trusting the model's ranking for this week's retention campaign. Rank customers by the Lab 3 recency baseline (`churn_risk_score`) for the top-10% list instead. It is a simple rule that doesn't depend on the drifted feature mix, and it already reaches AUC ~0.72. The endpoint keeps serving. Only the campaign list switches, so retention outreach continues at reduced accuracy rather than stopping.
- **Option C: Fix the pipeline.** If triage step 3 finds a data bug, fix it, re-run the Glue jobs, and re-run the drift job. No model change is needed.

### Escalation
- **Trigger for escalating beyond on-call:** the drift is unexplained after triage **and** still present on the next daily drift run, **or** two or more of the three features checked with PSI drift together, **or** the P2 `northstar-p2-churn-alert-volume-drop` alarm also fires.
- **Who is paged:** the on-call ML engineer owns the ticket. Escalation brings in the **ML engineering lead** (retrain decision) and notifies the **retention marketing lead** if Option B changes this week's campaign list.
- **SLA for response:** P3 is acknowledged within the same business day (≤ 4 hours). After escalation, the lead responds within **60 minutes**, and the retrain decision is made before the Monday 6 AM ET scoring deadline.

### Resolution Verification
- **How to confirm:** run `monitoring/run_evidently_job.py` on a fresh window of ≥ 500 records, then `publish_metrics.py`.
- **Metrics that must return to normal:** `DriftPSI` for `days_since_last_purchase` **≤ 0.2** on **two consecutive** drift runs, `DriftDetected = 0`, and the composite alarm `northstar-p3-model-drift` back in **OK**. If the model was retrained, the new model must also pass the Lab 4 promotion gate (Recall@10% ≥ 0.25) before it serves.

### Post-Incident Actions
- **Within 24 hours:** add a ticket note with the cause (seasonal / pipeline bug / real shift), the containment option chosen, and the drift values. If it was seasonal, add the event to the drift calendar so next year's run is annotated in advance.
- **Postmortem required:** **yes** if Option B (fallback ranking) was used, a retrain was triggered, or a pipeline bug reached the endpoint. **No** for explained seasonal drift handled with Option A.

## Runbook: Inference Latency Spike (Scenario B)

### Detection
- **Alert:** `northstar-p1-latency-p95-over-500ms` (P1, wakes on-call and pages the manager). Champion p95 `ModelLatency` **> 500000 µs (500 ms) for 5 consecutive minutes**. The P2 `northstar-p2-latency-p95-over-200ms` usually fires first.
- **Secondary signals that confirm it:**
  - `CPUUtilization` / `MemoryUtilization` on champion (`/aws/sagemaker/Endpoints`) are high. Overload is the likely cause; `northstar-p3-endpoint-cpu-high` may also be in ALARM.
  - `Invocations` jumped: a traffic spike.
  - `Invocation5XXErrors` rising: requests are starting to time out or fail, which puts the availability SLO at risk too.
  - `OverheadLatency` normal while `ModelLatency` is high: the slowness is inside the model container, not SageMaker's routing.

### Triage (< 5 minutes)
- **Step 1:** Check whether this is a deployment. `aws sagemaker describe-endpoint --endpoint-name northstar-churn-prod` shows `Updating`, or a new `EndpointConfigName` from the last hour. Cold start (~24,000 µs) shouldn't reach 500 ms, so a deploy plus 500 ms points at the new model or config.
- **Step 2:** Check whether it's one variant or both. Compare `ModelLatency` p95 for `champion` and `canary`. If only the canary is slow, it's the new model.
- **Step 3:** Check load. Compare `Invocations` to its normal level and look at CPU and memory. High traffic with high CPU means capacity; normal traffic with high latency means the model or instance is unhealthy.

### Containment Options
- **Option A: Shift traffic off the slow variant.** If the canary or the new config is slow, set its weight to 0 (`rollback-action.sh`, or `update-endpoint-weights-and-capacities` with champion 100 / canary 0). Takes effect in about a minute. Choose this when only one variant is slow.
- **Option B: Add capacity.** If it's load, raise the champion instance count (`update-endpoint-weights-and-capacities` with `DesiredInstanceCount: 2`), or move to `ml.m5.large`. Burstable `ml.t2.medium` instances run out of CPU credits under sustained load and can't be auto-scaling targets. Choose this when CPU is high and both variants are slow.
- **Option C: Graceful degradation to last week's batch scores.** Churn scoring is a weekly batch by Monday 6 AM ET, so nothing *needs* a real-time score immediately. If latency can't be fixed within 30 minutes, callers read each customer's score from last week's batch output in S3 instead of calling the endpoint. Scores are up to a week old, but nothing fails, and the retention campaign runs on schedule. Real-time calls resume once p95 is back under 200 ms.

### Escalation
- **Trigger for escalating beyond on-call:** p95 still > 500 ms **30 minutes** after containment started, **or** `Invocation5XXErrors` triggers `northstar-p1-endpoint-5xx-errors`, **or** the availability SLO's 7-day freeze trigger is hit (≥ 108 min of budget used).
- **Who is paged:** the P1 page already includes the **on-call ML engineer** and the **ML engineering manager**. Escalation adds the **platform/infrastructure on-call** (capacity, quotas, AWS support case) and notifies the **retention marketing lead** if Option C delays fresh scores.
- **SLA for response:** P1 is acknowledged within **15 minutes**, any hour, with containment started within **30 minutes**.

### Resolution Verification
- **How to confirm:** watch the dashboard's latency widget (`NorthStar-AI-Platform`, Application row) for 15 minutes after containment.
- **Metrics that must return to normal:** champion p95 `ModelLatency` **< 200000 µs** for **15 consecutive minutes** (back near the ~4,150 µs steady state), `northstar-p1-latency-p95-over-500ms` and `northstar-p2-latency-p95-over-200ms` both in **OK**, and `Invocation5XXErrors` at **0**.

### Post-Incident Actions
- **Within 24 hours:** record the timeline (alarm time, containment, recovery), the cause (bad deploy / load / instance), and how much latency and availability error budget the incident used. If the budget used crosses a Task 4 freeze trigger, announce the deployment freeze.
- **Postmortem required:** **yes** for every P1. Specifically, any incident where p95 stayed > 500 ms for more than 5 minutes, where Option C (batch fallback) was used, or where a deploy caused it.
