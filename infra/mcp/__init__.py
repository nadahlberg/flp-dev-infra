"""One MCP server deployment per slug, at mcp-<slug>.flp-dev.nates.ai.

Two or more replicas behind one Service, spread across nodes, so that
whatever a client hits next is never guaranteed to be the pod it hit last.
Each slug authenticates users against the CourtListener slug its spec names.
"""

import os
from dataclasses import dataclass

import pulumi
import pulumi_digitalocean as do
import pulumi_docker_build as docker_build
import pulumi_kubernetes as k8s
import pulumi_random as random

import core
from core.dns import subdomain
from core.images import pull_secret
from deploys import McpDeploy, mcp_host


@dataclass(frozen=True)
class Mcp:
    """What other modules need to know about a deployed MCP slug."""

    slug: str
    host: str
    namespace: str
    project_urns: list[pulumi.Output[str]]


def deploy(
    slug: str,
    spec: McpDeploy,
    image_ref: str,
    image: docker_build.Image,
    courtlistener_host: str,
    client_id: pulumi.Output[str],
    client_secret: pulumi.Output[str],
) -> Mcp:
    """Stand up every resource of one MCP slug."""
    name = f"mcp-{slug}"
    host = mcp_host(slug)
    hosts = [host, *spec.aliases]
    k8s_opts = pulumi.ResourceOptions(provider=core.doks)

    namespace = k8s.core.v1.Namespace(
        f"{name}-namespace", metadata={"name": name}, opts=k8s_opts
    )
    ns_opts = pulumi.ResourceOptions(provider=core.doks, depends_on=[namespace])

    database = do.DatabaseDb(
        f"{name}-db", cluster_id=core.postgres.id, name=f"mcp_{slug}"
    )

    registry_pull = pull_secret(f"{name}-ghcr-pull", name, ns_opts)
    pull_secrets = [{"name": registry_pull.metadata.name}]

    # --- Redis -------------------------------------------------------------

    redis_labels = {"app": "redis"}
    k8s.apps.v1.Deployment(
        f"{name}-redis",
        metadata={"namespace": name, "name": "redis"},
        spec={
            "selector": {"match_labels": redis_labels},
            "template": {
                "metadata": {"labels": redis_labels},
                "spec": {
                    "containers": [
                        {
                            "name": "redis",
                            "image": "redis:7",
                            "ports": [{"container_port": 6379}],
                            "resources": {"requests": {"memory": "64Mi"}},
                        }
                    ],
                },
            },
        },
        opts=ns_opts,
    )
    redis_service = k8s.core.v1.Service(
        f"{name}-redis-service",
        metadata={"name": "redis", "namespace": name},
        spec={"selector": redis_labels, "ports": [{"port": 6379}]},
        opts=ns_opts,
    )

    # --- Secrets -----------------------------------------------------------

    secret_key = random.RandomPassword(f"{name}-secret-key", length=64, special=False)
    jwt_signing_key = random.RandomPassword(f"{name}-jwt-key", length=64, special=False)
    # A Fernet key is 32 random bytes in url-safe base64.
    storage_key = random.RandomBytes(f"{name}-storage-key", length=32)
    fernet_key = storage_key.base64.apply(
        lambda b64: b64.replace("+", "-").replace("/", "_")
    )

    env: dict[str, pulumi.Input[str]] = {
        "TARGET_ENV": "prod",
        "MCP_WORKERS": "2",
        "MCP_BASE_URL": f"https://{host}",
        "REDIS_URL": "redis://redis:6379",
        "PGHOST": core.postgres.private_host,
        "PGPORT": core.postgres.port.apply(str),
        "PGUSER": core.postgres.user,
        "PGPASSWORD": core.postgres.password,
        "PGDATABASE": database.name,
        "PGPOOL_MAX": "4",
        "MCP_SECRET_KEY": secret_key.result,
        "MCP_STORAGE_ENCRYPTION_KEY": fernet_key,
        "MCP_JWT_SIGNING_KEY": jwt_signing_key.result,
        # The CourtListener slug this server brokers logins for and calls.
        "COURTLISTENER_OAUTH_ISSUER": f"https://{courtlistener_host}",
        "COURTLISTENER_API_BASE_URL": f"https://{courtlistener_host}/api/rest/v4",
        "COURTLISTENER_OAUTH_CLIENT_ID": client_id,
        "COURTLISTENER_OAUTH_CLIENT_SECRET": client_secret,
        "SENTRY_DSN": os.getenv("MCP_SENTRY_DSN", ""),
    }
    env.update(spec.env)

    app_env = k8s.core.v1.Secret(
        f"{name}-app-env",
        metadata={"namespace": name, "name": "app-env"},
        string_data=env,
        opts=ns_opts,
    )
    env_from = [{"secret_ref": {"name": app_env.metadata.name}}]

    # --- Server ------------------------------------------------------------

    labels = {"app": "mcp"}
    k8s.apps.v1.Deployment(
        f"{name}-web",
        metadata={"namespace": name, "name": "mcp"},
        spec={
            "replicas": spec.replicas,
            "selector": {"match_labels": labels},
            "strategy": {
                "type": "RollingUpdate",
                "rolling_update": {"max_surge": 1, "max_unavailable": 0},
            },
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "image_pull_secrets": pull_secrets,
                    # Prefer one pod per node so "which pod did I hit" is a
                    # real question across machines, not just processes.
                    "affinity": {
                        "pod_anti_affinity": {
                            "preferred_during_scheduling_ignored_during_execution": [
                                {
                                    "weight": 100,
                                    "pod_affinity_term": {
                                        "label_selector": {"match_labels": labels},
                                        "topology_key": "kubernetes.io/hostname",
                                    },
                                }
                            ]
                        }
                    },
                    "containers": [
                        {
                            "name": "mcp",
                            "image": image_ref,
                            "ports": [{"container_port": 8080}],
                            "env_from": env_from,
                            "env": [
                                {
                                    "name": "POD_NAME",
                                    "value_from": {
                                        "field_ref": {"field_path": "metadata.name"}
                                    },
                                }
                            ],
                            "resources": {"requests": {"memory": "256Mi"}},
                            "liveness_probe": {
                                "tcp_socket": {"port": 8080},
                                "period_seconds": 30,
                            },
                            "readiness_probe": {
                                "http_get": {"path": "/health", "port": 8080},
                                "period_seconds": 10,
                                "timeout_seconds": 5,
                            },
                        }
                    ],
                },
            },
        },
        opts=pulumi.ResourceOptions(
            provider=core.doks,
            depends_on=[namespace, image, app_env, redis_service, database],
        ),
    )
    service = k8s.core.v1.Service(
        f"{name}-service",
        metadata={"name": "mcp", "namespace": name},
        spec={
            "selector": labels,
            "ports": [{"name": "http", "port": 80, "target_port": 8080}],
        },
        opts=ns_opts,
    )

    # --- Ingress and DNS ---------------------------------------------------

    k8s.networking.v1.Ingress(
        f"{name}-ingress",
        metadata={
            "namespace": name,
            "annotations": {
                "cert-manager.io/cluster-issuer": "letsencrypt",
                # Streamable HTTP holds connections open and streams responses.
                "nginx.ingress.kubernetes.io/proxy-buffering": "off",
                "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
                "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
            },
        },
        spec={
            "ingress_class_name": "nginx",
            "tls": [
                {"hosts": [h], "secret_name": f"tls-{h.replace('.', '-')}"}
                for h in hosts
            ],
            "rules": [
                {
                    "host": h,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "path_type": "Prefix",
                                "backend": {
                                    "service": {"name": "mcp", "port": {"number": 80}}
                                },
                            }
                        ],
                    },
                }
                for h in hosts
            ],
        },
        opts=pulumi.ResourceOptions(
            provider=core.doks,
            depends_on=[service, core.ingress, core.cluster_issuer],
        ),
    )

    project_urns: list[pulumi.Output[str]] = []
    for alias in spec.aliases:
        domain = subdomain(f"{name}-alias-{alias.replace('.', '-')}", alias, core.lb_ip)
        project_urns.append(domain.domain_urn)

    return Mcp(slug=slug, host=host, namespace=name, project_urns=project_urns)
