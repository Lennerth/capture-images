FROM python:3.11-slim

# Install ping and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    jq \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
RUN chmod +x scripts/*.sh

COPY src/ src/
COPY config.yaml .

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
