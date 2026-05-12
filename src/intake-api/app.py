import os
import uuid
import boto3
import logging
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import imghdr

# Configure structured logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("IntakeAPI")

app = Flask(__name__)
# Restrict max content length to 10MB to prevent memory exhaustion
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

def upload_to_s3_async(file_bytes, s3_key, content_type):
    """Background task for uploading to S3 without blocking Gunicorn workers"""
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type
        )
        logger.info(f"Successfully uploaded {s3_key}")
    except Exception as e:
        logger.error(f"Failed to upload {s3_key}: {str(e)}")

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

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
        
        # Strict validation using magic bytes (imghdr)
        image_type = imghdr.what(None, h=file_bytes)
        if image_type not in ['jpeg', 'png']:
            logger.warning(f"Upload rejected: Invalid file type detected -> {image_type}")
            return jsonify({"error": "Invalid file type. Only JPEG and PNG are supported."}), 415

        original_filename = secure_filename(file.filename)
        unique_id = str(uuid.uuid4())
        s3_key = f"{unique_id}_{original_filename}"
        
        # Offload the S3 upload to a background thread pool
        content_type = file.content_type if file.content_type else f"image/{image_type}"
        executor.submit(upload_to_s3_async, file_bytes, s3_key, content_type)
        
        logger.info(f"Upload accepted, async processing started for {s3_key}")
        return jsonify({
            "message": "Upload accepted, processing started.",
            "s3_key": s3_key
        }), 202

@app.errorhandler(413)
def request_entity_too_large(error):
    logger.warning("Upload rejected: Payload too large (>10MB)")
    return jsonify({"error": "File size exceeds the 10MB limit."}), 413

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
