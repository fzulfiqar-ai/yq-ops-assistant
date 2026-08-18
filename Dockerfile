# YQ Bahrain ops assistant — production API image.
# NO Claude CLI in production (the deployed app calls LLM APIs over HTTPS).
#
# Tuned for a 512 MB / 0.1 CPU free-tier container: no build toolchain, no
# Streamlit stack, and a single uvicorn worker.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: ffmpeg renders the marketing reels; DejaVu + Noto (incl. Arabic)
# fonts back the Pillow ad cards and captions.
#
# build-essential is deliberately NOT installed — every pinned dependency ships a
# manylinux wheel, so the compiler was ~300 MB of image for zero benefit. If a
# future dependency is sdist-only its build will fail loudly here; add it back then.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# scripts/ powers the data-ingest + verified-refresh path (/ingest, MRN/PO uploads).
# Without it, `from scripts.ingest import ...` raises ModuleNotFoundError → 500 on upload.
COPY scripts ./scripts

EXPOSE 8000

# Hosts inject $PORT (Railway, Render, Cloud Run); default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
