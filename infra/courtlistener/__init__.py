"""One CourtListener deployment per slug, at cl-<slug>.flp-dev.nates.ai.

Runs the real image in production mode (gunicorn, DEVELOPMENT=off) with a
settings overlay from this folder covering what production assumes but this
environment does not have (AWS S3, SES). A bootstrap Job migrates, seeds
users and the OAuth applications of the MCP deploys that point here, and
optionally loads and indexes sample data, on every deploy.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pulumi
import pulumi_digitalocean as do
import pulumi_docker_build as docker_build
import pulumi_kubernetes as k8s
import pulumi_random as random
import pulumi_tls as tls

import core
from core.dns import subdomain
from core.images import pull_secret
from deploys import LEGACY_ALIAS_RESOURCES, CourtListenerDeploy, cl_host, mcp_host

HERE = Path(__file__).parent


@dataclass(frozen=True)
class OAuthClient:
    """An MCP deploy's confidential OAuth application at this CourtListener."""

    name: str
    client_id: pulumi.Output[str]
    client_secret: pulumi.Output[str]
    redirect_uri: str


@dataclass(frozen=True)
class CourtListener:
    """What other modules need to know about a deployed CL slug."""

    slug: str
    host: str
    namespace: str
    project_urns: list[pulumi.Output[str]]


