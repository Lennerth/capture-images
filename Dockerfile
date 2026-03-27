FROM python:3.11-slim

# Install WireGuard and ping
RUN apt-get update && apt-get install -y --no-install-recommends \
    wireguard-tools \
    iproute2 \
    iputils-ping \
    jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
RUN chmod +x scripts/*.sh

COPY src/ src/
COPY config.yaml .

# Provide a default placeholder if not mounted
RUN mkdir -p /etc/wireguard

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
