# Animal Behavior Webinterface

Web application for Hogeschool VIVES. Ships first with account management
(admin-managed users, login, password reset) and a landing page; further
domain features (the actual animal-behavior functionality) will be added
later and are out of scope for the current design round.

## Language

**Account**:
A stored identity with an email address and password hash, used to log in. Every Account has exactly one role: Admin or User.
_Avoid_: login, user record

**Admin**:
An Account whose role grants account management (create/deactivate other Accounts) plus everything a User can do. A role value on Account, not a separate entity. The first Admin is seeded from `.env` at startup.

**User**:
An Account created only by an Admin (no self-registration), with access to the application's future animal-behavior features. Sets their own password via an emailed invite link before first login.
_Avoid_: gebruiker (use "User" in code/discussion), member

**Home**:
The authenticated page every Account (Admin or User) lands on right after logging in; carries the site's usage information. Requires login — there is no public page a visitor sees before authenticating.
_Avoid_: Landing page (conventionally means a public pre-login page, which this is not)

**Admin page**:
A separate page, reachable from a nav link on Home, where an Admin manages User accounts (create, deactivate). Not reachable by a User.

**Test**:
The unit a user searches for by ID (e.g. `T001`). Backed by the `cuts/<Test>/` prefix in the S3 bucket — one prefix per Test.
_Avoid_: "test" for anything else, in particular a Dataset's `train`/`valid`/`test` split — see Split.

**Cut**:
One processed video file under `cuts/<Test>/` in S3, belonging to exactly one Test. The unit a user views, plays, inspects, and downloads after selecting a Test.

**Source Video**:
An original, unsplit video under `source/` in S3, before being split into Cuts. Not exposed by the application in the current round — see Decisions.

**Dataset**:
A versioned collection of files at its own top-level prefix in S3 (e.g.
`dataset_v0.9/`, `dataset_pose_v0.1/`) — sibling to `cuts/` and `source/`,
not nested under a shared `dataset/` parent — identified by a name
starting with `dataset`, browsable as a folder tree.
_Avoid_: "test" for a Dataset's split subfolder — see Split.

**Split**:
A `train`/`valid`/`test` subfolder inside a Dataset. Deliberately not called "Test" — that name is reserved for the `T001`-style unit above.

**Camera**:
Which physical camera recorded a Cut, encoded as `C1`/`C2` in its filename (e.g. `T001_C1_ME_F1.mp4`).

**Condition**:
The behavioral condition a Cut was recorded under, encoded as `ME` (Met Eigenaar — with owner) or `ZE` (Zonder Eigenaar — without owner) in its filename.

**Phase**:
Which segment of a Test a Cut represents, encoded as `F1`–`F8` (Fase 1–8, Dutch for "phase") in its filename — matches "individual test phases," the language already used for how `cuts/` relates to `source/`.

## Decisions & Approved Deviations

**Scope (this round):** Covers only Account/Admin/User management (login,
invite-to-set-password, password reset, deactivate) plus the Landing and
Admin page shells. The animal-behavior domain itself is explicitly out of
scope and undesigned for now.

**Login identifier:** Email address. No separate username.

**New-account activation:** Adding a User sends an emailed invite link
(same token mechanism as password reset) so they set their own password.
Admins never set or see a User's password.

**Landing page access:** The Landing page requires authentication — it is
not a public marketing page. Unauthenticated visitors only ever see a
login screen.

**Bootstrap:** The first Admin account is seeded from `.env` credentials
on startup (Alembic data migration or startup check), matching the
project's existing `.env`/`.env.example` convention.

