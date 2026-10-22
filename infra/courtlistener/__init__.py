"""CourtListener at cl.nates.ai.

Runs the real image in production mode (gunicorn, DEVELOPMENT=off) with a
settings overlay from this folder covering what production assumes but this
environment does not have (AWS S3, SES). A bootstrap Job migrates, seeds
users and the MCP's OAuth application, and loads sample data on every deploy.
"""

import os
from pathlib import Path

import pulumi
import pulumi_digitalocean as do
import pulumi_kubernetes as k8s
import pulumi_random as random
import pulumi_tls as tls

import core
from core.dns import subdomain
from core.images import build_image, pull_secret

DOMAIN = "cl.nates.ai"
MCP_DOMAIN = "cl-mcp.nates.ai"
APP = "courtlistener"
HERE = Path(__file__).parent

image_tag = os.getenv("CL_IMAGE_TAG", "dev")
build_context = os.getenv("CL_CONTEXT", "../../courtlistener")
dev_user_name = os.getenv("DEV_USER_NAME", "dev")

k8s_opts = pulumi.ResourceOptions(provider=core.doks)

namespace = k8s.core.v1.Namespace(
    "cl-namespace",
    metadata={"name": APP},
    opts=k8s_opts,
)

ns_opts = pulumi.ResourceOptions(provider=core.doks, depends_on=[namespace])

database = do.DatabaseDb(
    "cl-db",
    cluster_id=core.postgres.id,
    name=APP,
)

# Static files (and any S3-backed media) live on Spaces, DO's S3-compatible
# object store, since CL's production settings only know how to serve static
# files from a bucket.
spaces_key = do.SpacesKey(
    "cl-spaces",
    grants=[do.SpacesKeyGrantArgs(bucket="", permission="fullaccess")],
)

spaces = do.Provider(
    "cl-spaces-provider",
    spaces_access_id=spaces_key.access_key,
    spaces_secret_key=spaces_key.secret_key,
)

public_bucket = do.SpacesBucket(
    "cl-storage",
    region=core.region,
    acl="public-read",
    opts=pulumi.ResourceOptions(provider=spaces),
)

do.SpacesBucketCorsConfiguration(
    "cl-storage-cors",
    bucket=public_bucket.id,
    region=core.region,
    cors_rules=[
        do.SpacesBucketCorsConfigurationCorsRuleArgs(
            allowed_methods=["GET", "HEAD"],
            allowed_origins=[f"https://{DOMAIN}"],
            allowed_headers=["*"],
            max_age_seconds=86400,
        )
    ],
    opts=pulumi.ResourceOptions(provider=spaces),
)

private_bucket = do.SpacesBucket(
    "cl-private-storage",
    region=core.region,
    opts=pulumi.ResourceOptions(provider=spaces),
)

spaces_endpoint = f"https://{core.region}.digitaloceanspaces.com"
storage_domain = public_bucket.name.apply(
    lambda name: f"{name}.{core.region}.digitaloceanspaces.com"
)

image_ref, image = build_image(
    "cl-image",
    name="flp-dev-courtlistener",
    tag=image_tag,
    context=build_context,
    dockerfile=f"{build_context}/docker/django/Dockerfile",
    build_args={"BUILD_ENV": "prod"},
)

registry_pull = pull_secret("cl-ghcr-pull", APP, ns_opts)
pull_secrets = [{"name": registry_pull.metadata.name}]

# --- Redis -----------------------------------------------------------------

redis_labels = {"app": "redis"}

redis_deployment = k8s.apps.v1.Deployment(
    "cl-redis",
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
    "cl-redis-service",
    metadata={"name": "redis", "namespace": APP},
    spec={"selector": redis_labels, "ports": [{"port": 6379}]},
    opts=ns_opts,
)

# --- Elasticsearch ---------------------------------------------------------

