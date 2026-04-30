#!/bin/bash
set -e

if [ -n "${NVR_HOST:-}" ]; then
    echo "Testing NVR LAN connectivity to ${NVR_HOST}..."
    ping -c 1 -W 2 "$NVR_HOST" >/dev/null 2>&1 \
        && echo "NVR reachable at $NVR_HOST" \
        || echo "WARNING: NVR ping failed for $NVR_HOST (continuing)"
fi

echo "Starting camera capture service..."
exec python -u src/main.py
