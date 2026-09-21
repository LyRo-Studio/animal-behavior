# Animal Behavior Webinterface

Web application for Hogeschool VIVES for browsing animal-behavior test
footage (Tests, Cuts, Datasets) and running DogTrace analyses on it.
Originally shipped with its own account management (login, admin-managed
users, password reset); ticket #72 removed all of that — Mechatronics, an
external layer in front of the app, is now the only authentication boundary.

## Language

**Identity**:
The person Mechatronics authenticated, as the raw string it forwards in an HTTP header on every request (the header's name is the `IDENTITY_HEADER_NAME` setting). Read by the backend purely for attribution — which analysis was run by whom — never for authorization; there is no role, no login and no per-person visibility. `null` when no header is configured or sent (local development). The application does not authenticate anyone itself since ticket #72 — see "Application-level authentication removed (ticket #72)" below and `docs/adr/0004-trust-mega-tronics-remove-application-auth.md`.
_Avoid_: Account, Admin, User, login, session — those concepts no longer exist.

**Home**:
The page the site opens on; carries the site's usage information, links to the Media Browser and Analyses, and shows who the person is (their Identity) when known. There is no login step before it.
_Avoid_: Landing page (conventionally means a public pre-login page)

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

**Scope (this round) — superseded by ticket #72 (application auth removed; kept as history):** Covers only Account/Admin/User management (login,
invite-to-set-password, password reset, deactivate) plus the Landing and
Admin page shells. The animal-behavior domain itself is explicitly out of
scope and undesigned for now.

**Login identifier — superseded by ticket #72 (application auth removed; kept as history):** Email address. No separate username.

**New-account activation — superseded by ticket #72 (application auth removed; kept as history):** Adding a User sends an emailed invite link
(same token mechanism as password reset) so they set their own password.
Admins never set or see a User's password.

**Landing page access — superseded by ticket #72 (application auth removed; kept as history):** The Landing page requires authentication — it is
not a public marketing page. Unauthenticated visitors only ever see a
login screen.

**Bootstrap — superseded by ticket #72 (application auth removed; kept as history):** The first Admin account is seeded from `.env` credentials
on startup (Alembic data migration or startup check), matching the
project's existing `.env`/`.env.example` convention.