# CL's index analyzers reference these dictionaries by path inside the ES
# config directory, so they ride along from the CL checkout.
es_files = Path(build_context) / "cl" / "search" / "elasticsearch_files"
es_dictionaries = k8s.core.v1.ConfigMap(
    "cl-es-dictionaries",
    metadata={"namespace": APP, "name": "es-dictionaries"},
    data={
        "synonyms_en.txt": (es_files / "synonyms_en.txt").read_text(),
        "stopwords_en.txt": (es_files / "stopwords_en.txt").read_text(),
    },
    opts=ns_opts,
)

elasticsearch_labels = {"app": "elasticsearch"}

elasticsearch_statefulset = k8s.apps.v1.StatefulSet(
    "cl-elasticsearch",
    metadata={"namespace": APP, "name": "elasticsearch"},
    spec={
        "service_name": "elasticsearch",
        "selector": {"match_labels": elasticsearch_labels},
        "template": {
            "metadata": {"labels": elasticsearch_labels},
            "spec": {
                "security_context": {"fs_group": 1000},
                "init_containers": [
                    {
                        "name": "max-map-count",
                        "image": "busybox:1.37",
                        "command": ["sysctl", "-w", "vm.max_map_count=262144"],
                        "security_context": {"privileged": True},
                    }
                ],
                "containers": [
                    {
                        "name": "elasticsearch",
                        "image": "elastic/elasticsearch:9.3.1",
                        "ports": [{"container_port": 9200}],
                        "env": [
                            {"name": "discovery.type", "value": "single-node"},
                            {"name": "cluster.name", "value": "courtlistener-cluster"},
                            {
                                "name": "cluster.routing.allocation.disk.threshold_enabled",
                                "value": "false",
                            },
                            {"name": "xpack.security.enabled", "value": "false"},
                            {"name": "ES_JAVA_OPTS", "value": "-Xms1g -Xmx1g"},
                        ],
                        "resources": {"requests": {"memory": "2Gi"}},
                        "volume_mounts": [
                            {
                                "name": "data",
                                "mount_path": "/usr/share/elasticsearch/data",
                            },
                            {
                                "name": "dictionaries",
                                "mount_path": "/usr/share/elasticsearch/config/dictionaries",
                            },
                        ],
                        "readiness_probe": {
                            "http_get": {"path": "/_cluster/health", "port": 9200},
                            "period_seconds": 30,
                            "timeout_seconds": 5,
                        },
                    }
                ],
                "volumes": [
                    {
                        "name": "dictionaries",
                        "config_map": {"name": es_dictionaries.metadata.name},
                    }
                ],
            },
        },
        "volume_claim_templates": [
            {
                "metadata": {"name": "data"},
                "spec": {
                    "access_modes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "10Gi"}},
                },
            }
        ],
    },
    opts=ns_opts,
)

elasticsearch_service = k8s.core.v1.Service(
    "cl-elasticsearch-service",
    metadata={"name": "elasticsearch", "namespace": APP},
    spec={"selector": elasticsearch_labels, "ports": [{"port": 9200}]},
    opts=ns_opts,
)

# --- Secrets and config ----------------------------------------------------

secret_key = random.RandomPassword("cl-secret-key", length=64, special=False)
oidc_key = tls.PrivateKey("cl-oidc-key", algorithm="RSA", rsa_bits=4096)

# The MCP server's confidential OAuth application at this CourtListener. Both
# sides read the same generated values: seed.py registers the application,
# the mcp module hands them to the server as its client credentials.
oauth_client_id = random.RandomPassword("cl-mcp-client-id", length=40, special=False)
oauth_client_secret = random.RandomPassword(
    "cl-mcp-client-secret", length=96, special=False
)

# Everything the bootstrap Job and the pods need, mounted at /opt/flp-dev.
overlay = k8s.core.v1.ConfigMap(
    "cl-overlay",
    metadata={"namespace": APP, "name": "flp-dev"},
    data={
        "flp_dev_settings.py": (HERE / "flp_dev_settings.py").read_text(),
        "bootstrap.sh": (HERE / "bootstrap.sh").read_text(),
        "seed.py": (HERE / "seed.py").read_text(),
    },
    opts=ns_opts,
)

