"""What this stack deploys: one entry per slug.

Edit this file and merge the PR; that is the whole workflow. Each slug gets
its own namespace, logical database on the shared Postgres, Redis,
Elasticsearch (CourtListener) and Spaces bucket, and a stable hostname:

    cl-<slug>.flp-dev.nates.ai      CourtListener
    mcp-<slug>.flp-dev.nates.ai     MCP server

`ref` is any git ref: a branch (floats, redeploys when it moves), a tag, or
a sha (pinned). `checkout.py` fetches each unique (repo, ref) before Pulumi
runs; images are tagged with the resolved sha.
"""

from dataclasses import dataclass, field

ZONE = "flp-dev.nates.ai"

# Alias hostnames that existed before the slug scheme, keyed by the Pulumi
# resource prefix they were created under, so Pulumi adopts the existing DO
# Domain instead of trying to create it a second time.
LEGACY_ALIAS_RESOURCES = {"cl.nates.ai": "cl"}

CL_REPO = "freelawproject/courtlistener"
MCP_REPO = "freelawproject/courtlistener-api-client"


@dataclass(frozen=True)
class CourtListenerDeploy:
    """One CourtListener deployment.

    mcp: slug of the MCP deploy this CL hands dynamically registered OAuth
        clients to (`OAUTH2_MCP_URL`). None leaves CL's default.
    aliases: extra hostnames routed to this deploy, each with its own DO
        Domain and TLS certificate.
    env: raw environment overrides merged last into the app's Secret.
    seed_dev_data / index_search / test_users / bootstrap_extra: what the
        bootstrap Job does beyond migrations and the OAuth apps.
        bootstrap_extra is a list of shell commands run at the end, inside
        the CL image at /opt/courtlistener.
    """

    ref: str
    repo: str = CL_REPO
    mcp: str | None = None
    aliases: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    seed_dev_data: bool = True
    index_search: bool = True
    test_users: int = 3
    bootstrap_extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class McpDeploy:
    """One MCP server deployment.

    courtlistener: slug of the CL deploy it authenticates users against and
        calls for every tool. Its OAuth client credentials are generated per
        (mcp, cl) pair and registered by that CL's bootstrap.
    """

    ref: str
    courtlistener: str
    repo: str = MCP_REPO
    replicas: int = 2
    aliases: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


COURTLISTENER: dict[str, CourtListenerDeploy] = {
    "main": CourtListenerDeploy(
        ref="main",
        mcp="main",
        aliases=("cl.nates.ai",),
    ),
    # PR #8178: hand DCR clients over to the MCP server behind a flag.
    "dcr": CourtListenerDeploy(
        ref="8154-forward-dcr-clients-to-mcp-20261015",
        mcp="dcr",
    ),
    # PR #8237: COOP unsafe-none on the pages OAuth popups render, so
    # popup-based clients (MS Copilot) can finish their flow.
    "copilot-fix": CourtListenerDeploy(
        ref="7633-relax-coop-on-oauth-popup-pages-20261023",
        mcp="copilot-fix",
    ),
}

MCP: dict[str, McpDeploy] = {
    "main": McpDeploy(ref="main", courtlistener="main"),
    "dcr": McpDeploy(ref="main", courtlistener="dcr"),
    # PR courtlistener-api-client#387: Copilot Studio sends MCP requests to
    # <origin>/mcp regardless of the configured URL; this branch aliases it.
    "copilot-fix": McpDeploy(
        ref="mcp-path-alias-20261023", courtlistener="copilot-fix"
    ),
}


def cl_host(slug: str) -> str:
    """Public hostname of a CourtListener slug."""
    return f"cl-{slug}.{ZONE}"


def mcp_host(slug: str) -> str:
    """Public hostname of an MCP slug."""
    return f"mcp-{slug}.{ZONE}"


def sources() -> list[tuple[str, str]]:
    """Unique (repo, ref) pairs the config needs checked out."""
    seen: dict[tuple[str, str], None] = {}
    for spec in [*COURTLISTENER.values(), *MCP.values()]:
        seen[(spec.repo, spec.ref)] = None
    return list(seen)


def validate() -> None:
    """Fail early on a slug that points at a deploy that does not exist."""
    for slug, spec in MCP.items():
        if spec.courtlistener not in COURTLISTENER:
            raise ValueError(
                f"MCP[{slug!r}].courtlistener = {spec.courtlistener!r} "
                "is not a CourtListener slug"
            )
    for slug, spec in COURTLISTENER.items():
        if spec.mcp is not None and spec.mcp not in MCP:
            raise ValueError(
                f"COURTLISTENER[{slug!r}].mcp = {spec.mcp!r} is not an MCP slug"
            )
    for slug in [*COURTLISTENER, *MCP]:
        if not slug.replace("-", "").isalnum() or slug != slug.lower():
            raise ValueError(
                f"slug {slug!r} must be lowercase letters, digits and dashes "
                "(it becomes a hostname and a namespace)"
            )