**Bootstrap — inactive sole Admin recovery (ticket #13) — superseded by ticket #72 (application auth removed; kept as history):** Startup seeding
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

**Account removal — superseded by ticket #72 (application auth removed; kept as history):** Soft-delete (deactivate) rather than hard-delete —
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

**Admin creation — superseded by ticket #72 (application auth removed; kept as history):** Only User accounts can be created, deactivated, or
reactivated through the app; there is exactly one Admin (the
`.env`-seeded one) for this round. The `role` field itself isn't
restricted to a single Admin at the data layer — there's just no
UI/endpoint to create a second one yet.

**Post-login routing — superseded by ticket #72 (application auth removed; kept as history):** Both Admin and User land on Home. Admin sees an
added nav link to the Admin page; User does not.

**Home content:** Static for this round — written directly into the page,
not editable by an Admin through the UI.

**Session mechanism — superseded by ticket #72 (application auth removed; kept as history):** JWT bearer token (not a cookie session) — returned
on login, sent in an `Authorization` header, not subject to CSRF. Short-
lived access token + a refresh token checked against the database on each
refresh, so a deactivated Account is locked out within one refresh cycle
rather than only once a long-lived token expires. See `docs/adr/0001-jwt-access-refresh-tokens.md`.

**Display name — superseded by ticket #72 (application auth removed; kept as history):** Derived automatically from the email's local-part —
the token before the first `.` (e.g. `jan.peeters@vives.be` → "Jan"),
title-cased. No separate name field is typed at account creation. Two
accounts sharing a first name are disambiguated in the UI by also showing
their email address, since only the first token is used.

**Allowed email domains — superseded by ticket #72 (application auth removed; kept as history):** Account creation is restricted to
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

**Brute-force throttling (ticket #7) — superseded by ticket #72 except the limiter itself:** the login/refresh/forgot-password limits below went away with those endpoints; the in-memory `rate_limit.py` machinery now guards only media-token issuance and `/media/cuts/info`, keyed per identity. In-memory, per-process fixed-window
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

**Media browser — access:** Everyone past Mechatronics (no per-role or
per-person gating since ticket #72) gets full access to this feature — search Tests, play/inspect/
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
limited per identity (per Account before ticket #72), reusing the `rate_limit.py` machinery built for
ticket #7 — streaming a Cut is exactly the "expensive operation" that
`ENGINEERING-STANDARDS.md`'s Denial-of-Service section says must not be
"triggered repeatedly without appropriate controls." The streaming
endpoint itself isn't separately rate limited — it can't be reached
without a valid, already-rate-limited token in the first place.
`GET /media/cuts/info` (ticket #22) gets the same per-identity limiter — a
cache miss there does a full S3 download plus an `ffprobe` subprocess
call, the same expense class as token issuance.

**Media browser — real-bucket smoke test (ticket #23):**
`backend/tests/test_s3_client_real_bucket.py` exercises `BotoS3Client`
(list/head/range-read) against the actual bucket and its real gateway, to
catch gateway-level drift the `FakeS3Client`-backed tests can't — this is
how the "no presigned S3 URLs" gateway behavior above was originally
found. Marked `real_bucket` and excluded from the default `pytest` run
(and therefore CI) by `addopts` in `backend/pyproject.toml`; run it
explicitly with `pytest -m real_bucket`. Skips (not fails) if S3 isn't
configured, so this is safe to leave selected by accident.

**Media browser — no access audit trail (for now):** Who viewed/downloaded
which Cut is not recorded in this round, despite this being research
footage — nothing in the current scope asked for it, and it's addable
later without disrupting anything already designed. An explicit choice,
not an oversight — revisit if a data-governance requirement surfaces.

**CI/CD — self-hosted runner on production:** CI and CD both run on a
single self-hosted GitHub Actions runner installed directly on the
production box (dedicated `gha-runner` service account: Docker-group
membership only, no root), rather than a GitHub-hosted runner reaching
into the box over Tailscale — the box has no public IP and this avoids
exposing SSH publicly or maintaining a tailnet auth key. Registered
runner: `lynn-delaere-prod`, running as a systemd service
(`actions.runner.LyRo-Studio-animal-behavior.lynn-delaere-prod.service`)
so it survives reboots. See
`docs/adr/0003-self-hosted-runner-on-production-for-ci-cd.md`.
**Amended by ticket #70:** that runner was never actually on the production
box — it's on the development VM and now serves CI only (label `ci`); deploys
run on a second runner on `dogtrace-app` (label `production-deploy`). See
"CI/CD retarget (ticket #70)" below and ADR-0003's amendment.

**CI/CD — deploy.yml (ticket #36):** Triggered by `workflow_run` off
`ci.yml` completing successfully for `master` (never re-runs the checks),
plus a `workflow_dispatch` input (`image_tag`) for manual rollback that
skips build/push and redeploys an already-published tag. Builds and
pushes `ghcr.io/lyro-studio/animal-behavior-{backend,frontend}` tagged
both `sha-<short-commit>` (immutable, what rollback targets) and `latest`,
authenticated with only the workflow's own `GITHUB_TOKEN`
(`permissions: packages: write`) — no extra secret.

- `docker-compose.prod.yml` is a new override file (`-f docker-compose.yml
  -f docker-compose.prod.yml`, applied only by the deploy step) that adds
  `image:` to `backend`/`frontend` on top of the base file's `build:`,
  read from `BACKEND_IMAGE_REF`/`FRONTEND_IMAGE_REF` env vars the workflow
  sets. A plain local `docker compose up` never passes `-f`, so it keeps
  building from source unchanged. Deploy runs
  `docker compose ... up -d --pull always --no-build --wait` — `--no-build`
  guarantees the pulled tag is used rather than rebuilt on the box.
- **Superseded by ticket #70 (see "CI/CD retarget" below):** the frontend
  image's `VITE_API_BASE_URL` build-arg (baked in at build time, per
  `frontend/Dockerfile`) was read from the production box's own hand-
  maintained `.env`. It's now the `production` GitHub Environment's
  `VITE_API_BASE_URL` variable, read directly by the build step.
  **Superseded again by ticket #71:** no longer a variable at all — `deploy.yml`
  builds the frontend with the fixed value `/api` (see "Reverse proxy and
  `/api` prefix (ticket #71)" below).
- **Superseded by ticket #70 for `deploy.yml`:** both `ci.yml` and
  `deploy.yml`'s checkout steps set `clean: false`.
  `actions/checkout`'s default `git clean -ffdx` removes gitignored files
  too, which would delete the box's `.env` (shared workspace: the one
  runner reuses the same checkout directory for every workflow) before
  `deploy.yml` ever gets to read it. Without this, deploy would silently
  fail on a fresh/rebuilt runner workspace.
- **Resolved (ticket #41):** ADR-0003's original claim that a failed
  deploy "leaves the previous containers running" was wrong — fixed host
  ports mean `backend`/`frontend`'s old container stops before the new one
  can bind that port, so a failed healthcheck means a brief real outage
  for that service, not a silent no-op (`db` is unaffected — same image
  every deploy, never recreated). Decision: accept this at current scale
  rather than add a reverse-proxy blue-green cutover; ADR-0003 has been
  corrected to state this accurately instead of reopened. See the updated
  ADR-0003 for the full reasoning.
- **Follow-up, out of scope for #36:** nothing prunes superseded
  GHCR-pulled image layers on the production box after `--pull always`;
  disk usage grows unbounded over time. Revisit (e.g. a periodic `docker
  image prune`) if/when this becomes a real problem — not solved
  proactively here to avoid an unattended job that could prune an image a
  manual rollback still needs.

**Analysis worker (ticket #47, part of #44):** the `worker` Docker service
that claims a `queued` `AnalysisJob`, runs it through DogTrace, and
persists the result — see issue #44 for the full design (job state
machine, S3 input flow, report persistence, DogTrace integration
boundary).

- **Source reuse, not a package install:** `worker/Dockerfile` builds
  `FROM lynndelaere/dogtrace:1.1.1` and `COPY`s specific files out of
  `backend/app` (core config, db, models, and the FastAPI-free service
  modules `s3_client.py`/`media_browser.py`/`analyses.py`) directly into
  the image, rather than installing the backend as a package or
  reimplementing S3/DB access a second time — same "no parallel client"
  principle as `s3_client.py`'s own move into the backend. The worker's
  `requirements.txt` deliberately excludes `fastapi`, `argon2-cffi`, and
  `pyjwt` — nothing it imports needs them (backend's `app/api` and
  `app/main.py` are never copied in). Locally/in tests (outside that
  image), `worker/pyproject.toml`'s `pythonpath` points straight at
  `../backend` instead, so `app.*` resolves to the exact same source with
  no duplicated copy on disk either way.
- **Job-lifecycle DB transitions live in `backend/app/services/analyses.py`**
  (`claim_next_queued_job`, `finalize_analysis_job`,
  `requeue_stuck_running_jobs`), alongside `create_analysis_job`/
  `cancel_analysis_job` from tickets #45/#46 — one module owns the whole
  `AnalysisJob` state machine regardless of whether the API or the worker
  drives a given transition, rather than splitting job-state logic across
  two places that could drift.
- **Per-video status comes from a live progress callback, not post-hoc
  inference (ticket #48):** `dogtrace.runner.run_reporting` (upstream
  `dogtrace-core`, bumped to 1.1.1 — a separate, externally-versioned repo
  at `github.com:vanniew/dogtrace-core`, published as the
  `lynndelaere/dogtrace` Docker Hub image) now accepts an optional
  `progress: Callable[[Path, str], None]` invoked twice per video: once
  with `"started"` right before it's attempted, once more with
  `"succeeded"` or `"failed"` once its outcome is known — mirroring its
  own per-video loop, which still catches each video's own exception and
  keeps going (fault isolation unchanged). `worker/orchestrator.py`'s
  `_make_progress_callback` maps these onto `analysis_job_videos.status`
  (`processing`, then `succeeded`/`failed`) and commits after every event,
  so `GET /analyses/{id}` observes each video's progress in near-real-time
  while the job is still `running`, not only once it reaches a terminal
  state. This replaced ticket #47's coarse mechanism (glob for
  `<video_stem>/*/track_report.xlsx` under the output dir once the whole
  call returned) — removed along with the now-unused `_video_produced_output`.
  A whole-batch failure (e.g. the model failing to load, raised before any
  video's `"started"` fires) still fails every video still `pending`/
  `processing`, exactly as ticket #47 already did, just extended to cover
  `processing` too now that a video can be in that state when the crash
  happens.
- **Model loaded once per job, not once per video (ticket #48):**
  `dogtrace.runner.run_reporting` also now loads the YOLO model once
  itself (before its per-video loop) and passes it to `CASIOP(...,
  model=...)`, which threads it through instead of each video's
  `VideoReport.from_video` implicitly reloading it. This is entirely
  internal to the upstream `run_reporting` call — the worker doesn't load
  or pass a model itself, it just calls the updated `run_reporting` (via
  `dogtrace_runner.run_reporting`'s existing seam) and gets the reuse for
  free.
- **`progress` is called outside `run_reporting`'s own per-video
  `try`/`except` (dogtrace-core 1.1.1, a same-day patch on top of 1.1.0):**
  if `progress` itself raises (e.g. `_make_progress_callback`'s `db.commit()`
  hitting a transient DB error) from inside that `try`, it would otherwise
  be caught by dogtrace's own exception handler and misreported as that
  video's pipeline having failed, masking a real infrastructure error as an
  ordinary per-video failure. Calling `progress` only after the `try`/except
  resolves (with the outcome captured in a local, not decided by the
  callback) means such an error propagates straight out of `run_reporting`
  instead, correctly surfacing as the whole-batch failure it is.
- **Worker fallback if a video is never given a terminal progress event
  (ticket #48):** `_run_claimed_job`'s `else` branch (paired with the
  `try` around `dogtrace_runner.run_reporting`) sweeps any video still
  `pending`/`processing` to `failed` after a *successful* return, not just
  after a raised exception. `dogtrace_runner` is a separate, externally-
  versioned dependency (`dogtrace-core`) whose "always call `progress` with
  a terminal event per video" contract isn't enforced by the type system —
  without this sweep, a violation of that contract would leave an
  `AnalysisJobVideo` stuck non-terminal forever under an otherwise-finished
  job, with no recovery path (`requeue_stuck_running_jobs` only rescues a
  job still `running`, not one already terminal with a stuck video row).
- **A Cut missing from S3 at download time fails only that video, not the
  whole job** — Cut keys are validated for shape at request time
  (`POST /analyses`) but never checked against S3 until the worker
  downloads them (see `InvalidCutSelectionError`'s docstring), so a Cut
  deleted in the interim is treated the same as any other per-video
  pipeline failure: that video is marked `failed`, the rest of the job's
  videos still run.
- **`report_s3_prefix` is only set if something was actually uploaded:**
  the worker uploads every file under the job's output directory (not
  just `casiop_report.xlsx`) to `reports/<test_id>/<analysis_id>/`, but
  leaves `report_s3_prefix` (and thus `AnalysisJob.report_available`)
  `null` if that directory was empty or never created — e.g. every
  requested Cut failed to download, so DogTrace was never even invoked.
  `report_available` must never point at an S3 prefix with nothing in it.
- **Crash recovery: requeue, not fail.** Issue #44 explicitly left the
  exact policy for a job orphaned `running` by a crashed/restarted worker
  open ("to be finalized during implementation"). Chosen: on worker
  startup, any `AnalysisJob` still `running` is reset to `queued` (and its
  videos back to `pending`) rather than marked `failed` — with exactly one
  worker instance ever processing jobs, a `running` row at startup can
  only mean a previous process died mid-job, never a second worker
  legitimately still owning it, so retrying automatically is safe and
  loses the user's request less often than failing it outright would.
  **Resolved (ticket #50):** this policy and its DB-level transition
  (`requeue_stuck_running_jobs`) already shipped as part of #47, but had no
  test coverage for the worker-process-specific half of recovery — removing
  a recovered job's now-orphaned temp directory. `worker/worker/__main__.py`'s
  `requeue_orphaned_jobs` (renamed from the previously-private
  `_requeue_orphaned_jobs`, matching `process_next_job`'s own
  not-underscore-prefixed, directly-tested-seam convention) now takes an
  injected `db: Session` and keyword-only `work_root` instead of opening its
  own `SessionLocal()` internally, so `worker/tests/test_startup_recovery.py`
  can exercise it directly against a real (Alembic-migrated) DB and a fake
  work root. This incidentally fixed a real latent bug, not just added test
  coverage: the previous version closed its own DB session *before* reading
  `job.id` in its cleanup loop, and `SessionLocal`'s default
  `expire_on_commit=True` plus `requeue_stuck_running_jobs`'s internal
  commit meant that read would have raised `DetachedInstanceError` the
  first time a real crash ever left a job orphaned — recovery itself would
  have crashed on the worker's next startup. Never triggered before, since
  nothing previously exercised this function with an actual stuck job.
- **Worker build context is the repo root**, not `worker/` — needed so its
  Dockerfile can `COPY` files out of `backend/app` (see "Source reuse"
  above). A new root-level `.dockerignore` keeps that context from
  pulling in `.git`, `.env`, `backend/.venv`, `frontend/node_modules`, etc.
  (`backend`'s and `frontend`'s own Dockerfiles keep their existing scoped
  contexts and `.dockerignore` files unchanged).
- **`docker-compose.yml` only** (not `docker-compose.prod.yml`) gets the
  new `worker` service this ticket — a prod image-override entry mirroring
  `backend`/`frontend`'s pattern is deferred to the Docker/CI/CD wiring
  phase issue #44 describes separately.
- **`worker` sits behind a Compose profile (`profiles: ["worker"]`)** —
  its GPU device reservation means a bare `docker compose up` would
  otherwise fail outright on any machine without an NVIDIA GPU +
  nvidia-container-toolkit (e.g. a contributor's laptop), taking
  db/backend/frontend down with it even though none of them need a GPU.
  `docker compose --profile worker up` opts in explicitly; the production
  box (which has the GPU) is expected to always pass that flag.
- **Follow-up, not fixed here:** if the worker crashes mid-job after
  partially uploading output but before `finalize_analysis_job` commits,
  `requeue_stuck_running_jobs` retries the job on restart, but nothing
  deletes the first attempt's already-uploaded files first — DogTrace's
  own per-video timestamp subfolder means the retry's output tree won't
  exactly overwrite the first attempt's, so stray files can accumulate
  under that job's `reports/<test_id>/<analysis_id>/` prefix. `S3Client`
  has no delete capability today; adding one just for this is deferred
  until it's an actual problem, not speculatively built into this ticket.

**Analysis report download (ticket #49):** `GET /analyses/{id}/report`
serves `<report_s3_prefix>casiop_report.xlsx` — the only artifact of
`reports/<test_id>/<analysis_id>/` exposed as a download in v1, per issue
#44's "Report persistence" decision.

- **Plain bearer-authenticated download, not the media-token pattern:**
  unlike Cut streaming (`docs/adr/0002-media-access-tokens-in-url.md`),
  this endpoint is never reached by a browser's native `<video src>` or
  bare `<a href>` request that can't carry an `Authorization` header — the
  frontend (ticket #52) triggers it via an ordinary authenticated fetch, so
  it lives on `analyses.py`'s existing bearer-authenticated `router` like
  every other `/analyses` endpoint, with no URL-embedded token and no new
  exception to `ENGINEERING-STANDARDS.md`'s "no auth state in URLs" rule.
  Resolves the "confirm during implementation" question issue #44/#49 left
  open.
- **Availability check is `status` *and* `report_s3_prefix`, not
  `report_available` alone:** `get_analysis_report_key`
  (`app/services/analyses.py`) requires `status` in `{completed,
  completed_with_errors}` and `report_s3_prefix is not None` before
  returning a key. The two are expected to always agree (`finalize_analysis_job`
  only reaches one of those two statuses when at least one video
  succeeded), but checking both directly is cheap defense against a
  worker-contract violation leaving them mismatched, rather than trusting
  `report_available` as if it specifically meant "casiop_report.xlsx
  exists" (it only means "the worker uploaded *something* to this
  prefix" — see `AnalysisJob.report_available`'s docstring).
- **No Range/seek support and no separate rate limit:** unlike Cut
  streaming, this is a one-shot whole-file download (no native player
  seeking to support), and it isn't a repeatable expensive operation in the
  same sense as media-token issuance or `/cuts/info` (no `ffprobe`
  subprocess, no S3 download-then-probe) — it's a `head_object` plus a
  streamed read of a file the worker already produced once. Streams via
  `read_range` in fixed-size chunks regardless, so a large report still
  never gets fully buffered in memory (`ENGINEERING-STANDARDS.md`'s DoS
  guidance), same mechanism as Cut streaming just without the Range-parsing
  half of it.

**Analysis detail/progress view (ticket #52):** `AnalysisView.vue`
(`/analyses/:id`), reached from `MediaBrowserView`'s "Analyze selected"
button. Polls `GET /analyses/{id}` every 2s while `status` is
`queued`/`running` (stops once terminal/cancelled — nothing left to
change), same `setTimeout`-based poll/retry shape as `StatusView.vue`'s
health check.

- **Report download is `fetch` + `Blob` + a synthetic `<a download>` click,
  not a plain `<a href>`:** the frontend half of the #49 decision above —
  since the endpoint is bearer-authenticated (not a token-in-URL the
  browser's own navigation can carry), `analyses.ts`'s
  `downloadAnalysisReport` fetches the file with the normal
  `Authorization` header and returns a `Blob`; the view turns that into an
  object URL only long enough to trigger the save, then revokes it.
- **Download button gated on `job.reportAvailable` alone**, not a
  client-side "terminal && ≥1 succeeded" recomputation — the backend field
  already means exactly that (see `AnalysisJob.report_available`'s
  docstring), so re-deriving it here would just be a second, driftable copy
  of the same rule.
- **"Processed" count, not "succeeded" count, drives the running-state
  progress text:** `{{ processedCount }}/{{ totalCount }} videos` counts
  every non-`pending` video (succeeded *and* failed), matching the ticket's
  own "2/5 videos, current: ..." example read as "how far through the
  batch", not "how many have succeeded so far".

**Analyses history (ticket #53):** `AnalysesHistoryView.vue` (`/analyses`,
linked from Home next to Media Browser — every authenticated Account, no
role gating) lists the current Account's own jobs via `GET /analyses`, and
`MediaBrowserView`'s Test view gets an inline "Previous analyses for
{Test}" panel via `GET /analyses?test_id=...`. Both reuse the existing
`listAnalyses` addition to `analyses.ts` — no new backend endpoint or
authorization logic: `list_analysis_jobs` already scopes to
`requested_by=account.id` (issue #44's "no cross-user visibility" decision,
Admin included), so this ticket is frontend-only.

- **The inline panel's fetch is independent of the Cuts fetch, and must stay
  that way:** `MediaBrowserView.search()` kicks off `loadPreviousAnalyses`
  alongside `listCuts`, not nested inside its `try`/`catch` — a Test can
  have prior `AnalysisJob` rows even when its Cuts are currently
  unavailable (e.g. removed/renamed in S3 after being analyzed, or a
  transient Cuts-listing failure), so the panel renders as a sibling of the
  Cuts `<template v-else-if="cuts">` block, not nested inside it. It was
  initially built nested (caught in review) — a Cuts-side 404/error/loading
  state silently hid an already-loaded, successful analyses panel. Guarded
  by the same `searchToken`, not a separate token: it's started from within
  the same `search()` call for the same Test switch, so no independent
  staleness window exists for it to guard against.
- **Route order:** `/analyses` (this ticket) is declared before
  `/analyses/:id` (ticket #52) in `router/index.ts` — doesn't change
  matching behavior (vue-router already prefers a static segment over a
  dynamic one regardless of declaration order), but keeps the two visually
  grouped in source in the order a reader encounters them in the UI.
- **`formatDate` (ISO string → locale string) moved to `frontend/src/utils/
  date.ts`:** previously defined separately inside `MediaBrowserView.vue`
  (for a Cut's "Last modified") and duplicated verbatim into
  `AnalysesHistoryView.vue`; the second copy was caught in review and
  factored out instead, since both views (plus the inline panel) now need
  the identical formatting.

**Docker/CI/CD wiring for the analysis worker (ticket #54):** `worker` is
now deployed and built the same way `backend`/`frontend` already are. The
GPU device reservation, S3/DB env vars, and `profiles: ["worker"]` gate
already existed from ticket #47 (local `docker-compose.yml` only) — this
ticket only adds the *deployment* plumbing: image publishing, the prod
compose override, and actually starting `worker` on deploy.

- **`docker-compose.prod.yml` gets a `worker` image override**, same
  `image:`-on-top-of-`build:` pattern as `backend`/`frontend`
  (`WORKER_IMAGE_REF`), read by `deploy.yml`'s Deploy step. Its
  `profiles`/`deploy.resources` blocks stay defined only in the base file,
  unchanged.
- **`deploy.yml` builds and pushes `ghcr.io/lyro-studio/animal-behavior-worker`**,
  tagged `sha-<short-commit>` and `latest`, same `docker/build-push-action`
  step shape and `GITHUB_TOKEN`-only auth as backend/frontend — except
  `context: .` (repo root) with an explicit `file: worker/Dockerfile`,
  since `worker/Dockerfile` itself needs the repo root as its build context
  to `COPY` files out of `backend/app` (see this file's "Analysis worker"
  section, "Worker build context is the repo root" decision).
- **Deploy's `docker compose ... up` now passes `--profile worker`** —
  found missing during a stuck-`queued`-job investigation and filed as a
  comment on this ticket before implementation started. Without it, the
  base file's `profiles: ["worker"]` gate (deliberately there so a
  GPU-less `docker compose up` doesn't fail outright — ticket #47) would
  silently exclude `worker` from every deploy even with its image
  built/pushed/overridden correctly; `backend`/`frontend` health-checking
  successfully would have masked the gap indefinitely, since neither
  depends on `worker`.
- **`--wait-timeout` bumped 300s → 600s:** `worker`'s image is built
  `FROM lynndelaere/dogtrace:1.1.1`, which bundles a full CUDA/torch/
  ultralytics/opencv stack — multiple GB, versus backend/frontend's much
  smaller images. A `--pull always` of a freshly-pushed worker image is
  now expected to dominate the wait time, not the db→backend→frontend
  health chain the original 300s budget was sized for.
- **No new environment variables:** `worker`'s `DATABASE_URL`/`S3_*`/
  `ANALYSIS_WORKER_*` vars were already added to `docker-compose.yml` (the
  base file, shared by every compose invocation) in ticket #47.
  `docker-compose.prod.yml` only ever overrides `image:`, never
  `environment:`, so nothing further was needed here.
- **Opt-in real-GPU/real-image test** (`worker/tests/test_real_gpu_inference.py`,
  `real_gpu` marker, excluded from the default run via `worker/pyproject.toml`'s
  `addopts` — same shape as ticket #23's `real_bucket` marker): exercises
  `RealDogTraceRunner` — the actual `dogtrace` package, not a fake —
  against a real CUDA GPU and a real C2 Cut pulled from the real S3
  bucket. Skips (never fails) on any of three independent missing
  prerequisites: `torch`/`dogtrace` unimportable (not actually running
  with the dogtrace image's dependencies available), no CUDA device
  (`torch.cuda.is_available()`), or S3 not configured (same check as
  `test_s3_client_real_bucket.py`). Neither `worker/Dockerfile` nor
  `worker/requirements.txt` installs pytest/alembic in the production
  image (ticket #47's "no FastAPI/test deps" boundary), so this test is
  run by installing `worker/requirements-dev.txt` into a shell inside a
  running `worker` container (or an equivalent GPU+dogtrace environment)
  — see the test file's own docstring for the exact command.
- **Follow-up, not decided here (per the ticket):** whether to keep
  pulling `lynndelaere/dogtrace` straight from Docker Hub in
  `deploy.yml`/`worker/Dockerfile`, or mirror it into
  `ghcr.io/lyro-studio/...` for consistency with the other three images.
- **Known limitation, accepted rather than engineered around:** a
  `workflow_dispatch` rollback to a tag from *before* this ticket merged
  will fail — `WORKER_IMAGE_REF` is required unconditionally and
  `--profile worker` is now always passed, but no
  `ghcr.io/lyro-studio/animal-behavior-worker` image was ever published at
  those older tags. Every tag from this ticket's merge onward always gets
  all three images built together (same unconditional step shape as
  backend/frontend), so this only affects rolling back to a handful of
  already-stale tags right after merge, not an ongoing risk — not worth
  the added complexity of detecting per-tag image existence for a
  self-resolving, one-time gap.

**Test DB isolation: `db_session` joins its per-test transaction via a
SAVEPOINT, not a bare `begin()` (ticket #60):** `backend/tests/conftest.py`
and `worker/tests/conftest.py`'s `db_session` fixture previously bound its
`Session` straight to a connection-level `db_connection.begin()`, then
relied on rolling that same transaction back at teardown. Most
service-layer functions (`create_analysis_job`, `claim_next_queued_job`,
`finalize_analysis_job`, `create_account`, ...) call `session.commit()`
themselves — filed as a risk that an internal commit ends the outer
transaction early, leaving the fixture's own `transaction.rollback()`
nothing left to undo, so committed test rows would persist for real in a
long-lived local Postgres instance and corrupt later runs.

- **Fix:** both fixtures now call `session.begin_nested()` (a SAVEPOINT)
  right after creating the session, plus a `session`-scoped
  `after_transaction_end` event listener that restarts the SAVEPOINT every
  time it ends — SQLAlchemy's own documented pattern for "joining a session
  into an external transaction" (`docs.sqlalchemy.org`'s
  `session_transaction.html`). An internal `commit()` now only ends the
  SAVEPOINT, never the connection-level `transaction`, regardless of how
  many times a test (or the code it calls) commits.
- **Could not reproduce the described leak directly** against a fresh,
  throwaway Postgres 16 container with the exact installed versions
  (SQLAlchemy 2.0.54, `psycopg[binary]` v3): a `Session` bound to a
  `Connection` that already has `.begin()` called on it does not appear to
  issue a real `COMMIT` on `session.commit()` in this combination (verified
  with SQLAlchemy engine echo logging and direct `psql` checks across
  repeated full-suite runs of both `backend/tests` and `worker/tests`).
  Applied the fix anyway — it's the correct, strictly-safer, standard
  pattern for this exact scenario regardless of whether this specific
  environment happens to already avoid the symptom, and guards against a
  future SQLAlchemy/psycopg point-release changing that incidental
  behavior.
- **Two new `test_db_session_isolation.py` files** (`backend/tests/`,
  `worker/tests/`) guard this going forward: each commits a row via a real
  service-layer call (`create_account`/`create_analysis_job`, not a raw ORM
  `add`), then checks it's gone through a completely independent
  connection. Self-contained within one test each — an earlier version
  split this across two tests relying on definition order (caught in
  review: running only the "did it leak" test in isolation, e.g. while
  debugging, passed trivially since nothing had committed a row yet).
  Instead, the test drives `db_session`'s actual fixture generator manually
  (via `__wrapped__`, since pytest fixture functions refuse to be called
  directly otherwise) so the real teardown this ticket is about runs
  inside the one test, before it checks the post-teardown database state.
- **Not de-duplicated between `backend/tests/conftest.py` and
  `worker/tests/conftest.py`:** both now carry the identical SAVEPOINT-join
  fixture body (already true of the fixture before this fix — worker's
  copy's docstring already said "same as backend/tests/conftest.py's
  identical fixture"). Flagged in review as duplication; not changed,
  because `worker/tests/doubles.py`'s own docstring documents *why* this is
  deliberate: neither `tests/` directory is a real package (no
  `__init__.py`), and both sit on `sys.path` during a worker test run
  (`pythonpath = [".", "../backend"]`) — Python treats them as one merged
  namespace package, so `from tests.conftest import ...` from worker/tests
  would resolve ambiguously (likely to worker's own `conftest.py`, by
  `sys.path` order) rather than reliably reaching backend's. Duplicating
  the small fixture body is safer than a cross-suite import that could
  silently resolve to the wrong module.
- **Not done here (per the ticket's own "Also clean up" note):** manually
  truncating/resetting the actual long-lived local/production Postgres
  `db` container's tables. That's a destructive action against shared,
  real data outside this repo's version control — needs the project
  owner's own explicit go-ahead and timing, not something to run
  unilaterally as part of landing this fix.

**Production architecture / auth-removal round — settled so far (pre-
implementation planning; grilling in progress, not everything below is

> **Implemented by ticket #72** — see "Application-level authentication removed (ticket #72)" at the end of this file for what actually shipped and where it deviated from the plan below.
final):**

- **Development VM vs. production VM, named:** `lynn-delaere`
  (`100.91.91.89`, Tailscale) is the development VM; `dogtrace-app`
  (`100.87.177.17`, Tailscale) is the production VM the application must
  actually run on. These are two distinct machines.
- **ADR-0003 correction — the self-hosted CI/CD runner is on the wrong
  box today:** ADR-0003 states the runner (`lynn-delaere-prod`) lives on
  "the production box." In fact (confirmed directly: the runner's own
  systemd unit is `active`/`running` on the host literally named
  `lynn-delaere`, which is the *development* VM above) it has been running
  on the development VM the whole time — so every `deploy.yml` run has
  been deploying onto the dev box, not onto a separate production VM.
  **Workflow half implemented in ticket #70 — see "CI/CD retarget" below.**
  Fix, once `dogtrace-app` is reachable (see Open Questions): register a
  **second, dedicated self-hosted runner directly on `dogtrace-app`**,
  labeled distinctly (e.g. `production-deploy`) and used only by
  `deploy.yml`'s deploy job. The existing `lynn-delaere` runner keeps
  `ci.yml`'s test/lint/build jobs (its `production` label there was always
  misleading and should be dropped). Still **no SSH anywhere** in the
  pipeline — "deploy" stays "the runner sitting on the box runs `docker
  compose up` locally," just relocated to the correct box, preserving
  ADR-0003's original no-SSH/no-tailnet-key rationale rather than
  reversing it.
- **Mechatronics is the single authentication boundary — the application
  must not authenticate its own users.** It's an external firewall in
  front of the production box that also forwards the authenticated
  person's identity via an HTTP header present on every proxied request
  (header name/exact format still unconfirmed — see Open Questions). This
  supersedes `docs/adr/0001-jwt-access-refresh-tokens.md` in full: Account/
  Admin/User role, JWT access/refresh tokens, password hashing, password
  reset, email-invite activation, and the SMTP transport that exists only
  to send those emails are all being removed. A new ADR will document this
  once implemented, marking ADR-0001 superseded rather than deleting it.
- **AnalysisJob visibility scoping is also being removed, not just the
  Admin/User role split:** issue #44's "no cross-user visibility — each
  Account only sees its own analyses" rule goes away; analysis history
  becomes fully shared (everyone past Mechatronics sees every analysis).
  The identity header is kept only for attribution, as a plain nullable
  string column directly on `AnalysisJob` (not a separate account/identity
  table — nothing in this round needs a roster of people, just a "run by"
  label per analysis).
- **No TLS between Mechatronics and the application:** the production box
  has no public IP (tailnet-only), and Mechatronics/the tailnet is already
  the trust boundary, so the new reverse proxy in front of frontend/backend
  serves plain HTTP — no certificate or domain needed.
- **Postgres/S3 split needs no migration — already correct:** `Account`,
  `RefreshToken`, `AccountActionToken`, `AnalysisJob`, `AnalysisJobVideo`,
  and `CutMediaInfo` already live in Postgres via SQLAlchemy/Alembic; S3 is
  already scoped to Cut/Dataset/report blobs only, with Postgres already
  holding the S3-key references (`report_s3_prefix`). Confirmed during
  grilling rather than assumed.

**CI/CD retarget (ticket #70):** `deploy.yml`'s deploy job now runs on the
`dogtrace-app` runner (`production-deploy` label, ticket #69) instead of the
development VM, and production config is generated from a GitHub
Environment instead of a hand-maintained file. The `.env` decisions above
that this replaces are marked superseded in place.

- **Runner labels:** `deploy.yml` → `[self-hosted, linux, x64,
  production-deploy]`; `ci.yml`'s three jobs → `[self-hosted, linux, x64,
  ci]` (was `production`). The label change on the development VM's runner
  itself is a manual GitHub-side step — it has to exist there *before* this
  merges, or CI jobs queue forever (and, since `deploy.yml` fires off CI
  succeeding, nothing deploys either). The `ci` label must never be added to
  the `dogtrace-app` runner, or CI would start executing on the production
  box — the whole point of splitting them (CI compute never sees production
  secrets or the production box).
- **`.env` is generated on every deploy** by `deploy.yml`'s "Generate .env"
  step from the `production` GitHub Environment (`environment: production`
  on the deploy job, so only that job can read it). Runs *before* any image
  build so a missing value fails in seconds. Written on the runner's own
  filesystem (no network transfer), `umask 077` then `chmod 600`, via a
  temp file + `mv` so a failed run never leaves a half-written `.env`.
  Values reach the script only as `env:` entries (never interpolated into
  script text) and errors name the variable, never its value; `secrets.*`
  are additionally masked by GitHub. Never `cat`s `.env`.
- **Values are single-quoted in `.env`** so `docker compose` reads them
  literally — verified against a real `docker compose config` with a
  password containing `$`, `"` and `#`; an unquoted `$` would be
  interpolated. A value containing a single quote or line break can't be
  represented that way, so it's rejected outright (which also blocks a
  newline from injecting extra keys) instead of written mangled. Practical
  consequence: no production secret may contain `'`.
- **`POSTGRES_PASSWORD` is further restricted to `[A-Za-z0-9._~-]`:**
  `docker-compose.yml` splices it into `DATABASE_URL` without URL-encoding, so
  `@ / : ? # %` would corrupt the URL and the backend would crash-loop unable
  to connect (found in review — the quoting check above only proves `.env`
  parsing, not that the value survives being embedded in a URL). Checked in
  the generator so it fails in seconds with a clear message instead.
- **Required vs optional:** required (job fails naming everything missing):
  `POSTGRES_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `MEDIA_TOKEN_SECRET_KEY` (secrets); `S3_BUCKET`, `S3_ENDPOINT`,
  `CORS_ORIGINS` (variables; `VITE_API_BASE_URL` was here until ticket #71
  fixed it in the workflow). Optional, omitted from
  `.env` when empty so compose's own defaults apply: `POSTGRES_USER`,
  `POSTGRES_DB`, `S3_ADDRESSING_STYLE`. `IDENTITY_HEADER_NAME` is also
  optional and omitted when empty, but it's the exception to "compose's
  defaults apply": neither compose nor the backend reads it yet, so writing
  it to `.env` is currently a no-op until ticket #72's header discovery.
- **Transitional bridge — removed by ticket #72:** the backend still reads
  its media-token signing key as `JWT_SECRET_KEY`, and `docker-compose.yml`
  still hard-requires `FIRST_ADMIN_EMAIL`/`FIRST_ADMIN_PASSWORD` (app-level
  auth isn't removed until #72). The ticket's Environment list names only
  `MEDIA_TOKEN_SECRET_KEY`, so left as-is the first deploy would fail at
  compose's `:?` checks. So the generator (a) also writes
  `JWT_SECRET_KEY` = `MEDIA_TOKEN_SECRET_KEY`, and (b) additionally
  *requires* `FIRST_ADMIN_EMAIL` (variable) and `FIRST_ADMIN_PASSWORD`
  (secret) in the Environment — required here, rather than left to compose,
  so the failure message says which GitHub Environment entry is missing.
  Ticket #72 deletes all three lines when it renames the setting and drops
  the auth variables.
- **Also passed through, optional (found in review) — removed by ticket #72:** `SMTP_HOST`/
  `SMTP_PORT`/`SMTP_USERNAME`/`SMTP_FROM_EMAIL`/`SMTP_USE_TLS` (variables),
  `SMTP_PASSWORD` (secret) and `FRONTEND_BASE_URL` (variable). The old hand-
  kept `.env` carried these; a regenerated one that silently dropped them
  would leave invite emails only logged and invite links pointing at
  `localhost:5173` until #72 removes the invite flow. Omitted when empty (an
  empty `SMTP_HOST` is a legitimate "log, don't send" choice). Token
  lifetimes and rate-limit knobs are *not* carried — compose's defaults
  already match `.env.example`, so a value only needs adding if production
  ever deviates. All of these go away with #72.
- **(Resolved by ticket #72 — `.env.example` now lists both.) `.env.example` deliberately did not yet list `MEDIA_TOKEN_SECRET_KEY`
  or `IDENTITY_HEADER_NAME`:** nothing in the application or compose reads
  either today (the backend still reads `JWT_SECRET_KEY`), so documenting
  them there now would describe variables that do nothing. Ticket #72 adds
  both when it renames the setting and starts reading the header.
- **`.env.example` must keep the auth variables until #72:** a follow-up
  commit (`d62b670`, same title as the main one) stripped 22 of them —
  `JWT_SECRET_KEY`, `FIRST_ADMIN_*`, `SMTP_*`, token lifetimes, rate-limit
  knobs — leaving `cp .env.example .env && docker compose config` failing on
  compose's `:?` checks for `JWT_SECRET_KEY`/`FIRST_ADMIN_EMAIL`/
  `FIRST_ADMIN_PASSWORD` (reproduced), and breaking ENGINEERING-STANDARDS.md
  §2's "document every required variable". Restored to the first commit's
  content, so `.env.example`'s only net change for #70 is its header. #72
  removes them together with the code that reads them.
- **`deploy.yml`'s checkout no longer sets `clean: false`:** its only
  reason was preserving a hand-placed `.env` across checkouts. With `.env`
  regenerated every run, the default clean is now a benefit — a key removed
  from the Environment can't linger from a previous deploy. `ci.yml` keeps
  `clean: false` for now (its workspace may still hold the `.env` of the
  stack earlier deploys started on the development VM); safe to drop once
  that stack is decommissioned.
- **The `production` Environment and its values are configured by hand in
  GitHub** (Settings → Environments) — a secret's value isn't something this
  repo (or an agent) should set. No `PROD_HOST`/`PROD_USER`/`PROD_SSH_KEY`/
  `PROD_PORT` secrets exist; there is no SSH step. **Recommended when
  creating it: restrict its deployment branches to `master`.** Without that,
  a `workflow_dispatch` run from any branch (a `workflow_dispatch` can target
  any ref a writer picks) could read every production secret. Not enforceable
  from this repo's YAML.
- **`image_tag` is no longer interpolated into the shell (separate commit):**
  `Determine image tag` used to splice the free-text `image_tag` dispatch
  input straight into a `run:` script on the production-adjacent runner.
  Pre-existing rather than introduced here, but this job now also holds
  production secrets, so it was fixed alongside: values go through `env:`,
  and a rollback tag must match Docker's tag charset (also because it flows
  into multi-line `tags:` inputs).
- **"Fail loudly on a failed migration" holds, but slowly:** migrations run
  as `alembic upgrade head && uvicorn` in the backend image's `CMD`, so a
  failing migration exits the container; with `restart: unless-stopped` it
  then restart-loops rather than staying down, never turns healthy, and
  `up --wait` fails at `--wait-timeout` (up to 10 minutes) — nonzero exit,
  red run, but not instant, and the old backend container is already gone by
  then (ADR-0003's accepted outage window, ticket #41). Unchanged existing
  behaviour; not verified against a real failing migration.
- **Verification is operational, per the ticket:** no unit-test seam (YAML +
  shell). The generator script was exercised locally (extracted verbatim from
  the workflow) for: happy path (mode `600`, no leftover temp file),
  missing-variable failure, single-quote and newline rejection, and a
  `docker compose config` round-trip. Still to confirm after merge, on the
  real runner: a `workflow_dispatch`/`master` run succeeding, `docker compose
  ps` healthy on `dogtrace-app`, and the run showing on the `dogtrace-app`
  runner in GitHub's own UI.
- **Follow-up, not done here:** ADR-0003's body still describes one runner on
  one box; it now carries an amendment note pointing at this section rather
  than being rewritten, since the ADR records the original reasoning.

**Application-level authentication removed (ticket #72):** the app no longer
authenticates anyone. Mechatronics authenticates the person in front of it and
forwards their identity in an HTTP header; the backend reads that purely for
attribution. Supersedes ADR-0001 in full (see
`docs/adr/0004-trust-mega-tronics-remove-application-auth.md`). Removed:
login/refresh/forgot-password/set-password/invite/`accounts`/`admin` endpoints,
the Admin/User role, password hashing, access/refresh JWTs, SMTP, the first-
admin seeding, the login/reset rate limits, and on the frontend the login/
forgot-password/set-password/Admin views, the session store and every
`Authorization` header. `argon2-cffi` and `email-validator` are dropped from
`backend/requirements.txt`.

- **Identity is read by one FastAPI dependency (`get_identity`, `app/api/deps.py`),
  configured by `IDENTITY_HEADER_NAME`.** Unset or empty (`.env` may carry
  `IDENTITY_HEADER_NAME=`) means "read nothing" — also what local development
  runs with. The value is untrusted input, so it is trimmed, a blank value is
  treated as absent, and it is *truncated* (not rejected) at 320 characters —
  the `requested_by_identity` column length. Truncating rather than 400ing
  means an oversized header can neither 500 from the database nor lock someone
  out of the whole app over a header they don't control. There is deliberately
  no check that the header really came from Mechatronics (ADR-0004: the network
  topology is what makes that safe).
- **`GET /whoami` echoes the identity back** (`{"identity": string | null}`) so
  Home can show "Signed in as …". Served as `/api/whoami` since ticket #71 (it had no prefix
  when #72 shipped). A failed lookup just hides the line on Home.
- **`AnalysisJob.requested_by` (FK to `accounts`) became `requested_by_identity`**
  (`String(320)`, nullable, no FK) and is exposed on `AnalysisJobOut` so history
  and detail views can show who ran each analysis. Every analysis is now visible
  to, and cancellable and downloadable by, everyone (issue #44's "no cross-user
  visibility" decision is gone).
- **`list_analysis_jobs` is now capped at the newest 500** (`MAX_LISTED_ANALYSIS_JOBS`)
  and eager-loads each job's videos: with per-account scoping gone it would
  otherwise be an unbounded query over every job ever run, plus one query per
  row (ENGINEERING-STANDARDS.md §5, DoS). Not in the issue; a direct
  consequence of it. Real pagination is deferred until the cap is ever hit.
- **Migration `0006_remove_application_auth`** adds `requested_by_identity`,
  backfills it from `accounts.email` (deactivated accounts included), *then*
  drops `requested_by`, the three auth tables and the `account_role` /
  `account_action_token_purpose` enum types. **Irreversible** — `downgrade()`
  raises, since the account rows and password hashes can't be reconstructed.
  `test_migration_remove_application_auth.py` migrates a scratch database to
  `0005`, seeds accounts/tokens/jobs, upgrades, and asserts both the backfill
  and the drops; the scratch database is created on the test server, so it
  needs `CREATEDB` (CI's Postgres user is a superuser).
- **Rate limits are keyed by identity, and never skipped.** The media-token and
  `/media/cuts/info` limits (`*_per_identity` settings, renamed from
  `*_per_account`; not wired through compose or `.env.example`, so nothing else
  to rename) use `identity_rate_limit_key`. With no identity every caller shares
  one `anonymous` bucket rather than the check being skipped: a limit that
  vanishes whenever the header is missing would leave the expensive operations
  it guards unprotected exactly when something is misconfigured. **Production
  consequence to be aware of:** until `IDENTITY_HEADER_NAME` is set on
  `dogtrace-app`, *every real user* falls into that one shared bucket — 30 token
  mints and 30 `/media/cuts/info` calls per 5 minutes for the whole group, each
  Play/Download/Info click consuming one. That's a real availability risk
  (ENGINEERING-STANDARDS.md §5: controls must not become an easy DoS against
  legitimate users), accepted here only because the alternatives were worse
  (skip the limit, or key on a client IP that is just the proxy's). Set the
  header name promptly after discovery, or raise
  `MEDIA_TOKEN_RATE_LIMIT_MAX_ATTEMPTS_PER_IDENTITY` /
  `CUT_INFO_RATE_LIMIT_MAX_ATTEMPTS_PER_IDENTITY` (settings, not yet wired
  through compose) in the meantime. Anyone who can reach the backend directly can
  also rotate identity values to dodge a per-identity limit — accepted under
  ADR-0004's "reaching the backend at all means you passed Mechatronics"
  premise. Expired windows are pruned in O(1) per hit, so memory is bounded by
  the number of distinct keys seen within one window, not by history — not an
  absolute cap. `RateLimiter.peek` (login's pre-auth check) had no other caller
  and was removed.
- **Media tokens: "kept, renamed" — the issue's default — because the gate could
  not be run.** Whether a native `<video src>`/`<a download>` request carries
  Mechatronics' identity header needs a live `dogtrace-app`, which this work had
  no access to. So `create_media_token`/`decode_media_token` stay, signed with
  `media_token_secret_key` (env `MEDIA_TOKEN_SECRET_KEY`, renamed from
  `jwt_secret_key`), and `POST /media/cuts/token` and the stream endpoint work as
  before. Consequence to keep in mind (see ADR-0002's amendment): with login
  gone `POST /media/cuts/token` is itself open to anyone who can reach the
  backend, so a token buys only a 15-minute single-Cut scope, not access
  control. **Still to do, by a human:** run the gate on `dogtrace-app`; if it
  passes, follow the "If the gate passes" list in issue #72 (delete the token
  mechanism, key the stream endpoint on the Cut key directly with its own shape
  validation, decide explicitly whether the stream endpoint or Nginx/
  Mechatronics owns its DoS limit, drop `MEDIA_TOKEN_SECRET_KEY`).
- **Resolved by ticket #71 — the backend port is no longer published.** (Cited
  there and in the PR as "ticket #3", which is actually the closed Login &
  session ticket; the reverse-proxy work is #71.) It used to be open:
  `docker-compose.yml` mapped `8000:8000` on the `backend` service. ADR-0004 calls the network topology
  "load-bearing" (only Mechatronics' routed path should reach the backend, since
  the identity header is trusted unverified), so a host-published backend port is
  now part of what that decision depends on. Left alone in #72 as reverse-proxy
  work, and closed by #71.
- **The 500-row cap is silent.** `list_analysis_jobs` drops jobs beyond the newest
  500 with no indication in the History or per-Test views, which sits awkwardly
  with "see every analysis that's been run". Fine at today's volume; the fix is
  pagination or a "showing newest 500" hint, not raising the cap indefinitely.
- **Identity attribution is the trimmed/truncated header, not the raw one** the
  issue describes; truncation at 320 characters can alter a (pathologically
  long) value silently. Chosen over rejecting the request for the reasons above.
- **CORS:** `allow_credentials` is unset (it always was) — there is no cookie or
  session for a cross-origin page to ride on. `CORS_ORIGINS` narrowing to the
  production origin is a deploy-configuration matter (the `production`
  Environment's `CORS_ORIGINS` variable), not a code change.
- **The worker no longer logs an owner.** Its log lines carried the numeric
  `account_id`; the replacement would be an identity that is likely an email
  address, i.e. personal data in logs. `analysis_id` already lets anyone look the
  owner up in the database, so the field was dropped instead of replaced.
- **Deploy-side cleanup done in this ticket** (the #70 transitional bridge):
  `deploy.yml`'s "Generate .env" no longer requires `FIRST_ADMIN_EMAIL`/
  `FIRST_ADMIN_PASSWORD`, no longer passes `SMTP_*`/`FRONTEND_BASE_URL` through,
  and no longer writes the `JWT_SECRET_KEY` alias — `MEDIA_TOKEN_SECRET_KEY` is
  written under its own name. `docker-compose.yml`'s `backend` service drops the
  JWT/first-admin/SMTP/token-lifetime/login-and-reset-rate-limit variables and
  gains `MEDIA_TOKEN_SECRET_KEY` (required) and `IDENTITY_HEADER_NAME` (optional).
  `.env.example` lists both new variables. The generator script was re-run
  locally (extracted verbatim from the workflow): happy path (mode `600`, no
  temp file left), a missing `MEDIA_TOKEN_SECRET_KEY` failing by name, and a
  clean-environment `docker compose config` read-back of a `$`/`"`/`#`-bearing
  secret.
- **Manual GitHub steps still to do** (an agent shouldn't set or delete
  Environment secrets): in the `production` Environment delete the now-unused
  `FIRST_ADMIN_EMAIL`, `FIRST_ADMIN_PASSWORD`, `SMTP_PASSWORD`, `SMTP_HOST`,
  `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS` and
  `FRONTEND_BASE_URL`; keep `MEDIA_TOKEN_SECRET_KEY` (see the media-token
  decision above); set `IDENTITY_HEADER_NAME` once the real header is known.
  **Order matters on the first deploy of this change:** the old application
  still expects the removed variables, but the new `.env` no longer provides
  them, so deploy the new images together with this workflow (they ship in the
  same merge) rather than redeploying an old tag afterwards — a rollback
  `workflow_dispatch` to a pre-#72 tag will fail at `docker compose`'s `:?`
  checks for `JWT_SECRET_KEY`/`FIRST_ADMIN_*`, the same one-time,
  self-resolving gap ticket #54 accepted for its own rollback limitation. And the
  migration cannot be rolled back, so a pre-#72 image would not find its
  `accounts` table anyway.
- **Identity-header discovery is still open.** The real header name is unknown
  until `dogtrace-app` is live behind Mechatronics. The issue's suggested
  temporary `GET /api/debug/request-headers` diagnostic was **not** added: an
  endpoint echoing every request header would also echo any credential-bearing
  header Mechatronics forwards, and shipping it to reach a production box was
  not something to do unilaterally. Until it's known, `IDENTITY_HEADER_NAME`
  stays empty and analyses are attributed to no one (`requested_by_identity`
  `null`); analyses that existed before this change keep the email they were
  backfilled with.
- **Amendments to earlier decisions.** "Analysis report download (ticket #49)"
  and "Analysis detail/progress view (ticket #52)" describe the report download
  as bearer-authenticated and the view as handling an expired session — there is
  no bearer token or session any more; the download is a plain `fetch` + `Blob`
  and `AnalysisView` simply retries generic errors (the 401 / silent-refresh
  handling, ticket #63's territory, was deleted with the mechanism it served).
  "Analyses history (ticket #53)" lists every analysis, not "the current
  Account's own".

**Reverse proxy and `/api` prefix (ticket #71):** the site is reached at
`https://dogtrace.mechatronics.be` — Mechatronics terminates TLS and forwards
to `dogtrace-app`, port 5173. Everything is now one origin: nginx serves `/`
from the frontend container and `/api/` from the backend container.

- **Found after ticket #72 deployed, not designed in advance:** the frontend
  was built with `VITE_API_BASE_URL=http://100.91.91.89:8000` — the
  *development* VM's address — so every API call from the HTTPS page was an
  `http://` request to a host and port Mechatronics doesn't route, blocked by
  the browser as mixed content ("NetworkError when attempting to fetch
  resource" in the UI). Correcting the IP would not have helped: no absolute
  `http://` backend URL can work from that page, and the backend port isn't
  reachable through Mechatronics at all.
- **Every backend route is mounted under `/api`** (`app/main.py`'s
  `API_PREFIX`), including the interactive docs (`/api/docs`,
  `/api/openapi.json`) — otherwise `/docs` would fall through to the
  frontend's SPA fallback. The proxy therefore never rewrites a path. There is
  no unprefixed alias (tested); the backend healthcheck is `/api/health`.
- **nginx is a config-only image, not a bind-mounted file:** `nginx/Dockerfile`
  copies `nginx/default.conf` into `nginx:1.27-alpine`, and `deploy.yml` builds
  and pushes it as `ghcr.io/lyro-studio/animal-behavior-nginx` next to the
  other three (`NGINX_IMAGE_REF` in `docker-compose.prod.yml`). ENGINEERING-
  STANDARDS.md §3 wants production on immutable images built from
  Dockerfiles, and a rollback tag then carries the nginx config that matched
  it instead of whatever is on the checked-out branch. The issue said only "a
  new `nginx` service"; this is an implementation choice within that.
- **Only nginx is published.** The base `docker-compose.yml` gives
  `backend`/`frontend`/`nginx` no host ports; `docker-compose.prod.yml`
  publishes nginx on `5173` — the port Mechatronics already forwards to, so
  nothing changes on their side. The issue also wanted local development to
  keep using `:5173`/`:8000` directly, which contradicts dropping the ports
  from the base file; reconciled with `docker-compose.local.yml`, selected by
  `COMPOSE_FILE` in the developer's `.env` (`.env.example` sets it) and never
  present in production's generated `.env`. It re-publishes `8000`/`5173` and
  adds the proxied entry point on `8080` (only meaningful when the frontend
  was built with `VITE_API_BASE_URL=/api`, since the default bakes in the
  direct URL). It is deliberately not `docker-compose.override.yml`: compose
  merges that automatically, so a hand-run `docker compose up` on
  `dogtrace-app` would have re-published the backend port and bypassed
  nginx/Mechatronics (found in review). Cost: an existing local `.env` needs
  `COMPOSE_FILE`/`COMPOSE_PATH_SEPARATOR` added and `VITE_API_BASE_URL`
  changed to `.../api` (README's "Upgrading an existing `.env`").
- **`VITE_API_BASE_URL` is a fixed `/api` in `deploy.yml`, not an Environment
  variable** — it was the misconfiguration behind the outage above, and with
  one origin there is only one correct value. It is dropped from the required
  list and from the generated `.env`; the `production` Environment's copy of
  the variable can be deleted. `CORS_ORIGINS`, unused for the same reason,
  moved from required to optional. The frontend's local default is now
  `http://localhost:8000/api`.
- **The frontend resolves a relative base against the page origin**
  (`apiUrl()` in `services/apiBase.ts`): four call sites did `new URL(
  API_BASE_URL + path)`, which throws for a relative base like `/api`.
- **nginx behaviour worth knowing:** upstreams are resolved per request via
  Docker's DNS (`resolver 127.0.0.11` + variables in `proxy_pass`), so a
  redeployed backend/frontend container with a new IP doesn't leave nginx
  proxying to a dead address; request bodies are capped at 1 MB (all are small
  JSON, ENGINEERING-STANDARDS.md §5); `/api/` is unbuffered so a Cut streamed
  with Range requests isn't spooled to disk; `/api/` reads may take up to 120 s
  (`/api/media/cuts/info` downloads a Cut and runs `ffprobe`);
  `underscores_in_headers on` so an identity header whose name contains
  underscores (still unknown, ticket #72) isn't silently dropped. Mechatronics'
  identity header is forwarded untouched — no `proxy_set_header` overrides it.
- **The media token stays out of nginx's logs (ADR-0002):** the stream
  endpoint's `token` query parameter would otherwise be written verbatim by
  nginx's default access log. For `/api/media/stream`, `default.conf` logs the
  normalised path only and drops the query string entirely (matched on `$uri`,
  so an encoded path can't dodge it) — a first version that blanked `token=`
  with a regex leaked when the parameter was repeated (found in review).
  nginx's *error* log quotes the request line
  on upstream failures and can't be redacted, so it is set to `crit`: a failed
  upstream still shows as a 502 in the access log, with the cause in the
  backend's own log. Accepted trade-off.
- **`X-Forwarded-For` is still not trusted.** There is a proxy now (the note
  under "Brute-force throttling" above says there was none), but the only
  rate limits left are keyed per identity, not per IP, so nothing reads it.
- **Tested:** `nginx/test.sh` (a CI job, `nginx`) builds the image, puts it in
  front of two stub upstreams on a throwaway Docker network, and checks: it
  starts with no upstream and finds them later; `/` -> frontend, `/api/...` ->
  backend with the path untouched; the identity header, `Host` (with port) and
  `Range` reach the backend; `/api` redirects relatively; a 2 MB body is 413;
  and no `token=` value — including a repeated one, a percent-encoded path
  and a token-first query — appears in nginx's log. Run against the first
  version of the redaction (a regex over the request URI) it caught a
  repeated-`token=` leak that code review had found, so the check can fail.
  **Not exercised:** the real backend/frontend containers behind it, or TLS
  and Mechatronics. After deploying, check `https://dogtrace.mechatronics.be/`
  and `https://dogtrace.mechatronics.be/api/health`, then that a Cut plays and
  seeks.
- **Known limitation, accepted:** a rollback `workflow_dispatch` to a tag from
  before this ticket fails (no `animal-behavior-nginx` image exists at those
  tags, and those frontend images call an absolute backend URL) — the same
  kind of one-time gap tickets #54 and #72 accepted.
- **Follow-up, not done here:** `db` still publishes `5432:5432` on the
  production box. Out of this ticket's scope (it was never routed through
  anything), but it sits awkwardly with ADR-0004's "only Mechatronics' route
  should reach the app's data" and should be closed the same way.
