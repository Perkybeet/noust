# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the session says of the built-in assistant, so that the console draws nothing when there is
nothing to offer (item 171).

``GET /api/auth/session`` is how the console starts, so this is read on every sign-in and must cost
nothing when the assistant is off: it imports none of the assistant (only :mod:`noust.core.assistant.state`,
which reads two facts from one row), and it never fails the session.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

from noust.core.exceptions import NoustError
from noust.core.server_text import ServerText, say
from noust.web.api.deps import CodedText
from noust.web.permissions import Permission

logger = logging.getLogger(__name__)


#: Why the assistant is not offered to a caller, the whole list. The console reads the code to decide what to
#: draw, so it is one of these and never a word it has to guess: ``off`` (nobody turned it on), ``permission``
#: (the caller's role does not let them use it) and ``account`` (the caller is not a person's account: the
#: master token, a central). It is not a row of the table of functions (:mod:`noust.central.features`): that
#: table says what the role and the machine can do, and the assistant needs neither. It talks to a provider
#: and calls the API as the person, so a central that deploys nothing offers it like any other Noust, and its
#: one answer is this one, in the session.
ASSISTANT_REASONS = (
    "assistant.unavailable.off",
    "assistant.unavailable.permission",
    "assistant.unavailable.account",
)


class AssistantReason(CodedText):
    """
    Why the assistant is not available to this caller, as a sentence with a code.

    Attributes:
        code: One of :data:`ASSISTANT_REASONS`: ``assistant.unavailable.off``,
            ``assistant.unavailable.permission`` or ``assistant.unavailable.account``.
        text: The sentence in English, always sent.
        hint: How to fix it in English, when the sentence has a fix.
    """

    code: Literal[
        "assistant.unavailable.off",
        "assistant.unavailable.permission",
        "assistant.unavailable.account",
    ]
    text: str
    hint: str | None = None


class AssistantSession(BaseModel):
    """
    The assistant, as the session shows it.

    Attributes:
        enabled: The assistant is on.
        can_use: It is on and this caller may use it: draw it.
        tools: The model was found to call tools, so it may investigate; False means it only
            explains what it is shown. Meaningless unless ``enabled``.
        reason: Why ``can_use`` is False, when it is: one of a closed list, so the console compares nothing
            it has to know the spelling of.
        model: The model it talks to, for the label that says it is an AI and which one. Only for
            a caller who may use it.
        location: Where what is said to it ends up: ``machine``, ``network``, ``eu``, ``outside``
            or ``unknown``. Only for a caller who may use it.
        mask_personal: Emails and addresses are disguised before they are sent. Only for a caller
            who may use it.
    """

    enabled: bool = False
    can_use: bool = False
    tools: bool = False
    reason: AssistantReason | None = None
    model: str | None = None
    location: str | None = None
    mask_personal: bool | None = None


def _reason(sentence: ServerText) -> AssistantReason:
    """
    Args:
        sentence: A sentence of the catalog.

    Returns:
        It as the session carries it: code, values, English text and fix.
    """
    return AssistantReason(**sentence.to_dict(), text=sentence.text, hint=sentence.hint)


def session_assistant(session: Mapping[str, Any]) -> AssistantSession:
    """
    Say what the assistant is for the caller of a session.

    Args:
        session: The authenticated payload.

    Returns:
        Whether it is on, whether this caller may use it and why not when they may not. Never
        raises: a store that cannot be read is an assistant that is off.
    """
    from noust.core.assistant.state import read_state

    # A central that deploys nothing is offered it like any other Noust: what it looks at there is the
    # servers the central manages, through the central.
    try:
        state = read_state()
        enabled, tools = state.enabled, state.calls_tools
    except (NoustError, sqlite3.Error, OSError) as exc:
        logger.warning("The state of the assistant could not be read: %s", exc)
        return AssistantSession(reason=_reason(say("assistant.unavailable.off")))
    if not enabled:
        return AssistantSession(reason=_reason(say("assistant.unavailable.off")))
    if Permission.ASSISTANT_USE not in (session.get("permissions") or ()):
        return AssistantSession(
            enabled=True, tools=tools, reason=_reason(say("assistant.unavailable.permission"))
        )
    if session.get("account_id") is None and session.get("owner_account_id") is None:
        return AssistantSession(
            enabled=True, tools=tools, reason=_reason(say("assistant.unavailable.account"))
        )
    return AssistantSession(
        enabled=True,
        can_use=True,
        tools=tools,
        model=state.model,
        location=state.location,
        mask_personal=state.mask_personal,
    )
