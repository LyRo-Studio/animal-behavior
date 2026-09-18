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
`GET /media/cuts/info` (ticket #22) gets the same per-Account limiter — a
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
- The frontend image's `VITE_API_BASE_URL` build-arg (baked in at build
  time, per `frontend/Dockerfile`) is read from the production box's own
  `.env` rather than duplicated into a GitHub Secret — consistent with
  `.env` already being the one place production config lives. It isn't a
  secret itself, so surfacing it via `GITHUB_OUTPUT` is safe; nothing else
  from `.env` is echoed.
- Both `ci.yml` and `deploy.yml`'s checkout steps set `clean: false`.
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
