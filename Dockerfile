# LODESTAR — FastAPI + SSE server. Portable image for Fly.io / Railway / Render /
# Hugging Face Spaces (Docker).
FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

# app (the scenario-C label image + OCR cache ship in assets/)
COPY . .

# IMPORTANT: one worker only — the in-memory logistics DB is a module global,
# reset per run. Multiple workers would let concurrent runs clobber each other.
ENV PORT=8000 LIVE_ACTIONS=0
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1"]
