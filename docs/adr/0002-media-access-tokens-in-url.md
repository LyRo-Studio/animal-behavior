# Carry media-access tokens in the URL, as a scoped exception to the no-auth-in-URLs rule

**Status:** accepted

The media browser feature (Cut playback/download) needs the browser's native `<video src="...">`
and download-link requests to authenticate against the backend — but those requests are issued
by the browser itself and can't carry the app's normal `Authorization` bearer header (see ADR
0001). The backend instead mints a short-lived (15 min), single-object-scoped media-access token
and the frontend embeds it as a query parameter on the streaming endpoint's URL.

This is a deliberate, narrow exception to `ENGINEERING-STANDARDS.md`'s "session or authentication
state must not be exposed through URLs" rule. It's scoped tightly enough that we judge the
trade-off acceptable:

- It is never the app's real session/auth state — that stays exactly where ADR 0001 put it. It's
  a separate, single-purpose capability grant limited to one S3 object and one action (play or
  download), and it expires in 15 minutes.
- It's the same shape the project's original brief asked for ("short-lived/presigned URLs where
  appropriate") — S3 presigned URLs work identically (signature in the query string) and were the
  first choice here, before the bucket's gateway turned out to reject them (see `CONTEXT.md`).
  This token is a backend-issued substitute for that same pattern, not a new risk category.
- The alternative — a short-lived `HttpOnly` cookie scoped to the media-streaming path — avoids
  the URL exposure but mixes a second, cookie-based auth mechanism into an app that deliberately
  went bearer-only in ADR 0001, and reopens the CSRF question that ADR closed.

## Consequences

- Anyone with the URL (e.g. from browser history, or an unredacted access/proxy log line) can use
  it until it expires. 15 minutes bounds that window; it is not zero risk.
- Access/proxy logging for the media-streaming endpoint should redact the `token` query parameter
  where feasible, and the response should set `Referrer-Policy: no-referrer`.
- If this bucket's gateway is ever fixed to accept presigned URLs, or the auth model changes, this
  decision should be revisited — the reasoning here doesn't extend to the app's actual session
  tokens.
- **Open question raised by ADR 0004 (application-level auth removed):** this ADR's entire premise
  is that a native browser element can't carry the app's own `Authorization` header, unlike a JS
  `fetch()` call. Under ADR 0004, identity instead comes from Mechatronics attaching a header at
  the network/proxy layer, in front of *every* request the box receives — if that applies uniformly
  to native-element requests (not just JS-initiated ones), the gap this ADR works around no longer
  exists, and this entire token mechanism could be removed rather than merely kept/renamed. Not
  something to act on without confirming that assumption empirically (same diagnostic step as
  discovering the identity header's name/format — see issue #72) — flagged here, not decided.
- **Amended by ticket #72 (application-level auth removed):** the token mechanism is *kept*,
  renamed away from its "JWT access token" framing — it now signs with its own
  `MEDIA_TOKEN_SECRET_KEY` — because the empirical check above could not be run (it needs a live
  `dogtrace-app` behind Mechatronics). The premise about the app's own `Authorization` header is
  no longer literally true, but the mechanism's remaining job is unchanged: scoping a
  browser-issued request to one Cut and one action for 15 minutes. One consequence worth stating
  plainly: with login gone, `POST /media/cuts/token` is itself reachable by anyone who can reach
  the backend, so a token now buys that expiry and single-Cut scoping, *not* access control —
  Mechatronics is the access control. That, plus the fact that the token issuance limiter is the
  only thing throttling streaming, is the case for removing the mechanism outright if the
  empirical check passes; the removal checklist is in issue #72's "Media-token decision" section.
