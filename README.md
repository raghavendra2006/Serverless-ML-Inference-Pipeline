# 🏢 Serverless ML Inference Pipeline

> **Automated Real Estate Image Processing** — A production-grade, event-driven pipeline using AWS Step Functions, Rekognition, Lambda, and Ansible for the PropTech industry.

[![AWS](https://img.shields.io/badge/AWS-Step_Functions-orange?logo=amazonaws)](https://aws.amazon.com/step-functions/)
[![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://docker.com)
[![Ansible](https://img.shields.io/badge/Ansible-Automation-EE0000?logo=ansible)](https://ansible.com)

---

## 📋 Table of Contents

- [Architecture Overview](#architecture-overview)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Detailed Setup](#detailed-setup)
- [API Reference](#api-reference)
- [Testing](#testing)
- [Configuration](#configuration)
- [FAQ](#faq)

---

## Architecture Overview

```
┌──────────────┐     ┌─────────┐     ┌──────────────────────────────────────────┐
│  Intake API  │────▶│   S3    │────▶│         AWS Step Functions               │
│  (Flask +    │     │ Bucket  │     │                                          │
│   Gunicorn)  │     └─────────┘     │  ┌──────────────┐  ┌─────────────────┐  │
└──────────────┘         │           │  │  Rekognition  │  │  Rekognition    │  │
       │                 │           │  │  Moderation   │  │  Label Detect   │  │
    ┌──┴──┐              │           │  └──────┬───────┘  └────────┬────────┘  │
    │Nginx│              │           │         │                   │           │
    │Proxy│          Event Trigger   │  ┌──────▼───────┐  ┌───────▼────────┐  │
    └─────┘              │           │  │ Choice:      │  │ Lambda:        │  │
       │                 │           │  │ Moderate?    │  │ Blur Check     │  │
  Ansible Setup          │           │  └──────────────┘  └───────┬────────┘  │
                         │           │                            │           │
                         │           │  ┌─────────────────────────▼────────┐  │
                         │           │  │         SNS Notification         │  │
                         │           │  │   (APPROVED / REJECTED / ERROR)  │  │
                         │           │  └─────────────────────────────────┘  │
                         │           └──────────────────────────────────────────┘
                         │
                    ┌────▼─────┐
                    │LocalStack│  (Local Dev)
                    └──────────┘
```

### Pipeline Flow

1. **Upload** → Agent uploads image via `POST /upload` to the Intake API
2. **Store** → API stores image in S3 bucket `proptech-image-uploads`
3. **Trigger** → S3 event notification triggers the Step Functions state machine
4. **Moderate** → Rekognition `DetectModerationLabels` checks for inappropriate content
5. **Classify** → Rekognition `DetectLabels` identifies room type (Kitchen, Bedroom, etc.)
6. **Quality** → Custom Lambda function checks for blur using OpenCV Laplacian variance
7. **Notify** → SNS publishes APPROVED or REJECTED result to subscribers

---

## Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Orchestration** | AWS Step Functions (ASL) | Workflow coordination with error handling |
| **ML/AI** | AWS Rekognition | Content moderation & object detection |
| **Compute** | AWS Lambda + Python | Custom blur detection with OpenCV |
| **Storage** | AWS S3 | Image storage with lifecycle policies |
| **Messaging** | AWS SNS/SQS | Event notifications |
| **API Server** | Flask + Gunicorn + Nginx | Image upload endpoint |
| **IaC** | Ansible | Server provisioning & configuration |
| **Local Dev** | Docker + LocalStack | AWS service emulation |
| **Backup** | AWS Backup | Daily snapshots, 35-day retention |

---

## Project Structure

```
├── docker-compose.yml          # Local development environment
├── Dockerfile                  # Intake API container
├── Dockerfile.lambda           # Lambda deployment package builder
├── .env.example                # Environment variable template
├── .gitignore                  # Git exclusions
│
├── ansible/
│   ├── playbook.yml            # Server provisioning playbook
│   ├── inventory.ini           # Host inventory
│   └── templates/
│       ├── nginx.conf.j2       # Nginx reverse proxy config
│       └── intake-api.service.j2  # Systemd service unit
│
├── src/
│   ├── intake-api/
│   │   ├── app.py              # Flask upload API
│   │   └── requirements.txt    # Python dependencies
│   └── lambda-quality-check/
│       ├── lambda_function.py  # Blur detection Lambda
│       └── requirements.txt    # Lambda dependencies
│
├── state-machine/
│   └── definition.json         # Step Functions ASL definition
│
├── scripts/
│   └── localstack-init.sh      # AWS resource initialization
│
├── tests/
│   ├── run_e2e_tests.sh        # Test runner (shell)
│   ├── run_e2e_tests.py        # Test harness (Python)
│   ├── generate_samples.py     # Sample image generator
│   └── sample_images/          # Test images (24 files)
│
└── results/
    └── report.json             # E2E test results (generated)
```

---

## Prerequisites

- **Docker** & **Docker Compose** (v2.0+)
- **Python 3.9+** (for running tests locally)
- **Ansible** 2.14+ (for server provisioning)
- **AWS CLI** (optional, for manual verification)

---

## Quick Start

```bash
# 1. Clone and configure
git clone <repository-url>
cd Serverless-ML-Inference-Pipeline
cp .env.example .env

# 2. Start all services
docker-compose up -d

# 3. Wait for initialization (check health)
docker-compose ps   # All services should show "healthy"

# 4. Generate test images and run E2E tests
python3 tests/generate_samples.py
bash tests/run_e2e_tests.sh

# 5. Check results
cat results/report.json
```

---

## Detailed Setup

### 1. Local Development with Docker & LocalStack

```bash
# Start LocalStack and Intake API
docker-compose up -d

# Verify services are healthy
docker-compose ps

# Check LocalStack initialization completed
docker-compose logs localstack | tail -20

# Verify AWS resources were created
aws --endpoint-url=http://localhost:4566 s3 ls
aws --endpoint-url=http://localhost:4566 sns list-topics
aws --endpoint-url=http://localhost:4566 stepfunctions list-state-machines
aws --endpoint-url=http://localhost:4566 lambda list-functions
```

### 2. Verify S3 Configuration

```bash
# Check lifecycle policy
aws --endpoint-url=http://localhost:4566 \
    s3api get-bucket-lifecycle-configuration \
    --bucket proptech-image-uploads

# Check event notification
aws --endpoint-url=http://localhost:4566 \
    s3api get-bucket-notification-configuration \
    --bucket proptech-image-uploads
```

### 3. Deploy Intake API with Ansible

For deploying to a remote VM (Oracle Cloud Always Free, AWS EC2, etc.):

```bash
# Update inventory with your server IP
vim ansible/inventory.ini

# Run the playbook
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml

# Verify idempotency (second run should show changed=0)
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml
```

The playbook performs:
- Installs Nginx, Python 3, and pip
- Deploys the Flask application code
- Configures Nginx as a reverse proxy with security headers
- Sets up a systemd service with Gunicorn
- Configures UFW firewall (SSH + HTTP/HTTPS)

### 4. Test the Upload Endpoint

```bash
# Upload a test image
curl -X POST http://localhost:8080/upload \
    -F "image=@tests/sample_images/approved_kitchen_00.bmp"

# Expected response (202 Accepted):
# {"message": "Upload accepted, processing started.", "s3_key": "uuid_filename.bmp"}
```

---

## API Reference

### `POST /upload`

Upload an image for ML pipeline processing.

| Parameter | Type | Description |
|-----------|------|-------------|
| `image` | `file` (multipart/form-data) | JPEG or PNG image file (max 10MB) |

**Success Response** `202 Accepted`:
```json
{
    "message": "Upload accepted, processing started.",
    "s3_key": "uuid_filename.jpg"
}
```

**Error Responses**:
- `400` — Missing image field or empty filename
- `413` — File exceeds 10MB limit
- `415` — Unsupported file type

### `GET /health`

Health check endpoint.

**Response** `200 OK`:
```json
{
    "status": "healthy",
    "service": "PropTech Intake API",
    "version": "1.0.0"
}
```

---

## Testing

### Generate Sample Images

```bash
python3 tests/generate_samples.py
```

Creates 24 test images:
- 10 sharp room images → Expected: **APPROVED**
- 8 blurry images → Expected: **REJECTED** (low_quality)
- 6 inappropriate pattern images → Expected: **REJECTED** (inappropriate_content)

### Run E2E Tests

```bash
# Using the shell wrapper (recommended)
bash tests/run_e2e_tests.sh

# Or directly with Python
python3 tests/run_e2e_tests.py
```

### Test Report

Results are written to `results/report.json`:

```json
{
    "summary": {
        "total_images": 24,
        "accuracy": 0.95,
        "avg_latency_ms": 1250.5
    },
    "results": [
        {
            "image_file": "approved_kitchen_00.bmp",
            "pipeline_status": "APPROVED",
            "expected_status": "APPROVED",
            "latency_ms": 980.23,
            "correctly_processed": true
        }
    ]
}
```

---

## Configuration

All configuration is managed through environment variables. See `.env.example` for the complete list:

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_ACCESS_KEY_ID` | `test` | AWS credentials (use 'test' for LocalStack) |
| `AWS_SECRET_ACCESS_KEY` | `test` | AWS credentials |
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region |
| `INTAKE_API_URL` | `http://localhost:8080/upload` | Upload endpoint URL |
| `LOCALSTACK_URL` | `http://localhost:4566` | LocalStack endpoint |
| `BLUR_THRESHOLD` | `100.0` | Laplacian variance threshold for blur detection |
| `UPLOAD_WORKERS` | `10` | Thread pool size for async S3 uploads |
| `S3_BUCKET_NAME` | `proptech-image-uploads` | Target S3 bucket |

---

## FAQ

**Q: Why LocalStack instead of real AWS?**
LocalStack enables rapid local development without AWS costs or network latency. The init script provisions all resources automatically on startup.

**Q: How does blur detection work?**
The Lambda function computes the variance of the Laplacian of the image using OpenCV. Sharp images have high variance (many edges), blurry images have low variance. Threshold: 100.0.

**Q: Is the Ansible playbook idempotent?**
Yes. Running `ansible-playbook` twice produces `changed=0` on the second run. It uses `template`, `apt`, `systemd`, and `ufw` modules that are inherently idempotent, with `notify`/`handlers` to restart services only when configuration changes.

**Q: How do I add a new room type?**
Room classification is handled by Rekognition's `DetectLabels` API. No code changes needed — Rekognition automatically identifies Kitchen, Bedroom, Bathroom, Living Room, and 1000+ other labels.

---

## License

This project is built for educational and demonstration purposes as part of an advanced cloud computing portfolio project.
