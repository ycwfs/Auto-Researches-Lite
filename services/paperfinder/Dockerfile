# Sidecar for ICLR26_Paper_Finder semantic search. Heavy ML deps live here only.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# The sidecar embeds on CPU, so install CPU-only torch first. Otherwise
# sentence-transformers pulls the multi-GB CUDA build (nvidia-cu* wheels), which
# bloats the image by ~10GB and can exhaust the host disk on rebuild.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# Warm the embedding model into the image so the first request isn't slow and
# the service needs no model download at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY server.py download_data.sh seed_demo_db.py build_corpus.py collect_openreview.py collect_cvf.py add_venue.sh ./
RUN chmod +x download_data.sh add_venue.sh

ENV PAPERFINDER_DB_PATH=/data/ICLR2026 \
    PYTHONUNBUFFERED=1
EXPOSE 8100

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8100"]
