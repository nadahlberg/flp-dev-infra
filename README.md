# flp-tunnel

A dev tunnel. Expose a port on your machine at a stable public HTTPS
hostname, for the things localhost can't do: OAuth redirect URIs, webhooks,
and MCP clients like Claude and ChatGPT that have to reach your server over
the open internet.

```sh
ssh -p 2222 -R <name>:80:localhost:<port> tunnel.flp-tunnel.xyz
```

That's the whole client. `https://<name>.flp-tunnel.xyz` now routes to
`localhost:<port>` until the ssh exits. Names are first come, first served
while held; the bind fails if someone else has it.

## Getting access

Add your GitHub username to [users](users) and merge the PR. The server
re-syncs everyone's keys from `github.com/<user>.keys` every minute, and you
connect with the matching private key. Nothing to install or configure.

## Using it

A Django dev server on :8000, public at `https://cl-nathan.flp-tunnel.xyz`:

```sh
ssh -p 2222 -R cl-nathan:80:localhost:8000 tunnel.flp-tunnel.xyz
```

To ride through network drops, the same thing via autossh:

```sh
autossh -M 0 -p 2222 -R cl-nathan:80:localhost:8000 tunnel.flp-tunnel.xyz
```

The app behind the tunnel still has to accept its public hostname — for
Django that's `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and
`SECURE_PROXY_SSL_HEADER`; for the MCP server, `MCP_BASE_URL`.

## How it works

One small VM runs the two containers in [docker-compose.yml](docker-compose.yml):

- [sish](https://github.com/antoniomika/sish) terminates the ssh tunnels on
  :2222 and routes each HTTP request by hostname to the tunnel that claimed
  it. Auth is the synced public keys — [users](users) is the entire access
  model.
- [Caddy](https://caddyserver.com) in front holds a single wildcard
  certificate, solved with a DNS-01 challenge against the zone.

A cron runs [scripts/sync-keys.sh](scripts/sync-keys.sh) every minute to
mirror the `users` file's GitHub keys into sish's watched directory, so
access changes need no deploy.

## Deploying

The service is the repo root mirrored to `/opt/tunnel` on the VM. From
scratch:

1. One small VM (a t4g.small-class instance is plenty) with Docker, the
   repo root at `/opt/tunnel`, and `docker compose up --build --detach`.
2. A dedicated domain with `tunnel` and `*` A records at the VM's static
   IP. Use a domain of its own, not a subdomain of a production site —
   tunneled apps would otherwise be same-site with production (cookie
   scope, SameSite).
3. TLS: swap the Caddy DNS plugin in
   [docker/caddy/Dockerfile](docker/caddy/Dockerfile) for the zone's
   provider (e.g. `caddy-dns/route53`), adjust the `tls` block in the
   [Caddyfile](docker/caddy/Caddyfile), and hand it a token scoped to the
   zone (the `DO_DNS_TOKEN` env in the compose file).
4. Open inbound 22 (admin ssh), 80, 443 and 2222 (tunnels).
5. Point the URL in [scripts/sync-keys.sh](scripts/sync-keys.sh) at the
   `users` file on your fork.

[infra/](infra) and [.github/workflows/](.github/workflows) are one
concrete deployment of the above and the only pieces tied to it — delete
both and do the five steps your way.
