# Current deploys

One row per slug in [infra/deploys.py](../infra/deploys.py). **Every PR that
changes a deploy updates this table in the same PR**, so it always matches
what is running. Hostnames are `cl-<slug>.flp-dev.nates.ai` and
`mcp-<slug>.flp-dev.nates.ai`.

| Slug | CourtListener ref | MCP ref | Purpose |
|------|-------------------|---------|---------|
| `main` | `main` | `main` | Tracks both mains; `cl.nates.ai` aliases the CL. |
| `dcr` | `8154-forward-dcr-clients-to-mcp-20261015` | `main` | courtlistener#8178 testing; superseded by `legacy-clients`. |
| `legacy-clients` | `81528ac6d` (main's 2026-10-09 tip) | `d0d414ef` (pre-AS, the commit api-client#357 merged onto) | Legacy client migration experiment — see [legacy-clients.md](legacy-clients.md). |
