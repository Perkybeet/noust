# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a provider turned out to be able to do (item 171).

A preset promises nothing: the probe is what tells the truth. It asks the provider what models it
has (``GET <base>/models``) and for one call to a toy tool, and says what it found as sentences
with a code, the way every other thing Noust tells a person is said:

- whether the provider answers, and why not when it does not (the client's own sentences: the
  address is refused, the key is refused, nobody is listening);
- whether it has the model that was chosen;
- whether the model made the toy call **well formed**: the right tool, arguments that are a JSON
  object with the field asked for. A model that did not is only offered the part of the assistant
  that explains: it neither investigates nor acts, which is the owner's rule for everything in
  this release (what cannot be done is not offered);
- and, when the model runs on this very machine, an honest word about its memory: below 16 GB and
  with no GPU it will not have the room to investigate, said with this machine's own figure and no
  promise about speed.

What the probe sends is a fixed sentence and a fixed toy tool: nothing of this server, and the key
only in the header.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from noust.core.assistant.client import (
    CancelToken,
    MalformedArguments,
    ProviderClient,
    ProviderError,
)
from noust.core.server_text import ServerText, say

__all__ = [
    "MEMORY_FLOOR_GB",
    "Machine",
    "ProbeResult",
    "probe_configured",
    "read_machine",
    "run_probe",
]

logger = logging.getLogger(__name__)

#: Below this much memory, and with no GPU, a model on this machine will not investigate.
MEMORY_FLOOR_GB = 16.0

#: Devices whose presence says there is a GPU a model could use.
_GPU_DEVICES = ("/dev/nvidia0", "/dev/kfd", "/dev/dri/renderD128")

_TOY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_status",
        "description": "Report the status of a service.",
        "parameters": {
            "type": "object",
            "properties": {"service": {"type": "string", "description": "The service's name."}},
            "required": ["service"],
        },
    },
}
_TOY_QUESTION = (
    'Call the tool get_status with the service set to "probe". Answer with that call only, '
    "not in words."
)

#: What a failure of the model list means that nothing else will fix: the call is not tried.
_STOPS = frozenset(
    {
        "assistant.provider.destination_refused",
        "assistant.provider.unresolved",
        "assistant.provider.insecure_address",
        "assistant.provider.bad_address",
        "assistant.provider.unreachable",
        "assistant.provider.unauthorized",
        "assistant.provider.timeout",
        "assistant.provider.cancelled",
        "assistant.provider.redirected",
        "assistant.provider.server_error",
        "assistant.provider.rate_limited",
    }
)


@dataclass(frozen=True)
class Machine:
    """
    What this machine has, as far as running a model on it is concerned.

    Attributes:
        memory_gb: Its memory in gigabytes (decimal), to one decimal.
        gpu: Whether a GPU a model could use is there.
    """

    memory_gb: float
    gpu: bool


def read_machine() -> Machine | None:
    """
    Read this machine's memory and whether it has a GPU.

    Returns:
        What it has; None when its memory cannot be read.
    """
    try:
        import psutil

        total = psutil.virtual_memory().total
    except (ImportError, OSError) as exc:
        logger.warning("The machine's memory could not be read for the probe: %s", exc)
        return None
    return Machine(
        memory_gb=round(total / 1e9, 1),
        gpu=any(Path(device).exists() for device in _GPU_DEVICES),
    )


