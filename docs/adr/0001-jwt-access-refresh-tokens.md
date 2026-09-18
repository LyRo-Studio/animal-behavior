# Use JWT access + refresh tokens instead of cookie sessions

**Status:** superseded by ADR-0004 (application-level authentication removed
in favor of trusting an external access layer)

The backend (FastAPI) and frontend (Vue SPA) authenticate via a JWT bearer
token sent in the `Authorization` header, rather than a server-tracked
cookie session. This was a deliberate choice over the engineering
standards' implicit cookie-session assumption (its CSRF section is only
scoped to "when authentication relies on browser-managed credentials such
as cookies"): a bearer token isn't browser-managed credentials, so CSRF
protection doesn't apply to it.

The trade-off cookie sessions avoid for free — a deactivated Account
should stop working immediately, but a bare JWT can't be individually
revoked before it expires — is addressed with a short-lived access token
(minutes) plus a refresh token that is checked against the database on
every refresh. A deactivated Account is locked out within one refresh
cycle instead of only once a long-lived token naturally expires.

## Considered options

- **Cookie session**, server-tracked in Postgres: immediate revocation for
  free, but requires CSRF protection on every state-changing request.
- **Long-lived JWT with a server-side revocation list checked per
  request**: immediate revocation, but forces a DB hit on every single
  authenticated request, giving up most of JWT's statelessness benefit.
- **Long-lived JWT, no revocation** (accept the delay): simplest, but a
  deactivated account would remain usable until its token expires —
  rejected as directly undermining the account-deactivation requirement.
