import os
import uuid
import boto3
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configure boto3 client
endpoint_url = os.environ.get('AWS_ENDPOINT_URL')
# For LocalStack testing
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

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'image' not in request.files:
        return jsonify({"error": "No image part in the request"}), 400
    
    file = request.files['image']
    
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file:
        original_filename = secure_filename(file.filename)
        # Generate a unique S3 key
        unique_id = str(uuid.uuid4())
        s3_key = f"{unique_id}_{original_filename}"
        
        try:
            # Upload to S3
            s3_client.upload_fileobj(file, BUCKET_NAME, s3_key)
            
            return jsonify({
                "message": "Upload accepted, processing started.",
                "s3_key": s3_key
            }), 202
            
        except Exception as e:
            return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
