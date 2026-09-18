# Trust Mechatronics as the sole authentication boundary; remove application-level authentication

**Status:** accepted

Production access to the Animal Behavior application is now gated by Mega
Tronics, an external firewall that authenticates the person and forwards
their identity via an HTTP header present on every proxied request. The
application previously implemented its own authentication (JWT access +
refresh tokens per ADR-0001, an Admin/User role, password reset, and
email-invite registration).

We removed application-level authentication entirely. The application
trusts Mechatronics as the single authentication boundary and reads the
identity header purely for attribution — which analysis was run by whom —
never for authorization: there is no more Admin/User role distinction, and
analysis history is fully shared rather than scoped per account. This
supersedes ADR-0001 in full.

Maintaining two authentication boundaries (Mechatronics plus the app's own
login) would add real complexity — login/registration UI, JWT/session
handling, SMTP for account emails, Admin account management — without
adding real security, since anyone who can reach the application at all has
already passed Mechatronics. A second, redundant boundary is also a genuine
risk of its own: two independent systems that can silently drift out of
sync (e.g. an account deactivated in one but not the other), and a larger,
doubled attack surface for no additional protection.

## Considered options

- **Keep both boundaries (defense in depth):** rejected — the app's own
  login would have nothing left to protect against that Mechatronics
  doesn't already gate; doubling the auth surface for no additional
  protection works against having one clear boundary.
- **Keep the Admin/User role for authorization, derived somehow from the
  Mechatronics header:** rejected — the header carries identity only, no
  role information, so there's nothing to derive a role from; inventing a
  separate role-mapping system would be new authorization machinery with no
  real requirement behind it.
- **Keep per-account visibility scoping on `AnalysisJob`, keyed by the
  trusted header instead of a login session:** considered, but declined in
  favor of fully shared analysis history — the header is used for
  attribution only, not for hiding anyone's runs from anyone else.

## Consequences

- The signed-token mechanism for Cut streaming/download (ADR-0002) is
  unrelated to login; kept for now, renamed away from its "JWT access
  token" framing, pending an open question ADR-0002 now flags: whether
  Mechatronics attaches its identity header to native browser-element
  requests too (not just JS `fetch()` calls), in which case that whole
  mechanism becomes removable rather than merely renamed. Not decided
  here — see ADR-0002's own note.
- The backend has zero verification of the identity header's authenticity
  — anything that can reach the backend directly, bypassing Mechatronics,
  can claim any identity. This makes the reverse-proxy/network topology
  (only Mechatronics's routed path should ever reach the backend) load-
  bearing for this decision's safety, not just a routing convenience.
