# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking the assistant from the machine it runs on: ``noust assistant ask`` (item 171).

The terminal is a client of the same loop the console's panel is (rule 3). Run as root on the server, it
names the account the assistant acts for (``--as``), builds the console's own application in this process and
runs one turn against it **as that account's assistant token**, so what it may read is what that account may read
and what it may change is what the policy lets it: the same API, the same teams, the same audit trail. The turn
runs in the calling thread, the text comes out as it is said, and what the assistant **proposes is returned and
never carried out**: a person does it in the console, in their own session.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from typing import Any

from noust.core.assistant.client import CancelToken
from noust.core.assistant.conversations import Conversations
from noust.core.assistant.records import Turns
from noust.core.assistant.settings import AssistantSettings
from noust.core.assistant.state import read_state
from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.server_text import say
from noust.core.teams.scope import bind
from noust.validators.assistant_page import validate_page
from noust.web.assistant.bridge import LoopBridge
from noust.web.assistant.identity import AssistantIdentity
from noust.web.assistant.loop import TokenCache, TurnContext, TurnRunner
from noust.web.assistant.present import present_turn
from noust.web.assistant.reach import resolve_reach
from noust.web.assistant.toolbox import Reach

__all__ = ["ask_as", "parse_page"]

#: What ``--page`` may name, and the field each takes.
_PAGES = {"deployment": "id", "diagnosis": "domain", "alert": "rule", "page": "route"}


def parse_page(text: str | None) -> dict[str, Any] | None:
    """
    Args:
        text: ``KIND:VALUE`` (``deployment:14``, ``diagnosis:shop.example.com``, ``alert:RULE``,
            ``page:/apps``), or None.

    Returns:
        The page, judged by the same allowlist the console's is.

    Raises:
        ValidationError: It is not one Noust can read for the assistant.
    """
    if not text:
        return None
    kind, _, value = text.partition(":")
    field = _PAGES.get(kind)
    if field is None:
        return validate_page({"kind": kind})
    return validate_page(
        {"kind": kind, field: int(value) if field == "id" and value.isdigit() else value}
    )


def ask_as(
    username: str,
    question: str,
    *,
    server: str | None = None,
    page: Mapping[str, Any] | None = None,
    on_text: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    Ask the assistant one question as an account and wait for the answer.

    Args:
        username: The account it acts for.
        question: What is asked.
        server: The server it is about (a name, ``*``), or None for this one.
        page: The page the question is asked from, judged.
        on_text: Called with the answer's text as it is said.

    Returns:
        The turn as the console draws it (:func:`noust.web.assistant.present.present_turn`).

    Raises:
        AccountNotFound: There is no such account.
        NoustPermissionError: The assistant is off, or the account may not use it.
        ValidationError: The server is not one of the account's.
    """
    from noust.web.permissions.principal import scope_of
    from noust.web.permissions.roles import instance_role_of, permissions_of_account
    from noust.web.permissions.team_roles import team_fields
    from noust.web.server import create_app, get_token_manager

    manager = get_token_manager()
    account = manager.accounts.require(username)
    if not read_state().enabled:
        raise NoustPermissionError.from_text(say("assistant.unavailable.off"))
    store = manager.accounts.store
    owned = team_fields(
        account.id, instance_role_of(account), permissions_of_account(account, store), store
    )
    permissions = frozenset(owned["permissions"])
    if "assistant.use" not in permissions:
        raise NoustPermissionError.from_text(say("assistant.unavailable.permission"))
    scope = scope_of({"account_id": account.id, **owned})
    app = create_app()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="noust-assistant-ask", daemon=True)
    thread.start()
    conversations = Conversations()
    conversation = conversations.create(int(account.id))
    try:
        with bind(scope):
            reach_server, names = resolve_reach(server)
            settings = AssistantSettings().read()
            turn = Turns().begin(
                conversation.id,
                int(account.id),
                question=question,
                model=settings.model,
                location=settings.location,
                page=dict(page) if page else None,
                server=reach_server,
            )

        def publish(payload: dict[str, Any]) -> None:
            if on_text is not None and payload.get("type") == "text":
                on_text(str(payload["delta"]))

        from noust.central import role

        context = TurnContext(
            account=account,
            person_permissions=permissions,
            scope=scope,
            conversation_id=conversation.id,
            turn=turn,
            question=question,
            page=dict(page) if page else None,
            reach=Reach(reach_server, names),
            hub=role() == "hub",
            locale=None,
            bridge=LoopBridge(app, loop, client_host="127.0.0.1", host="127.0.0.1", scheme="http"),
            openapi=app.openapi(),
            cancel=CancelToken(),
            publish=publish,
            tokens=TokenCache(),
        )
        TurnRunner(context).run()
        from noust.core.assistant.records import Proposals

        ended = Turns().get(turn.id, int(account.id))
        if ended is None:
            raise RuntimeError("The turn that was just run is not there")
        masker = _masker(conversation.id, int(account.id))
        return present_turn(
            ended, Proposals().for_conversation(conversation.id, int(account.id)), masker.restore
        )
    finally:
        # What the turn acted with ends with it: its token is taken back.
        AssistantIdentity().revoke_conversation(conversation.id)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


def _masker(conversation_id: str, account_id: int) -> Any:
    """
    Args:
        conversation_id: The conversation.
        account_id: Its author.

    Returns:
        What puts back the emails and addresses that were disguised in it.
    """
    from noust.core.assistant.masking import Masker

    return Masker(mask_personal=True, markers=Conversations().markers(conversation_id, account_id))
