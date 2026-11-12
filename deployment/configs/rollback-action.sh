#!/usr/bin/env bash
# Roll back the canary: send 100% of traffic to the champion, 0% to the canary.
# Takes ~90 s. The endpoint keeps serving while it reads Updating.
# This does NOT stop the canary instance billing; only deleting the endpoint does.
#
# Usage: bash deployment/configs/rollback-action.sh
set -euo pipefail
EP=northstar-churn-prod

aws sagemaker update-endpoint-weights-and-capacities \
  --endpoint-name "$EP" \
  --desired-weights-and-capacities \
    VariantName=champion,DesiredWeight=1 \
    VariantName=canary,DesiredWeight=0

echo "Waiting for $EP to return to InService..."
aws sagemaker wait endpoint-in-service --endpoint-name "$EP"

aws sagemaker describe-endpoint --endpoint-name "$EP" \
  --query 'ProductionVariants[].[VariantName,CurrentWeight]' --output text
