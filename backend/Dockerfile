# Build context is the repo root (docker-compose.yml's `backend` service),
# not backend/ — ticket #115, part of issue #113's Excel consolidation
# feature: this image needs to reach the sibling consolidation/ folder too,
# the same reason worker/Dockerfile and cutting-worker/Dockerfile already
# build from repo root (see the root .dockerignore's note on this).
FROM python:3.12-slim

WORKDIR /app

# ffprobe: probes a Cut's duration/resolution/codec (ticket #22) — see
# app/services/media_prober.py. ffmpeg's package also provides ffmpeg
# itself, which nothing here uses; ffprobe alone isn't packaged separately
# on Debian.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
# Only the two domain modules the adapter (app/services/consolidation.py)
# actually imports — not consolidation/'s own requirements.txt, notebook,
# or sample fixtures, mirroring worker/Dockerfile's "only the slice this
# image actually needs" style of explicit-file COPY.
COPY consolidation/consolidatie.py ./consolidation/consolidatie.py
COPY consolidation/observer_import.py ./consolidation/observer_import.py

EXPOSE 8000

# Apply migrations before serving — docker-compose up otherwise never runs
# Alembic at all, leaving the acceptance criterion unmet in practice.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