app_env = k8s.core.v1.Secret(
    "cl-app-env",
    metadata={"namespace": APP, "name": "app-env"},
    string_data={
        # Settings selection: CL's own settings plus the overlay.
        "DJANGO_SETTINGS_MODULE": "flp_dev_settings",
        "PYTHONPATH": "/opt/courtlistener:/opt/flp-dev",
        # Django
        "SECRET_KEY": secret_key.result,
        "DEBUG": "off",
        "DEVELOPMENT": "off",
        "ALLOWED_HOSTS": f"{DOMAIN},localhost,127.0.0.1",
        "TIMEZONE": "America/New_York",
        # Database (the port is applied by the overlay; CL only reads DB_HOST)
        "DB_NAME": database.name,
        "DB_USER": core.postgres.user,
        "DB_PASSWORD": core.postgres.password,
        "DB_HOST": core.postgres.private_host,
        "DB_PORT": core.postgres.port.apply(str),
        "DB_SSL_MODE": "require",
        # Redis + Elasticsearch
        "REDIS_HOST": "redis",
        "ELASTICSEARCH_DSL_HOST": "http://elasticsearch:9200",
        # Celery
        "CELERY_QUEUES": (
            "celery,etl_tasks,feeds,es_sweep,batch0,batch1,batch2,batch3,"
            "recap_fetch,iquery,free_pacer_docs,ia_uploads,inception_gpu,"
            "inception_s3"
        ),
        "CELERY_PREFORK_CONCURRENCY": "2",
        "CELERY_WORKER_CONCURRENCY": "2",
        # Gunicorn
        "NUM_WORKERS": "2",
        "MAX_REQUESTS": "1000",
        # OAuth / OIDC
        "OIDC_RSA_PRIVATE_KEY": oidc_key.private_key_pem,
        "OAUTH2_MCP_URL": f"https://{MCP_DOMAIN}",
        "MCP_BASE_URL": f"https://{MCP_DOMAIN}",
        "MCP_OAUTH_CLIENT_ID": oauth_client_id.result,
        "MCP_OAUTH_CLIENT_SECRET": oauth_client_secret.result,
        # Spaces (S3-compatible); the overlay reads the endpoint and domain
        "AWS_ACCESS_KEY_ID": spaces_key.access_key,
        "AWS_SECRET_ACCESS_KEY": spaces_key.secret_key,
        "AWS_STORAGE_BUCKET_NAME": public_bucket.name,
        "AWS_PRIVATE_STORAGE_BUCKET_NAME": private_bucket.name,
        "AWS_S3_ENDPOINT_URL": spaces_endpoint,
        "AWS_S3_REGION_NAME": core.region,
        "AWS_S3_CUSTOM_DOMAIN": storage_domain,
        # hCaptcha's published test keys: the widget always passes
        "HCAPTCHA_SITEKEY": "10000000-ffff-ffff-ffff-000000000001",
        "HCAPTCHA_SECRET": "0x0000000000000000000000000000000000000000",
        # Seeding
        "DEV_USER_NAME": dev_user_name,
        "DEV_USER_EMAIL": os.getenv("DEV_USER_EMAIL", ""),
        "DEV_USER_PASSWORD": os.getenv("DEV_USER_PASSWORD", ""),
        "TEST_USER_COUNT": "3",
        "SEED_DEV_DATA": "on",
        "CL_API_TOKEN": os.getenv("CL_API_TOKEN", ""),
        # Misc
        "SENTRY_DSN": os.getenv("CL_SENTRY_DSN", ""),
    },
    opts=ns_opts,
)

env_from = [{"secret_ref": {"name": app_env.metadata.name}}]
overlay_volume = {"name": "flp-dev", "config_map": {"name": overlay.metadata.name}}
overlay_mount = {"name": "flp-dev", "mount_path": "/opt/flp-dev", "read_only": True}

# --- Bootstrap: migrate, seed, index --------------------------------------

