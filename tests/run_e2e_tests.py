#!/usr/bin/env python3
"""
PropTech ML Pipeline - End-to-End Test Harness
Uploads sample images, polls SQS for results, generates report.json
"""
import os, sys, time, json, glob, uuid, boto3, urllib.request, urllib.error, logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("E2ETests")

LOCALSTACK_URL = os.environ.get("LOCALSTACK_URL", "http://localhost:4566")
INTAKE_API_URL = os.environ.get("INTAKE_API_URL", "http://localhost:8080/upload")
POLL_TIMEOUT = 45

cfg = dict(endpoint_url=LOCALSTACK_URL, region_name='us-east-1',
           aws_access_key_id='test', aws_secret_access_key='test')
sqs = boto3.client('sqs', **cfg)
sns = boto3.client('sns', **cfg)
s3 = boto3.client('s3', **cfg)

def expected_status(f):
    b = os.path.basename(f).lower()
    return 'REJECTED' if b.startswith('rejected') else 'APPROVED'

def setup_queue():
    q = sqs.create_queue(QueueName=f"e2e-{uuid.uuid4().hex[:8]}")['QueueUrl']
    arn = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=['QueueArn'])['Attributes']['QueueArn']
    topics = sns.list_topics().get('Topics', [])
    t = next((t['TopicArn'] for t in topics if 'proptech-notifications' in t['TopicArn']), None)
    if not t:
        t = sns.create_topic(Name='proptech-notifications')['TopicArn']
    sns.subscribe(TopicArn=t, Protocol='sqs', Endpoint=arn)
    return q

def post_image(fp):
    bd = uuid.uuid4().hex
    with open(fp, 'rb') as f: data = f.read()
    fn = os.path.basename(fp)
    body = f'--{bd}\r\nContent-Disposition: form-data; name="image"; filename="{fn}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + data + f'\r\n--{bd}--\r\n'.encode()
    try:
        t0 = time.time()
        r = urllib.request.urlopen(urllib.request.Request(INTAKE_API_URL, data=body, headers={'Content-Type': f'multipart/form-data; boundary={bd}'}), timeout=30)
        return json.loads(r.read()).get('s3_key'), time.time()-t0
    except Exception as e:
        logger.error(f"Upload failed {fp}: {e}"); return None, 0

def poll(queue_url, key):
    t0 = time.time()
    while time.time()-t0 < POLL_TIMEOUT:
        try:
            for m in sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=5, MaxNumberOfMessages=10).get('Messages', []):
                try:
                    p = json.loads(json.loads(m['Body']).get('Message','{}'))
                    if p.get('image_key') == key:
                        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m['ReceiptHandle'])
                        return p, time.time()-t0
                except: pass
        except: pass
        time.sleep(0.5)
    return None, POLL_TIMEOUT

def run():
    logger.info("PropTech ML Pipeline - E2E Tests")
    try: q = setup_queue()
    except Exception as e: logger.error(f"Setup failed: {e}"); return 1
    
    sd = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sample_images')
    imgs = sorted(glob.glob(os.path.join(sd,'*.bmp')) + glob.glob(os.path.join(sd,'*.jpg')) + glob.glob(os.path.join(sd,'*.png')))
    if not imgs: logger.error("No images found"); return 1
    
    results, lat, correct = [], 0.0, 0
    for i, img in enumerate(imgs, 1):
        fn, exp = os.path.basename(img), expected_status(img)
        logger.info(f"[{i}/{len(imgs)}] {fn} (expect: {exp})")
        key, ut = post_image(img)
        if not key:
            results.append(dict(image_file=fn, pipeline_status="UPLOAD_FAILED", expected_status=exp, latency_ms=0, correctly_processed=False)); continue
        p, pd = poll(q, key)
        if p:
            st = p.get('status','UNKNOWN'); ms = (ut+pd)*1000; lat += ms; ok = st==exp
            if ok: correct += 1
            r = dict(image_file=fn, pipeline_status=st, expected_status=exp, latency_ms=round(ms,2), correctly_processed=ok)
            if 'reason' in p: r['rejection_reason'] = p['reason']
            if 'tags' in p: r['detected_tags'] = p['tags']
            results.append(r); logger.info(f"  {'✓' if ok else '✗'} {st} ({ms:.0f}ms)")
        else:
            results.append(dict(image_file=fn, pipeline_status="TIMEOUT", expected_status=exp, latency_ms=POLL_TIMEOUT*1000, correctly_processed=False))
    
    tot = len(imgs); acc = correct/tot if tot else 0; avg = lat/tot if tot else 0
    report = {"summary": {"total_images": tot, "accuracy": round(acc,4), "avg_latency_ms": round(avg,2)}, "results": results}
    rd = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'results')
    os.makedirs(rd, exist_ok=True)
    rp = os.path.join(rd, 'report.json')
    with open(rp, 'w') as f: json.dump(report, f, indent=2)
    logger.info(f"Report: {rp} | Accuracy: {acc:.1%} | Avg Latency: {avg:.0f}ms")
    return 0 if acc >= 0.8 else 1

if __name__ == '__main__': sys.exit(run())
