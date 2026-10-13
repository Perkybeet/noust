# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
API tokens that are about to stop working (checklist K-23).

A token with an expiry date fails one morning, in a CI job nobody was watching,
with a 401. This watch reads the tokens twice a day and says so while there is
still time: ``monitor.token_warning_days`` (7; 0 turns it off) before, again
the day it expires, and once when it has. Each stage is announced once per
token, so a token that sits in the warning window for a week is one message and
not seven; a token that is revoked or replaced is forgotten.

A token that expired long ago and nobody noticed is not news: only those that
expired in the last week are announced. Tokens never expire unless their
creator gave them a lifetime (the ENS profile makes that mandatory), so a
server without expiring tokens gets nothing from this.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any

from noust.core.exceptions import DependencyError
from noust.core.notifications.composers_ops import compose_token_expiring
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the stage last announced for each token.
SCOPE = "tokens"

DEFAULT_WARNING_DAYS = 7
CHECK_SECONDS = 12 * 3600

#: An expired token is announced only if it expired this recently.
EXPIRED_WINDOW_DAYS = 7

#: The stages, in the order a token goes through them.
STAGES = ("warn", "today", "expired")


def stage_of(days_left: int, warning_days: int) -> str | None:
    """
    Say where a token is on its way to expiring.

    Args:
        days_left: Whole days until it expires; negative once it has.
        warning_days: How many days before the end the warning starts.

    Returns:
        ``warn``, ``today`` or ``expired``; None when there is nothing to say
        (far from the end, or expired too long ago to be news).
    """
    if days_left < -EXPIRED_WINDOW_DAYS:
        return None
    if days_left < 0:
        return "expired"
    if days_left == 0:
        return "today"
    return "warn" if days_left <= warning_days else None


class TokenWatch(Watch):
    """Announces API tokens that are about to expire, or just did."""

    name = "tokens"
    default_interval_s = CHECK_SECONDS

    def __init__(self, tokens: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        """
        Args:
            tokens: Lists the token records (``id``, ``name``, ``expires_at``,
                ``revoked_at``, ``created_by``); the console's token manager's
                by default. Tests inject one.
        """
        self._tokens = tokens

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            Twice a day; zero when ``monitor.token_warning_days`` is zero.
        """
        days = int(services.config.get("monitor.token_warning_days", DEFAULT_WARNING_DAYS) or 0)
        return CHECK_SECONDS if days > 0 else 0

    def _read(self) -> list[dict[str, Any]]:
        """
        Returns:
            The token records; none when the console's dependencies are not
            installed (a server without a console has no tokens).
        """
        if self._tokens is not None:
            return self._tokens()
        try:
            from noust.cli.web_state import token_manager

            return list(token_manager().list_api_tokens())
        except DependencyError:
            return []

    def check(self, services: WatchServices) -> None:
        """
        Compare each token's expiry with today.

        Args:
            services: What the monitor lends.
        """
        warning_days = int(services.config.get("monitor.token_warning_days", DEFAULT_WARNING_DAYS))
        now = datetime.fromtimestamp(services.clock(), tz=timezone.utc)
        watched: set[str] = set()
        for token in self._read():
            expires = token.get("expires_at")
            if not expires or token.get("revoked_at"):
                continue
            key = str(token["id"])
            watched.add(key)
            end = datetime.fromtimestamp(float(expires), tz=timezone.utc)
            days_left = (end.date() - now.date()).days
            stage = stage_of(days_left, warning_days)
            if stage is None:
                continue
            said = services.state.get(SCOPE, key)
            if said in STAGES and STAGES.index(said) >= STAGES.index(stage):
                continue
            services.state.put(SCOPE, key, stage)
            services.publish(
                partial(
                    compose_token_expiring,
                    str(token.get("name") or key),
                    end.date().isoformat(),
                    days_left,
                    owner=str(token["created_by"]) if token.get("created_by") else None,
                )
            )
        services.state.keep_only(SCOPE, watched)
