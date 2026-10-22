# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS providers Noust can answer a DNS-01 challenge through.

Two kinds, and the difference matters to what an account can do:

- **Native** (Cloudflare, DigitalOcean, Hetzner, Gandi, Amazon Route 53): Noust speaks the
  provider's API itself (:mod:`noust.managers.dns`). It lists the zones, creates and deletes
  records under the ownership rules, and answers the DNS-01 challenge through its own hook.
  No certbot plugin needs to be installed. Cloudflare also runs tunnels. A provider that keeps
  no note with a record (``comments`` false) cannot carry the ownership marker at the provider;
  there Noust deletes only a record it made that is still exactly what it made.
- **Plugin** (the others listed): certbot's own DNS plugin answers the challenge, reading a
  credentials file Noust writes (0600, in the secret store). Nothing else is automated for
  these - no managed records - because Noust does not speak their APIs. The plugin must be
  installed; ``noust dns provider add`` says which package when it is not.

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
        tunnels: It can carry tunnels (only Cloudflare).
        comments: The provider keeps a note with each record, which is where the ownership
            marker is written and read back; without it the marker rests on what Noust made.
        propagation: Seconds a challenge record is waited for, when the account sets none; 0
            for the module's default.
    """

    kind: str
    label: str
    native: bool
    fields: tuple[CredentialField, ...]
    plugin: str | None = None
    template: str = ""
    package: str = ""
    help: str = ""
    tunnels: bool = False
    comments: bool = True
    propagation: int = 0


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
        tunnels=True,
    ),
    "digitalocean": ProviderKind(
        kind="digitalocean",
        label="DigitalOcean",
        native=True,
        fields=(CredentialField("token", "API token"),),
        help="A personal access token with write scope (cloud.digitalocean.com/account/api/tokens).",
        comments=False,
    ),
    "route53": ProviderKind(
        kind="route53",
        label="Amazon Route 53",
        native=True,
        fields=(
            CredentialField("access_key_id", "Access key id", secret=False),
            CredentialField("secret_access_key", "Secret access key"),
        ),
        help="The access key of an IAM user allowed route53:ListHostedZones, "
        "route53:ListResourceRecordSets and route53:ChangeResourceRecordSets.",
        comments=False,
        propagation=60,
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
        native=True,
        fields=(CredentialField("token", "Cloud API token"),),
        help="An API token of the Cloud project that holds the zones, with read and write "
        "rights (console.hetzner.com, Security, API tokens). Zones still on the old DNS Console "
        "must be migrated to the Cloud Console first.",
        propagation=30,
    ),
    "gandi": ProviderKind(
        kind="gandi",
        label="Gandi LiveDNS",
        native=True,
        fields=(CredentialField("token", "Personal access token"),),
        help="A Gandi personal access token with the right to manage the domains' technical "
        "configuration (LiveDNS).",
        comments=False,
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
