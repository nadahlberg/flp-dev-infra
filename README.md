# flp-dev-infra

A disposable, production-shaped deployment of CourtListener and its MCP
server on DigitalOcean, for testing OAuth and multi-pod behaviour with real
MCP clients. Managed with Pulumi; deployed from GitHub Actions. See
[plan.md](plan.md) for the why and the roadmap.

| Service       | URL                     |
|---------------|-------------------------|
| CourtListener | https://cl.nates.ai     |
| MCP server    | https://cl-mcp.nates.ai |

## Layout

- `infra/` — the Pulumi program (Python, uv). Stack: `dev`.
  - `core/` — the DO project (`flp-dev`), Kubernetes cluster, managed
    Postgres, ingress-nginx + its load balancer, cert-manager, and helpers
    for GHCR images and subdomain DNS.
  - `courtlistener/` — the CL namespace: redis, elasticsearch, Spaces
    buckets, the settings overlay and bootstrap/seed scripts, the bootstrap
    Job, web + celery, ingress, DNS.
  - `mcp/` — the MCP namespace: redis, the server (2+ replicas), ingress, DNS.
- `.github/workflows/ci.yml` — `pulumi preview` on every PR and push to main.
- `.github/workflows/deploy.yml` — `pulumi up` after ci succeeds on main, or
  by hand with a choice of app repo/ref.

## Conventions

Borrowed from nadahlberg/infra:

- **Naming: `<project>-<resource>`.** Shared resources use `flp-dev`
  (`flp-dev-k8s`, `flp-dev-db`, `flp-dev-lb`); app resources use `cl-` and
  `mcp-`. Kubernetes objects get short, stable names (`web`, `celery`,
  `redis`, `elasticsearch`, `mcp`) inside per-app namespaces.
- **Core owns, apps point.** Apps add logical databases to `flp-dev-db` and
  Ingress rules behind `flp-dev-lb`; they never create their own cluster, LB
  or database cluster. Apps connect as the shared admin user.
- **Secrets are passed through explicitly.** The workflows name every secret
  they expose; modules read each one with `os.getenv`. Pulumi generates the
  rest (Django secret key, OIDC key, OAuth client credentials, MCP keys).
- **Images on GHCR**, `ghcr.io/nadahlberg/flp-dev-{courtlistener,mcp}`,
  tagged `<app short sha>-<run number>`. DO allows one registry per account
  and the infra repo has it.
- **Each app module exposes `PROJECT_URNS`**; `__main__.py` assigns them to
  the DO project.

## Secrets and variables

GitHub Actions secrets on this repo:

| Secret                | Purpose                                                       |
|-----------------------|---------------------------------------------------------------|
| `PULUMI_ACCESS_TOKEN` | Pulumi Cloud                                                  |
| `DIGITALOCEAN_TOKEN`  | DigitalOcean API                                              |
| `GH_TOKEN`            | Classic PAT: `repo`, `read:packages`, `write:packages`. Checks out the app repos, pushes images, backs the cluster's pull secret |
| `DEV_USER_EMAIL`      | Email of the CL superuser the bootstrap creates               |
| `DEV_USER_PASSWORD`   | Its password, also used for `test1..test3`                    |
| `CL_API_TOKEN`        | Optional. courtlistener.com token for `clone_from_cl`         |
| `CL_SENTRY_DSN`       | Optional                                                      |
| `MCP_SENTRY_DSN`      | Optional                                                      |

Repository variables (all optional): `DEV_USER_NAME` (default `dev`),
`CL_REPO`, `CL_REF`, `MCP_REPO`, `MCP_REF` (defaults: `freelawproject/*` at
`main`). Set a `*_REF` variable to keep deploying a branch; dispatch the
workflow with inputs for a one-off.

Locally the same names live in `.env` (gitignored); see `.env.example`.

## Deploying

Merging to main deploys. To deploy a branch of either app without touching
this repo, run the `deploy` workflow by hand and fill in the ref (and repo,
for a fork).

Locally, with the app checkouts beside this repo:

```sh
cd infra
set -a; source ../.env; set +a
pulumi up --stack dev
```

Pulumi config (optional): `region` (nyc3), `nodeSize` (s-4vcpu-8gb),
`nodeCount` (2), `mcpReplicas` (2).

## Operating

```sh
doctl kubernetes cluster kubeconfig save flp-dev-k8s
kubectl -n courtlistener get pods
kubectl -n courtlistener logs job/<bootstrap job>          # migrate/seed output
kubectl -n courtlistener exec deploy/web -- ./manage.py shell
kubectl -n mcp get pods -o wide                             # which node each MCP pod is on
kubectl -n mcp logs deploy/mcp --all-containers -f
```

The bootstrap Job re-runs on every deploy: migrations, `collectstatic`,
`seed.py` (users, the MCP's OAuth application, `make_dev_data` on an empty
database) and the Elasticsearch index commands. Edit
`infra/courtlistener/seed.py` to change what gets seeded.

`pulumi destroy --stack dev` removes everything, including the DO project.
