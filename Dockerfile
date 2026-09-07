# One image, four hosts: Render, Fly.io, Hugging Face Spaces, Cloud Run.
#
# It ships with the deterministic mock LLM, so the container is useful with no
# API key and no model download: a stranger can open the URL and the assistant,
# the evaluation console and the red-team run all work. Set LLM_PROVIDER and a
# key to put a real model behind it.
#
#   docker build -t retailmind .
#   docker run -p 8000:8000 retailmind

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: a source edit must not re-resolve the world.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face Spaces runs containers as uid 1000 with nothing writable at
# /app, so the database lives in a directory this user owns.
RUN useradd -m -u 1000 app \
 && mkdir -p /data \
 && chown -R app:app /data /app
USER app

ENV PYTHONPATH=/app/backend \
    PROFILE=docker \
    DATABASE_URL=sqlite+aiosqlite:////data/retailmind.db \
    LLM_PROVIDER=mock \
    EMBEDDING_PROVIDER=hashed \
    AUTO_BOOTSTRAP=true \
    PUBLIC_DEMO=true \
    RATE_LIMIT_PER_MIN=30 \
    RATE_LIMIT_BURST=10 \
    DAILY_REQUEST_LIMIT=0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8000') + '/health').read()"

# Shell form so ${PORT} expands. --proxy-headers is what makes the rate
# limiter see the real caller instead of the platform's load balancer.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
