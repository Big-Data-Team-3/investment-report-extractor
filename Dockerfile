FROM apache/airflow:2.10.2-python3.10

USER airflow

RUN pip install --no-cache-dir --upgrade pip

COPY --chown=airflow:root requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir playwright && \
    pip install --no-cache-dir -r /tmp/requirements.txt

USER root

SHELL ["/bin/bash", "-c"]

# Install with retry logic - try 3 times before failing
RUN for pkg in libnss3 libnspr4 libgbm1 libx11-6 libxcb1 libxext6 libglib2.0-0 libdbus-1-3 libatk1.0-0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxkbcommon0 libasound2; do \
        for i in 1 2 3; do \
            apt-get update && \
            apt-get install -y --no-install-recommends $pkg && \
            rm -rf /var/lib/apt/lists/* && \
            break || { \
                echo "Retry $i for $pkg"; \
                sleep 2; \
            }; \
        done; \
    done

USER airflow

RUN playwright install chromium

WORKDIR /opt/airflow