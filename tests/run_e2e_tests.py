import os
import time
import json
import glob
import uuid
import threading
import urllib.request
import urllib.parse

# This script does not use boto3 or external dependencies to ensure it can run natively or with minimal setup.
# Actually, standard library works for polling SNS/SQS locally via LocalStack API.

LOCALSTACK_URL = "http://localhost:4566"
INTAKE_API_URL = os.environ.get("INTAKE_API_URL", "http://localhost:8080/upload")

def aws_request(service, action, params):
    data = urllib.parse.urlencode({'Action': action, 'Version': '2012-11-05', **params}).encode('utf-8')
    req = urllib.request.Request(LOCALSTACK_URL, data=data)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        response = urllib.request.urlopen(req)
        return response.read().decode('utf-8')
    except Exception as e:
        print(f"AWS Request Error ({service}:{action}):", e)
        return ""

def setup_sqs_subscription():
    # Since we can't easily parse XML with standard lib safely, we'll just use basic string matching
    # Create SQS queue
    queue_name = f"test-queue-{uuid.uuid4()}"
    res = aws_request('sqs', 'CreateQueue', {'QueueName': queue_name})
    queue_url = f"{LOCALSTACK_URL}/000000000000/{queue_name}"
    
    # Get SNS Topic ARN (assuming it's proptech-notifications)
    topic_arn = "arn:aws:sns:us-east-1:000000000000:proptech-notifications"
    
    # Subscribe SQS to SNS
    aws_request('sns', 'Subscribe', {
        'TopicArn': topic_arn,
        'Protocol': 'sqs',
        'Endpoint': queue_url
    })
    
    return queue_url

def receive_messages(queue_url):
    data = urllib.parse.urlencode({'Action': 'ReceiveMessage', 'QueueUrl': queue_url, 'Version': '2012-11-05', 'WaitTimeSeconds': '5'}).encode('utf-8')
    req = urllib.request.Request(LOCALSTACK_URL, data=data)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    messages = []
    try:
        response = urllib.request.urlopen(req).read().decode('utf-8')
        # Simple extraction of Body
        parts = response.split('<Body>')
        for i in range(1, len(parts)):
            body = parts[i].split('</Body>')[0]
            # HTML decode
            body = body.replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            messages.append(body)
    except Exception as e:
        pass
    return messages

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
    queue_url = setup_sqs_subscription()
    print(f"Listening on queue: {queue_url}")
    
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
            msgs = receive_messages(queue_url)
            for msg in msgs:
                try:
                    sns_msg = json.loads(msg)
                    payload = json.loads(sns_msg['Message'])
                    if payload.get('image_key') == s3_key:
                        latency_ms = (time.time() - start_poll + post_latency) * 1000
                        total_latency += latency_ms
                        status = payload.get('status', 'UNKNOWN')
                        
                        results.append({
                            "image_file": os.path.basename(img),
                            "pipeline_status": status,
                            "expected_status": "APPROVED", # Mock assumption
                            "latency_ms": round(latency_ms, 2),
                            "correctly_processed": status in ["APPROVED", "REJECTED"]
                        })
                        found = True
                        break
                except Exception as e:
                    print("Parse error on message:", e)
            if found:
                break
            time.sleep(2)
            
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
