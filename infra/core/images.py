"""Build app images and push them to GHCR; let the cluster pull them.

DigitalOcean allows one container registry per account and that one belongs
to the infra repo, so this stack keeps its images on ghcr.io under the
GHCR_NAMESPACE (default: nadahlberg). GH_TOKEN must be a classic PAT with
read:packages and write:packages; the same token pushes from CI and backs
the pull secret in each namespace.
"""

import base64
import json
import os

import pulumi
import pulumi_docker_build as docker_build
import pulumi_kubernetes as k8s

REGISTRY = "ghcr.io"

ghcr_namespace = os.getenv("GHCR_NAMESPACE", "nadahlberg")
ghcr_username = os.getenv("GHCR_USERNAME", ghcr_namespace)
ghcr_token = os.environ["GH_TOKEN"]


def image_ref(name: str, tag: str) -> str:
    """Full reference of an image this stack builds."""
    return f"{REGISTRY}/{ghcr_namespace}/{name}:{tag}"


def build_image(
    resource_name: str,
    *,
    name: str,
    tag: str,
    context: str,
    dockerfile: str,
    build_args: dict[str, str] | None = None,
) -> tuple[str, docker_build.Image]:
    """Build `context` with `dockerfile` for linux/amd64 and push it to GHCR.

    Returns the image reference (usable in pod specs) and the resource, for
    `depends_on`.
    """
    ref = image_ref(name, tag)
    image = docker_build.Image(
        resource_name,
        tags=[ref],
        context={"location": context},
        dockerfile={"location": dockerfile},
        build_args=build_args or {},
        platforms=[docker_build.Platform.LINUX_AMD64],
        push=True,
        registries=[
            {
                "address": REGISTRY,
                "username": ghcr_username,
                "password": pulumi.Output.secret(ghcr_token),
            }
        ],
    )
    return ref, image


def pull_secret(
    resource_name: str, namespace: str, opts: pulumi.ResourceOptions
) -> k8s.core.v1.Secret:
    """A dockerconfigjson Secret in `namespace` that can pull from GHCR."""
    auth = base64.b64encode(f"{ghcr_username}:{ghcr_token}".encode()).decode()
    dockerconfig = json.dumps(
        {
            "auths": {
                REGISTRY: {
                    "username": ghcr_username,
                    "password": ghcr_token,
                    "auth": auth,
                }
            }
        }
    )
    return k8s.core.v1.Secret(
        resource_name,
        metadata={"namespace": namespace, "name": "ghcr-pull"},
        type="kubernetes.io/dockerconfigjson",
        string_data={".dockerconfigjson": pulumi.Output.secret(dockerconfig)},
        opts=opts,
    )
