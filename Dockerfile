FROM python:3.12-slim-bookworm

# System packages required by WeasyPrint (PDF generation) and Playwright/Crawl4AI (browser automation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # WeasyPrint — Cairo-based PDF renderer
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    # General utilities
    curl \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies before copying source so this layer is cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium and all its OS-level dependencies via Playwright
RUN playwright install chromium --with-deps

# Initialise Crawl4AI (downloads browser binaries it needs)
RUN crawl4ai-setup || python -m crawl4ai.async_configs || true

# Pre-download the sentence-transformers embedding model so the first request is fast
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Copy application source
COPY . .

# Runtime data directories (mounted as volumes in production)
RUN mkdir -p resumes .chroma .files

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["chainlit", "run", "chainlit_app.py", "--host", "0.0.0.0", "--port", "8000"]
