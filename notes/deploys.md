# Current deploys

One row per slug in [infra/deploys.py](../infra/deploys.py). **Every PR that
changes a deploy updates this table in the same PR**, so it always matches
what is running. Hostnames are `cl-<slug>.flp-dev.nates.ai` and
`mcp-<slug>.flp-dev.nates.ai`.

| Slug | CourtListener ref | MCP ref | Purpose |
|------|-------------------|---------|---------|
| `main` | `main` | `main` | Tracks both mains; `cl.nates.ai` aliases the CL. |
| `legacy-clients` | `8154-forward-dcr-clients-to-mcp-20261015` (courtlistener#8178) | `main` | Legacy client migration experiment, phase 2 (flag off) — see [legacy-clients.md](legacy-clients.md). |
