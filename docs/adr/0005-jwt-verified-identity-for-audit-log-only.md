# Verify identity cryptographically for the audit log only, not everywhere else

**Status:** accepted

ADR 0004 established that this app trusts the identity Mechatronics forwards (`X-authentik-email`)
purely because of network topology — nothing verifies it, and that's deliberate: "reaching the
backend at all means you passed Mechatronics." Every existing use of identity (`AnalysisJob`
attribution, the media-token/`cuts-info` rate-limit keys, `GET /whoami`) relies on that same
unverified trust.

The new `audit_log` (Feature A, issue #79) exists specifically to answer "who did X" as an
accountability record — a stronger claim than attribution or a rate-limit bucket ever needed to
make. So `audit_log` writes verify `X-authentik-jwt` against the JWKS Mechatronics also forwards
(`X-authentik-meta-jwks`), via `PyJWKClient` (already-installed `pyjwt`, no new dependency), and
record whether verification succeeded (`identity_verified`). Every other use of identity in the app
is untouched — still the plain, unverified header, exactly as ADR 0004 left it.

This means the app now has two different trust levels for "the same" identity concept, deliberately:

- Widening verification to every use of identity was considered and rejected — it's real scope
  creep against building the audit log as the first, minimal piece of a larger, still-unbuilt
  effort (see `CONTEXT.md`'s "Feature decomposition" note), and nothing about `AnalysisJob`
  attribution or rate-limiting needs a stronger guarantee than ADR 0004 already accepted for them.
- Skipping verification for the audit log too (matching everywhere else) was also considered and
  rejected — an accountability record that's only as trustworthy as an unverified header one
  compromised or misconfigured hop could spoof undermines the one thing this table exists to do.

## Consequences

- A future reader comparing `get_identity` (plain header, used almost everywhere) against the
  audit log's verification path should not assume this is an oversight or "fix" it into
  consistency in either direction without re-reading this ADR — the split is intentional.
- Verification failure (missing/expired JWT, bad signature, unreachable JWKS) never blocks the
  action being logged. The row is still written from the plain header value, with
  `identity_verified=false` — an audit log with an occasional unverified entry is more useful than
  one with silent gaps, and the underlying action (starting an analysis, downloading a Cut) must
  never fail because an external JWKS endpoint hiccuped.
- `PyJWKClient`'s default in-process caching is relied on as-is (auto-refetches on an unrecognized
  `kid`); no separate cache-lifetime tuning was added. In-process caching, not a shared store,
  follows the same single-backend-instance assumption `rate_limit.py` already relies on.
- If a real incident or compliance requirement later demands verified identity everywhere (not
  just the audit log), this ADR's scoping decision should be revisited explicitly — the reasoning
  here doesn't extend to `AnalysisJob` attribution or rate limiting on its own.
