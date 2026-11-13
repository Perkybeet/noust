# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The console's Settings pages."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

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


def warm_api_schema(app: FastAPI) -> None:
    """
    Build the API's schema before the server says it is ready.

    Settings > API reference fetches it, and building it walks every route: three seconds on an
    idle machine, and past the ten a test waits for on the two cores of a CI runner, where it was
    asked for twice (the page's first request is cancelled and sent again) and each build took the
    other's time. Like the recipe library, it is a cost of a process that has just started and not
    of the page that asks, and whichever test of a worker came first paid it.

    Args:
        app: The application the server serves.
    """
    app.openapi()


def pin_settings_update_check(*, up_to_date: bool = False) -> str:
    """
    Answer the update check from the sandbox instead of GitHub.

    ``GET /api/system/version`` asks GitHub and the installation's package
    source when its cache is older than five minutes, which would make the
    About page depend on the network and on whatever was released last. Both
    answers are pinned to the next minor version of the one installed, so the
    page always shows an update to offer.

    Args:
        up_to_date: Pin them to the version installed instead: the product video's servers
            run the release it presents, and have nothing to offer.

    Returns:
        The version the check reports as released.
    """
    import noust
    from noust.core import package_index
    from noust.core.update_checker import UpdateChecker

    numbers = [int(part) for part in re.findall(r"\d+", noust.__version__)[:2]] + [0, 0]
    latest = noust.__version__ if up_to_date else f"{numbers[0]}.{numbers[1] + 1}.0"

    def fetch_latest(cls: type[UpdateChecker]) -> str:
        """Report the pinned release as published."""
        return latest

    def fetch_offer(cls: type[UpdateChecker], method: str) -> package_index.Offer:
        """Report the pinned release as what the installation's source offers, without PyPI."""
        return package_index.Offer(repository=latest)

    def detect(cls: type[UpdateChecker]) -> str:
        """Report a pip installation without asking the sandbox's fake runner."""
        return "pip"

    UpdateChecker._fetch_published_version = classmethod(fetch_latest)  # type: ignore[method-assign,assignment]
    UpdateChecker._fetch_offer = classmethod(fetch_offer)  # type: ignore[method-assign,assignment]
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
    # A group that is not a forum: what asking for its topics says when the bot has them to make.
    "7000000003:console-sandbox-plain": [
        {"id": -1001555555555, "type": "supergroup", "title": "Noust plain group"},
    ],
    # A forum whose administrator rights the bot lacks: what asking for its topics says when the bot may not.
    "7000000004:console-sandbox-norights": [
        {"id": -1001444444444, "type": "supergroup", "title": "Noust forum, no rights"},
    ],
}

#: What each modelled bot is in its group: whether the group has topics, what the bot may do there and
#: the topics that exist (the number of one a message may be posted in). The first bot is an
#: administrator of a forum that already has a topic; the second asks for nothing; the third is in a
#: group with no topics turned on.
TELEGRAM_GROUPS: dict[str, dict[str, Any]] = {
    "7000000001:console-sandbox-bot": {
        "is_forum": True,
        "status": "administrator",
        "can_manage_topics": True,
        "topics": {2},
    },
    "7000000003:console-sandbox-plain": {
        "is_forum": False,
        "status": "administrator",
        "can_manage_topics": True,
        "topics": set(),
    },
    "7000000004:console-sandbox-norights": {
        "is_forum": True,
        "status": "administrator",
        "can_manage_topics": False,
        "topics": {2},
    },
}


#: The ids of the messages the modelled bots have posted, so each answer carries a new one.
TELEGRAM_SENT: list[int] = []


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
    import noust.web.api.notifications as notifications_api

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
        group = TELEGRAM_GROUPS.get(token)
        asked = json.loads(request.data.decode("utf-8")) if request.data else {}
        if method in ("sendMessage", "getChat", "getChatMember", "createForumTopic"):
            if str(asked.get("chat_id")) not in {str(chat["id"]) for chat in chats}:
                raise refuse(url, 400, "Bad Request", "Bad Request: chat not found")
        if method == "getMe":
            me = {"id": int(token.split(":")[0]), "is_bot": True, "username": "noust_sandbox_bot"}
            return io.BytesIO(json.dumps({"ok": True, "result": me}).encode("utf-8"))
        if method == "getChat":
            chat = {"id": asked["chat_id"], "type": "supergroup", "title": "Noust alerts"}
            if group is not None and group["is_forum"]:
                chat["is_forum"] = True
            return io.BytesIO(json.dumps({"ok": True, "result": chat}).encode("utf-8"))
        if method == "getChatMember":
            member: dict[str, Any] = {
                "status": (group or {}).get("status", "member"),
                "user": {"id": asked["user_id"]},
            }
            if member["status"] == "administrator":
                member["can_manage_topics"] = bool((group or {}).get("can_manage_topics"))
            return io.BytesIO(json.dumps({"ok": True, "result": member}).encode("utf-8"))
        if method == "createForumTopic":
            if group is None or not group["is_forum"]:
                raise refuse(url, 400, "Bad Request", "Bad Request: the chat is not a forum")
            if not group["can_manage_topics"]:
                raise refuse(
                    url, 400, "Bad Request", "Bad Request: not enough rights to create a topic"
                )
            number = max(group["topics"] | {1}) + 1
            group["topics"].add(number)
            created = {"message_thread_id": number, "name": asked["name"], "icon_color": 7322096}
            return io.BytesIO(json.dumps({"ok": True, "result": created}).encode("utf-8"))
        if method == "sendMessage":
            thread = asked.get("message_thread_id")
            if (
                group is not None
                and group["is_forum"]
                and thread is not None
                and thread not in group["topics"]
            ):
                raise refuse(url, 400, "Bad Request", "Bad Request: message thread not found")
            TELEGRAM_SENT.append(len(TELEGRAM_SENT) + 100)
            return io.BytesIO(
                json.dumps({"ok": True, "result": {"message_id": TELEGRAM_SENT[-1]}}).encode(
                    "utf-8"
                )
            )
        raise refuse(url, 404, "Not Found", "Not Found")

    def modelled(build: Any) -> Any:
        """Wrap a notifier builder so that what it builds answers the Bot API from the model."""

        def build_notifier() -> Any:
            notifier = build()
            opener = notifier._opener

            def open_or_answer(request: Any, timeout: float | None = None) -> Any:
                if str(request.full_url).startswith(api):
                    return answer(request)
                return opener(request, timeout=timeout)

            notifier._opener = open_or_answer
            return notifier

        return build_notifier

    notifier_module._require_public_destination = require_public_destination  # type: ignore[assignment]
    # The test button and the finder of chats build their notifier in the configuration's router; the
    # topics a group is asked to create, in the notifications router. Both reach the model.
    config_api._build_notifier = modelled(config_api._build_notifier)
    notifications_api._notifier = modelled(notifications_api._notifier)
