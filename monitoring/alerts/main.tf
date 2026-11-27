# ── Lab 6 Task 3: NorthStar churn model alert architecture ───────────────────
# Spec and rationale: monitoring/alerts/alert-spec.md
#
# Separate root module with LOCAL state (*.tfstate is gitignored, never commit it).
#   terraform init && terraform apply     # after the endpoint is InService
#   terraform destroy                     # BEFORE scripts/teardown-lab6.sh
#
# Destroy first: teardown-lab6.sh deletes northstar METRIC alarms only. It does
# not remove the composite alarm, the EventBridge rule or the SNS topics, and
# CloudWatch refuses to delete a metric alarm a composite still references.
#
# UNITS: ModelLatency is MICROSECONDS (200 ms = 200000, 500 ms = 500000).
#        CPUUtilization is summed across vCPUs: 0-200% on a 2-vCPU instance.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "endpoint_name" {
  type    = string
  default = "northstar-churn-prod"
}

variable "variant_name" {
  description = "Variant the application and infrastructure alarms watch"
  type        = string
  default     = "champion"
}

variable "alert_email" {
  description = "Optional: subscribe this address to every tier (AWS emails a confirmation link). Empty = no subscription."
  type        = string
  default     = ""
}

locals {
  namespace = "NorthStar/ChurnModel"
  ep_dims   = { EndpointName = var.endpoint_name }
  var_dims  = { EndpointName = var.endpoint_name, VariantName = var.variant_name }

  # Escalation per tier. Each tier is one SNS topic; the paging integrations
  # (PagerDuty / Slack / ticketing) subscribe to the topic in production.
  tiers = {
    p1 = "P1: wake on-call ML engineer AND page the ML engineering manager"
    p2 = "P2: page on-call ML engineer"
    # SNS tag values allow only letters, digits, spaces and _ . : / = + - @
    p3 = "P3: post to northstar-ml-alerts Slack channel AND open a ticket"
    p4 = "P4: open a ticket only - next business day"
  }
}

