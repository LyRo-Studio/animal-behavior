# Build context is the repo root (see docker-compose.yml's `worker`
# service) — needed so this image can reuse the backend's own S3/DB/domain
# source directly (CONTEXT.md's "Analysis worker" decision) rather than
# reimplementing it, the same way `s3_client.py` is already shared within
# the backend itself.
FROM lynndelaere/dogtrace:1.1.1

WORKDIR /worker

COPY worker/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Only the FastAPI-free slice of backend/app: core config, DB session/base,
# ORM models, and the specific service modules the worker calls through
# (s3_client, media_browser for parse_cut_key, analyses for the job-state
# transitions). Never app/api or app/main — those pull in FastAPI, which
# this image never installs (issue #44: "no FastAPI dependency").
COPY backend/app/__init__.py ./app/__init__.py
COPY backend/app/core ./app/core
COPY backend/app/db ./app/db
COPY backend/app/models ./app/models
COPY backend/app/services/__init__.py ./app/services/__init__.py
COPY backend/app/services/s3_client.py ./app/services/s3_client.py
COPY backend/app/services/media_browser.py ./app/services/media_browser.py
COPY backend/app/services/analyses.py ./app/services/analyses.py

COPY worker/worker ./worker

# The base image sets ENTRYPOINT ["dogtrace"] (its own CLI) — reset it so
# CMD below actually runs, instead of being appended as arguments to
# `dogtrace` (which would crash-loop the container on every start).
ENTRYPOINT []
CMD ["python", "-m", "worker"]
