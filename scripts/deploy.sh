#!/bin/bash
set -e

SERVER="alif"
REMOTE_DIR="/opt/alif"
EXPO_URL="exp://alifstian.duckdns.org:8081"

echo "Deploying to $SERVER..."
ssh $SERVER "cd $REMOTE_DIR && git pull && docker compose up -d --build && cd frontend && npm install && cd .. && systemctl restart alif-expo"

echo "Waiting for startup..."
sleep 5

STATUS=$(ssh $SERVER "curl -sf http://localhost:3000/api/stats" 2>&1)
if [ $? -eq 0 ]; then
    echo "Backend OK: $STATUS"
else
    echo "Backend may have failed. Checking logs..."
    ssh $SERVER "docker logs alif-backend-1 --tail 20"
fi

echo ""
echo "=== Expo URL (stable) ==="
echo "  $EXPO_URL"
echo "  http://alifstian.duckdns.org:8081"
