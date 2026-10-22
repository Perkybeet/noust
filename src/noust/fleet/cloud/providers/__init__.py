# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The providers a central can create servers at, and the one place that names them.

Adding a provider is one module with a :class:`~noust.fleet.cloud.providers.base.CloudProvider`
subclass and one line in :data:`PROVIDERS`; the accounts, the console, the CLI and the
provisioning job read this table and nothing else.
"""

from __future__ import annotations

from typing import Any

from noust.core.exceptions import ValidationError
from noust.fleet.cloud.providers.base import CloudProvider
from noust.fleet.cloud.providers.digitalocean import DigitalOcean
from noust.fleet.cloud.providers.hetzner import HetznerCloud
from noust.fleet.cloud.providers.vultr import Vultr

#: Every provider, by the key accounts record.
PROVIDERS: dict[str, type[CloudProvider]] = {
    provider.kind: provider for provider in (HetznerCloud, DigitalOcean, Vultr)
}

__all__ = ["PROVIDERS", "CloudProvider", "describe_providers", "provider_class", "provider_for"]


def provider_class(kind: str) -> type[CloudProvider]:
    """
    Find a provider by its key.

    Args:
        kind: ``hetzner``, ``digitalocean`` or ``vultr``.

    Returns:
        Its adapter class.

    Raises:
        ValidationError: When no provider has that key.
    """
    found = PROVIDERS.get(kind) if isinstance(kind, str) else None
    if found is None:
        raise ValidationError(
            f"Unknown provider: {kind!r}",
            details=f"Use one of: {', '.join(PROVIDERS)}.",
            field="provider",
        )
    return found


def provider_for(kind: str, token: str, **options: Any) -> CloudProvider:
    """
    Build the adapter for a provider and a token.

    Args:
        kind: The provider's key.
        token: The account's API token.
        **options: Passed to the adapter (a test's opener or sleep).

    Returns:
        The adapter.

    Raises:
        ValidationError: When no provider has that key.
    """
    return provider_class(kind)(token, **options)


def describe_providers() -> list[dict[str, Any]]:
    """
    Describe every provider for a form: what it is called, where its token is made.

    Returns:
        One entry per provider.
    """
    return [
        {
            "kind": provider.kind,
            "name": provider.display_name,
            "token_url": provider.credentials_url,
            "token_help": provider.credentials_help,
            "capabilities": provider.capabilities.to_dict(),
            "user_data_limit": provider.user_data_limit,
        }
        for provider in PROVIDERS.values()
    ]
