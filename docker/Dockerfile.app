FROM python:3.11-slim
LABEL authors="habithon1"

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies for document parsing and OCR
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        poppler-utils \
        tesseract-ocr \
        libjpeg-dev \
        zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy preprocessing package source
COPY src/preprocessing /app/preprocessing

# Upgrade pip and install the preprocessing package with all optional features
RUN pip install --upgrade pip setuptools wheel && \
    pip install /app/preprocessing[all]

# Copy helper script for downloading models
COPY scripts/download_models.py /app/download_models.py

# Optionally preload offline models into cache at build time
ARG PRELOAD_MODELS=false
RUN if [ "$PRELOAD_MODELS" = "true" ]; then \
        python3 /app/download_models.py --cache_dir /root/.cache/huggingface; \
    fi

# Set entrypoint to the preprocessing CLI
ENTRYPOINT ["preprocessing"]
CMD ["--help"]
