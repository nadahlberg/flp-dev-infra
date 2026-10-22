"""Point a subdomain of nates.ai at the shared load balancer.

nates.ai is on DigitalOcean nameservers but its zone is managed by hand (it
carries live email records). Creating a DO Domain for the subdomain itself
gives this stack its own zone that resolves without touching nates.ai.
"""

import pulumi
import pulumi_digitalocean as do


def subdomain(resource_name: str, host: str, ip: pulumi.Input[str]) -> do.Domain:
    """Create a DO Domain for `host` with an apex A record at `ip`."""
    domain = do.Domain(f"{resource_name}-domain", name=host)
    do.DnsRecord(
        f"{resource_name}-dns",
        domain=domain.id,
        type="A",
        name="@",
        value=ip,
        ttl=300,
    )
    return domain
