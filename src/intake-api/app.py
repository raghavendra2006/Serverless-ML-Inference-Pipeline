"""
PropTech Intake API - Image Upload Service
Accepts image uploads via multipart/form-data and stores them in S3
for downstream ML pipeline processing.
"""

import os
import uuid
import boto3
import logging
import hashlib
import base64
import threading
import sys
import json
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from pythonjsonlogger import jsonlogger

# Configure structured JSON logging for enterprise observability
logger = logging.getLogger("IntakeAPI")
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(correlation_id)s %(message)s')
logHandler.setFormatter(formatter)
logger.addHandler(logHandler)
logger.setLevel(logging.INFO)
# Remove default handlers to avoid duplicate text logs
if len(logger.handlers) > 1:
    logger.handlers = [logHandler]

# Fail-fast validation for immutable production deployments
if not os.environ.get('AWS_ACCESS_KEY_ID'):
    logger.warning("AWS_ACCESS_KEY_ID is missing in environment. Using fallback 'test' for local development.", extra={"correlation_id": "startup"})
    os.environ['AWS_ACCESS_KEY_ID'] = 'test'
    os.environ['AWS_SECRET_ACCESS_KEY'] = 'test'

app = Flask(__name__)

# Configure strict rate limiting to prevent DDoS
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1000 per day", "200 per hour"]
)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    # Strict security headers
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

# Restrict max content length to 10MB
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

WORKER_COUNT = int(os.environ.get('UPLOAD_WORKERS', 10))

# Setup ThreadPool for non-blocking S3 uploads
executor = ThreadPoolExecutor(max_workers=WORKER_COUNT)

# Strict Backpressure: BoundedSemaphore prevents unbounded queue growth and memory exhaustion
upload_semaphore = threading.BoundedSemaphore(WORKER_COUNT)

def upload_task_wrapper(file_bytes, s3_key, detected_mime, md5_sum, correlation_id):
    """Wrapper to guarantee semaphore release even if S3 fails."""
    try:
        upload_to_s3(file_bytes, s3_key, detected_mime, md5_sum, correlation_id)
    finally:
        upload_semaphore.release()

# Determine AWS endpoint URL (LocalStack or real AWS)
endpoint_url = os.environ.get('AWS_ENDPOINT_URL')
if not endpoint_url and os.environ.get('LOCALSTACK_HOSTNAME'):
    endpoint_url = f"http://{os.environ.get('LOCALSTACK_HOSTNAME')}:4566"
elif not endpoint_url and os.environ.get('FLASK_ENV') == 'development':
    endpoint_url = "http://localhost:4566"

s3_client = boto3.client(
    's3',
    endpoint_url=endpoint_url,
    region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'),
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID', 'test'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY', 'test')
)

BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'proptech-image-uploads')

# Allowed MIME types based on file signatures (magic bytes)
JPEG_MAGIC = b'\xff\xd8\xff'
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def detect_image_type(file_bytes):
    """Detect image MIME type from magic bytes (no external dependency)."""
    if file_bytes[:3] == JPEG_MAGIC:
        return 'image/jpeg'
    elif file_bytes[:8] == PNG_MAGIC:
        return 'image/png'
    return None


def calculate_md5(file_bytes):
    """Calculate base64-encoded MD5 hash for S3 integrity verification."""
    hash_md5 = hashlib.md5(file_bytes)
    return base64.b64encode(hash_md5.digest()).decode('utf-8')


