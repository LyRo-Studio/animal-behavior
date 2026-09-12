# Animal Behavior — Hogeschool VIVES

Web application for Hogeschool VIVES. See `CONTEXT.md` for the domain
glossary and approved deviations from `ENGINEERING-STANDARDS.md`, and
`docs/adr/` for architectural decisions.

## Stack

- **Backend**: FastAPI, SQLAlchemy, Alembic, Postgres — `backend/`
- **Frontend**: Vue 3, TypeScript, Vite, Tailwind CSS — `frontend/`

## Running locally

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (see `/health`)

## Development (without Docker)

**Backend** (`backend/`):

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
ruff check .
pytest
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
