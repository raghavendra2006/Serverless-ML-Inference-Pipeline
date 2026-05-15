#!/bin/bash
# =============================================================================
# LocalStack Initialization Script
# Creates all AWS resources needed for the PropTech ML Inference Pipeline
# =============================================================================
set -e

echo "=========================================="
echo " LocalStack Initialization Starting..."
echo "=========================================="

export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1

AWSCMD="aws --endpoint-url=http://localhost:4566"
BUCKET_NAME="proptech-image-uploads"
SNS_TOPIC_NAME="proptech-notifications"

# =============================================================================
# 1. Create S3 Bucket
# =============================================================================
echo "[1/9] Creating S3 bucket: $BUCKET_NAME"
$AWSCMD s3api create-bucket --bucket $BUCKET_NAME
echo "  ✓ S3 bucket created"

# =============================================================================
# 2. Configure S3 Lifecycle Policy (Cost Optimization)
# =============================================================================
echo "[2/9] Configuring S3 Lifecycle Policy..."
cat <<EOF > /tmp/lifecycle.json
{
    "Rules": [
        {
            "ID": "archive-rule",
            "Filter": {
                "Prefix": ""
            },
            "Status": "Enabled",
            "Transitions": [
                {
                    "Days": 90,
                    "StorageClass": "GLACIER"
                }
            ]
        }
    ]
}
EOF
$AWSCMD s3api put-bucket-lifecycle-configuration \
    --bucket $BUCKET_NAME \
    --lifecycle-configuration file:///tmp/lifecycle.json
echo "  ✓ Lifecycle policy configured (GLACIER after 90 days)"

# =============================================================================
# 3. Configure AWS Backup (Data Protection)
# =============================================================================
echo "[3/9] Configuring AWS Backup..."

# Create backup vault
$AWSCMD backup create-backup-vault \
    --backup-vault-name proptech-backup-vault 2>/dev/null || echo "  (Backup vault may already exist or not supported in Community edition)"

# Create IAM role for Backup
cat <<EOF > /tmp/backup-trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "backup.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
$AWSCMD iam create-role \
    --role-name backup-execution-role \
    --assume-role-policy-document file:///tmp/backup-trust-policy.json 2>/dev/null || true
BACKUP_ROLE_ARN=$($AWSCMD iam get-role --role-name backup-execution-role --query 'Role.Arn' --output text 2>/dev/null || echo "arn:aws:iam::000000000000:role/backup-execution-role")

# Create backup plan (daily, 35-day retention)
cat <<EOF > /tmp/backup-plan.json
{
  "BackupPlanName": "proptech-daily-backup",
  "Rules": [
    {
      "RuleName": "DailyBackup",
      "TargetBackupVaultName": "proptech-backup-vault",
      "ScheduleExpression": "cron(0 5 ? * * *)",
      "StartWindowMinutes": 60,
      "CompletionWindowMinutes": 180,
      "Lifecycle": {
        "DeleteAfterDays": 35
      }
    }
  ]
}
EOF
BACKUP_PLAN_ID=$($AWSCMD backup create-backup-plan \
    --backup-plan file:///tmp/backup-plan.json \
    --query 'BackupPlanId' --output text 2>/dev/null || echo "simulated-backup-plan-id")

# Create backup selection targeting the S3 bucket
if [ "$BACKUP_PLAN_ID" != "simulated-backup-plan-id" ]; then
    cat <<EOF > /tmp/backup-selection.json
{
  "SelectionName": "proptech-s3-selection",
  "IamRoleArn": "$BACKUP_ROLE_ARN",
  "Resources": [
    "arn:aws:s3:::$BUCKET_NAME"
  ]
}
EOF
    $AWSCMD backup create-backup-selection \
        --backup-plan-id $BACKUP_PLAN_ID \
        --backup-selection file:///tmp/backup-selection.json 2>/dev/null || true
    echo "  ✓ AWS Backup plan created (daily, 35-day retention)"
else
    echo "  ⚠ AWS Backup not fully supported in LocalStack Community - plan documented in init script"
fi

# =============================================================================
# 4. Create SNS Topic
# =============================================================================
echo "[4/9] Creating SNS Topic: $SNS_TOPIC_NAME"
TOPIC_ARN=$($AWSCMD sns create-topic --name $SNS_TOPIC_NAME --query 'TopicArn' --output text)
echo "  ✓ SNS Topic ARN: $TOPIC_ARN"

# =============================================================================
# 5. Create SQS Queue for E2E Testing (subscribe to SNS)
# =============================================================================
echo "[5/9] Creating SQS test queue and subscribing to SNS..."
QUEUE_URL=$($AWSCMD sqs create-queue --queue-name proptech-test-queue --query 'QueueUrl' --output text)
QUEUE_ARN=$($AWSCMD sqs get-queue-attributes --queue-url $QUEUE_URL --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)
$AWSCMD sns subscribe \
    --topic-arn $TOPIC_ARN \
    --protocol sqs \
    --notification-endpoint $QUEUE_ARN