bootstrap = k8s.batch.v1.Job(
    "cl-bootstrap",
    metadata={"namespace": APP},
    spec={
        "backoff_limit": 1,
        "ttl_seconds_after_finished": 3600,
        "template": {
            "spec": {
                "restart_policy": "Never",
                "image_pull_secrets": pull_secrets,
                "volumes": [overlay_volume],
                "containers": [
                    {
                        "name": "bootstrap",
                        "image": image_ref,
                        "command": ["/bin/sh", "/opt/flp-dev/bootstrap.sh"],
                        "env_from": env_from,
                        "volume_mounts": [overlay_mount],
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(
        provider=core.doks,
        depends_on=[
            namespace,
            image,
            app_env,
            overlay,
            redis_service,
            elasticsearch_service,
            elasticsearch_statefulset,
        ],
        custom_timeouts=pulumi.CustomTimeouts(create="30m"),
    ),
)

# --- Web and celery -------------------------------------------------------

web_labels = {"app": "web"}

web_deployment = k8s.apps.v1.Deployment(
    "cl-web",
    metadata={"namespace": APP, "name": "web"},
    spec={
        "selector": {"match_labels": web_labels},
        "template": {
            "metadata": {"labels": web_labels},
            "spec": {
                "image_pull_secrets": pull_secrets,
                "volumes": [overlay_volume],
                "containers": [
                    {
                        "name": "web",
                        "image": image_ref,
                        "args": ["web-prod"],
                        "ports": [{"container_port": 8000}],
                        "env_from": env_from,
                        "volume_mounts": [overlay_mount],
                        "resources": {"requests": {"memory": "1Gi"}},
                        "liveness_probe": {
                            "tcp_socket": {"port": 8000},
                            "period_seconds": 30,
                        },
                        "readiness_probe": {
                            "tcp_socket": {"port": 8000},
                            "period_seconds": 10,
                        },
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(provider=core.doks, depends_on=[bootstrap]),
)

celery_labels = {"app": "celery"}

celery_deployment = k8s.apps.v1.Deployment(
    "cl-celery",
    metadata={"namespace": APP, "name": "celery"},
    spec={
        "selector": {"match_labels": celery_labels},
        "template": {
            "metadata": {"labels": celery_labels},
            "spec": {
                "image_pull_secrets": pull_secrets,
                "volumes": [overlay_volume],
                "containers": [
                    {
                        "name": "celery",
                        "image": image_ref,
                        "args": ["celery"],
                        "env_from": env_from,
                        "volume_mounts": [overlay_mount],
                        "resources": {"requests": {"memory": "1Gi"}},
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(provider=core.doks, depends_on=[bootstrap]),
)

web_service = k8s.core.v1.Service(
    "cl-web-service",
    metadata={"name": "web", "namespace": APP},
    spec={
        "selector": web_labels,
        "ports": [{"name": "http", "port": 80, "target_port": 8000}],
    },
    opts=ns_opts,
)

ingress = k8s.networking.v1.Ingress(
    "cl-ingress",
    metadata={
        "namespace": APP,
        "annotations": {
            "cert-manager.io/cluster-issuer": "letsencrypt",
            "nginx.ingress.kubernetes.io/proxy-body-size": "100m",
            "nginx.ingress.kubernetes.io/proxy-read-timeout": "180",
        },
    },
    spec={
        "ingress_class_name": "nginx",
        "tls": [{"hosts": [DOMAIN], "secret_name": "cl-tls"}],
        "rules": [
            {
                "host": DOMAIN,
                "http": {
                    "paths": [
                        {
                            "path": "/",
                            "path_type": "Prefix",
                            "backend": {
                                "service": {"name": "web", "port": {"number": 80}}
                            },
                        }
                    ],
                },
            }
        ],
    },
    opts=pulumi.ResourceOptions(
        provider=core.doks,
        depends_on=[web_service, core.ingress, core.cluster_issuer],
    ),
)

domain = subdomain("cl", DOMAIN, core.lb_ip)

PROJECT_URNS = [
    domain.domain_urn,
    public_bucket.bucket_urn,
    private_bucket.bucket_urn,
]
