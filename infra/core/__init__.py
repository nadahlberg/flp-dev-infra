"""Shared resources: the DO project, cluster, database, ingress, TLS and the
DNS zone every slug lives under.

Everything here is created once and pointed at by the per-slug deploys,
which never create their own cluster, load balancer or database cluster.
Resource names here are load-bearing: renaming one makes Pulumi replace it.
"""

import pulumi
import pulumi_digitalocean as do
import pulumi_kubernetes as k8s

from core.dns import zone
from core.project import project_args
from deploys import ZONE

config = pulumi.Config()
region = config.get("region") or "nyc3"
node_size = config.get("nodeSize") or "s-4vcpu-8gb"
node_count = config.get_int("nodeCount") or 3

PROJECT_NAME = "flp-dev"
ACME_EMAIL = "nadahlberg@gmail.com"
LB_NAME = "flp-dev-lb"

project_inputs, project_import_id = project_args(PROJECT_NAME, do.get_project)
project = do.Project(
    "project",
    **{
        "description": "Disposable CourtListener + MCP deployment for OAuth testing",
        "purpose": "Operational / Developer tooling",
        "environment": "Development",
        **project_inputs,
    },
    opts=pulumi.ResourceOptions(import_=project_import_id),
)

postgres = do.DatabaseCluster(
    "flp-dev-db",
    name="flp-dev-db",
    engine="pg",
    version="17",
    region=region,
    size="db-s-1vcpu-1gb",
    node_count=1,
)

cluster = do.KubernetesCluster(
    "flp-dev-k8s",
    name="flp-dev-k8s",
    region=region,
    version=do.get_kubernetes_versions().latest_version,
    node_pool=do.KubernetesClusterNodePoolArgs(
        name="default",
        size=node_size,
        node_count=node_count,
    ),
)

do.DatabaseFirewall(
    "flp-dev-db-firewall",
    cluster_id=postgres.id,
    rules=[do.DatabaseFirewallRuleArgs(type="k8s", value=cluster.id)],
)

doks = k8s.Provider(
    "doks",
    kubeconfig=cluster.kube_configs[0].raw_config,
)

ingress = k8s.helm.v3.Release(
    "flp-dev-ingress",
    chart="ingress-nginx",
    version="4.12.1",
    namespace="ingress-nginx",
    create_namespace=True,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://kubernetes.github.io/ingress-nginx",
    ),
    values={
        "fullnameOverride": "ingress-nginx",
        "controller": {
            "service": {
                "annotations": {
                    "service.beta.kubernetes.io/do-loadbalancer-name": LB_NAME,
                    "service.beta.kubernetes.io/do-loadbalancer-type": "REGIONAL",
                    "service.beta.kubernetes.io/do-loadbalancer-size-unit": "1",
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(provider=doks),
)

cert_manager = k8s.helm.v3.Release(
    "flp-dev-cert-manager",
    chart="cert-manager",
    version="1.16.3",
    namespace="cert-manager",
    create_namespace=True,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://charts.jetstack.io",
    ),
    values={"crds": {"enabled": True}},
    opts=pulumi.ResourceOptions(provider=doks),
)

cluster_issuer = k8s.apiextensions.CustomResource(
    "flp-dev-cluster-issuer",
    api_version="cert-manager.io/v1",
    kind="ClusterIssuer",
    metadata={"name": "letsencrypt"},
    spec={
        "acme": {
            "server": "https://acme-v02.api.letsencrypt.org/directory",
            "email": ACME_EMAIL,
            "privateKeySecretRef": {"name": "letsencrypt"},
            "solvers": [{"http01": {"ingress": {"ingressClassName": "nginx"}}}],
        },
    },
    opts=pulumi.ResourceOptions(provider=doks, depends_on=[cert_manager]),
)

ingress_controller_service = k8s.core.v1.Service.get(
    "ingress-controller",
    pulumi.Output.concat(ingress.status.namespace, "/ingress-nginx-controller"),
    opts=pulumi.ResourceOptions(provider=doks),
)

# Public IP of flp-dev-lb; every hostname points at it.
lb_ip = ingress_controller_service.status.load_balancer.ingress[0].ip

# flp-dev.nates.ai with a wildcard: every slug's hostname resolves with no
# DNS work per deploy.
shared_zone = zone("flp-dev-zone", ZONE, lb_ip)

PROJECT_URNS = [
    postgres.cluster_urn,
    cluster.cluster_urn,
    shared_zone.domain_urn,
]