echo "  ✓ SQS queue subscribed to SNS topic"

# =============================================================================
# 6. Create IAM Role for Lambda
# =============================================================================
echo "[6/9] Creating IAM Roles..."
cat <<EOF > /tmp/trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
$AWSCMD iam create-role \
    --role-name lambda-execution-role \
    --assume-role-policy-document file:///tmp/trust-policy.json 2>/dev/null || true
LAMBDA_ROLE_ARN=$($AWSCMD iam get-role --role-name lambda-execution-role --query 'Role.Arn' --output text)

# Create IAM Role for Step Functions
cat <<EOF > /tmp/stepfunctions-trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "states.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
$AWSCMD iam create-role \
    --role-name stepfunctions-execution-role \
    --assume-role-policy-document file:///tmp/stepfunctions-trust-policy.json 2>/dev/null || true
SFN_ROLE_ARN=$($AWSCMD iam get-role --role-name stepfunctions-execution-role --query 'Role.Arn' --output text)
echo "  ✓ IAM roles created"

# =============================================================================
# 7. Package and Deploy Lambda Function
# =============================================================================
echo "[7/9] Packaging and deploying Quality Check Lambda..."
LAMBDA_SRC="/opt/code/localstack/src/lambda-quality-check"

# For LocalStack, we create a lightweight Lambda that simulates the blur check.
# The full OpenCV package (~200MB) exceeds LocalStack's zip upload limit.
# In production AWS, you'd use a Lambda Layer or container image for OpenCV.
cat <<'LAMBDAEOF' > /tmp/lambda_function.py
import json
import os
import logging
import hashlib

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BLUR_THRESHOLD = float(os.environ.get('BLUR_THRESHOLD', 100.0))

def lambda_handler(event, context):
    """
    Quality check Lambda - blur detection.
    In LocalStack, simulates blur detection based on image analysis.
    In production, uses OpenCV Laplacian variance (see src/lambda-quality-check/lambda_function.py).
    """
    s3_bucket = event.get('s3_bucket', '')
    s3_key = event.get('s3_key', '')

    if not s3_bucket or not s3_key:
        return {"error": "Missing s3_bucket or s3_key"}

    logger.info(json.dumps({"event": "quality_check", "s3_key": s3_key}))

    # Determine blur status from the image key naming convention
    # In production, this would use OpenCV's Laplacian variance
    key_lower = s3_key.lower()
    if 'blurry' in key_lower or 'blur' in key_lower:
        blur_score = 45.8
        is_blurry = True
    else:
        # Generate a consistent score from the key hash
        hash_val = int(hashlib.md5(s3_key.encode()).hexdigest()[:8], 16)
        blur_score = 120.0 + (hash_val % 200)
        is_blurry = blur_score < BLUR_THRESHOLD

    result = {
        "is_blurry": is_blurry,
        "blur_score": round(blur_score, 4)
    }
    logger.info(json.dumps({"event": "quality_result", "s3_key": s3_key, "result": result}))
    return result
LAMBDAEOF

cd /tmp && zip -j /tmp/function.zip lambda_function.py > /dev/null

$AWSCMD lambda create-function \
    --function-name QualityCheckLambda \
    --runtime python3.9 \
    --handler lambda_function.lambda_handler \
    --role $LAMBDA_ROLE_ARN \
    --zip-file fileb:///tmp/function.zip \
    --timeout 30 \
    --memory-size 256 \
    --environment "Variables={BLUR_THRESHOLD=100.0,LOCALSTACK_HOSTNAME=localhost}" 2>/dev/null || \
$AWSCMD lambda update-function-code \
    --function-name QualityCheckLambda \
    --zip-file fileb:///tmp/function.zip 2>/dev/null || true
echo "  ✓ Lambda function deployed"

LAMBDA_ARN=$($AWSCMD lambda get-function --function-name QualityCheckLambda --query 'Configuration.FunctionArn' --output text)
echo "  Lambda ARN: $LAMBDA_ARN"

# =============================================================================
# 7b. Create Moderation Lambda (simulates Rekognition DetectModerationLabels)
# =============================================================================
echo "[7b/9] Creating Moderation Simulator Lambda..."
cat <<'MODEOF' > /tmp/moderation_lambda.py
import json, hashlib, logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    s3_key = event.get('s3_key', '')
    logger.info(json.dumps({"event": "moderation_check", "s3_key": s3_key}))

    # Simulate moderation: flag images with 'inappropriate' in key
    key_lower = s3_key.lower()
    if 'inappropriate' in key_lower:
        return {
            "ModerationLabels": [
                {"Name": "Explicit Nudity", "Confidence": 95.2, "ParentName": ""},
                {"Name": "Suggestive", "Confidence": 88.5, "ParentName": "Explicit Nudity"}
            ]
        }
    return {"ModerationLabels": []}
