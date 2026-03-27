#!/bin/bash

# Check VPN connectivity if interface exists
if ip link show wg0 > /dev/null 2>&1; then
    if ! ping -c 1 -W 2 100.66.241.254 > /dev/null 2>&1; then
        echo "VPN is down."
        exit 1
    fi
fi

# Basic check if Python process is running as PID 1
# Since we used exec in entrypoint.sh, python is PID 1
if ! kill -0 1 2>/dev/null; then
    echo "Main process is not running."
    exit 1
fi

# Optional: Check if health.json hasn't been updated recently 
# (e.g. file modified more than 15 minutes ago, assuming capture interval is < 15 min)
HEALTH_FILE="/data/state/health.json"
if [ -f "$HEALTH_FILE" ]; then
    LAST_MOD=$(stat -c %Y "$HEALTH_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    DIFF=$((NOW - LAST_MOD))
    
    # If the file hasn't been touched in 30 minutes, mark unhealthy
    if [ "$DIFF" -gt 1800 ]; then
        echo "Health file is stale (not updated in 30+ mins)."
        exit 1
    fi
fi

exit 0
