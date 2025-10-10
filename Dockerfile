# Dockerfile
FROM apache/airflow:3.1.0-python3.11

USER root

# Install minimal system dependencies including Chromium for Selenium
# Split into stages to handle potential mirror issues
RUN apt-get update && \
    apt-get install -y wget curl && \
    apt-get clean && \
    apt-get update && \
    apt-get install -y chromium chromium-driver || true && \
    rm -rf /var/lib/apt/lists/*

USER airflow

# Copy requirements and install Python packages
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# Install Playwright Chromium browser only
# This works cross-platform (ARM64 and AMD64)
RUN playwright install chromium

# Install only the minimal system dependencies needed for Chromium
# Using --dry-run first to see what's needed, then install selectively
USER root
RUN apt-get update && \
    python3 -m playwright install-deps chromium || true && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Switch back to airflow user for runtime
USER airflow