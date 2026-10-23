# flp-dev-infra

A dev tunnel. Expose a port on your machine at a stable public HTTPS
hostname, for the things localhost can't do: OAuth redirect URIs, webhooks,
and MCP clients like Claude and ChatGPT that have to reach your server over
the open internet.

```sh
ssh -p 2222 -R <name>:80:localhost:<port> tunnel.docspace.ai
```

That's the whole client. `https://<name>.docspace.ai` now routes to
`localhost:<port>` until the ssh exits. Names are first come, first served
while held; the bind fails if someone else has it.

## Getting access

Add your GitHub username to [users](users) and merge the PR (label it
`skip-deploy` — no deploy needed). The server re-syncs everyone's keys from
`github.com/<user>.keys` every minute, and you connect with the matching
private key. Nothing to install or configure on your side.

## Using it

A CourtListener dev server on :8000, public at `https://cl-nathan.docspace.ai`:

```sh
ssh -p 2222 -R cl-nathan:80:localhost:8000 tunnel.docspace.ai
```

To ride through network drops, same thing via autossh:

```sh
autossh -M 0 -p 2222 -R cl-nathan:80:localhost:8000 tunnel.docspace.ai
```

The app behind the tunnel still has to accept its public hostname — for
Django that's `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and
`SECURE_PROXY_SSL_HEADER`; for the MCP server, `MCP_BASE_URL`.

## How it works

One $12/mo droplet (`flp-dev-tunnel`) runs two containers:

- [sish](https://github.com/antoniomika/sish) terminates the ssh tunnels on
  :2222 and routes each HTTP request by hostname to the tunnel that claimed
  it. Auth is the synced public keys — [users](users) is the entire access
  model.
- [Caddy](https://caddyserver.com) in front holds a single wildcard
  `*.docspace.ai` certificate (DNS-01 against the DigitalOcean zone).

The repo root is the droplet: [docker-compose.yml](docker-compose.yml),
[docker/caddy/](docker/caddy) and [scripts/sync-keys.sh](scripts/sync-keys.sh)
are written to `/opt/tunnel` by cloud-init. [infra/](infra) is ~100 lines of
Pulumi: the droplet, a reserved IP, two DNS records and a firewall.

## Deploying and operating

Merging to main deploys (`pulumi preview` runs on PRs; a merged PR labeled
`skip-deploy` doesn't deploy). Secrets: `PULUMI_ACCESS_TOKEN` and
`DIGITALOCEAN_TOKEN`. Locally:

```sh
set -a; source .env; set +a
cd infra && pulumi up --stack dev
```

Changing any of the mirrored files replaces the droplet (a few minutes;
open tunnels reconnect, and the reserved IP keeps DNS stable). Admin:
`ssh root@tunnel.docspace.ai` (root gets the `ADMIN` user's GitHub keys),
then `cd /opt/tunnel && docker compose logs -f`.

Hardening TODO: Caddy holds the account-wide `DIGITALOCEAN_TOKEN` for the
DNS challenge; swap in a token scoped to the docspace.ai zone.

## Porting to FLP's AWS

The service is three files at the repo root. Moving it is an EC2 instance, a
Route53 wildcard record and the same cloud-init; nothing else changes.

## notes/

Working notes from this repo's previous incarnation (production-shaped
CourtListener + MCP deploys on DO Kubernetes), kept while the
[legacy-clients experiment](notes/legacy-clients.md) finishes.