# ── Escalation: one SNS topic per tier ───────────────────────────────────────
resource "aws_sns_topic" "tier" {
  for_each     = local.tiers
  name         = "northstar-alerts-${each.key}"
  display_name = "NorthStar ${upper(each.key)}"
  tags         = { Escalation = each.value }
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = var.alert_email == "" ? {} : local.tiers
  topic_arn = aws_sns_topic.tier[each.key].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# EventBridge must be allowed to publish the Glue failure event to P3.
data "aws_iam_policy_document" "p3_events" {
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.tier["p3"].arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "p3_events" {
  arn    = aws_sns_topic.tier["p3"].arn
  policy = data.aws_iam_policy_document.p3_events.json
}

# ═════════════════════════════════ P1 ════════════════════════════════════════

# 1. Application: p95 latency above 500 ms for 5 minutes (Runbook B).
resource "aws_cloudwatch_metric_alarm" "p1_latency_500ms" {
  alarm_name          = "northstar-p1-latency-p95-over-500ms"
  alarm_description   = "P1. Champion p95 ModelLatency > 500 ms (500000 us) for 5 consecutive minutes. Runbook B."
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = local.var_dims
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 500000 # microseconds = 500 ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p1"].arn]
  ok_actions          = [aws_sns_topic.tier["p1"].arn]
}

# 2. Availability: the endpoint is returning server errors to callers.
resource "aws_cloudwatch_metric_alarm" "p1_5xx" {
  alarm_name          = "northstar-p1-endpoint-5xx-errors"
  alarm_description   = "P1. Champion Invocation5XXErrors >= 5 per minute in 3 of 5 minutes. Burns the 99.5% availability budget."
  namespace           = "AWS/SageMaker"
  metric_name         = "Invocation5XXErrors"
  dimensions          = local.var_dims
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p1"].arn]
  ok_actions          = [aws_sns_topic.tier["p1"].arn]
}

# ═════════════════════════════════ P2 ════════════════════════════════════════

# 3. Application: p95 latency above 200 ms (the Task 1 threshold). Five 1-minute
#    periods so the ~24,000 us cold start after a deploy cannot trip it.
resource "aws_cloudwatch_metric_alarm" "p2_latency_200ms" {
  alarm_name          = "northstar-p2-latency-p95-over-200ms"
  alarm_description   = "P2. Champion p95 ModelLatency > 200 ms (200000 us) in 5 of 5 minutes."
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  dimensions          = local.var_dims
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 200000 # microseconds = 200 ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p2"].arn]
}

# 4. Business: daily churn-alert volume fell > 30% below its 7-day average.
#    publish_metrics.py computes the % change and publishes it once there is
#    history; with no history there is no datapoint (INSUFFICIENT_DATA).
resource "aws_cloudwatch_metric_alarm" "p2_churn_volume_drop" {
  alarm_name          = "northstar-p2-churn-alert-volume-drop"
  alarm_description   = "P2. ChurnAlertVolume more than 30% below its 7-day average. Retention team would silently get fewer customers to contact."
  namespace           = local.namespace
  metric_name         = "ChurnAlertVolumeChangePct"
  dimensions          = local.ep_dims
  statistic           = "Minimum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = -30
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p2"].arn]
}

# ═════════════════════════════════ P3 ════════════════════════════════════════

# 5. Model: data drift. The metric alarm carries NO action; the composite below
#    does, so the maintenance-window suppressor can silence it (suppression rule).
resource "aws_cloudwatch_metric_alarm" "p3_drift_signal" {
  alarm_name          = "northstar-p3-drift-detected-signal"
  alarm_description   = "P3 signal (no action). DriftDetected = 1: a DRIFT_PLAN feature crossed its threshold (PSI > 0.2 or KS p < 0.05). Notifies via northstar-p3-model-drift."
  namespace           = local.namespace
  metric_name         = "DriftDetected"
  dimensions          = local.ep_dims
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_composite_alarm" "p3_model_drift" {
  alarm_name        = "northstar-p3-model-drift"
  alarm_description = "P3. Data drift detected (Runbook A). SUPPRESSED while northstar-maintenance-window is in ALARM (scheduled retraining or deployment)."
  alarm_rule        = "ALARM(\"${aws_cloudwatch_metric_alarm.p3_drift_signal.alarm_name}\")"
  alarm_actions     = [aws_sns_topic.tier["p3"].arn]

  actions_suppressor {
    alarm            = aws_cloudwatch_metric_alarm.maintenance_window.alarm_name
    wait_period      = 300
    extension_period = 3600 # stay quiet 1 h after the window closes
  }
}

# 6. Infrastructure: CPU. Threshold 80 on a 0-200% per-core sum = 40% of the
#    machine (the conservative reading). 3 x 5-min periods so the junk first
#    datapoint after InService (1,101% on the reference run) cannot trip it.
resource "aws_cloudwatch_metric_alarm" "p3_cpu" {
  alarm_name          = "northstar-p3-endpoint-cpu-high"
  alarm_description   = "P3. Champion CPUUtilization avg > 80 (per-core sum, 0-200 on 2 vCPUs = 40% of machine) for 3 x 5 min."
  namespace           = "/aws/sagemaker/Endpoints"
  metric_name         = "CPUUtilization"
  dimensions          = local.var_dims
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p3"].arn]
}

# 7. Pipeline: any Glue job failure. State-change EVENT, not task metrics:
#    a driver that dies on AccessDenied reports numFailedTasks = 0 (trap 14).
resource "aws_cloudwatch_event_rule" "p3_glue_failed" {
  name        = "northstar-p3-glue-job-failed"
  description = "P3. Any NorthStar Glue job ends FAILED, ERROR or TIMEOUT."
  event_pattern = jsonencode({
    source        = ["aws.glue"]
    "detail-type" = ["Glue Job State Change"]
    detail = {
      jobName = ["northstar-dev-transform", "northstar-dev-feature-engineer"]
      state   = ["FAILED", "ERROR", "TIMEOUT"]
    }
  })
}

resource "aws_cloudwatch_event_target" "p3_glue_failed" {
  rule      = aws_cloudwatch_event_rule.p3_glue_failed.name
  target_id = "northstar-p3-sns"
  arn       = aws_sns_topic.tier["p3"].arn
}

# ═════════════════════════════════ P4 ════════════════════════════════════════

# 8. Infrastructure: memory pressure. Trend to fix in working hours.
resource "aws_cloudwatch_metric_alarm" "p4_memory" {
  alarm_name          = "northstar-p4-endpoint-memory-high"
  alarm_description   = "P4. Champion MemoryUtilization avg > 85% for 3 x 5 min."
  namespace           = "/aws/sagemaker/Endpoints"
  metric_name         = "MemoryUtilization"
  dimensions          = local.var_dims
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.tier["p4"].arn]
}

# ══════════════════════ Suppression: maintenance window ══════════════════════
# Whoever starts a retraining run or deployment publishes MaintenanceWindow=1
# every 5 min while it runs:
#   aws cloudwatch put-metric-data --namespace NorthStar/ChurnModel \
#     --metric-name MaintenanceWindow --dimensions EndpointName=northstar-churn-prod --value 1
# This alarm goes to ALARM, and suppresses northstar-p3-model-drift. No actions:
# it is a switch, not an alert.
resource "aws_cloudwatch_metric_alarm" "maintenance_window" {
  alarm_name          = "northstar-maintenance-window"
  alarm_description   = "Suppressor (no action). In ALARM while a scheduled retraining or deployment publishes MaintenanceWindow=1."
  namespace           = local.namespace
  metric_name         = "MaintenanceWindow"
  dimensions          = local.ep_dims
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}

output "sns_topics" {
  value = { for k, t in aws_sns_topic.tier : k => t.arn }
}
