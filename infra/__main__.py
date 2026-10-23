"""One droplet running a dev tunnel: sish behind Caddy.

Anyone listed in the repo's `users` file can expose a local port at
https://<name>.flp-tunnel.xyz with a single ssh command. See README.md.
"""

import os
from pathlib import Path

import pulumi
import pulumi_digitalocean as do
import yaml

DOMAIN = "flp-tunnel.xyz"  # dedicated to the tunnel; the zone is created below
ADMIN = "nadahlberg"  # GitHub user whose keys get root on the droplet
REGION = "nyc3"

REPO = Path(__file__).resolve().parent.parent


def repo_file(path: str, mode: str = "0644") -> dict:
    """A cloud-init write_files entry mirroring a repo file to /opt/tunnel."""
    return {
        "path": f"/opt/tunnel/{path}",
        "content": (REPO / path).read_text(),
        "permissions": mode,
    }


cloud_config = {
    "package_update": True,
    "packages": ["docker.io", "docker-compose-v2", "rsync", "ssh-import-id"],
    "write_files": [
        repo_file("docker-compose.yml"),
        repo_file("docker/caddy/Dockerfile"),
        repo_file("docker/caddy/Caddyfile"),
        repo_file("scripts/sync-keys.sh", mode="0755"),
        {
            "path": "/opt/tunnel/.env",
            "content": f"DO_DNS_TOKEN={os.environ['DIGITALOCEAN_TOKEN']}\n",
            "permissions": "0600",
        },
        {
            "path": "/etc/cron.d/sync-keys",
            "content": "* * * * * root /opt/tunnel/scripts/sync-keys.sh\n",
        },
    ],
    "runcmd": [
        f"ssh-import-id gh:{ADMIN}",
        "/opt/tunnel/scripts/sync-keys.sh",
        "cd /opt/tunnel && docker compose up --build --detach",
    ],
}

droplet = do.Droplet(
    "tunnel",
    name="flp-dev-tunnel",
    image="ubuntu-24-04-x64",
    size="s-1vcpu-2gb",
    region=REGION,
    user_data=pulumi.Output.secret("#cloud-config\n" + yaml.safe_dump(cloud_config)),
)

# The reserved IP keeps DNS stable across droplet replacements (any change
# to the files above replaces the droplet).
ip = do.ReservedIp("ip", region=REGION)
do.ReservedIpAssignment(
    "ip-assignment", ip_address=ip.ip_address, droplet_id=droplet.id.apply(int)
)

zone = do.Domain("zone", name=DOMAIN)
do.DnsRecord(
    "dns-tunnel", domain=zone.id, type="A", name="tunnel", value=ip.ip_address, ttl=300
)
do.DnsRecord(
    "dns-wildcard", domain=zone.id, type="A", name="*", value=ip.ip_address, ttl=300
)

do.Firewall(
    "firewall",
    name="flp-dev-tunnel",
    droplet_ids=[droplet.id.apply(int)],
    inbound_rules=[
        do.FirewallInboundRuleArgs(
            protocol="tcp", port_range=port, source_addresses=["0.0.0.0/0", "::/0"]
        )
        # admin ssh, http, https, sish
        for port in ("22", "80", "443", "2222")
    ],
    outbound_rules=[
        do.FirewallOutboundRuleArgs(
            protocol=protocol,
            port_range="all",
            destination_addresses=["0.0.0.0/0", "::/0"],
        )
        for protocol in ("tcp", "udp")
    ],
)

do.Project(
    "project",
    name="flp-dev",
    description=f"Dev tunnel: https://<name>.{DOMAIN} -> a local port",
    purpose="Web Application",
    environment="Development",
    resources=[droplet.droplet_urn],
)

pulumi.export("ip", ip.ip_address)
pulumi.export("connect", f"ssh -p 2222 -R <name>:80:localhost:<port> tunnel.{DOMAIN}")
