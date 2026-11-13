# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A few providers that speak the protocol, as data (item 171).

A preset saves typing an address and says where the data of a conversation ends up. It promises
nothing else: whether the model behind it calls tools is what the probe finds out, never what a
table says. "Another address" always exists, with no preset: anything that speaks Chat Completions
may be used.

Each address was read on the page of the service's own documentation (``docs``), and the place the
data stays is only what that documentation states: a service whose page does not say where its
servers are is ``unknown``, which is treated as outside for everything that depends on it (the
disguise of personal data, the ENS control). A service whose documentation could not be read was
left out. The operator may declare another place for a provider they have a contract with; a
provider on this machine or the operator's network is told by its address, not by this table.
"""

from __future__ import annotations

from dataclasses import dataclass

from noust.core.exceptions import ValidationError

__all__ = [
    "LOCATIONS",
    "OWN_SIDE",
    "PRESETS",
    "Preset",
    "preset",
]

#: Where what is said to a model ends up: ``machine`` (this server), ``network`` (the operator's
#: own network), ``eu`` (the European Union, as the provider says), ``outside`` (elsewhere) and
#: ``unknown`` (the provider's documentation does not say).
LOCATIONS = ("machine", "network", "eu", "outside", "unknown")

#: The places that are the operator's own: nothing leaves their side, so nothing is disguised.
OWN_SIDE = frozenset({"machine", "network"})


@dataclass(frozen=True)
class Preset:
    """
    One provider, as the settings offer it.

    Attributes:
        id: Its stable name; never renamed once released.
        name: What it is called.
        base_url: The address a client is given, up to its version.
        needs_key: Whether it asks for a key. A guess for a server you run yourself, which the
            probe settles.
        location: One of :data:`LOCATIONS`, as the service's documentation says.
        docs: The page of its documentation the address was read from.
    """

    id: str
    name: str
    base_url: str
    needs_key: bool
    location: str
    docs: str


#: The presets, the ones on this machine first. Six: more would be more to keep true.
PRESETS: tuple[Preset, ...] = (
    Preset(
        "ollama",
        "Ollama",
        "http://localhost:11434/v1",
        False,
        "machine",
        "https://docs.ollama.com/api/openai-compatibility",
    ),
    Preset(
        "lm_studio",
        "LM Studio",
        "http://localhost:1234/v1",
        False,
        "machine",
        "https://lmstudio.ai/docs/developer/openai-compat",
    ),
    Preset(
        "vllm",
        "vLLM",
        "http://localhost:8000/v1",
        False,
        "machine",
        "https://docs.vllm.ai/en/latest/getting_started/quickstart/",
    ),
    Preset(
        "mistral",
        "Mistral",
        "https://api.mistral.ai/v1",
        True,
        "eu",
        "https://docs.mistral.ai/api/",
    ),
    Preset(
        "ionos",
        "IONOS AI Model Hub",
        "https://openai.inference.de-txl.ionos.com/v1",
        True,
        "unknown",
        "https://docs.ionos.com/cloud/ai/ai-model-hub/how-tos/tool-calling",
    ),
    Preset(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        True,
        "unknown",
        "https://openrouter.ai/docs/quickstart",
    ),
)


def preset(preset_id: str) -> Preset:
    """
    Find a preset.

    Args:
        preset_id: Its id.

    Returns:
        The preset.

    Raises:
        ValidationError: When there is no such preset, with the ones there are.
    """
    for item in PRESETS:
        if item.id == preset_id:
            return item
    raise ValidationError(
        f"There is no provider preset called {preset_id!r}",
        details=f"Use one of: {', '.join(p.id for p in PRESETS)}, or give an address.",
        field="preset",
    )
