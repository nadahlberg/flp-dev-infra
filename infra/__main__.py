"""Assemble every slug in deploys.py on top of the shared core."""

import pulumi
import pulumi_digitalocean as do
import pulumi_random as random

import core
import courtlistener
import deploys
import mcp
from core.images import build_image
from core.sources import source

deploys.validate()

# One image per (repo, sha), shared by every slug on that sha.
_images: dict[tuple[str, str], tuple[str, object]] = {}


def image_for(repo: str, ref: str, name: str, dockerfile: str, build_args):
    """Build (once) the image for `repo` at `ref`; return (ref, resource, source)."""
    src = source(repo, ref)
    key = (name, src.sha)
    if key not in _images:
        _images[key] = build_image(
            f"{name}-{src.tag}",
            name=name,
            tag=src.tag,
            context=src.path,
            dockerfile=f"{src.path}/{dockerfile}",
            build_args=build_args(src),
        )
    image_ref, image = _images[key]
    return image_ref, image, src


# One confidential OAuth client per MCP slug, registered at the CL slug it
# points to. Generated here so both sides read the same values.
oauth_pairs: dict[str, tuple[random.RandomPassword, random.RandomPassword]] = {}
for mcp_slug, mcp_spec in deploys.MCP.items():
    pair_name = f"oauth-{mcp_slug}-{mcp_spec.courtlistener}"
    oauth_pairs[mcp_slug] = (
        random.RandomPassword(f"{pair_name}-id", length=40, special=False),
        random.RandomPassword(f"{pair_name}-secret", length=96, special=False),
    )

project_urns = list(core.PROJECT_URNS)
cl_deploys: dict[str, courtlistener.CourtListener] = {}
cl_shas: dict[str, str] = {}

for cl_slug, cl_spec in deploys.COURTLISTENER.items():
    image_ref, image, src = image_for(
        cl_spec.repo,
        cl_spec.ref,
        "flp-dev-courtlistener",
        "docker/django/Dockerfile",
        lambda _src: {"BUILD_ENV": "prod"},
    )
    clients = [
        courtlistener.OAuthClient(
            name=f"flp-dev MCP server ({mcp_slug})",
            client_id=oauth_pairs[mcp_slug][0].result,
            client_secret=oauth_pairs[mcp_slug][1].result,
            redirect_uri=f"https://{deploys.mcp_host(mcp_slug)}/auth/callback",
        )
        for mcp_slug, mcp_spec in deploys.MCP.items()
        if mcp_spec.courtlistener == cl_slug
    ]
    cl_deploys[cl_slug] = courtlistener.deploy(
        cl_slug, cl_spec, image_ref, image, src.path, clients
    )
    cl_shas[cl_slug] = src.sha
    project_urns += cl_deploys[cl_slug].project_urns

for mcp_slug, mcp_spec in deploys.MCP.items():
    image_ref, image, src = image_for(
        mcp_spec.repo,
        mcp_spec.ref,
        "flp-dev-mcp",
        "Dockerfile",
        lambda src: {"TARGET_ENV": "prod", "GIT_SHA": src.tag},
    )
    client_id, client_secret = oauth_pairs[mcp_slug]
    deployed = mcp.deploy(
        mcp_slug,
        mcp_spec,
        image_ref,
        image,
        cl_deploys[mcp_spec.courtlistener].host,
        client_id.result,
        client_secret.result,
    )
    project_urns += deployed.project_urns
    pulumi.export(f"mcp_{mcp_slug}_url", f"https://{deployed.host}")
    pulumi.export(f"mcp_{mcp_slug}_sha", src.sha)
    pulumi.export(
        f"mcp_{mcp_slug}_courtlistener", cl_deploys[mcp_spec.courtlistener].host
    )

for cl_slug, deployed in cl_deploys.items():
    pulumi.export(f"courtlistener_{cl_slug}_url", f"https://{deployed.host}")
    pulumi.export(f"courtlistener_{cl_slug}_sha", cl_shas[cl_slug])

do.ProjectResources(
    "project-resources",
    project=core.project.id,
    resources=project_urns,
)

pulumi.export("cluster_name", core.cluster.name)
pulumi.export(
    "kubeconfig", pulumi.Output.secret(core.cluster.kube_configs[0].raw_config)
)
pulumi.export("load_balancer_ip", core.lb_ip)
pulumi.export("zone", deploys.ZONE)
pulumi.export("postgres_host", core.postgres.host)
pulumi.export("postgres_port", core.postgres.port)
pulumi.export("postgres_user", core.postgres.user)
pulumi.export("postgres_password", pulumi.Output.secret(core.postgres.password))