def upload_to_s3(file_bytes, s3_key, content_type, md5_sum, correlation_id):
    """Upload file to S3 and trigger Step Functions pipeline."""
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
            ContentMD5=md5_sum,
            Metadata={'correlation-id': correlation_id}
        )
        logger.info(f"Successfully uploaded {s3_key} to {BUCKET_NAME}", extra={"correlation_id": correlation_id})

        # Trigger Step Functions state machine directly
        # (S3 event notifications to Step Functions aren't reliable in LocalStack)
        try:
            sfn_client = boto3.client(
                'stepfunctions',
                endpoint_url=endpoint_url,
                region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'),
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID', 'test'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY', 'test')
            )
            
            # Use LocalStack ARN format for testing
            account_id = "000000000000" 
            region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
            state_machine_arn = f"arn:aws:states:{region}:{account_id}:stateMachine:proptech-image-pipeline"
            
            sfn_client.start_execution(
                stateMachineArn=state_machine_arn,
                input=json.dumps({
                    "s3_bucket": BUCKET_NAME,
                    "s3_key": s3_key,
                    "correlation_id": correlation_id
                })
            )
            logger.info(f"Triggered Step Functions for {s3_key}", extra={"correlation_id": correlation_id})
        except Exception as sfn_err:
            logger.warning(f"Step Functions trigger failed: {sfn_err}", extra={"correlation_id": correlation_id})

    except Exception as e:
        logger.error(f"CRITICAL: Failed to upload {s3_key}: {str(e)}", extra={"correlation_id": correlation_id})


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for load balancers and monitoring."""
    return jsonify({
        "status": "healthy",
        "service": "PropTech Intake API",
        "version": "1.0.0"
    }), 200


@app.route('/upload', methods=['POST', 'OPTIONS'])
@limiter.limit("50 per minute; 5 per second")
def upload_image():
    """
    Upload an image to S3 for processing.
    
    Expects multipart/form-data with a file field named 'image'.
    """
    # Handle CORS preflight explicitly
    if request.method == 'OPTIONS':
        return '', 200

    # Generate correlation ID for distributed tracing
    correlation_id = request.headers.get('X-Correlation-ID', str(uuid.uuid4()))

    if 'image' not in request.files:
        logger.warning("Upload rejected: No 'image' field in request", extra={"correlation_id": correlation_id})
        return jsonify({"error": "No image part in the request"}), 400

    file = request.files['image']

    if file.filename == '':
        logger.warning("Upload rejected: Empty filename", extra={"correlation_id": correlation_id})
        return jsonify({"error": "No selected file"}), 400

    file_bytes = file.read()

    if len(file_bytes) == 0:
        logger.warning("Upload rejected: Empty file body", extra={"correlation_id": correlation_id})
        return jsonify({"error": "Empty file"}), 400

    # Validate image type using magic bytes (no external dependency)
    detected_mime = detect_image_type(file_bytes)

    if detected_mime not in ('image/jpeg', 'image/png'):
        logger.warning(
            f"Upload rejected: Invalid file type "
            f"(detected: {detected_mime or 'unknown'})",
            extra={"correlation_id": correlation_id}
        )
        return jsonify({
            "error": f"Unsupported file type. Only JPEG and PNG are accepted."
        }), 415

    # Calculate integrity hash
    md5_sum = calculate_md5(file_bytes)

    # Generate unique S3 key
    original_filename = secure_filename(file.filename)
    unique_id = str(uuid.uuid4())
    s3_key = f"{unique_id}_{original_filename}"

    # Apply strict backpressure - immediately reject if thread pool is saturated
    if not upload_semaphore.acquire(blocking=False):
        logger.error(
            f"Upload rejected: Thread pool exhausted (Concurrency limit: {WORKER_COUNT}). Backpressure applied.",
            extra={"correlation_id": correlation_id}
        )
        return jsonify({"error": "Service overloaded. Please try again later."}), 503

    # Offload S3 upload to background thread
    executor.submit(upload_task_wrapper, file_bytes, s3_key, detected_mime, md5_sum, correlation_id)

    logger.info(
        f"Accepted {s3_key} (Size: {len(file_bytes)} bytes, Type: {detected_mime})",
        extra={"correlation_id": correlation_id}
    )

    response = jsonify({
        "message": "Upload accepted, processing started.",
        "s3_key": s3_key,
        "correlation_id": correlation_id
    })
    response.headers['X-Correlation-ID'] = correlation_id
    return response, 202


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle requests that exceed the 10MB content limit."""
    return jsonify({"error": "Payload exceeds 10MB limit."}), 413


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 8080)),
        debug=os.environ.get('FLASK_ENV') == 'development'
    )
