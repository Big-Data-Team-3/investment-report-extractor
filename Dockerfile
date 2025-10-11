# ============================================================
# Airflow 3.0.0 + Selenium + Playwright + ChromeDriver
# ============================================================

FROM apache/airflow:3.0.0-python3.11

# ------------------------------------------------------------
# 0️⃣ Root: system dependencies for browsers
# ------------------------------------------------------------
USER root

RUN set -eux; \
    apt-get update -y; \
    for i in 1 2 3; do \
        apt-get install -y --no-install-recommends \
            wget curl unzip gnupg ca-certificates fonts-liberation \
            libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
            libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2 \
            libpangocairo-1.0-0 libpango-1.0-0 libcairo2 libdrm2 libx11-xcb1 \
            libxss1 libgtk-3-0 libglib2.0-0 libxshmfence1 xvfb \
            && break || (echo "apt-get failed (attempt $i), retrying..."; sleep 10); \
    done; \
    rm -rf /var/lib/apt/lists/* /tmp/*

# ------------------------------------------------------------
# 1️⃣ Python dependencies
# ------------------------------------------------------------
USER airflow
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# ------------------------------------------------------------
# 2️⃣ Playwright + Chromium (airflow user)
# ------------------------------------------------------------
ENV PLAYWRIGHT_BROWSERS_PATH=/home/airflow/.cache/ms-playwright
RUN pip install --no-cache-dir playwright && \
    python3 -m playwright install --with-deps chromium

# ------------------------------------------------------------
# 3️⃣ Selenium + WebDriver Manager (airflow user)
# ------------------------------------------------------------
RUN pip install --no-cache-dir selenium webdriver-manager

# ------------------------------------------------------------
# 4️⃣ Environment
# ------------------------------------------------------------
ENV PATH="${PATH}:/usr/local/bin"

USER airflow

# ============================================================
# ✅ Image ready: Airflow 3.0.0 + Selenium + Playwright + ChromeDriver
# ============================================================
