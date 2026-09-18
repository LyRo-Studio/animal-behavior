# Run CI/CD on a self-hosted GitHub Actions runner installed directly on the production box

**Status:** accepted

The application's only production environment is a single machine (reachable over Tailscale, no
public IP) — there is no cloud host, load balancer, or existing deployment pipeline. GitHub's
own cloud-hosted Actions runners have no network route into that machine, so deploying from them
would require either exposing SSH publicly (undermining the deliberate choice to keep this box
off the public internet) or joining an ephemeral runner to the tailnet on every workflow run
(`tailscale/github-action` + a stored auth key + tailnet ACLs).

Instead, CI and CD both run on a single self-hosted GitHub Actions runner installed on the
production box itself, under a dedicated non-root service account (Docker-group membership only,
no `root`). A deploy step is then just `docker compose up -d --wait` executed locally — no SSH
keys, no Tailscale auth key, no network configuration to maintain at all.

This trades runner isolation for operational simplicity: at this project's current scale (small,
mostly-solo team; a single low-traffic internal app) the isolation GitHub-hosted runners would
normally provide is judged not worth the extra infrastructure (key rotation, tailnet config, a
second class of runner to reason about). Revisit if CI load or team size grows enough that sharing
the box with production becomes a real contention problem, or if the trust boundary changes (e.g.
outside contributors start opening PRs).

## Consequences

- CI (including a PR's test/lint/build steps) now runs with implicit proximity to the real
  production `.env`, the Docker daemon, and the production database/containers — a bad dependency
  or a buggy test has a materially larger blast radius here than on an isolated cloud runner.
  Mitigated by: the dedicated non-root service account, requiring a PR (never a direct push) for
  every change to reach the runner's deploy path at all, and workflow steps never echoing `.env`
  or process-environment contents to logs.
- Production availability now depends on this one runner process staying registered and alive on
  this one box — there's no separate CI infrastructure to fall back on if it goes down. Redeploying
  it means re-running the one-time setup (create the service account, install/register the runner)
  documented alongside this ADR.
- Because the same job that builds an image can also run it, a container registry (GHCR) is not
  strictly required for deployment to work — it was still adopted anyway, purely for rollback
  traceability (redeploy a past `sha-<commit>` tag without rebuilding) and to answer "what's
  running in production right now" precisely. This is a separate decision from the runner
  placement above, but the two were adopted together.
- Rollback is a manual `workflow_dispatch` step, not automatic. Note this does *not* rest on a
  failed deploy leaving production untouched — it doesn't (see below); automatically redeploying an
  older tag on a failure was judged more likely to cause churn (e.g. against a transient external
  failure) than to help, independent of whether that failure caused an outage.
- **A failed deploy causes a brief real outage, not a silent no-op (ticket #41):** `backend`/
  `frontend` publish fixed host ports, so `docker compose up` must stop each service's old container
  before the new one can bind that port — there is no window where old and new run side by side.
  If a newly deployed image fails its healthcheck, `--wait` fails the workflow (so the failure *is*
  reported), but the affected service's *old* container is already gone by then — production is
  briefly down for that service, not "still serving traffic on the old version." `db` is unaffected
  (same image every deploy, so compose never recreates it). Accepted at current scale, consistent
  with this ADR's own solo-team/low-traffic trade-off above; would need a reverse proxy in front of
  backend/frontend (health-check a new container on a separate port, then cut traffic over before
  tearing down the old one) to close this gap — revisit if it becomes unacceptable.
- No staging environment exists — every merge to `master` deploys straight to production. Revisit
  if this app ever needs to protect real users' uptime expectations the current internal-only
  scope doesn't have.

## Amendment (ticket #70): two runners, not one — the original premise was wrong

This ADR says the runner lives "on the production box." It never did: the
runner (`lynn-delaere-prod`) was installed on the *development* VM
(`lynn-delaere`), so every deploy landed there rather than on a separate
production machine. The no-SSH, self-hosted-runner reasoning above still
holds, but it's now applied to two boxes with two roles:

- **`lynn-delaere` runner** (label `ci`): runs `ci.yml`'s test/lint/build jobs
  only. It never receives production secrets and never deploys.
- **`dogtrace-app` runner** (label `production-deploy`, the real production
  VM): runs `deploy.yml`'s deploy job only, and is the only place the
  `production` GitHub Environment's secrets are ever read.

This also narrows the first bullet under "Consequences": CI (including a PR's
test/lint/build steps) no longer runs beside production's `.env`, Docker
daemon, or database — that blast-radius concern now applies to the
development VM only. See `CONTEXT.md`'s "CI/CD retarget (ticket #70)".
