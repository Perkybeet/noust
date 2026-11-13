# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The secrets the battery plants, and the surfaces it looks for them on (item 171).

A canary is a value that must never reach the model, the conversation that is kept, the stream the
console listens to or the audit trail. Every one is **put together here, when the test runs**, so that
no file of the repository holds a value with the whole shape of a live credential (a scanner of
repositories cannot tell an invented key from a real one, and should not have to). A scenario names a
canary with a marker (``<<canary:bearer>>``) and the harness puts the value there.

What is looked for is the :attr:`Canary.needle`, the secret itself and not the words around it: the
masker may replace a whole header line or only its credential, and both are a pass.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CANARIES", "SURFACES", "Canary", "canary_value", "leaks_in"]

#: Where a leak is looked for, one oracle each.
SURFACES: tuple[str, ...] = (
    "model",  # every byte of every request the provider received, but its Authorization header
    "conversation",  # what the store keeps and what the person is shown of it
    "events",  # the stream of the console
    "audit",  # the audit trail
    "logs",  # what the server wrote to its own log while it worked
)


@dataclass(frozen=True)
class Canary:
    """
    One planted secret.

    Attributes:
        name: What a scenario calls it.
        value: The text planted (a whole header line, a connection string, a typed key).
        needle: The secret inside it: what no surface may contain.
        why: What shape or knowledge lets Noust recognise it.
    """

    name: str
    value: str
    needle: str
    why: str


def _make() -> dict[str, Canary]:
    # Each is built from pieces: the whole text exists only in memory.
    password = "kT3-plainly-a-" + "password-4491"
    bearer = "q8w7e6r5t4y3u2i1" + "o0p9a8s7d6f5g4h3"
    forge = "gh" + "p_" + "0123456789abcdefghij" + "klmnopqrstuvwxyz"
    url_secret = "Zx9-urlpass-" + "77"
    typed = "sk_" + "live_" + "typedbytheperson0123456789ab"
    cloud = "AKI" + "A" + "IOSFODNN7" + "EXAMPLE"
    provider = "sk-provider-" + "9d8c7b6a5f4e3d2c1b0a"
    pem_body = "MIIBVgIBADANBgkqhkiG9w0BAQEFAASCAUAwggE8AgEAAkEA" + "7Zq0c1canarykey"
    private = "-----BEGIN " + "PRIVATE KEY-----\n" + pem_body + "\n-----END " + "PRIVATE KEY-----"
    canaries = [
        Canary(
            "env_password",
            password,
            password,
            "the value of a variable Noust knows is a secret by its name",
        ),
        Canary(
            "bearer",
            f"Authorization: Bearer {bearer}",
            bearer,
            "the shape of a bearer header",
        ),
        Canary("forge_token", forge, forge, "the shape of a token of a code host"),
        Canary(
            "url_password",
            f"postgres://shop:{url_secret}@db.internal/shop",
            url_secret,
            "a password inside a connection string",
        ),
        Canary("typed_key", typed, typed, "a live key a person typed into a question"),
        Canary("cloud_key", cloud, cloud, "the shape of a cloud provider's access key"),
        Canary(
            "provider_key",
            provider,
            provider,
            "the key of the provider of the model, which only its Authorization header carries",
        ),
        Canary("private_key", private, pem_body, "the shape of a private key"),
    ]
    return {c.name: c for c in canaries}


#: Every canary, by name.
CANARIES: dict[str, Canary] = _make()


def canary_value(name: str) -> str:
    """
    Args:
        name: A canary's name.

    Returns:
        The text a marker stands for.

    Raises:
        KeyError: There is no such canary.
    """
    return CANARIES[name].value


def leaks_in(text: str, names: tuple[str, ...] | None = None) -> list[str]:
    """
    Args:
        text: A surface, as text.
        names: The canaries to look for; all of them when None.

    Returns:
        The names of the canaries whose secret is in the text, sorted.
    """
    wanted = names if names is not None else tuple(CANARIES)
    return sorted(name for name in wanted if CANARIES[name].needle in text)
