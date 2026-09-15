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
