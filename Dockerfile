FROM python:3.12-slim

WORKDIR /app

# System deps: gcc for Python packages, curl + Node.js for Claude Code
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

# data/ und tools/ werden als Volumes gemountet — Verzeichnisse sicherstellen
RUN mkdir -p /app/data /app/tools

CMD ["python3", "-m", "kern"]
