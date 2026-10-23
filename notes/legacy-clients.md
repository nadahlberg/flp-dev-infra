# Legacy client migration experiment

Validates [courtlistener#8178](https://github.com/freelawproject/courtlistener/pull/8178)
(hand dynamically registered OAuth clients over to the MCP server behind a
flag) against clients that connected in the era when CourtListener was the
authorization server — before
[courtlistener-api-client#357](https://github.com/freelawproject/courtlistener-api-client/pull/357)
(merged 2026-10-10) made the MCP its own AS. Everything runs on the
`legacy-clients` slug, upgraded **in place** so registered clients and issued
tokens ride through each step exactly as they would in production.

## Phase 1: connect real clients via the old path — DONE 2026-10-23

Deployed the pre-cutover era: CourtListener `81528ac6d` (main's first-parent
tip of 2026-10-09, CL as authorization server) and MCP `d0d414ef` (the commit
api-client#357 merged onto; verifies bearers by CL introspection).

- PRs: [#13](https://github.com/nadahlberg/flp-dev-infra/pull/13) (slug),
  [#15](https://github.com/nadahlberg/flp-dev-infra/pull/15) (fixes below).
- Two traps found when the first Claude attempt got "no tools":
  - Pin by `git rev-list --first-parent`. The date-picked sha `07456f46a`
    was a feature-branch commit whose base predated courtlistener#8087
    (introspection `sub`), so the MCP rejected every token as
    `invalid_token` ("introspection response has no `sub`").
  - Blank `PGHOST` on this MCP era: its unused OAuth-store probe runs
    asyncpg's default pool of ten per worker (predates the `PGPOOL_MAX`
    fix) and exhausted the shared Postgres's connection slots. **Drop the
    override in phase 2** — the main-ref MCP needs the store.
- Clients confirmed in the `cl-legacy-clients` database (2026-10-23):
  - **Claude** — confidential, authorization-code, redirect
    `https://claude.ai/api/mcp/auth_callback`. App id 5 is the live one
    (18:41 UTC); ids 2–3 are registrations from the failed attempts before
    PR #15.
  - **ChatGPT** — public (PKCE), redirect
    `https://chatgpt.com/connector_platform_oauth_redirect`, app id 4.
  - App id 1 is the slug's own confidential MCP app from the bootstrap.
  - Both clients hold live access tokens (user `dev`, scope `openid api`,
    1-hour expiry) and there are 4 unrevoked refresh tokens — the state
    phases 2 and 3 must carry through.

## Phase 2: upgrade in place, flag OFF — next

Repoint the same slug: MCP to `main` (its own authorization server, which
still accepts CL-issued bearers by introspection and re-registers legacy
client ids on first use) and CourtListener to
`8154-forward-dcr-clients-to-mcp-20261015` (the courtlistener#8178 branch,
in review). The flag stays OFF.

- PR: _added when it lands_.
- Tests (Nathan, by hand):
  - The phase-1 Claude and ChatGPT connections keep working through the
    legacy fallbacks — no re-auth prompt, tools list and run.
  - Bust an access token (expire or delete it in the CL database) and
    confirm the client refreshes successfully against CL and keeps working.

## Phase 3: flag ON — not started

Turn the #8178 flag on (for Nathan's user) on `cl-legacy-clients` and verify
the handoff: new dynamic registrations go to the MCP, the phase-1/2 legacy
connections still work, refreshes still succeed. Then tear the slug down
(plan.md Phase 8 step 4).