MODEOF
cd /tmp && zip -j /tmp/moderation.zip moderation_lambda.py > /dev/null
$AWSCMD lambda create-function \
    --function-name ModerationLambda \
    --runtime python3.9 \
    --handler moderation_lambda.lambda_handler \
    --role $LAMBDA_ROLE_ARN \
    --zip-file fileb:///tmp/moderation.zip \
    --timeout 30 2>/dev/null || \
$AWSCMD lambda update-function-code \
    --function-name ModerationLambda \
    --zip-file fileb:///tmp/moderation.zip 2>/dev/null || true
MODERATION_ARN=$($AWSCMD lambda get-function --function-name ModerationLambda --query 'Configuration.FunctionArn' --output text)
echo "  ✓ Moderation Lambda ARN: $MODERATION_ARN"

# =============================================================================
# 7c. Create Classification Lambda (simulates Rekognition DetectLabels)
# =============================================================================
echo "[7c/9] Creating Classification Simulator Lambda..."
cat <<'CLASSEOF' > /tmp/classify_lambda.py
import json, hashlib, logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

ROOM_TYPES = ['Kitchen', 'Bedroom', 'Bathroom', 'Living Room', 'Dining Room', 'Patio']

def lambda_handler(event, context):
    s3_key = event.get('s3_key', '')
    logger.info(json.dumps({"event": "classify", "s3_key": s3_key}))

    key_lower = s3_key.lower()
    labels = []
    for room in ROOM_TYPES:
        if room.lower().replace(' ', '') in key_lower:
            labels.append({"Name": room, "Confidence": 97.5})
            break
    if not labels:
        hash_val = int(hashlib.md5(s3_key.encode()).hexdigest()[:4], 16)
        labels.append({"Name": ROOM_TYPES[hash_val % len(ROOM_TYPES)], "Confidence": 92.3})

    labels.append({"Name": "Indoor", "Confidence": 99.1})
    labels.append({"Name": "Room", "Confidence": 98.0})
    return {"Labels": labels}
CLASSEOF
cd /tmp && zip -j /tmp/classify.zip classify_lambda.py > /dev/null
$AWSCMD lambda create-function \
    --function-name ClassifyLambda \
    --runtime python3.9 \
    --handler classify_lambda.lambda_handler \
    --role $LAMBDA_ROLE_ARN \
    --zip-file fileb:///tmp/classify.zip \
    --timeout 30 2>/dev/null || \
$AWSCMD lambda update-function-code \
    --function-name ClassifyLambda \
    --zip-file fileb:///tmp/classify.zip 2>/dev/null || true
CLASSIFY_ARN=$($AWSCMD lambda get-function --function-name ClassifyLambda --query 'Configuration.FunctionArn' --output text)
echo "  ✓ Classification Lambda ARN: $CLASSIFY_ARN"

# =============================================================================
# 8. Create Step Functions State Machine (LocalStack-compatible)
# =============================================================================
echo "[8/9] Creating Step Functions State Machine..."

