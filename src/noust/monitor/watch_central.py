# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central that manages this server going quiet.

Every server delivers its own notifications, so it keeps telling its operator about itself when the
central is down or the line between them is cut. What it cannot tell without help is that the
central is gone, because the central is who tells it things. A server cannot call its central; it
can only notice that the central stopped calling (:mod:`noust.fleet.central_calls`). The monitor of
a central probes every server every half minute for as long as it runs and says so in the call, so
a central that said it keeps watch and then stops for longer than a quarter of an hour
(``notifications.central_silent_minutes``, 0 turns it off) is told by this server, through its own
channels, once; and the first call after that is told too, so the message closes.

Only a central that said it keeps watch is missed: one that never did is the console of a central
that probes while somebody has it open, and nobody promised to call again. The limit is never
shorter than three of the calls that were promised, so a central that says it calls once an hour is
not missed after sixteen minutes.

One announcement per silence (:mod:`noust.monitor.watches`): it is remembered in the store, so a
restart of the monitor does not say it twice. A central this server no longer lets in (its fleet
token was revoked, or expired, and no other replaced it) is not missed: its operator ended the
relationship on purpose.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from noust.core.exceptions import DependencyError
from noust.core.notifications.composers_watch import compose_central
from noust.fleet.central_calls import CentralCalls, central_of_token
from noust.monitor.watches import Watch, WatchServices

__all__ = ["DEFAULT_MINUTES", "SCOPE", "CentralWatch"]

#: What this watch remembers: the centrals it has said are silent.
SCOPE = "central"

#: Minutes a central that keeps watch may stay quiet before it is told.
DEFAULT_MINUTES = 15

#: How many of the calls a central promised may be missed before it is said to be quiet.
MISSED_CALLS = 3

#: How often the watch looks, in seconds.
LOOK_EVERY = 60.0


class CentralWatch(Watch):
    """Tells when the central that manages this server stops calling, and when it calls again."""

    name = "central"
    default_interval_s = LOOK_EVERY

    def __init__(
        self,
        calls: CentralCalls | None = None,
        tokens: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        """
        Args:
            calls: What the server noted of its centrals; the store's by default.
            tokens: Lists the token records (``name``, ``revoked_at``, ``expires_at``); the
                console's token manager's by default. Tests inject one.
        """
        self._calls = calls
        self._tokens = tokens

    def _let_in(self, now: float) -> set[str] | None:
        """
        Args:
            now: The time.

        Returns:
            The centrals that hold a fleet token this server still honours; None when the tokens
            cannot be listed (a server without a console has none), which judges by the calls.
        """
        try:
            if self._tokens is not None:
                records = self._tokens()
            else:
                from noust.core.web_gate import token_manager

                records = list(token_manager().list_api_tokens())
        except DependencyError:
            return None
        let_in: set[str] = set()
        for record in records:
            central = central_of_token(str(record.get("name") or ""))
            expires = record.get("expires_at")
            if central and not record.get("revoked_at") and (not expires or float(expires) > now):
                let_in.add(central)
        return let_in

    def _minutes(self, services: WatchServices) -> int:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            Minutes a central may stay quiet; 0 when the operator switched this off.
        """
        return int(
            services.config.get("notifications.central_silent_minutes", DEFAULT_MINUTES) or 0
        )

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            Every minute; zero (off) when ``notifications.central_silent_minutes`` is zero.
        """
        return LOOK_EVERY if self._minutes(services) > 0 else 0

    def check(self, services: WatchServices) -> None:
        """
        Compare the last call of every central that keeps watch with the time.

        Args:
            services: What the monitor lends.
        """
        if self._minutes(services) <= 0:
            return
        calls = self._calls or CentralCalls(clock=services.clock)
        now = services.clock()
        let_in = self._let_in(now)
        known: set[str] = set()
        for call in calls.list():
            if let_in is not None and call.central not in let_in:
                continue
            known.add(call.central)
            if call.heartbeat_s is None:
                continue
            limit = max(self._minutes(services) * 60.0, MISSED_CALLS * call.heartbeat_s)
            silent_for = now - call.last_call_at
            said = services.state.get_json(SCOPE, call.central)
            if silent_for > limit and not said:
                services.state.put_json(SCOPE, call.central, {"last_heard": call.last_call_at})
                services.publish(
                    partial(
                        compose_central,
                        call.central,
                        True,
                        last_heard=call.last_call_at,
                        silent_for_s=silent_for,
                    )
                )
            elif silent_for <= limit and said:
                services.state.delete(SCOPE, call.central)
                services.publish(
                    partial(
                        compose_central,
                        call.central,
                        False,
                        # From the last call before the silence to the first after it.
                        silent_for_s=max(call.last_call_at - float(said["last_heard"]), 0.0),
                    )
                )
        services.state.keep_only(SCOPE, known)
