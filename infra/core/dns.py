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


def subdomain(
    resource_name: str,
    host: str,
    ip: pulumi.Input[str],
    legacy_name: str | None = None,
) -> do.Domain:
    """A DO Domain for a single host (used for aliases outside the zone).

    legacy_name: the resource prefix this host was created under before a
    rename; Pulumi then treats the new names as the same resources.
    """
    domain_opts = record_opts = None
    if legacy_name:
        domain_opts = pulumi.ResourceOptions(
            aliases=[pulumi.Alias(name=f"{legacy_name}-domain")]
        )
        record_opts = pulumi.ResourceOptions(
            aliases=[pulumi.Alias(name=f"{legacy_name}-dns")]
        )
    domain = do.Domain(f"{resource_name}-domain", name=host, opts=domain_opts)
    do.DnsRecord(
        f"{resource_name}-dns",
        domain=domain.id,
        type="A",
        name="@",
        value=ip,
        ttl=300,
        opts=record_opts,
    )
    return domain
