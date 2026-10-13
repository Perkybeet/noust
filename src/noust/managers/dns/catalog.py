# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS providers Noust can answer a DNS-01 challenge through.

Two kinds, and the difference matters to what an account can do:

- **Native** (Cloudflare): Noust speaks the provider's API itself
  (:mod:`noust.managers.dns.cloudflare`). It lists the zones, creates and deletes
  records with an ownership marker, answers the DNS-01 challenge through its own
  hook, and runs tunnels. No certbot plugin needs to be installed.
- **Plugin** (everyone else listed): certbot's own DNS plugin answers the
  challenge, reading a credentials file Noust writes (0600, in the secret store).
  Nothing else is automated for these - no managed records - because Noust does not
  speak their APIs. The plugin must be installed; ``noust dns provider add`` says
  which package when it is not.

The credential templates below are the plugins' documented option names; the file
is written exactly as certbot reads it, because certbot reads it again at every
renewal, long after anyone is looking.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialField:
    """
    One piece of what an account needs.

    Attributes:
        name: The key in the request and the credentials file template.
        label: What the console asks for it.
        secret: It is a credential, never shown again.
        required: The account cannot work without it.
        default: What it is when not given.
    """

    name: str
    label: str
    secret: bool = True
    required: bool = True
    default: str = ""


@dataclass(frozen=True)
class ProviderKind:
    """
    A kind of DNS provider.

    Attributes:
        kind: Its name.
        label: How the console calls it.
        native: Noust speaks its API (managed records, hook, tunnels).
        fields: What an account needs.
        plugin: certbot's authenticator for the plugin kinds, ``dns-digitalocean``.
        template: The credentials file the plugin reads, with ``{field}`` slots.
        package: The package that installs the plugin, for the error that asks for it.
        help: Where the credentials come from, one sentence.
    """

    kind: str
    label: str
    native: bool
    fields: tuple[CredentialField, ...]
    plugin: str | None = None
    template: str = ""
    package: str = ""
    help: str = ""


KINDS: dict[str, ProviderKind] = {
    "cloudflare": ProviderKind(
        kind="cloudflare",
        label="Cloudflare",
        native=True,
        fields=(
            CredentialField("token", "API token"),
            CredentialField("account_id", "Account id (tunnels)", secret=False, required=False),
        ),
        help="An API token with Zone:Read and DNS:Edit on the zones, and for tunnels "
        "Account > Cloudflare Tunnel:Edit (dash.cloudflare.com/profile/api-tokens).",
    ),
    "digitalocean": ProviderKind(
        kind="digitalocean",
        label="DigitalOcean",
        native=False,
        fields=(CredentialField("token", "API token"),),
        plugin="dns-digitalocean",
        template="dns_digitalocean_token = {token}\n",
        package="python3-certbot-dns-digitalocean",
        help="A personal access token with write scope.",
    ),
    "linode": ProviderKind(
        kind="linode",
        label="Linode / Akamai",
        native=False,
        fields=(CredentialField("key", "API key"),),
        plugin="dns-linode",
        template="dns_linode_key = {key}\ndns_linode_version = 4\n",
        package="python3-certbot-dns-linode",
        help="A personal access token with Domains read/write.",
    ),
    "ovh": ProviderKind(
        kind="ovh",
        label="OVH",
        native=False,
        fields=(
            CredentialField("endpoint", "Endpoint", secret=False, default="ovh-eu"),
            CredentialField("application_key", "Application key", secret=False),
            CredentialField("application_secret", "Application secret"),
            CredentialField("consumer_key", "Consumer key"),
        ),
        plugin="dns-ovh",
        template=(
            "dns_ovh_endpoint = {endpoint}\n"
            "dns_ovh_application_key = {application_key}\n"
            "dns_ovh_application_secret = {application_secret}\n"
            "dns_ovh_consumer_key = {consumer_key}\n"
        ),
        package="python3-certbot-dns-ovh",
        help="An OVH API application with the consumer key granted on /domain/zone.",
    ),
    "rfc2136": ProviderKind(
        kind="rfc2136",
        label="RFC 2136 (BIND and others)",
        native=False,
        fields=(
            CredentialField("server", "Name server", secret=False),
            CredentialField("port", "Port", secret=False, required=False, default="53"),
            CredentialField("name", "Key name", secret=False),
            CredentialField("secret", "Key secret (base64)"),
            CredentialField("algorithm", "Algorithm", secret=False, default="HMAC-SHA512"),
        ),
        plugin="dns-rfc2136",
        template=(
            "dns_rfc2136_server = {server}\n"
            "dns_rfc2136_port = {port}\n"
            "dns_rfc2136_name = {name}\n"
            "dns_rfc2136_secret = {secret}\n"
            "dns_rfc2136_algorithm = {algorithm}\n"
        ),
        package="python3-certbot-dns-rfc2136",
        help="A TSIG key the name server accepts dynamic updates with.",
    ),
    "hetzner": ProviderKind(
        kind="hetzner",
        label="Hetzner DNS",
        native=False,
        fields=(CredentialField("api_token", "API token"),),
        plugin="dns-hetzner",
        template="dns_hetzner_api_token = {api_token}\n",
        package="certbot-dns-hetzner (pip)",
        help="A Hetzner DNS API token.",
    ),
    "gandi": ProviderKind(
        kind="gandi",
        label="Gandi LiveDNS",
        native=False,
        fields=(CredentialField("token", "Personal access token"),),
        plugin="dns-gandi",
        template="dns_gandi_token = {token}\n",
        package="certbot-plugin-gandi (pip)",
        help="A Gandi personal access token with LiveDNS rights.",
    ),
}


def kind_of(kind: str) -> ProviderKind | None:
    """
    Args:
        kind: A provider's name.

    Returns:
        Its description, or None when Noust does not know it.
    """
    return KINDS.get(kind)
