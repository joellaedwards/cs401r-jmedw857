# NorthStar Churn Model: Alert Specification (Lab 6 Task 3)

Implemented in `monitoring/alerts/main.tf`. Every alarm name contains `northstar`, so the evidence capture and the teardown can find it.

## Alerts

| # | Alert | Layer | Tier | Condition | Notes |
|---|---|---|---|---|---|
| 1 | `northstar-p1-latency-p95-over-500ms` | Application | **P1** | Champion p95 `ModelLatency` > **500000 µs** (500 ms) in 5 of 5 one-minute periods | Triggers Runbook B |
| 2 | `northstar-p1-endpoint-5xx-errors` | Application | **P1** | Champion `Invocation5XXErrors` ≥ 5 per minute in 3 of 5 minutes | Callers are getting errors; burns the availability budget |
| 3 | `northstar-p2-latency-p95-over-200ms` | Application | **P2** | Champion p95 `ModelLatency` > **200000 µs** (200 ms) in 5 of 5 one-minute periods | Five periods so the ~24,000 µs cold start after a deploy can't trip it |
| 4 | `northstar-p2-churn-alert-volume-drop` | Business | **P2** | `ChurnAlertVolumeChangePct` < −30 (daily volume more than 30% below its 7-day average) | See "Business-layer threshold" below |
| 5 | `northstar-p3-model-drift` | Model | **P3** | `DriftDetected` = 1: any `DRIFT_PLAN` feature has PSI > 0.2 or KS p < 0.05 | Composite alarm; suppressible (see below). Triggers Runbook A |
| 6 | `northstar-p3-endpoint-cpu-high` | Infrastructure | **P3** | Champion `CPUUtilization` average > 80 for 3 × 5 min | See "CPU threshold" below |
| 7 | `northstar-p3-glue-job-failed` | Pipeline | **P3** | EventBridge `Glue Job State Change` with state `FAILED`, `ERROR` or `TIMEOUT` on either NorthStar Glue job | Uses the state event, not task metrics: a driver failure reports `numFailedTasks` = 0 |
| 8 | `northstar-p4-endpoint-memory-high` | Infrastructure | **P4** | Champion `MemoryUtilization` average > 85% for 3 × 5 min | A trend to fix in working hours |

Helper alarms that are not alerts and have no actions: `northstar-p3-drift-detected-signal` (feeds #5) and `northstar-maintenance-window` (the suppressor).

## Escalation per tier

Each tier is an SNS topic (`northstar-alerts-p1` … `p4`). The paging, Slack and ticketing integrations subscribe to the topic.

| Tier | Topic | Escalation action | Response |
|---|---|---|---|
| P1 | `northstar-alerts-p1` | Wake the on-call ML engineer **and** page the ML engineering manager | Acknowledge in 15 min, any hour |
| P2 | `northstar-alerts-p2` | Page the on-call ML engineer | Acknowledge in 30 min, any hour |
| P3 | `northstar-alerts-p3` | Post to the `#northstar-ml-alerts` Slack channel **and** open a ticket | Same business day |
| P4 | `northstar-alerts-p4` | Open a ticket only | Next business day |

## Suppression rule

**Condition:** a scheduled retraining run or deployment is in progress. Whoever starts it publishes `MaintenanceWindow = 1` (namespace `NorthStar/ChurnModel`) every 5 minutes while it runs, which puts `northstar-maintenance-window` into ALARM.

**Suppressed:** `northstar-p3-model-drift`. It is a CloudWatch composite alarm whose `actions_suppressor` is the maintenance-window alarm. While the window is open, and for 1 hour after it closes, the drift alert changes state but sends no notification.

**Why:** retraining and redeploying change the model and its traffic on purpose. A drift alert in that window is expected, not an incident, and paging on it trains people to ignore the drift alert.

## Business-layer threshold: what counts as a churn alert

`ChurnAlertVolume` counts the distinct predictions in the last 24 hours with a churn score **≥ 0.5**, meaning the model thinks the customer is more likely than not to churn. Those are the customers the retention team would get an offer for.

A fixed score cutoff was chosen over "top 10% of scores" on purpose. The top 10% of any window is always exactly 10% of the traffic, so the count could never fall relative to traffic and the "> 30% drop vs. 7-day average" alert could never fire. A fixed cutoff lets the volume move when the model's view of risk moves. A sudden drop means either fewer at-risk customers or, more likely, a broken input or a shifted score distribution, which this alert is meant to catch.

`publish_metrics.py` publishes `ChurnAlertVolume` and the percent change against the average of the previous 7 daily values. With no history yet (the first day), it publishes no percent change, and the alarm sits in INSUFFICIENT_DATA rather than claiming the volume is normal.

## CPU threshold: which "80%"

`CPUUtilization` on SageMaker endpoints is summed across vCPUs, so the 2-vCPU instances used here report 0–200%. The threshold is **80**, which is **40% of the whole machine**. That's the conservative reading: it warns well before the instance saturates, and at the measured steady state of 0.1–0.9% it leaves a wide margin. The alarm needs 3 consecutive 5-minute periods because the first datapoint after `InService` is junk (1,101% on the reference run).

## Observer vs. execution identity

The platform has two monitoring roles, and they are kept separate for least privilege and separation of responsibilities. Each identity gets only the access its job needs.

- **`northstar-dev-ModelMonitor` (observer):** for people and automation that *watch* the platform, such as reading metrics, alarms and endpoint status. It is read-only: no S3 write and no ECR pull. Someone watching the system should not be able to change it.
- **`northstar-dev-ModelMonitorExecution` (execution):** the identity the Evidently processing job runs as. It needs a little more because it *does* work. It pulls the sklearn container from ECR, reads `datacapture/` and `monitoring/`, and writes its reports. It can write only under `monitoring/`, so the drift job can never overwrite the `raw/`, `features/` or `artifacts/` data it is judging. That also limits the damage if the job's `pip install evidently` ever pulled in a bad package. It has no CloudWatch write either, which is why metrics are published from the laptop by `publish_metrics.py`.

Handing the drift job the observer role fails because it cannot pull its container or write its reports. Handing an observer the execution role would give a watcher write access it does not need.
