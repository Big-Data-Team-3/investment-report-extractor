# Start from Airflow 2.10.2 with Python 3.10
FROM apache/airflow:2.10.2-python3.10

# Switch to airflow user directly (skip apt-get entirely if possible)
USER airflow

# Copy requirements file
COPY --chown=airflow:root requirements.txt /tmp/requirements.txt

# Upgrade pip and install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Install Playwright and chromium
RUN pip install --no-cache-dir playwright && \
    playwright install chromium

# Set working directory
WORKDIR /opt/airflow