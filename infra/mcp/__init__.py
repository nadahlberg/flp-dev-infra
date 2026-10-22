"""The CourtListener MCP server at cl-mcp.nates.ai.

Two or more replicas behind one Service, spread across nodes, so that
whatever a client hits next is never guaranteed to be the pod it hit last.
Authenticates users against the CourtListener deployed by this same stack.
"""

import os

import pulumi
import pulumi_digitalocean as do
import pulumi_kubernetes as k8s
import pulumi_random as random

import core
import courtlistener
from core.dns import subdomain
from core.images import build_image, pull_secret

DOMAIN = "cl-mcp.nates.ai"
APP = "mcp"

config = pulumi.Config()
replicas = config.get_int("mcpReplicas") or 2

image_tag = os.getenv("MCP_IMAGE_TAG", "dev")
build_context = os.getenv("MCP_CONTEXT", "../../courtlistener-api-client")

k8s_opts = pulumi.ResourceOptions(provider=core.doks)

namespace = k8s.core.v1.Namespace(
    "mcp-namespace",
    metadata={"name": APP},
    opts=k8s_opts,
)

ns_opts = pulumi.ResourceOptions(provider=core.doks, depends_on=[namespace])

database = do.DatabaseDb(
    "mcp-db",
    cluster_id=core.postgres.id,
    name=APP,
)

image_ref, image = build_image(
    "mcp-image",
    name="flp-dev-mcp",
    tag=image_tag,
    context=build_context,
    dockerfile=f"{build_context}/Dockerfile",
    build_args={"TARGET_ENV": "prod", "GIT_SHA": image_tag},
)

registry_pull = pull_secret("mcp-ghcr-pull", APP, ns_opts)
pull_secrets = [{"name": registry_pull.metadata.name}]

# --- Redis -----------------------------------------------------------------

redis_labels = {"app": "redis"}

redis_deployment = k8s.apps.v1.Deployment(
    "mcp-redis",
    metadata={"namespace": APP, "name": "redis"},
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
    "mcp-redis-service",
    metadata={"name": "redis", "namespace": APP},
    spec={"selector": redis_labels, "ports": [{"port": 6379}]},
    opts=ns_opts,
)

# --- Secrets ---------------------------------------------------------------

secret_key = random.RandomPassword("mcp-secret-key", length=64, special=False)
jwt_signing_key = random.RandomPassword("mcp-jwt-key", length=64, special=False)
# A Fernet key is 32 random bytes in url-safe base64.
storage_key = random.RandomBytes("mcp-storage-key", length=32)
fernet_key = storage_key.base64.apply(
    lambda b64: b64.replace("+", "-").replace("/", "_")
)

app_env = k8s.core.v1.Secret(
    "mcp-app-env",
    metadata={"namespace": APP, "name": "app-env"},
    string_data={
        "TARGET_ENV": "prod",
        "MCP_WORKERS": "2",
        "MCP_BASE_URL": f"https://{DOMAIN}",
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
        # The CourtListener this stack deploys is the identity provider and
        # the API behind every tool call.
        "COURTLISTENER_OAUTH_ISSUER": f"https://{courtlistener.DOMAIN}",
        "COURTLISTENER_API_BASE_URL": f"https://{courtlistener.DOMAIN}/api/rest/v4",
        "COURTLISTENER_OAUTH_CLIENT_ID": courtlistener.oauth_client_id.result,
        "COURTLISTENER_OAUTH_CLIENT_SECRET": courtlistener.oauth_client_secret.result,
        "SENTRY_DSN": os.getenv("MCP_SENTRY_DSN", ""),
    },
    opts=ns_opts,
)

env_from = [{"secret_ref": {"name": app_env.metadata.name}}]

# --- Server ----------------------------------------------------------------

mcp_labels = {"app": "mcp"}

mcp_deployment = k8s.apps.v1.Deployment(
    "mcp-web",
    metadata={"namespace": APP, "name": "mcp"},
    spec={
        "replicas": replicas,
        "selector": {"match_labels": mcp_labels},
        "strategy": {
            "type": "RollingUpdate",
            "rolling_update": {"max_surge": 1, "max_unavailable": 0},
        },
        "template": {
            "metadata": {"labels": mcp_labels},
            "spec": {
                "image_pull_secrets": pull_secrets,
                # Prefer one pod per node so "which pod did I hit" is a real
                # question across machines, not just processes.
                "affinity": {
                    "pod_anti_affinity": {
                        "preferred_during_scheduling_ignored_during_execution": [
                            {
                                "weight": 100,
                                "pod_affinity_term": {
                                    "label_selector": {"match_labels": mcp_labels},
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

mcp_service = k8s.core.v1.Service(
    "mcp-service",
    metadata={"name": "mcp", "namespace": APP},
    spec={
        "selector": mcp_labels,
        "ports": [{"name": "http", "port": 80, "target_port": 8080}],
    },
    opts=ns_opts,
)

ingress = k8s.networking.v1.Ingress(
    "mcp-ingress",
    metadata={
        "namespace": APP,
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
        "tls": [{"hosts": [DOMAIN], "secret_name": "mcp-tls"}],
        "rules": [
            {
                "host": DOMAIN,
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
        ],
    },
    opts=pulumi.ResourceOptions(
        provider=core.doks,
        depends_on=[mcp_service, core.ingress, core.cluster_issuer],
    ),
)

domain = subdomain("mcp", DOMAIN, core.lb_ip)

PROJECT_URNS = [domain.domain_urn]
