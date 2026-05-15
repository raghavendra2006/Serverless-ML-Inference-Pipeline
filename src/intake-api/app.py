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
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("IntakeAPI")

app = Flask(__name__)

# Restrict max content length to 10MB
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# Setup ThreadPool for non-blocking S3 uploads
executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get('UPLOAD_WORKERS', 10))
)

# Determine AWS endpoint URL (LocalStack or real AWS)
endpoint_url = os.environ.get('AWS_ENDPOINT_URL')
if not endpoint_url and os.environ.get('LOCALSTACK_HOSTNAME'):
    endpoint_url = f"http://{os.environ.get('LOCALSTACK_HOSTNAME')}:4566"
elif os.environ.get('FLASK_ENV') == 'development':
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


def upload_to_s3(file_bytes, s3_key, content_type, md5_sum):
    """Upload file to S3 with integrity check (runs in background thread)."""
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
            ContentMD5=md5_sum
        )
        logger.info(f"Successfully uploaded {s3_key} to {BUCKET_NAME}")
    except Exception as e:
        logger.error(f"CRITICAL: Failed to upload {s3_key}: {str(e)}")


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for load balancers and monitoring."""
    return jsonify({
        "status": "healthy",
        "service": "PropTech Intake API",
        "version": "1.0.0"
    }), 200


@app.route('/upload', methods=['POST'])
def upload_file():
    """
    Accept image uploads via multipart/form-data.
    
    Request: multipart/form-data with field name 'image'
    Response: 202 Accepted with s3_key for tracking
    """
    if 'image' not in request.files:
        logger.warning("Upload rejected: No 'image' field in request")
        return jsonify({"error": "No image part in the request"}), 400

    file = request.files['image']

    if file.filename == '':
        logger.warning("Upload rejected: Empty filename")
        return jsonify({"error": "No selected file"}), 400

    file_bytes = file.read()

    if len(file_bytes) == 0:
        logger.warning("Upload rejected: Empty file body")
        return jsonify({"error": "Empty file"}), 400

    # Validate image type using magic bytes (no external dependency)
    detected_mime = detect_image_type(file_bytes)

    if detected_mime not in ('image/jpeg', 'image/png'):
        logger.warning(
            f"Upload rejected: Invalid file type "
            f"(detected: {detected_mime or 'unknown'})"
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

    # Offload S3 upload to background thread
    executor.submit(upload_to_s3, file_bytes, s3_key, detected_mime, md5_sum)

    logger.info(
        f"Accepted {s3_key} (Size: {len(file_bytes)} bytes, "
        f"Type: {detected_mime})"
    )

    return jsonify({
        "message": "Upload accepted, processing started.",
        "s3_key": s3_key
    }), 202


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
