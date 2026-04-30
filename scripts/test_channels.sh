#!/bin/bash
# Usage: ./test_channels.sh 1 2 3 4
# Run this from the host or inside the container to test RTSP channel mapping.

if [ -z "$1" ]; then
  echo "Usage: $0 <channel1> [channel2...]"
  echo "Example: $0 1 2 3 4"
  exit 1
fi

# Try to load from .env if variables aren't set
if [ -f "../.env" ]; then
  export $(grep -v '^#' ../.env | xargs)
elif [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
fi

NVR_HOST=${NVR_HOST:-192.168.1.229}
NVR_USERNAME=${NVR_USERNAME:-admin}

if [ -z "$NVR_PASSWORD" ]; then
  echo "Error: NVR_PASSWORD is not set. Please set it in .env or export it."
  exit 1
fi

for ch in "$@"; do
  echo "Testing channel $ch..."
  ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -y \
    -i "rtsp://${NVR_USERNAME}:${NVR_PASSWORD}@${NVR_HOST}:554/cam/realmonitor?channel=${ch}&subtype=0" \
    -frames:v 1 -q:v 2 "channel_${ch}.jpg"
  
  if [ -f "channel_${ch}.jpg" ]; then
    echo "✅ Success: Saved channel_${ch}.jpg"
  else
    echo "❌ Failed to capture channel $ch"
  fi
done
