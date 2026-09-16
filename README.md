# Animal Behavior — Hogeschool VIVES

Web application for Hogeschool VIVES. See `CONTEXT.md` for the domain
glossary and approved deviations from `ENGINEERING-STANDARDS.md`, and
`docs/adr/` for architectural decisions.

## Stack

- **Backend**: FastAPI, SQLAlchemy, Alembic, Postgres — `backend/`
- **Frontend**: Vue 3, TypeScript, Vite, Tailwind CSS — `frontend/`
- **Media storage**: S3-compatible bucket (e.g. Ceph RGW) for Test/Cut/Dataset media
- **CI/CD**: GitHub Actions on a single self-hosted runner installed on the production box — see [Deployment](#deployment--cicd)

## Features

- **Auth**: JWT access + refresh tokens (no cookie sessions) — see `docs/adr/0001-jwt-access-refresh-tokens.md`
- **Accounts**: a single seeded Admin manages Users (invite by email, deactivate/reactivate); Users set their password via an emailed invite link — no self-registration
- **Password recovery**: email-based forgot-password flow
- **Media browser**: browse and stream Test/Cut media from the S3 bucket, with scoped, time-limited access tokens carried in the URL — see `docs/adr/0002-media-access-tokens-in-url.md`
- **Admin page**: create/deactivate User accounts (Admin-only)

Full domain terminology and feature-level decisions live in `CONTEXT.md`.

## Running locally

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (see `/health`)

`.env` holds local secrets (DB password, JWT signing key, S3 credentials, SMTP config) and is never committed — see `.env.example` for every variable and what it's for.

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

CI (`.github/workflows/ci.yml`) runs backend and frontend checks (lint, type-check, tests) on every pull request, on a self-hosted runner installed directly on the production box — see `docs/adr/0003-self-hosted-runner-on-production-for-ci-cd.md` for why.

Deploy (`.github/workflows/deploy.yml`) triggers automatically once `ci.yml` succeeds for a push to `master`:

1. Builds the backend and frontend images and pushes them to GHCR, tagged both `sha-<short-commit>` and `latest`.
2. Runs `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --pull always --no-build --wait` on the production box against those published images, blocking until all services report healthy.

A plain local `docker compose up` never touches `docker-compose.prod.yml` and always builds from source, unaffected by any of this.

**Rollback**: dispatch `deploy.yml` manually with a previously published tag (skips build/push and redeploys that image directly):

```bash
gh workflow run deploy.yml -f image_tag=sha-<short-commit>
```

Only commits that were actually deployed by this pipeline (i.e. already have a `sha-<commit>` tag published in GHCR) can be targeted — a commit predating `deploy.yml`'s existence, or one that was never pushed to `master`, has no image to roll back to.

**Known gap** (tracked in issue #41): a failed healthcheck on deploy correctly fails the workflow, but because `backend`/`frontend` bind fixed host ports, the old container is already stopped by the time the new one is verified — there's no side-by-side window, so a bad deploy briefly takes production down rather than leaving the previous version serving traffic. See `CONTEXT.md`'s "Contradicts ADR-0003" note for detail.
