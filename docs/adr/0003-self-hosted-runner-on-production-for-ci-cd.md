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
- Rollback is a manual `workflow_dispatch` step, not automatic — the deploy step already keeps the
  previous containers running until the new ones pass their health check, so a failed deploy alone
  never takes production down; automatically redeploying an older tag on top of that was judged
  more likely to cause churn (e.g. against a transient external failure) than to help.
- No staging environment exists — every merge to `master` deploys straight to production. Revisit
  if this app ever needs to protect real users' uptime expectations the current internal-only
  scope doesn't have.
