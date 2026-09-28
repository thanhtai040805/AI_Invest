# Rule 07: Branch, Review, and Deployment Workflow

For every feature, fix, or maintenance task, work on a dedicated branch and
open a pull request to `main`. Do not commit or push task changes directly to
`main`; `main` is updated only by merging a pull request after its required CI
checks pass. Use `feat/<short-name>` for features, `fix/<short-name>` for fixes,
and `codex/<short-name>` for other agent-owned work.

CI runs on pull requests and pushes to `main`, `feat/**`, `fix/**`, and
`codex/**`. Release images are published only from a successful CI run on
`main`, tagged with the full commit SHA in GitHub Container Registry (GHCR).
Deploy only a SHA whose CI passed and whose four release images exist in GHCR;
production deployment is started manually through the `Deploy to VPS` workflow.

The deploy script keeps a database backup under `/opt/aiinvest-backups` before
applying pending database migrations. These backups are separate from the
container images and are created only when such a deployment runs.