**Bootstrap — inactive sole Admin recovery (ticket #13):** Startup seeding
reactivates the existing Admin row if it's `is_active = false`, rather
than only checking that an Admin row exists at all. Not reachable through
the app today (`deactivate_account` refuses to deactivate an Admin, and
there's no in-app way to create a second one this round), but chosen over
a separate recovery mechanism (CLI/admin script) so the app self-heals on
its next restart with zero manual intervention if that state is ever
reached some other way — consistent with seeding already being a
zero-touch, idempotent startup step rather than a manual operation.

**Semantic UI colors:** `success`/`warning`/`danger` keep the original
ENGINEERING-STANDARDS.md hex values (`#16A34A`/`#D97706`/`#DC2626`) as a
scoped exception to the VIVES-palette styling deviation — the house style
defines no equivalent, and its 6 study-domain accent colors are reserved
for identifying study domains only.

**Account removal:** Soft-delete (deactivate) rather than hard-delete —
login is blocked, the row is kept, and its outstanding refresh tokens are
revoked. Deactivating is reversible: an Admin can reactivate the same
row, restoring `is_active = true` (and thus its email) rather than that
email being freed for a *new* Account to claim. `POST /admin/accounts`
rejects an email that belongs to an existing Account regardless of that
Account's active state — a deactivated Account's email is reused by
reactivating it, not by minting a second row for the same address (this
revises the original round-1 decision, which let a new Account reuse a
deactivated one's email; that path produced duplicate-email hazards once
reactivation existed — see ticket #15). The email-uniqueness index at the
DB layer stays scoped to active accounts only (needed to let the
reactivate step itself run without tripping it) — it's the application
layer, not the schema, that now keeps it to one row per email over time.

**Admin creation:** Only User accounts can be created, deactivated, or
reactivated through the app; there is exactly one Admin (the
`.env`-seeded one) for this round. The `role` field itself isn't
restricted to a single Admin at the data layer — there's just no
UI/endpoint to create a second one yet.

**Post-login routing:** Both Admin and User land on Home. Admin sees an
added nav link to the Admin page; User does not.

**Home content:** Static for this round — written directly into the page,
not editable by an Admin through the UI.

**Session mechanism:** JWT bearer token (not a cookie session) — returned
on login, sent in an `Authorization` header, not subject to CSRF. Short-
lived access token + a refresh token checked against the database on each
refresh, so a deactivated Account is locked out within one refresh cycle
rather than only once a long-lived token expires. See `docs/adr/0001-jwt-access-refresh-tokens.md`.

**Display name:** Derived automatically from the email's local-part —
the token before the first `.` (e.g. `jan.peeters@vives.be` → "Jan"),
title-cased. No separate name field is typed at account creation. Two
accounts sharing a first name are disambiguated in the UI by also showing
their email address, since only the first token is used.

**Allowed email domains:** Account creation is restricted to
`@vives.be` and `@student.vives.be`. Any other domain is rejected at
creation time — this also protects the display-name derivation, which
assumes a `name.surname` local-part.

**Styling scope note:** "Opmaak" (the styling deviation) is scoped to the
visual identity in `Huisstijlgids.pdf` (logo, colors, typography, imagery
rules). The other two guides in `companystyle/` (Schrijfgids — writing
style; Werkkader social media) are not in scope for this application
unless asked for separately.

**Styling deviation from ENGINEERING-STANDARDS.md (approved):**
The mandated fonts (Inter / JetBrains Mono) and semantic color values in
`ENGINEERING-STANDARDS.md` §"Styling and Design System" are replaced by
Hogeschool VIVES's house style, as documented in
`companystyle/Gidsen en richtlijnen/Huisstijlgids.pdf`. Tailwind CSS itself
stays mandatory; only the token *values* configured in it change.

- Justification: this application is built for and branded as a Hogeschool
  VIVES product; it must carry the VIVES house style rather than the
  organisation's generic template palette.
- Primary font: Poppins (fallback Arial). Secondary/quote font: Lora.
  Caption/footnote font: Barlow Condensed (never bold/extrabold/black).
- Core palette: VIVES-rood `#E00020` (primary), zwart `#1E1E1E`, wit
  `#FFFFFF`, zand `#EFEEE9` (secondary background/surface accent).
- Six subject-area accent colors exist (koraal, granaatappel, jeans, sunset,
  lavendel, heaven) but the huisstijlgids reserves them for identifying
  study domains, not general UI semantics (success/warning/danger) — exact
  mapping still open, see grilling round 1.
- `muted`/`border` (structural UI grays) also have no VIVES equivalent, and
  the huisstijlgids explicitly permits only 20%/40%/60%/80% tints of zwart
  as grayscale exceptions (no other percentages of any palette color are
  allowed) — so `border` uses 20% zwart and `muted` uses 40% zwart, rather
  than inventing a new gray or reusing the old blue-neutral scale.

**Brute-force throttling (ticket #7):** In-memory, per-process fixed-window
rate limiting (`backend/app/services/rate_limit.py`) — no Redis/shared
store, since the app runs as a single backend instance (`docker-compose.yml`
has no replicas); revisit if that ever changes. No `X-Forwarded-For` (or
similar) support — there is no reverse proxy in front of the app in any
current deployment, and trusting such a header from an untrusted client
would let the limit be spoofed away; revisit if a proxy is introduced.

- Login and refresh count only *failed* attempts — a success never
  consumes the budget — specifically so many legitimate users behind one
  shared IP (e.g. a campus NAT) succeeding normally can never lock each
  other out; only a run of failures (the actual brute-force signal) does.
- Login tracks failures on two independent dimensions, per client IP *and*
  per account (email), closing the gap a pure per-IP limit leaves open
  against an attacker distributing attempts across several source IPs at
  one victim account. The per-IP budget is peeked *before* authentication
  runs (to skip password verification for an already-exhausted IP) but the
  per-email budget is only ever `hit` *after* a confirmed failure, never
  peeked pre-auth — so a correct password always succeeds regardless of
  the account's recent failure history. Peeking the per-email budget
  pre-auth (as an earlier version of this code did) turns it into an
  account-specific denial-of-service gate: an attacker who fails enough
  distributed, per-IP-budget-evading guesses against one victim email
  exhausts the shared per-email bucket, and the real owner's *next*
  attempt — even with the right password — gets rejected by the peek
  before authentication is ever attempted. Ticket #7 requires throttling
  not lock out legitimate users, so this asymmetry (peek+hit on the IP
  dimension, hit-only on the email dimension) is deliberate, not an
  oversight — don't "fix" it back to symmetric peek-then-hit on both.
- Refresh is IP-only: there's no account identifier in a refresh request
  to key on (just an opaque token).
- Forgot-password counts *every* request, not just failures — there's no
  failure/success distinction visible to it, the response is always the
  same empty 204 — on both per-IP and per-email dimensions. Per-email is
  safe there in a way it wouldn't be for login if login counted every
  request too: exceeding it never blocks logging in, only requesting more
  reset emails for a bit.

**Media browser (S3 access) — scope (this round):** Covers Test search →
Cut listing, playback, inspection, and download, plus read-only Dataset
browsing. Source Video browsing is explicitly out of scope for this round
— add later as a separate extension once Test/Cut and Dataset browsing
are proven out.

**Media browser — access:** Every authenticated Account (User and Admin
alike) gets full access to this feature — search Tests, play/inspect/
download Cuts, browse Datasets. No extra per-role gating.

**Media browser — Dataset interaction:** Datasets are view/browse only in
this round — no per-file download from inside a Dataset yet (unlike
Cuts, which are downloadable). No whole-folder/zip download either.

**Media browser — S3 client:** `s3_client.py` (previously at the repo
root; also used outside this repo, e.g. by notebooks) moves into
`backend/app/services/s3_client.py` and becomes the shared S3-access
layer for this feature — no parallel client.

**Media browser — no Test/Cut catalog database (for now):** Search and
browse call S3 live (via `s3_client.py`) rather than reading from a
database index of Tests/Cuts. At current scale (~450 Tests today, ~30
Cuts each, per a live bucket check — user's estimate was "around 1000")
this is fast enough, and it means newly-added Tests/Datasets need no
ingestion step — they just appear. Kept behind a small set of named
service functions (e.g. "find a Test", "list a Test's Cuts", "list a
Dataset folder") so a future catalog database — already wanted, just not
built yet — can replace the live-S3 implementation later without
changing the API or frontend.

**Media browser — Cut media-info caching:** A Cut's probed media info
(duration, resolution, codec — via `ffprobe`) is cached in a small
database table keyed by the S3 key plus the object's ETag (so a
replaced file at the same key is re-probed automatically), rather than
re-probed on every view. This is a metadata cache, distinct from the
Test/Cut catalog database above — it doesn't tell you what Tests/Cuts
exist, only caches facts about ones already looked up.

**Media browser — Test discovery:** The full list of Test IDs (~450
today) is fetched once and filtered client-side as the user types
(type-to-filter), rather than requiring an exact ID or issuing a
request per keystroke.

**Media browser — no presigned S3 URLs:** The bucket's S3-compatible
endpoint sits behind a gateway (its error responses carry a `-kube`
request/host ID) that rejects every presigned URL tried against it —
both path-style and virtual-hosted addressing — with `403
SignatureDoesNotMatch`, while ordinary SDK-authenticated (header-signed)
calls succeed. Confirmed against the real bucket, not a boto3 config
issue. So: **the backend proxies Cut bytes itself** for playback and
download (streaming the S3 object through, forwarding HTTP Range
requests for video seeking) rather than redirecting the browser to an
S3-presigned URL. `ffprobe` likewise runs against a temporary local
download of the Cut (via `s3_client.py`'s existing `download_file`),
not a presigned URL — reusing existing capability rather than adding a
Range-over-HTTP probing path. Revisit if the gateway issue is ever fixed
on the infrastructure side.

**Media browser — media access tokens:** The backend-proxied streaming
endpoint (above) is reached by the browser's own native `<video src>`
and download-link requests, which can't carry the app's `Authorization`
bearer header. It's authenticated instead by a short-lived (15 min),
single-Cut-scoped token the backend mints on request and the frontend
embeds as a URL query parameter — a deliberate, narrow exception to
`ENGINEERING-STANDARDS.md`'s "no auth state in URLs" rule; see
`docs/adr/0002-media-access-tokens-in-url.md` for the justification.
Play and download each get their own token, minted lazily — only when
the user actually clicks Play or Download on a specific Cut, not eagerly
for every Cut in a Test's listing.

**Media browser — abuse protection:** Media-token issuance is rate
limited per Account, reusing the `rate_limit.py` machinery built for
ticket #7 — streaming a Cut is exactly the "expensive operation" that
`ENGINEERING-STANDARDS.md`'s Denial-of-Service section says must not be
"triggered repeatedly without appropriate controls." The streaming
endpoint itself isn't separately rate limited — it can't be reached
without a valid, already-rate-limited token in the first place.

**Media browser — no access audit trail (for now):** Who viewed/downloaded
which Cut is not recorded in this round, despite this being research
footage — nothing in the current scope asked for it, and it's addable
later without disrupting anything already designed. An explicit choice,
not an oversight — revisit if a data-governance requirement surfaces.
