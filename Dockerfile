FROM python:3.12-slim

# WeasyPrint runtime dependencies (Pango, Cairo, GDK, fonts)
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

# Default storage path inside container; mount Railway volume here
ENV STORAGE_PATH=/data
RUN mkdir -p /data/pdfs /data/certs

ENV PORT=8000
EXPOSE 8000

# Single worker keeps RAM low (cheaper on Railway). Bump if you need more concurrency.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
