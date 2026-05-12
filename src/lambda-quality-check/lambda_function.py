import os
import boto3
import cv2
import numpy as np
import json

def lambda_handler(event, context):
    try:
        s3_bucket = event.get('s3_bucket')
        s3_key = event.get('s3_key')
        
        if not s3_bucket or not s3_key:
            return {"error": "Missing s3_bucket or s3_key"}

        # Configure boto3 client. In LocalStack, we might need custom endpoint.
        endpoint_url = None
        if 'LOCALSTACK_HOSTNAME' in os.environ:
            endpoint_url = f"http://{os.environ['LOCALSTACK_HOSTNAME']}:4566"
        
        # S3 client
        s3 = boto3.client('s3', endpoint_url=endpoint_url)

        # Download image into memory
        response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        image_bytes = response['Body'].read()
        
        # Convert to numpy array and read with OpenCV
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            return {"error": "Could not decode image"}

        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Calculate variance of Laplacian
        blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
        
        is_blurry = bool(blur_score < 100.0)

        return {
            "is_blurry": is_blurry,
            "blur_score": float(blur_score)
        }
        
    except Exception as e:
        print(f"Error processing image {s3_bucket}/{s3_key}: {str(e)}")
        raise e
