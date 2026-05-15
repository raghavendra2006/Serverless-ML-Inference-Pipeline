import os
import boto3
import cv2
import numpy as np
import json
import logging
from tenacity import retry, stop_after_attempt, wait_exponential

# Configure robust structured JSON logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment variables
BLUR_THRESHOLD = float(os.environ.get('BLUR_THRESHOLD', 100.0))

# Configure boto3 client outside for reuse
endpoint_url = None
if 'LOCALSTACK_HOSTNAME' in os.environ:
    endpoint_url = f"http://{os.environ['LOCALSTACK_HOSTNAME']}:4566"

s3_client = boto3.client('s3', endpoint_url=endpoint_url)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_s3_object_with_retry(bucket, key):
    return s3_client.get_object(Bucket=bucket, Key=key)

def lambda_handler(event, context):
    try:
        s3_bucket = event.get('s3_bucket')
        s3_key = event.get('s3_key')
        correlation_id = event.get('correlation_id', 'unknown')
        
        if not s3_bucket or not s3_key:
            error_msg = "Missing s3_bucket or s3_key in event payload."
            logger.error(json.dumps({"event": "validation_failed", "correlation_id": correlation_id, "error": error_msg}))
            return {"error": error_msg}

        logger.info(json.dumps({
            "event": "quality_check_started",
            "correlation_id": correlation_id,
            "s3_bucket": s3_bucket,
            "s3_key": s3_key,
            "threshold": BLUR_THRESHOLD
        }))

        # Fetch with retry logic
        response = get_s3_object_with_retry(s3_bucket, s3_key)
        
        # Memory-efficient reading
        streaming_body = response['Body']
        file_bytes = bytearray()
        for chunk in streaming_body.iter_chunks(chunk_size=1024*1024): 
            file_bytes.extend(chunk)

        nparr = np.frombuffer(file_bytes, np.uint8)
        
        # Decode image
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if image is None:
            error_msg = "FAILED_DECODE: Bytes provided are not a valid image."
            logger.error(json.dumps({"event": "decode_failed", "correlation_id": correlation_id, "s3_key": s3_key, "error": error_msg}))
            return {"error": error_msg}

        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Calculate variance of Laplacian
        blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
        
        is_blurry = bool(blur_score < BLUR_THRESHOLD)

        result_payload = {
            "is_blurry": is_blurry,
            "blur_score": round(float(blur_score), 4),
            "threshold_used": BLUR_THRESHOLD
        }
        
        logger.info(json.dumps({
            "event": "quality_check_completed",
            "correlation_id": correlation_id,
            "s3_key": s3_key,
            "result": result_payload
        }))

        return result_payload
        
    except Exception as e:
        correlation_id = event.get('correlation_id', 'unknown') if isinstance(event, dict) else 'unknown'
        logger.error(json.dumps({
            "event": "quality_check_exception",
            "correlation_id": correlation_id,
            "s3_bucket": event.get('s3_bucket') if isinstance(event, dict) else 'unknown',
            "s3_key": event.get('s3_key') if isinstance(event, dict) else 'unknown',
            "error": str(e)
        }))
        raise e
    finally:
        # Explicitly release memory (critical for C++ bound variables across warm starts)
        for var in ['image', 'gray', 'nparr', 'file_bytes', 'streaming_body', 'response']:
            if var in locals():
                del locals()[var]
        
        import gc
        gc.collect()
