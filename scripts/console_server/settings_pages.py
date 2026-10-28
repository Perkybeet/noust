# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The console's Settings pages."""

from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Settings (the console's Settings pages)
# ---------------------------------------------------------------------------

#: Seeded API tokens: (name, scope, lifetime in hours or None, revoked).
SETTINGS_API_TOKENS: tuple[tuple[str, str, int | None, bool], ...] = (
    ("ci-deploy", "deploy", 90 * 24, False),
    ("grafana-read", "read", None, False),
    ("old-backup-script", "admin", None, True),
)


def seed_settings_api_tokens() -> None:
    """
    Issue the API tokens the Settings > API tokens page lists.

    A live deploy token, a read token that never expires and a revoked admin
    one, so each state the table draws exists. The clear tokens are discarded:
    nothing in the suite authenticates with them.
    """
    from noust.web.server import get_token_manager

    manager = get_token_manager()
    for name, scope, hours, revoked in SETTINGS_API_TOKENS:
        issued = manager.create_api_token(name, scope, hours)
        if revoked:
            manager.revoke_api_token(int(issued["id"]))


def warm_recipe_library() -> None:
    """
    Load the recipe library before the server says it is ready.

    The first request that lists recipes imports the recipe schema and validates every file of the
    library: a second and a half on an idle machine and several on a busy one. It is a cost of a
    process that has just started, not of the page that asks, and the wizard's recipe start and the
    catalog wait for it with a ten-second budget, so whichever test of a worker came first paid it
    and failed when the machine was loaded.
    """
    from noust.recipes import search_recipes

    search_recipes()


def pin_settings_update_check() -> str:
    """
    Answer the update check from the sandbox instead of GitHub.

    ``GET /api/system/version`` asks GitHub and the installation's package
    source when its cache is older than five minutes, which would make the
    About page depend on the network and on whatever was released last. Both
    answers are pinned to the next minor version of the one installed, so the
    page always shows an update to offer.

    Returns:
        The version the check reports as released.
    """
    from noust import __version__
    from noust.core.update_checker import UpdateChecker

    numbers = [int(part) for part in re.findall(r"\d+", __version__)[:2]] + [0, 0]
    latest = f"{numbers[0]}.{numbers[1] + 1}.0"

    def fetch_latest(cls: type[UpdateChecker], *args: object) -> str:
        """Report the pinned release, both published and installable."""
        return latest

    def detect(cls: type[UpdateChecker]) -> str:
        """Report a pip installation without asking the sandbox's fake runner."""
        return "pip"

    UpdateChecker._fetch_published_version = classmethod(fetch_latest)  # type: ignore[method-assign,assignment]
    UpdateChecker._fetch_installable_version = classmethod(fetch_latest)  # type: ignore[method-assign,assignment]
    UpdateChecker._detect_installation_method = classmethod(detect)  # type: ignore[method-assign,assignment]
    return latest


#: The modelled Telegram bots: token to the chats each has seen. The first has seen a group and
#: a private chat; the second none yet, which is what a bot never written to answers.
TELEGRAM_BOTS: dict[str, list[dict[str, Any]]] = {
    "7000000001:console-sandbox-bot": [
        {"id": -1001987654321, "type": "supergroup", "title": "Noust alerts"},
        {"id": 52345678, "type": "private", "username": "ops_oncall", "first_name": "Ops"},
    ],
    "7000000002:console-sandbox-quiet": [],
}


def model_telegram_bot_api() -> None:
    """
    Answer the Telegram Bot API from :data:`TELEGRAM_BOTS` instead of api.telegram.org.

    Settings > Notifications finds a bot's chats (``getUpdates``) and sends it a test
    (``sendMessage``); neither may leave the machine. The notifier the settings endpoints build
    keeps its real opener - the webhook test posts to a listener the E2E suite runs - but a
    request for the Bot API is answered here, the way Telegram answers: ``{"ok": true, ...}``,
    or an HTTP error whose body carries Telegram's own ``description``. Only the Bot API's
    fixed host is excused from the private-destination guard, since nothing is dialled for it.
    """
    import io
    from email.message import Message
    from urllib.error import HTTPError

    import noust.core.notifier as notifier_module
    import noust.web.api.config as config_api

    api = notifier_module._TELEGRAM_API + "/bot"
    guard = notifier_module._require_public_destination

    def require_public_destination(url: str, config: Any, *purpose: Any) -> None:
        # The guard also takes what the address is for (a probe's, a code host's): passed on, so
        # a caller that names it is judged by the real guard and not refused by this stand-in.
        if not url.startswith(api):
            guard(url, config, *purpose)

    def refuse(url: str, code: int, reason: str, description: str) -> HTTPError:
        body = json.dumps({"ok": False, "error_code": code, "description": description})
        return HTTPError(url, code, reason, Message(), io.BytesIO(body.encode("utf-8")))

    def answer(request: Any) -> io.BytesIO:
        url = str(request.full_url)
        token, _, method = url[len(api) :].partition("/")
        chats = TELEGRAM_BOTS.get(token)
        if chats is None:
            raise refuse(url, 401, "Unauthorized", "Unauthorized")
        if method == "getUpdates":
            updates = [
                {
                    "update_id": 800 + index,
                    "message": {"message_id": 1, "chat": chat, "text": "/start"},
                }
                for index, chat in enumerate(chats)
            ]
            return io.BytesIO(json.dumps({"ok": True, "result": updates}).encode("utf-8"))
        if method == "sendMessage":
            sent = json.loads(request.data.decode("utf-8"))
            if str(sent.get("chat_id")) not in {str(chat["id"]) for chat in chats}:
                raise refuse(url, 400, "Bad Request", "Bad Request: chat not found")
            return io.BytesIO(b'{"ok": true, "result": {"message_id": 2}}')
        raise refuse(url, 404, "Not Found", "Not Found")

    build = config_api._build_notifier

    def build_notifier() -> Any:
        notifier = build()
        opener = notifier._opener

        def open_or_answer(request: Any, timeout: float | None = None) -> Any:
            if str(request.full_url).startswith(api):
                return answer(request)
            return opener(request, timeout=timeout)

        notifier._opener = open_or_answer
        return notifier

    notifier_module._require_public_destination = require_public_destination  # type: ignore[assignment]
    config_api._build_notifier = build_notifier
