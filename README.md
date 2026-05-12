# Serverless ML Inference Pipeline

This project implements an automated, serverless image processing pipeline using AWS Step Functions, Rekognition, Lambda, and S3. It is designed for a PropTech company to automate the moderation and classification of apartment listing photos. It uses LocalStack for local development and Ansible for API server automation.

## Architecture Overview

1. **Intake API**: A Flask-based API running via Gunicorn behind Nginx. It accepts image uploads and puts them into an S3 bucket (`proptech-image-uploads`).
2. **S3 Event Notification**: Triggers an AWS Step Function State Machine upon `s3:ObjectCreated:*` events.
3. **Step Functions Orchestration**:
   - Uses **Rekognition** (`DetectModerationLabels`) to check for explicit content. Rejects if confidence > 80.0.
   - Uses **Rekognition** (`DetectLabels`) to classify the room type.
   - Triggers a **Custom Lambda Function** (using OpenCV) to calculate image blurriness (variance of the Laplacian). Rejects if score < 100.0.
4. **Notification**: Publishes an approval or rejection message to an **SNS Topic**, providing labels or the reason for rejection.

## Prerequisites

- Docker and Docker Compose
- Ansible (for deploying the Intake API)
- Python 3.9+ (if running the test harness locally outside Docker)

## Setup & Execution

### 1. Start LocalStack Environment

Run the following command to spin up LocalStack and automatically provision all AWS resources:
```bash
docker-compose up -d
```
*LocalStack will initialize the S3 bucket, Lambda function, SNS topic, and Step Functions State Machine.*

### 2. Setup Intake API Server with Ansible

The Ansible playbook will install Nginx, Python, deploy the Flask API, and set up a systemd service. To test locally, you can provision the `localhost`:
```bash
# Ensure you have passwordless sudo or add --ask-become-pass
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml
```

*Note: If you encounter issues on a Windows host with Ansible, you can run the Flask app manually using `python3 src/intake-api/app.py`.*

### 3. Run End-to-End Tests

The test harness script will upload 20 sample images to the Intake API, poll the SQS subscription of the SNS topic, and generate a final report.

```bash
# Set INTAKE_API_URL if your API is not running on localhost:8080
export INTAKE_API_URL=http://localhost:8080/upload
python3 tests/run_e2e_tests.py
```

Check `results/report.json` for the final accuracy and latency metrics.

## Notes
- **LocalStack Pro**: Rekognition endpoints and AWS Step Function advanced integrations might require LocalStack Pro depending on the exact API calls used. In LocalStack Community, Step Functions integrates basic tasks and Lambda invocations successfully.
- **Error Handling**: The State Machine contains a global `Catch` block for AWS errors to ensure no process fails silently.
