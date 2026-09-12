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

**Semantic UI colors:** `success`/`warning`/`danger` keep the original
ENGINEERING-STANDARDS.md hex values (`#16A34A`/`#D97706`/`#DC2626`) as a
scoped exception to the VIVES-palette styling deviation — the house style
defines no equivalent, and its 6 study-domain accent colors are reserved
for identifying study domains only.

**Account removal:** Soft-delete (deactivate) rather than hard-delete —
login is blocked, the row is kept. A deactivated Account's email is
excluded from the uniqueness check, so it can be reused by a new Account.

**Admin creation:** Only User accounts can be created/deactivated through
the app; there is exactly one Admin (the `.env`-seeded one) for this
round. The `role` field itself isn't restricted to a single Admin at the
data layer — there's just no UI/endpoint to create a second one yet.

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
