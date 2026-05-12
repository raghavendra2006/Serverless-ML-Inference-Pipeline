import os
import uuid
import boto3
import logging
import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import magic  # Replacing imghdr with python-magic for better detection

# Configure structured logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("IntakeAPI")

app = Flask(__name__)
# Restrict max content length to 10MB
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 

# Setup ThreadPool for non-blocking S3 uploads
executor = ThreadPoolExecutor(max_workers=int(os.environ.get('UPLOAD_WORKERS', 10)))

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

BUCKET_NAME = 'proptech-image-uploads'

def calculate_md5(file_bytes):
    hash_md5 = hashlib.md5(file_bytes)
    return base64.b64encode(hash_md5.digest()).decode('utf-8')

def upload_to_s3_async(file_bytes, s3_key, content_type, md5_sum):
    """Background task for uploading to S3 with integrity check"""
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
            ContentMD5=md5_sum
        )
        logger.info(f"Successfully uploaded {s3_key} with integrity check.")
    except Exception as e:
        logger.error(f"CRITICAL: Failed to upload {s3_key}: {str(e)}")

@app.route('/health', methods=['GET'])
def health_check():
    # Add deep health check if needed (e.g. check S3 connectivity)
    return jsonify({"status": "healthy", "service": "Intake API"}), 200

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'image' not in request.files:
        logger.warning("Upload rejected: No image part in request")
        return jsonify({"error": "No image part in the request"}), 400
    
    file = request.files['image']
    
    if file.filename == '':
        logger.warning("Upload rejected: No selected file")
        return jsonify({"error": "No selected file"}), 400
    
    if file:
        file_bytes = file.read()
        
        # Robust validation using python-magic
        mime = magic.Magic(mime=True)
        detected_mime = mime.from_buffer(file_bytes)
        
        if detected_mime not in ['image/jpeg', 'image/png']:
            logger.warning(f"Upload rejected: Forbidden MIME type -> {detected_mime}")
            return jsonify({"error": f"Forbidden MIME type: {detected_mime}. Only JPEG and PNG are supported."}), 415

        # Integrity check
        md5_sum = calculate_md5(file_bytes)

        original_filename = secure_filename(file.filename)
        unique_id = str(uuid.uuid4())
        s3_key = f"{unique_id}_{original_filename}"
        
        # Async offload
        executor.submit(upload_to_s3_async, file_bytes, s3_key, detected_mime, md5_sum)
        
        logger.info(f"Accepted {s3_key} (Size: {len(file_bytes)} bytes)")
        return jsonify({
            "message": "Upload accepted, background processing started.",
            "s3_key": s3_key,
            "integrity_md5": md5_sum
        }), 202

@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "Payload exceeds 10MB limit."}), 413

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
