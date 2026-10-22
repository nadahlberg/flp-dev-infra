# flp-dev-infra

Disposable, production-shaped deployments of CourtListener and its MCP
server on DigitalOcean, any number of versions side by side, for testing
OAuth and multi-pod behaviour with real MCP clients. Managed with Pulumi;
deployed from GitHub Actions. See [plan.md](plan.md) for the why, the design
and the roadmap.

Every deploy is a **slug** in [infra/deploys.py](infra/deploys.py) and gets a
stable hostname under `flp-dev.nates.ai`:

| Slug kind     | Hostname                          |
|---------------|-----------------------------------|
| CourtListener | `https://cl-<slug>.flp-dev.nates.ai`  |
| MCP server    | `https://mcp-<slug>.flp-dev.nates.ai` |

Adding, changing or removing a deploy is an edit to that file and a merged
PR. Nothing else: no DNS, no workflow inputs, no secrets.

## Layout

- `infra/` — the Pulumi program (Python, uv). Stack: `dev`.
  - `deploys.py` — the config: one `CourtListenerDeploy` / `McpDeploy` per
    slug (repo, git ref, env overrides, bootstrap options, which slug it
    points at).
  - `checkout.py` — fetches every (repo, ref) the config names into
    `../sources` and writes `manifest.json` (resolved shas). Run before
    Pulumi, locally and in CI.
  - `core/` — the DO project (`flp-dev`), Kubernetes cluster, managed
    Postgres, ingress-nginx + its load balancer, cert-manager, the shared
    DNS zone with its wildcard record, GHCR image helpers, the sources
    manifest reader.
  - `courtlistener/` — `deploy(slug, ...)`: namespace, redis, elasticsearch,
    Spaces buckets, the settings overlay and bootstrap/seed scripts, the
    bootstrap Job, web + celery, ingress.
  - `mcp/` — `deploy(slug, ...)`: namespace, redis, the server (2+ replicas),
    ingress.
  - `__main__.py` — builds one image per (repo, sha), generates one OAuth
    client pair per MCP slug, deploys every slug, assigns DO resources.
- `.github/workflows/ci.yml` — `pulumi preview` on every PR (the `preview`
  check the ruleset requires).
- `.github/workflows/deploy.yml` — `pulumi up` when a PR merges into main,
  or by hand.

## Conventions

Borrowed from nadahlberg/infra:

- **Naming: `<project>-<resource>`.** Shared resources use `flp-dev`
  (`flp-dev-k8s`, `flp-dev-db`, `flp-dev-lb`, `flp-dev-zone`); a slug's
  resources use `cl-<slug>-` and `mcp-<slug>-`. Kubernetes objects get
  short, stable names (`web`, `celery`, `redis`, `elasticsearch`, `mcp`)
  inside the slug's namespace (`cl-<slug>`, `mcp-<slug>`).
- **Core owns, slugs point.** Slugs add logical databases (`cl_<slug>`,
  `mcp_<slug>`) to `flp-dev-db` and Ingress rules behind `flp-dev-lb`; they
  never create their own cluster, LB or database cluster. Apps connect as
  the shared admin user. Each CourtListener slug has its own Redis,
  Elasticsearch and Spaces buckets; each MCP slug its own Redis.
- **A git ref is the unit of deployment.** Branch, tag or sha. Images are
  tagged with the resolved short sha, so slugs on the same sha share one
  image and an unchanged sha does not rebuild. A branch ref redeploys when
  the branch moves (on the next merge here, or a manual dispatch).
- **Secrets are passed through explicitly.** The workflows name every secret
  they expose; modules read each one with `os.getenv`. Pulumi generates the
  rest (Django secret key, OIDC key, per-pair OAuth client credentials, MCP
  keys).
- **Images on GHCR**, `ghcr.io/nadahlberg/flp-dev-{courtlistener,mcp}:<sha>`.
  DO allows one registry per account and the infra repo has it.
- **Deploy modules return a dataclass** (`host`, `namespace`,
  `project_urns`); `__main__.py` assigns the URNs to the DO project.

## Secrets and variables

GitHub Actions secrets on this repo:

| Secret                | Purpose                                                       |
|-----------------------|---------------------------------------------------------------|
| `PULUMI_ACCESS_TOKEN` | Pulumi Cloud                                                  |
| `DIGITALOCEAN_TOKEN`  | DigitalOcean API                                              |
| `GH_TOKEN`            | Classic PAT: `repo`, `read:packages`, `write:packages`. Fetches app sources, pushes images, backs the cluster's pull secrets |
| `DEV_USER_EMAIL`      | Email of the CL superuser every slug's bootstrap creates      |
| `DEV_USER_PASSWORD`   | Its password, also used for `test1..testN`                    |
| `CL_API_TOKEN`        | Optional. courtlistener.com token for `clone_from_cl`         |
| `CL_SENTRY_DSN`       | Optional                                                      |
| `MCP_SENTRY_DSN`      | Optional                                                      |

Variable (optional): `DEV_USER_NAME` (default `dev`). Locally the same
names live in `.env` (gitignored); see `.env.example`.

## Deploying

Merging a PR into main deploys. Typical changes to `infra/deploys.py`:

- **Deploy a branch**: add a slug with `ref="my-branch"`.
- **Pin a version**: `ref="<sha>"`; it never moves.
- **Point an MCP at a CourtListener**: `McpDeploy(ref=..., courtlistener="<cl slug>")`.
  The CL side registers that MCP's OAuth client automatically.
- **Tell a CourtListener which MCP gets its DCR clients**:
  `CourtListenerDeploy(..., mcp="<mcp slug>")`.
- **Override environment**: `env={"SOME_VAR": "value"}` on either kind.
- **Change the bootstrap**: `seed_dev_data`, `index_search`, `test_users`,
  `bootstrap_extra=("./manage.py something",)`.
- **Remove a deploy**: delete the slug. Pulumi deletes its namespace,
  database, buckets and certificates.

Locally:

```sh
cd infra
set -a; source ../.env; set +a
uv run python checkout.py          # fetches sources into ../sources
pulumi up --stack dev
```

Pulumi config (optional): `region` (nyc3), `nodeSize` (s-4vcpu-8gb),
`nodeCount` (3).

## Operating

```sh
doctl kubernetes cluster kubeconfig save flp-dev-k8s
kubectl get ns                                          # cl-<slug>, mcp-<slug>
kubectl -n cl-main get pods
kubectl -n cl-main logs job/<bootstrap job>             # migrate/seed output
kubectl -n cl-main exec deploy/web -- ./manage.py shell
kubectl -n mcp-main get pods -o wide                    # which node each MCP pod is on
kubectl -n mcp-main logs deploy/mcp --all-containers -f
pulumi stack output                                     # every slug's URL and sha
```

The bootstrap Job re-runs on every deploy of a slug: migrations,
`collectstatic`, `seed.py` (users, the OAuth applications of the MCP slugs
pointing at it, `make_dev_data` on an empty database when `seed_dev_data`),
the Elasticsearch index commands when `index_search`, then the slug's
`bootstrap_extra` commands.

`pulumi destroy --stack dev` removes everything, including the DO project.