@dataclass(frozen=True)
class ProbeResult:
    """
    What the probe found.

    Attributes:
        reachable: The provider answered something.
        model_found: The model is in the provider's list; None when it gives no list.
        tools: The model made the toy call well formed; None when it was not tried.
        models: The models the provider lists (the first fifty).
        sentences: What it found, in order, as sentences.
    """

    reachable: bool
    model_found: bool | None
    tools: bool | None
    models: list[str] = field(default_factory=list)
    sentences: tuple[ServerText, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as plain data, for the store and the API: each sentence with its code, its
            values, its English text and its fix.
        """
        return {
            "reachable": self.reachable,
            "model_found": self.model_found,
            "tools": self.tools,
            "models": list(self.models),
            "sentences": [
                {**sentence.to_dict(), "text": sentence.text, "hint": sentence.hint}
                for sentence in self.sentences
            ],
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> ProbeResult:
        """
        Args:
            document: What :meth:`to_dict` wrote.

        Returns:
            The result; a sentence whose code is no longer in the catalog is left out.
        """
        sentences: list[ServerText] = []
        for item in document.get("sentences") or []:
            try:
                sentences.append(say(str(item["code"]), **dict(item.get("params") or {})))
            except (KeyError, ValueError, TypeError):
                continue
        return cls(
            reachable=bool(document.get("reachable")),
            model_found=document.get("model_found"),
            tools=document.get("tools"),
            models=[str(name) for name in document.get("models") or []],
            sentences=tuple(sentences),
        )


def run_probe(
    client: ProviderClient,
    *,
    local: bool = False,
    machine: Machine | None = None,
    cancel: CancelToken | None = None,
) -> ProbeResult:
    """
    Find out what a provider can do.

    Args:
        client: The provider's client, with its model and key.
        local: Whether the model runs on this machine, which earns it the word about memory.
        machine: What this machine has; asked of :func:`read_machine` by the caller.
        cancel: Set from another thread to stop the probe.

    Returns:
        What was found. Never raises for what a provider does wrong: that is the finding.
    """
    sentences: list[ServerText] = []
    host = urlsplit(client.base_url).hostname or client.base_url
    models: list[str] = []
    listed = False
    try:
        models = client.list_models(cancel=cancel)
        listed = True
    except ProviderError as refusal:
        if refusal.code in _STOPS:
            return ProbeResult(False, None, None, [], (refusal.sentence,))
    model_found: bool | None = (client.model in models) if listed else None
    if listed:
        sentences.append(say("assistant.probe.answers", host=host))
        if not model_found:
            sentences.append(say("assistant.probe.model_missing", model=client.model))

    tools: bool | None = None
    reachable = listed
    try:
        done = client.complete(
            [{"role": "user", "content": _TOY_QUESTION}], [_TOY_TOOL], cancel=cancel
        )
    except ProviderError as refusal:
        if refusal.code == "assistant.provider.rejected":
            reachable = True
            tools = False
            sentences.extend(
                [refusal.sentence, say("assistant.probe.no_tools", model=client.model)]
            )
        else:
            reachable = reachable or refusal.code == "assistant.provider.not_found"
            sentences.append(refusal.sentence)
    else:
        if not listed:
            sentences.append(say("assistant.probe.answers", host=host))
        reachable = True
        tools = _made_the_call(done.tool_calls)
        sentences.append(
            say(
                "assistant.probe.tools_ok" if tools else "assistant.probe.no_tools",
                model=client.model,
            )
        )

    if local and machine is not None and machine.memory_gb < MEMORY_FLOOR_GB and not machine.gpu:
        sentences.append(say("assistant.probe.memory_low", memory_gb=machine.memory_gb))
    return ProbeResult(reachable, model_found, tools, models[:50], tuple(sentences))


def probe_configured(store: Any, current: Any) -> ProbeResult:
    """
    Probe the provider that is chosen and keep what was found with the settings.

    The one place that builds the client of a probe, for the console's route and the command line.

    Args:
        store: The :class:`~noust.core.assistant.settings.AssistantSettings`.
        current: Its settings as they are.

    Returns:
        What the probe found.
    """
    client = ProviderClient(
        current.base_url,
        current.model,
        api_key=store.api_key(),
        read_timeout=30.0,
        total_timeout=90.0,
        max_retries=1,
    )
    result = run_probe(client, local=current.location == "machine", machine=read_machine())
    store.record_probe(result.to_dict())
    return result


def _made_the_call(calls: tuple[Any, ...]) -> bool:
    """
    Args:
        calls: The calls the model made.

    Returns:
        Whether one is the toy call, well formed: the right tool and a JSON object whose
        ``service`` is text.
    """
    for call in calls:
        if call.name != "get_status":
            continue
        try:
            arguments = call.arguments_object()
        except MalformedArguments:
            continue
        if isinstance(arguments.get("service"), str):
            return True
    return False
