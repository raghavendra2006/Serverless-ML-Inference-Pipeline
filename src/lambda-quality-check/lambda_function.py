import os
import boto3
import cv2
import numpy as np
import json
import logging

# Configure robust structured JSON logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    try:
        s3_bucket = event.get('s3_bucket')
        s3_key = event.get('s3_key')
        
        if not s3_bucket or not s3_key:
            error_msg = "Missing s3_bucket or s3_key in event payload."
            logger.error(json.dumps({"event": "validation_failed", "error": error_msg}))
            return {"error": error_msg}

        logger.info(json.dumps({
            "event": "quality_check_started",
            "s3_bucket": s3_bucket,
            "s3_key": s3_key
        }))

        # Configure boto3 client efficiently
        endpoint_url = None
        if 'LOCALSTACK_HOSTNAME' in os.environ:
            endpoint_url = f"http://{os.environ['LOCALSTACK_HOSTNAME']}:4566"
        
        s3 = boto3.client('s3', endpoint_url=endpoint_url)

        # Stream the object instead of loading entirely into memory at once
        response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        
        # Read iteratively or use streaming buffer to numpy array
        # For OpenCV decoding from buffer, we still need the bytes, but using bytearray is more memory efficient
        streaming_body = response['Body']
        file_bytes = bytearray()
        for chunk in streaming_body.iter_chunks(chunk_size=1024*1024): # 1MB chunks
            file_bytes.extend(chunk)

        nparr = np.frombuffer(file_bytes, np.uint8)
        
        # Decode image
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            error_msg = "Could not decode image bytes into valid OpenCV format."
            logger.error(json.dumps({"event": "decode_failed", "s3_key": s3_key, "error": error_msg}))
            return {"error": error_msg}

        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Calculate variance of Laplacian
        blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
        
        is_blurry = bool(blur_score < 100.0)

        result_payload = {
            "is_blurry": is_blurry,
            "blur_score": float(blur_score)
        }
        
        logger.info(json.dumps({
            "event": "quality_check_completed",
            "s3_key": s3_key,
            "result": result_payload
        }))

        return result_payload
        
    except Exception as e:
        logger.error(json.dumps({
            "event": "quality_check_exception",
            "s3_bucket": event.get('s3_bucket'),
            "s3_key": event.get('s3_key'),
            "error": str(e)
        }))
        raise e