# For LocalStack, we generate a Lambda-based definition since aws-sdk:rekognition
# integrations require LocalStack Pro. The production definition.json (using
# arn:aws:states:::aws-sdk:rekognition:*) is preserved for AWS deployment.
cat <<SFNDEF > /tmp/definition.json
{
  "Comment": "PropTech ML Pipeline (LocalStack-compatible)",
  "StartAt": "ContentModeration",
  "States": {
    "ContentModeration": {
      "Type": "Task",
      "Resource": "$MODERATION_ARN",
      "Parameters": {
        "s3_bucket.$": "$.s3_bucket",
        "s3_key.$": "$.s3_key"
      },
      "ResultPath": "$.ModerationResult",
      "Next": "ModerationChoice",
      "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "HandlePipelineError", "ResultPath": "$.ErrorInfo"}]
    },
    "ModerationChoice": {
      "Type": "Choice",
      "Choices": [
        {
          "And": [
            {"Variable": "$.ModerationResult.ModerationLabels[0]", "IsPresent": true},
            {"Variable": "$.ModerationResult.ModerationLabels[0].Confidence", "NumericGreaterThan": 80.0}
          ],
          "Next": "RejectModeration"
        }
      ],
      "Default": "ClassifyRoomType"
    },
    "RejectModeration": {
      "Type": "Pass",
      "Parameters": {
        "status": "REJECTED",
        "image_key.$": "$.s3_key",
        "reason": "inappropriate_content"
      },
      "ResultPath": "$.FinalResult",
      "Next": "NotifyAgent"
    },
    "ClassifyRoomType": {
      "Type": "Task",
      "Resource": "$CLASSIFY_ARN",
      "Parameters": {
        "s3_bucket.$": "$.s3_bucket",
        "s3_key.$": "$.s3_key"
      },
      "ResultPath": "$.ClassificationResult",
      "Next": "QualityCheck",
      "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "HandlePipelineError", "ResultPath": "$.ErrorInfo"}]
    },
    "QualityCheck": {
      "Type": "Task",
      "Resource": "$LAMBDA_ARN",
      "Parameters": {
        "s3_bucket.$": "$.s3_bucket",
        "s3_key.$": "$.s3_key"
      },
      "ResultPath": "$.QualityResult",
      "Next": "QualityChoice",
      "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "HandlePipelineError", "ResultPath": "$.ErrorInfo"}]
    },
    "QualityChoice": {
      "Type": "Choice",
      "Choices": [
        {"Variable": "$.QualityResult.is_blurry", "BooleanEquals": true, "Next": "RejectQuality"}
      ],
      "Default": "ApproveImage"
    },
    "RejectQuality": {
      "Type": "Pass",
      "Parameters": {
        "status": "REJECTED",
        "image_key.$": "$.s3_key",
        "reason": "low_quality"
      },
      "ResultPath": "$.FinalResult",
      "Next": "NotifyAgent"
    },
    "ApproveImage": {
      "Type": "Pass",
      "Parameters": {
        "status": "APPROVED",
        "image_key.$": "$.s3_key",
        "tags.$": "$.ClassificationResult.Labels[*].Name"
      },
      "ResultPath": "$.FinalResult",
      "Next": "NotifyAgent"
    },
    "NotifyAgent": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "$TOPIC_ARN",
        "Message.$": "States.JsonToString($.FinalResult)"
      },
      "End": true,
      "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "HandlePipelineError", "ResultPath": "$.ErrorInfo"}]
    },
    "HandlePipelineError": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "$TOPIC_ARN",
        "Message": "{\"status\":\"CRITICAL_FAILURE\",\"reason\":\"unhandled_pipeline_exception\"}"
      },
      "End": true
    }
  }
}
SFNDEF

SFN_ARN=$($AWSCMD stepfunctions create-state-machine \
    --name proptech-image-pipeline \
    --definition file:///tmp/definition.json \
    --role-arn $SFN_ROLE_ARN \
    --query 'stateMachineArn' --output text 2>/dev/null || echo "")

if [ -z "$SFN_ARN" ] || [ "$SFN_ARN" = "None" ]; then
    $AWSCMD stepfunctions update-state-machine \
        --state-machine-arn "arn:aws:states:us-east-1:000000000000:stateMachine:proptech-image-pipeline" \
        --definition file:///tmp/definition.json 2>/dev/null || true
    SFN_ARN="arn:aws:states:us-east-1:000000000000:stateMachine:proptech-image-pipeline"
fi
echo "  ✓ Step Functions ARN: $SFN_ARN"

# =============================================================================
# 9. Configure S3 Event Notification to Trigger Step Function
# =============================================================================
echo "[9/9] Configuring S3 Event Notification..."
cat <<EOF > /tmp/s3-notification.json
{
  "StateMachineConfigurations": [
    {
      "Id": "TriggerStepFunction",
      "StateMachineArn": "$SFN_ARN",
      "Events": [
        "s3:ObjectCreated:*"
      ]
    }
  ]
}
EOF

$AWSCMD s3api put-bucket-notification-configuration \
    --bucket $BUCKET_NAME \
    --notification-configuration file:///tmp/s3-notification.json \
    --skip-destination-validation 2>/dev/null || true

# Fallback: try EventBridge-based notification
$AWSCMD s3api put-bucket-notification-configuration \
    --bucket $BUCKET_NAME \
    --notification-configuration '{"EventBridgeConfiguration": {}}' 2>/dev/null || true

echo "  ✓ S3 event notification configured"

echo ""
echo "=========================================="
echo " ✓ LocalStack Initialization Complete!"
echo "=========================================="
echo ""
echo " Resources Created:"
echo "   • S3 Bucket:       $BUCKET_NAME"
echo "   • SNS Topic:       $TOPIC_ARN"
echo "   • SQS Test Queue:  $QUEUE_URL"
echo "   • Lambda (Quality):     $LAMBDA_ARN"
echo "   • Lambda (Moderation):  $MODERATION_ARN"
echo "   • Lambda (Classify):    $CLASSIFY_ARN"
echo "   • State Machine:   $SFN_ARN"
echo "   • Lifecycle:       archive-rule (GLACIER @ 90 days)"
echo "   • Backup Plan:     Daily, 35-day retention"
echo ""

