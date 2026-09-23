# Build context is the repo root (see docker-compose.yml's `cutting-worker`
# service) — needed so this image can reuse the backend's own S3/DB/domain
# source directly, same "no reimplementation" reasoning as worker/Dockerfile.
#
# Plain CPU-only Python, unlike worker/Dockerfile's GPU-bearing
# `lynndelaere/dogtrace` base — `assist`'s workload (ffmpeg + scipy/numpy
# audio correlation) has no CUDA involvement (CONTEXT.md's Feature C "New
# CPU-only service, not `worker`" decision), so any contributor without a
# GPU can build and run this locally.
FROM python:3.12-slim

# ffmpeg provides both the `ffmpeg` binary `assist.slicer.Slicer` shells out
# to (via the `ffmpeg-python` bindings) and `ffprobe`, which `assist` also
# uses internally for start-time/sample-rate probing — `python:3.12-slim`
# has neither preinstalled (unlike a full `python` image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /cutting-worker

COPY cutting-worker/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# `assist` is vendored source (ticket #112, cutting-worker/assist/VENDORED.md)
# rather than a `pip install git+https://...` of the private upstream repo,
# so this build needs no GitHub credentials. Only its runtime dependencies
# are installed here; the source itself is COPYed below.
COPY cutting-worker/requirements-assist.txt ./
RUN pip install --no-cache-dir -r requirements-assist.txt

# Only the FastAPI-free slice of backend/app: core config, DB session/base,
# ORM models, and the specific service modules the cutting-worker calls
# through (s3_client, cutting_jobs for the job-state transitions). Never
# app/api or app/main — those pull in FastAPI, which this image never
# installs. Mirrors worker/Dockerfile's own COPY boundary.
COPY backend/app/__init__.py ./app/__init__.py
COPY backend/app/core ./app/core
COPY backend/app/db ./app/db
COPY backend/app/models ./app/models
COPY backend/app/services/__init__.py ./app/services/__init__.py
COPY backend/app/services/s3_client.py ./app/services/s3_client.py
COPY backend/app/services/media_prober.py ./app/services/media_prober.py
COPY backend/app/services/cutting_uploads.py ./app/services/cutting_uploads.py
COPY backend/app/services/timestamp_excel.py ./app/services/timestamp_excel.py
COPY backend/app/services/cutting_jobs.py ./app/services/cutting_jobs.py

COPY cutting-worker/assist ./assist
COPY cutting-worker/cutting_worker ./cutting_worker

CMD ["python", "-m", "cutting_worker"]
