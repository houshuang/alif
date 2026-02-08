#!/bin/bash
set -e

SERVER="alif"
REMOTE_DIR="/opt/alif"

echo "Deploying to $SERVER..."
ssh $SERVER "cd $REMOTE_DIR && git pull && docker compose up -d --build"
echo "Waiting for startup..."
sleep 5
STATUS=$(ssh $SERVER "curl -sf http://localhost:3000/api/stats" 2>&1)
if [ $? -eq 0 ]; then
    echo "Deploy OK: $STATUS"
else
    echo "Deploy may have failed. Checking logs..."
    ssh $SERVER "docker logs alif-backend-1 --tail 20"
fi
