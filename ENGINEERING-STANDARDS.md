# Engineering Standards

These standards define the mandatory technology choices and engineering conventions for company web application projects.
Project-specific deviations require an explicit technical or business justification and must be documented in `CONTEXT.md` before implementation.

## 1. Mandatory Technology Stack

### Database

- PostgreSQL is the required relational database.
- Do not introduce SQLite, MySQL, MariaDB, or another database without an approved deviation.
- Database schema changes must use Alembic migrations.

### Backend

- Python
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic / pydantic-settings

API endpoints should primarily handle HTTP concerns. Business logic belongs in service modules where appropriate.

### Frontend

- Vue 3
- TypeScript
- Vite
- Vue Router
- Vue Composition API with `<script setup lang="ts">`

Reusable UI components belong in `frontend/src/components/`. Page-level components belong in `frontend/src/views/`. Backend communication belongs in `frontend/src/services/`.

### Styling and Design System

- Tailwind CSS is mandatory.
- Do not introduce another CSS framework.
- Configure Tailwind using the fixed fonts and semantic colors defined below.
- Prefer semantic tokens such as `bg-primary`, `text-foreground`, and `border-border` over arbitrary color values.

#### Typography

- Default UI font: Inter
- Default Monospace font: JetBrains Mono

#### Colors

- Primary: `#2563EB`
- Primary hover: `#1D4ED8`
- Background: `#F8FAFC`
- Surface: `#FFFFFF`
- Foreground: `#0F172A`
- Muted: `#64748B`
- Border: `#E2E8F0`
- Success: `#16A34A`
- Warning: `#D97706`
- Danger: `#DC2626`

These colors must be exposed through semantic Tailwind design tokens.

Application code should use the semantic tokens rather than hard-coded
or arbitrary color values.

For example, prefer:

- `bg-primary`
- `bg-surface`
- `text-foreground`
- `text-muted`
- `border-border`
- `text-danger`

Do not change the fixed fonts or colors without an approved project-specific
deviation documented in `CONTEXT.md`.

### Frontend Code Quality

- ESLint is mandatory.
- Prettier is mandatory.
- TypeScript type checking is mandatory.
- Code must pass lint, format-check, typecheck, and build before an implementation is complete.

### Backend Code Quality

- Ruff is used for linting and formatting.
- Pytest is used for tests.
- Backend code must pass linting and tests before an implementation is complete.

## 2. Secrets and Configuration

- Secrets and environment-specific settings belong in `.env`.
- `.env` must never be committed.
- When environment variables are introduced, the repository must include `.env.example` documenting every required variable using safe development placeholders.
- Application code must read secrets and runtime settings from environment variables.
- Secrets must never be hard-coded in source code, Dockerfiles, compose files, or committed configuration.

## 3. Docker and Deployment

- Applications are developed and deployed using Docker containers.
- The repository must contain Dockerfiles for frontend and backend.
- `docker-compose.yml` defines the local multi-container environment.
- Production deployment should build immutable images from these Dockerfiles.
- PostgreSQL data must use a persistent volume in local Docker Compose.

## 4. Testing

- New behavior should be tested where practical.
- Bug fixes should include regression tests when the defect can reasonably be reproduced automatically.
- Prefer tests of externally visible behavior over implementation details.

## 5. Security

Security must be considered during implementation and verified before an
implementation is considered complete.

Security controls should be appropriate to the functionality and threat
surface of the application. Security requirements must not be postponed
until after implementation.

### Data Validation

- Treat all data received from users, clients, external services, files,
  URLs, headers, cookies, and environment-dependent sources as untrusted.
- Validate input on the backend before it is processed or persisted.
- Use explicit schemas, types, constraints, and allowed values where
  appropriate.
- Frontend validation may improve usability but must never be relied upon
  as the only validation layer.
- Do not expose sensitive implementation details through validation or
  error messages.

### SQL Injection Prevention

- Never construct SQL queries by concatenating or interpolating untrusted
  input into SQL strings.
- Use SQLAlchemy's parameterized query mechanisms and ORM/query APIs.
- Raw SQL should only be used when necessary and must use parameterized
  values for untrusted input.
- SQL injection protections must be covered by relevant automated or
  security tests where practical.

### Password Security

When the application manages passwords:

- Passwords must never be stored in plaintext.
- Passwords must be hashed using a modern password-hashing algorithm
  designed for password storage.
- Password hashes, credentials, tokens, or other authentication secrets
  must never be written to application logs.
- Authentication responses must avoid unnecessarily revealing whether
  a specific account exists.

### Brute-Force Attack Prevention

Authentication and other sensitive endpoints must be protected against
automated repeated attempts.

Where applicable:

- Apply rate limiting to authentication endpoints.
- Consider additional throttling or temporary restrictions after repeated
  failed authentication attempts.
- Avoid controls that allow attackers to trivially determine whether a
  username or email address exists.
- Security controls must not create an easy denial-of-service mechanism
  against legitimate users.

### Session Hijacking Prevention

When the application uses authenticated sessions or authentication tokens:

- Session identifiers and authentication tokens must be generated and
  handled securely.
- Authentication tokens must only be transmitted over secure connections
  in production.
- Cookies containing authentication or session information must use
  appropriate security attributes such as `Secure`, `HttpOnly`, and
  `SameSite`.
- Session or authentication state must not be exposed through URLs,
  application logs, or client-visible debugging information.
- Authentication state should expire and be invalidated appropriately.

### Cross-Site Request Forgery Prevention

When authentication relies on browser-managed credentials such as cookies:

- State-changing requests must be protected against Cross-Site Request
  Forgery (CSRF).
- Use an appropriate CSRF protection mechanism for the authentication
  architecture being used.
- Do not treat CORS configuration as a replacement for CSRF protection.
- State-changing operations must use appropriate HTTP methods and must
  not be performed through GET requests.

### Denial-of-Service Protection

Applications must avoid obvious implementation patterns that allow a
single client to consume excessive application resources.

Where appropriate:

- Apply rate limiting to public and sensitive endpoints.
- Limit request body and upload sizes.
- Validate pagination and query limits.
- Avoid unbounded database queries or processing of unbounded input.
- Apply reasonable timeouts and resource limits to external operations.
- Expensive operations should be designed so they cannot be triggered
  repeatedly without appropriate controls.

Infrastructure-level denial-of-service protection may be provided by the
deployment platform, reverse proxy, load balancer, or other infrastructure
and should not be reimplemented unnecessarily inside the application.

### Security Verification

Security requirements are part of the definition of done.

Before an implementation is considered complete:

- Review the implemented functionality for relevant security risks.
- Verify that untrusted input is validated.
- Verify that database access does not introduce SQL injection risks.
- Verify authentication and password handling when applicable.
- Verify brute-force protections on sensitive endpoints when applicable.
- Verify session and cookie security when applicable.
- Verify CSRF protection when applicable.
- Verify relevant request, upload, pagination, and resource limits.
- Add automated security or regression tests where the protection can
  reasonably be tested automatically.
- Run the relevant security tests together with the normal test suite.

Security testing must focus on the threats relevant to the functionality
being implemented. Do not add meaningless security tests merely to satisfy
a checklist.

Known security vulnerabilities must not be left unresolved when an
implementation is considered complete.

## 6. Git

- Keep commits focused.
- Do not mix unrelated changes.
- Feature and fix work should remain traceable to the corresponding issue.
- Work discovered outside the current issue scope should be captured separately.

## 7. General Principle

Prefer consistency across projects over introducing alternative technology for marginal convenience.
Existing repository configuration is authoritative for exact formatter, linter, Tailwind, and Docker settings.
