#!/usr/bin/env bash
set -e

GIT_ROOT=$(git root)
BACKEND_DIR="$GIT_ROOT/backend"
HEALTH_URL="http://localhost:8000/health"

echo '' > "$BACKEND_DIR/logs/app.log"
echo "Checking backend health at $HEALTH_URL ..."

if curl -s -o /dev/null -w "" --max-time 3 "$HEALTH_URL" 2>/dev/null; then
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$HEALTH_URL")
    if [ "$http_code" = "200" ]; then
        echo "Backend is already running (HTTP $http_code)."
        exit 0
    fi
fi

echo "Backend is not running. Starting it ..."
echo '' > "$BACKEND_DIR/logs/app.log"
cd "$BACKEND_DIR"
make run &

# Wait for the backend to become healthy
echo "Waiting for backend to start ..."
for i in $(seq 1 30); do
    sleep 1
    if curl -s -o /dev/null -w "" --max-time 2 "$HEALTH_URL" 2>/dev/null; then
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$HEALTH_URL")
        if [ "$http_code" = "200" ]; then
            echo "Backend is up (HTTP $http_code) after ~${i}s."
            exit 0
        fi
    fi
done

echo "ERROR: Backend did not become healthy within 30s."
exit 1
