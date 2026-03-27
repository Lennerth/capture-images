#!/bin/bash
set -e

echo "Starting WireGuard..."
if [ -f "/etc/wireguard/wg0.conf" ]; then
    # wg-quick might fail if permissions are wrong, so we catch errors
    wg-quick up wg0 || echo "Warning: wg-quick failed. Are NET_ADMIN caps provided?"
    
    echo "Testing VPN connectivity to 100.66.241.254..."
    ping -c 3 100.66.241.254 || echo "Warning: Could not ping 100.66.241.254, but continuing..."
else
    echo "No /etc/wireguard/wg0.conf found. Proceeding without VPN."
fi

echo "Starting camera capture service..."
exec python -u src/main.py
