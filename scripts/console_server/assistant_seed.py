# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The built-in assistant of the sandboxed console, talking to a model that answers what a test dictates.

No real provider is ever asked: the "provider" is :class:`tests.support.scripted_model.ScriptedModel`, a
server of the protocol on a loopback port of this process, and a browser test dictates what it answers
with ``POST <model>/__script`` (see :meth:`ScriptedModel._control`). The console's own code is the real one:
the settings are written by the product's own store, the probe is the product's probe, and the turns the
assistant takes run in the product's own loop, so what a browser sees is what a person would.
"""

from __future__ import annotations

from typing import Any

#: What ``--assistant`` can ask for.
MODES = ("off", "ready", "on", "no-tools")

#: The model the sandbox's provider lists, and the name a person sees.
MODEL = "tiny-model"

#: The reply that makes the probe see a model that calls tools well, and the one that does not.
_TOY_CALL = {"calls": [{"name": "get_status", "arguments": {"service": "noust"}}]}
_NO_CALL = {"say": "I can only talk."}


def seed_assistant(mode: str) -> dict[str, Any]:
    """
    Start the scripted model and bring the assistant to the state a test asks for.

    Args:
        mode: ``off`` leaves everything as a fresh server is (nothing is started); ``ready`` starts the
            model and leaves it unconfigured, so the page that turns the assistant on can do it end to
            end; ``on`` configures it, probes it and turns it on; ``no-tools`` does the same with a model
            that the probe sees answer but not call tools.

    Returns:
        What the announcement line says of it: the address of the model's control route.
    """
    if mode == "off":
        return {}
    from noust.core.assistant.probe import probe_configured
    from noust.core.assistant.settings import AssistantSettings
    from tests.support.scripted_model import Reply, ScriptedModel

    model = ScriptedModel(models=(MODEL, "tiny-model-large"))
    model.start()
    # Kept for as long as the process lives: the server thread is a daemon, and the object holds the queue.
    _MODELS.append(model)
    info: dict[str, Any] = {"assistant_model": model.base_url.removesuffix("/v1")}
    if mode == "ready":
        return info
    settings = AssistantSettings()
    settings.update(actor="seed", base_url=model.base_url, model=MODEL, allow_private_host=True)
    model.script(Reply.from_dict(_TOY_CALL if mode == "on" else _NO_CALL))
    probe_configured(settings, settings.read())
    settings.enable(actor="seed")
    return info


_MODELS: list[Any] = []