def deploy(
    slug: str,
    spec: CourtListenerDeploy,
    image_ref: str,
    image: docker_build.Image,
    source_path: str,
    oauth_clients: list[OAuthClient],
) -> CourtListener:
    """Stand up every resource of one CourtListener slug."""
    name = f"cl-{slug}"
    host = cl_host(slug)
    hosts = [host, *spec.aliases]
    k8s_opts = pulumi.ResourceOptions(provider=core.doks)

    namespace = k8s.core.v1.Namespace(
        f"{name}-namespace", metadata={"name": name}, opts=k8s_opts
    )
    ns_opts = pulumi.ResourceOptions(provider=core.doks, depends_on=[namespace])

    database = do.DatabaseDb(
        f"{name}-db", cluster_id=core.postgres.id, name=f"cl_{slug}"
    )

    # --- Spaces: static files (and S3-backed media) ------------------------

    spaces_key = do.SpacesKey(
        f"{name}-spaces",
        grants=[do.SpacesKeyGrantArgs(bucket="", permission="fullaccess")],
    )
    spaces = do.Provider(
        f"{name}-spaces-provider",
        spaces_access_id=spaces_key.access_key,
        spaces_secret_key=spaces_key.secret_key,
    )
    public_bucket = do.SpacesBucket(
        f"{name}-storage",
        region=core.region,
        acl="public-read",
        opts=pulumi.ResourceOptions(provider=spaces),
    )
    do.SpacesBucketCorsConfiguration(
        f"{name}-storage-cors",
        bucket=public_bucket.id,
        region=core.region,
        cors_rules=[
            do.SpacesBucketCorsConfigurationCorsRuleArgs(
                allowed_methods=["GET", "HEAD"],
                allowed_origins=[f"https://{h}" for h in hosts],
                allowed_headers=["*"],
                max_age_seconds=86400,
            )
        ],
        opts=pulumi.ResourceOptions(provider=spaces),
    )
    private_bucket = do.SpacesBucket(
        f"{name}-private-storage",
        region=core.region,
        opts=pulumi.ResourceOptions(provider=spaces),
    )
    spaces_endpoint = f"https://{core.region}.digitaloceanspaces.com"
    storage_domain = public_bucket.name.apply(
        lambda b: f"{b}.{core.region}.digitaloceanspaces.com"
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

    # --- Elasticsearch -----------------------------------------------------

    # CL's index analyzers reference these dictionaries by path inside the ES
    # config directory, so they ride along from this slug's checkout.
    es_files = Path(source_path) / "cl" / "search" / "elasticsearch_files"
    es_dictionaries = k8s.core.v1.ConfigMap(
        f"{name}-es-dictionaries",
        metadata={"namespace": name, "name": "es-dictionaries"},
        data={
            "synonyms_en.txt": (es_files / "synonyms_en.txt").read_text(),
            "stopwords_en.txt": (es_files / "stopwords_en.txt").read_text(),
        },
        opts=ns_opts,
    )
    es_labels = {"app": "elasticsearch"}
    elasticsearch = k8s.apps.v1.StatefulSet(
        f"{name}-elasticsearch",
        metadata={"namespace": name, "name": "elasticsearch"},
        spec={
            "service_name": "elasticsearch",
            "selector": {"match_labels": es_labels},
            "template": {
                "metadata": {"labels": es_labels},
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
                                {"name": "cluster.name", "value": f"{name}-cluster"},
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
    es_service = k8s.core.v1.Service(
        f"{name}-elasticsearch-service",
        metadata={"name": "elasticsearch", "namespace": name},
        spec={"selector": es_labels, "ports": [{"port": 9200}]},
        opts=ns_opts,
    )

    # --- Secrets and config ------------------------------------------------

    secret_key = random.RandomPassword(f"{name}-secret-key", length=64, special=False)
    oidc_key = tls.PrivateKey(f"{name}-oidc-key", algorithm="RSA", rsa_bits=4096)

    # The OAuth applications seed.py registers: one per MCP deploy that
    # authenticates against this slug.
    clients_json = pulumi.Output.all(
        *[c.client_id for c in oauth_clients],
        *[c.client_secret for c in oauth_clients],
    ).apply(
        lambda vals: json.dumps(
            [
                {
                    "name": c.name,
                    "client_id": vals[i],
                    "client_secret": vals[len(oauth_clients) + i],
                    "redirect_uri": c.redirect_uri,
                }
                for i, c in enumerate(oauth_clients)
            ]
        )
    )

    extra_sh = "\n".join(["#!/bin/sh", "set -eu", *spec.bootstrap_extra, ""])
    overlay = k8s.core.v1.ConfigMap(
        f"{name}-overlay",
        metadata={"namespace": name, "name": "flp-dev"},
        data={
            "flp_dev_settings.py": (HERE / "flp_dev_settings.py").read_text(),
            "bootstrap.sh": (HERE / "bootstrap.sh").read_text(),
            "seed.py": (HERE / "seed.py").read_text(),
            "extra.sh": extra_sh,
        },
        opts=ns_opts,
    )

    env: dict[str, pulumi.Input[str]] = {
        # Settings selection: CL's own settings plus the overlay.
        "DJANGO_SETTINGS_MODULE": "flp_dev_settings",
        "PYTHONPATH": "/opt/courtlistener:/opt/flp-dev",
        # Django
        "SECRET_KEY": secret_key.result,
        "DEBUG": "off",
        "DEVELOPMENT": "off",
        "ALLOWED_HOSTS": ",".join([*hosts, "localhost", "127.0.0.1"]),
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
        "MCP_OAUTH_CLIENTS": clients_json,
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
        # Bootstrap behaviour
        "DEV_USER_NAME": os.getenv("DEV_USER_NAME", "dev"),
        "DEV_USER_EMAIL": os.getenv("DEV_USER_EMAIL", ""),
        "DEV_USER_PASSWORD": os.getenv("DEV_USER_PASSWORD", ""),
        "TEST_USER_COUNT": str(spec.test_users),
        "SEED_DEV_DATA": "on" if spec.seed_dev_data else "off",
        "INDEX_SEARCH": "on" if spec.index_search else "off",
        "CL_API_TOKEN": os.getenv("CL_API_TOKEN", ""),
        # Misc
        "SENTRY_DSN": os.getenv("CL_SENTRY_DSN", ""),
    }
    if spec.mcp is not None:
        env["OAUTH2_MCP_URL"] = f"https://{mcp_host(spec.mcp)}"
    env.update(spec.env)

    app_env = k8s.core.v1.Secret(
        f"{name}-app-env",
        metadata={"namespace": name, "name": "app-env"},
        string_data=env,
        opts=ns_opts,
    )
    env_from = [{"secret_ref": {"name": app_env.metadata.name}}]
    overlay_volume = {"name": "flp-dev", "config_map": {"name": overlay.metadata.name}}
    overlay_mount = {"name": "flp-dev", "mount_path": "/opt/flp-dev", "read_only": True}

    # --- Bootstrap: migrate, seed, index -----------------------------------

    bootstrap = k8s.batch.v1.Job(
        f"{name}-bootstrap",
        metadata={"namespace": name},
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
                es_service,
                elasticsearch,
            ],
            custom_timeouts=pulumi.CustomTimeouts(create="30m"),
        ),
    )

    # --- Web and celery ----------------------------------------------------

    web_labels = {"app": "web"}
    k8s.apps.v1.Deployment(
        f"{name}-web",
        metadata={"namespace": name, "name": "web"},
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
    k8s.apps.v1.Deployment(
        f"{name}-celery",
        metadata={"namespace": name, "name": "celery"},
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
        f"{name}-web-service",
        metadata={"name": "web", "namespace": name},
        spec={
            "selector": web_labels,
            "ports": [{"name": "http", "port": 80, "target_port": 8000}],
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
                "nginx.ingress.kubernetes.io/proxy-body-size": "100m",
                "nginx.ingress.kubernetes.io/proxy-read-timeout": "180",
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
                                    "service": {"name": "web", "port": {"number": 80}}
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
            depends_on=[web_service, core.ingress, core.cluster_issuer],
        ),
    )

    # The slug's own hostname resolves through the shared wildcard zone;
    # aliases outside it get their own DO Domain.
    project_urns = [public_bucket.bucket_urn, private_bucket.bucket_urn]
    for alias in spec.aliases:
        domain = subdomain(
            f"{name}-alias-{alias.replace('.', '-')}",
            alias,
            core.lb_ip,
            legacy_name=LEGACY_ALIAS_RESOURCES.get(alias),
        )
        project_urns.append(domain.domain_urn)

    return CourtListener(
        slug=slug, host=host, namespace=name, project_urns=project_urns
    )
