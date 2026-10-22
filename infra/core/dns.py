"""DNS under nates.ai, which is on DigitalOcean nameservers but whose zone is
managed by hand (it carries live email records). Creating a DO Domain for a
subdomain gives this stack its own zone that resolves without touching
nates.ai.
"""

import pulumi
import pulumi_digitalocean as do


def zone(resource_name: str, name: str, ip: pulumi.Input[str]) -> do.Domain:
    """A DO Domain for `name` whose apex and every direct child resolve to `ip`.

    One of these serves every slug: a new `cl-<slug>.<name>` needs no DNS
    change at all.
    """
    domain = do.Domain(f"{resource_name}-domain", name=name)
    for label in ("@", "*"):
        do.DnsRecord(
            f"{resource_name}-dns-{'apex' if label == '@' else 'wildcard'}",
            domain=domain.id,
            type="A",
            name=label,
            value=ip,
            ttl=300,
        )
    return domain


def subdomain(resource_name: str, host: str, ip: pulumi.Input[str]) -> do.Domain:
    """A DO Domain for a single host (used for aliases outside the zone)."""
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
