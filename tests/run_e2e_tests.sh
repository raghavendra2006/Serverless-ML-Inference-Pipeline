#!/bin/bash
# =============================================================================
# PropTech ML Pipeline - End-to-End Test Runner
# =============================================================================
# Usage: ./tests/run_e2e_tests.sh
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo " PropTech ML Pipeline - E2E Test Runner"
echo "============================================"

# Load environment variables if .env exists
if [ -f "$PROJECT_DIR/.env" ]; then
    echo "Loading .env file..."
    export $(grep -v '^#' "$PROJECT_DIR/.env" | xargs)
fi

# Set defaults
export INTAKE_API_URL="${INTAKE_API_URL:-http://localhost:8080/upload}"
export LOCALSTACK_URL="${LOCALSTACK_URL:-http://localhost:4566}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

echo "Config:"
echo "  INTAKE_API_URL:  $INTAKE_API_URL"
echo "  LOCALSTACK_URL:  $LOCALSTACK_URL"
echo ""

# Step 1: Generate sample images if not present
SAMPLE_DIR="$SCRIPT_DIR/sample_images"
IMAGE_COUNT=$(find "$SAMPLE_DIR" -name "*.bmp" -o -name "*.jpg" -o -name "*.png" 2>/dev/null | wc -l)

if [ "$IMAGE_COUNT" -lt 20 ]; then
    echo "Generating sample images..."
    python3 "$SCRIPT_DIR/generate_samples.py"
    echo ""
fi

# Step 2: Wait for services to be ready
echo "Checking service health..."

# Check LocalStack
for i in $(seq 1 30); do
    if curl -sf "$LOCALSTACK_URL/_localstack/health" > /dev/null 2>&1; then
        echo "  ✓ LocalStack is healthy"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ✗ LocalStack not reachable at $LOCALSTACK_URL"
        echo "  Run: docker-compose up -d"
        exit 1
    fi
    sleep 2
done

# Check Intake API
API_BASE=$(echo "$INTAKE_API_URL" | sed 's|/upload$||')
for i in $(seq 1 15); do
    if curl -sf "$API_BASE/health" > /dev/null 2>&1; then
        echo "  ✓ Intake API is healthy"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "  ✗ Intake API not reachable at $API_BASE"
        echo "  Run: docker-compose up -d"
        exit 1
    fi
    sleep 2
done

echo ""

# Step 3: Run the Python test harness
echo "Running E2E tests..."
echo "--------------------------------------------"
python3 "$SCRIPT_DIR/run_e2e_tests.py"
TEST_EXIT_CODE=$?

# Step 4: Verify report was generated
REPORT_PATH="$PROJECT_DIR/results/report.json"
if [ -f "$REPORT_PATH" ]; then
    echo ""
    echo "============================================"
    echo " Report generated: $REPORT_PATH"
    echo "============================================"
    # Pretty-print summary
    python3 -c "
import json
with open('$REPORT_PATH') as f:
    r = json.load(f)
s = r['summary']
print(f\"  Total:    {s['total_images']} images\")
print(f\"  Accuracy: {s['accuracy']:.1%}\")
print(f\"  Latency:  {s['avg_latency_ms']:.0f}ms avg\")
" 2>/dev/null || true
else
    echo "ERROR: Report file not generated!"
    exit 1
fi

exit $TEST_EXIT_CODE
