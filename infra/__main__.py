import pulumi
import pulumi_digitalocean as do

import core
import courtlistener
import mcp

do.ProjectResources(
    "project-resources",
    project=core.project.id,
    resources=core.PROJECT_URNS + courtlistener.PROJECT_URNS + mcp.PROJECT_URNS,
)

pulumi.export("cluster_name", core.cluster.name)
pulumi.export(
    "kubeconfig", pulumi.Output.secret(core.cluster.kube_configs[0].raw_config)
)
pulumi.export("load_balancer_ip", core.lb_ip)
pulumi.export("postgres_host", core.postgres.host)
pulumi.export("postgres_port", core.postgres.port)
pulumi.export("postgres_user", core.postgres.user)
pulumi.export("postgres_password", pulumi.Output.secret(core.postgres.password))
pulumi.export("courtlistener_url", f"https://{courtlistener.DOMAIN}")
pulumi.export("courtlistener_image", courtlistener.image_ref)
pulumi.export("courtlistener_static_domain", courtlistener.storage_domain)
pulumi.export("mcp_url", f"https://{mcp.DOMAIN}")
pulumi.export("mcp_image", mcp.image_ref)
