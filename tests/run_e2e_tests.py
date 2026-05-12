import os
import time
import json
import glob
import uuid
import boto3
import urllib.request

LOCALSTACK_URL = "http://localhost:4566"
INTAKE_API_URL = os.environ.get("INTAKE_API_URL", "http://localhost:8080/upload")

# Configure boto3 clients
sqs_client = boto3.client(
    'sqs',
    endpoint_url=LOCALSTACK_URL,
    region_name='us-east-1',
    aws_access_key_id='test',
    aws_secret_access_key='test'
)
sns_client = boto3.client(
    'sns',
    endpoint_url=LOCALSTACK_URL,
    region_name='us-east-1',
    aws_access_key_id='test',
    aws_secret_access_key='test'
)

def setup_sqs_subscription():
    # Create SQS queue
    queue_name = f"test-queue-{uuid.uuid4()}"
    queue_res = sqs_client.create_queue(QueueName=queue_name)
    queue_url = queue_res['QueueUrl']
    
    # Get Queue ARN
    queue_attr = sqs_client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['QueueArn'])
    queue_arn = queue_attr['Attributes']['QueueArn']
    
    # Fetch SNS Topic ARN (proptech-notifications)
    topics = sns_client.list_topics()
    topic_arn = None
    for t in topics.get('Topics', []):
        if 'proptech-notifications' in t['TopicArn']:
            topic_arn = t['TopicArn']
            break
            
    if not topic_arn:
        # Fallback creation if not found
        topic_res = sns_client.create_topic(Name='proptech-notifications')
        topic_arn = topic_res['TopicArn']
        
    # Subscribe SQS to SNS
    sns_client.subscribe(
        TopicArn=topic_arn,
        Protocol='sqs',
        Endpoint=queue_arn
    )
    
    return queue_url

def post_image(filepath):
    import mimetypes
    boundary = uuid.uuid4().hex
    headers = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
    
    with open(filepath, 'rb') as f:
        file_content = f.read()
        
    filename = os.path.basename(filepath)
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f'Content-Type: image/jpeg\r\n\r\n'
    ).encode('utf-8') + file_content + f'\r\n--{boundary}--\r\n'.encode('utf-8')
    
    req = urllib.request.Request(INTAKE_API_URL, data=body, headers=headers)
    try:
        start_time = time.time()
        response = urllib.request.urlopen(req)
        resp_data = json.loads(response.read().decode('utf-8'))
        return resp_data.get('s3_key'), time.time() - start_time
    except Exception as e:
        print(f"Failed to post image {filepath}: {e}")
        return None, 0

def run_tests():
    try:
        queue_url = setup_sqs_subscription()
        print(f"Listening on queue: {queue_url}")
    except Exception as e:
        print("Could not connect to LocalStack to setup test queue:", e)
        return
        
    sample_dir = os.path.join(os.path.dirname(__file__), 'sample_images')
    images = glob.glob(os.path.join(sample_dir, '*.jpg'))
    
    results = []
    total_latency = 0
    
    for img in images:
        print(f"Testing {img}...")
        s3_key, post_latency = post_image(img)
        
        if not s3_key:
            results.append({
                "image_file": os.path.basename(img),
                "pipeline_status": "FAILED",
                "expected_status": "APPROVED",
                "latency_ms": 0,
                "correctly_processed": False
            })
            continue
            
        # Poll for message
        found = False
        start_poll = time.time()
        while time.time() - start_poll < 30: # 30s timeout
            try:
                msgs = sqs_client.receive_message(
                    QueueUrl=queue_url,
                    WaitTimeSeconds=5,
                    MaxNumberOfMessages=10
                )
                
                for msg in msgs.get('Messages', []):
                    sns_msg = json.loads(msg['Body'])
                    payload = json.loads(sns_msg['Message'])
                    
                    if payload.get('image_key') == s3_key:
                        latency_ms = (time.time() - start_poll + post_latency) * 1000
                        total_latency += latency_ms
                        status = payload.get('status', 'UNKNOWN')
                        
                        results.append({
                            "image_file": os.path.basename(img),
                            "pipeline_status": status,
                            "expected_status": "APPROVED", 
                            "latency_ms": round(latency_ms, 2),
                            "correctly_processed": status in ["APPROVED", "REJECTED"],
                            "has_metadata": all(k in payload for k in ["processed_at", "image_key", "status"])
                        })
                        if not all(k in payload for k in ["processed_at", "image_key", "status"]):
                            print(f"WARNING: Missing metadata in response for {s3_key}")
                        found = True
                        sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=msg['ReceiptHandle'])
                        break
            except Exception as e:
                print("Parse/poll error:", e)
                
            if found:
                break
            time.sleep(1)
            
        if not found:
            results.append({
                "image_file": os.path.basename(img),
                "pipeline_status": "TIMEOUT",
                "expected_status": "APPROVED",
                "latency_ms": 30000,
                "correctly_processed": False
            })

    accuracy = sum(1 for r in results if r["correctly_processed"]) / len(images) if images else 0
    avg_lat = total_latency / len(images) if images else 0
    
    report = {
        "summary": {
            "total_images": len(images),
            "accuracy": accuracy,
            "avg_latency_ms": round(avg_lat, 2)
        },
        "results": results
    }
    
    report_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'results', 'report.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
        
    print(f"Generated report at {report_path}")

if __name__ == '__main__':
    run_tests()
