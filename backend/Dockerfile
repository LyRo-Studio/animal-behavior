FROM python:3.12-slim

WORKDIR /app

# ffprobe: probes a Cut's duration/resolution/codec (ticket #22) — see
# app/services/media_prober.py. ffmpeg's package also provides ffmpeg
# itself, which nothing here uses; ffprobe alone isn't packaged separately
# on Debian.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Apply migrations before serving — docker-compose up otherwise never runs
# Alembic at all, leaving the acceptance criterion unmet in practice.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
