#!/bin/bash
set -e

SERVER="alif"
REMOTE_DIR="/opt/alif"
EXPO_URL="exp://alifstian.duckdns.org:8081"

echo "=== Pre-deploy checks ==="

# Check for known Expo Router incompatibilities (href + tabBarButton crashes)
if python3 -c "
import re
text = open('frontend/app/_layout.tsx').read()
# Find options blocks that contain both href and tabBarButton
blocks = re.findall(r'options=\{\{(.*?)\}\}', text, re.DOTALL)
bad = [b for b in blocks if 'href' in b and 'tabBarButton' in b]
exit(1 if bad else 0)
"; then
    echo "  Layout config OK"
else
    echo "FAIL: _layout.tsx has both href and tabBarButton on the same screen (Expo Router crashes)"
    exit 1
fi

# TypeScript check
echo "  Running tsc..."
(cd frontend && npx tsc --noEmit --skipLibCheck) || { echo "FAIL: TypeScript errors"; exit 1; }
echo "  TypeScript OK"

echo ""
echo "=== Deploying to $SERVER ==="
ssh $SERVER "cd $REMOTE_DIR && git pull && docker compose up -d --build && cd frontend && npm install && cd .. && systemctl restart alif-expo"

echo "Waiting for startup..."
sleep 10

# Check backend
STATUS=$(ssh $SERVER "curl -sf http://localhost:3000/api/stats" 2>&1)
if [ $? -eq 0 ]; then
    echo "Backend OK"
else
    echo "Backend may have failed. Checking logs..."
    ssh $SERVER "docker logs alif-backend-1 --tail 20"
fi

# Check Expo bundle compiles without errors
echo "Checking Expo bundle..."
EXPO_STATUS=$(ssh $SERVER "curl -sf http://localhost:8081 2>&1")
if echo "$EXPO_STATUS" | grep -qi "error"; then
    echo "WARN: Expo may have errors. Checking logs..."
    ssh $SERVER "journalctl -u alif-expo --no-pager -n 15 | grep -i error" || true
else
    echo "Expo OK"
fi

echo ""
echo "=== Expo URL (stable) ==="
echo "  $EXPO_URL"
echo "  http://alifstian.duckdns.org:8081"
