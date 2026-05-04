FROM python:3.12-slim

# WeasyPrint runtime dependencies (Pango, Cairo, GDK, fonts).
# pyhanko uses cryptography which needs OpenSSL (already in slim).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent storage path inside container.
# In Railway, attach a Volume mounted at /data so the SQLite DB,
# generated PDFs and the encrypted certificate survive redeploys.
ENV STORAGE_PATH=/data
RUN mkdir -p /data/pdfs /data/certs

EXPOSE 8000

# Shell form CMD so $PORT (provided by Railway at runtime) expands.
# Single worker keeps RAM usage minimal (cheaper on Railway).
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
