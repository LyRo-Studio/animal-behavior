# Animal Behavior — Hogeschool VIVES

Web application for Hogeschool VIVES. See `CONTEXT.md` for the domain
glossary and approved deviations from `ENGINEERING-STANDARDS.md`, and
`docs/adr/` for architectural decisions.

## Stack

- **Backend**: FastAPI, SQLAlchemy, Alembic, Postgres — `backend/`
- **Frontend**: Vue 3, TypeScript, Vite, Tailwind CSS — `frontend/`
- **Media storage**: S3-compatible bucket (e.g. Ceph RGW) for Test/Cut/Dataset media
- **CI/CD**: GitHub Actions on two self-hosted runners (CI on the development VM, deploy on the production VM) — see [Deployment](#deployment--cicd)

## Features

- **Authentication**: none in the app itself — Mechatronics, an external layer in front of it, authenticates everyone and forwards their identity in an HTTP header, which the backend reads only to show who ran each analysis (`IDENTITY_HEADER_NAME`) — see `docs/adr/0004-trust-mega-tronics-remove-application-auth.md`
- **Media browser**: browse and stream Test/Cut media from the S3 bucket, with scoped, time-limited access tokens carried in the URL — see `docs/adr/0002-media-access-tokens-in-url.md`
- **Analyses**: run DogTrace on a Test's Cuts and download the report; history is shared by everyone, each analysis showing who ran it

Full domain terminology and feature-level decisions live in `CONTEXT.md`.

## Running locally

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (see `/health`)

`.env` holds local secrets (DB password, media-token signing key, S3 credentials) and is never committed — see `.env.example` for every variable the local stack reads and what it's for.

## Development (without Docker)

**Backend** (`backend/`):

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
ruff check .
pytest
```

Database migrations are managed with Alembic (`backend/alembic/`):

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe the change"
```

**Frontend** (`frontend/`):

```bash
npm install
npm run dev
npm run lint
npm run format
npm run typecheck
npm run test
npm run build
```

## Deployment / CI/CD

CI (`.github/workflows/ci.yml`) runs backend and frontend checks (lint, type-check, tests) on every pull request, on the self-hosted runner labelled `ci` (development VM) — see `docs/adr/0003-self-hosted-runner-on-production-for-ci-cd.md` for why.

Deploy (`.github/workflows/deploy.yml`) triggers automatically once `ci.yml` succeeds for a push to `master`:

1. Generates `.env` on the runner from the `production` GitHub Environment (see below) — failing immediately if a required value is missing.
2. Builds the backend, frontend and worker images and pushes them to GHCR, tagged both `sha-<short-commit>` and `latest`.
3. Runs `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile worker up -d --pull always --no-build --wait` on the production VM (`dogtrace-app`, the runner labelled `production-deploy`) against those published images, blocking until all services report healthy.

**Production configuration** lives in the repo's `production` GitHub Environment (Settings → Environments), not in a hand-edited file on the box. Each deploy regenerates `.env` from it (`chmod 600`, never logged), so rotating a secret means updating GitHub and redeploying.

- Secrets: `POSTGRES_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `MEDIA_TOKEN_SECRET_KEY`.
- Variables: `S3_BUCKET`, `S3_ENDPOINT`, `CORS_ORIGINS`, `VITE_API_BASE_URL`; optionally `POSTGRES_USER`, `POSTGRES_DB`, `S3_ADDRESSING_STYLE`, `IDENTITY_HEADER_NAME` (empty until the real header name is known — the backend then reads no identity).
- No value may contain a single quote or a line break, and `POSTGRES_PASSWORD` may only contain letters, digits and `. _ ~ -` (it is embedded unescaped in `DATABASE_URL`). The deploy fails immediately, naming the variable, if not.
- Recommended: restrict the Environment's deployment branches to `master`.
- See `CONTEXT.md`'s "CI/CD retarget (ticket #70)" for the reasoning.

A plain local `docker compose up` never touches `docker-compose.prod.yml` and always builds from source, unaffected by any of this.

**Rollback**: dispatch `deploy.yml` manually with a previously published tag (skips build/push and redeploys that image directly):

```bash
gh workflow run deploy.yml -f image_tag=sha-<short-commit>
```

Only commits that were actually deployed by this pipeline (i.e. already have a `sha-<commit>` tag published in GHCR) can be targeted — a commit predating `deploy.yml`'s existence, or one that was never pushed to `master`, has no image to roll back to.

**Known gap** (tracked in issue #41): a failed healthcheck on deploy correctly fails the workflow, but because `backend`/`frontend` bind fixed host ports, the old container is already stopped by the time the new one is verified — there's no side-by-side window, so a bad deploy briefly takes production down rather than leaving the previous version serving traffic. See `CONTEXT.md`'s "Contradicts ADR-0003" note for detail.
