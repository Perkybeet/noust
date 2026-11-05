# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the pause of notifications (:mod:`noust.core.notifications.pause`).

The console asks "until when" when every notification is turned off, because that is almost always
"for a while". What is defended here is that the moment it records is read in one way by the
configuration that accepts it and by the notifier that judges it, and that a value nobody can read
never silences a server for good.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.exceptions import ConfigError
from noust.core.notifications.pause import is_paused, parse_pause, validate_pause
from noust.core.notifier import Notifier
from tests.support.notifier import CapturingOpener, config, make_event, public_dns  # noqa: F401

# A fixture imported from the support module is found by pytest in this module's namespace.
# ruff: noqa: F811

WEBHOOK_URL = "https://hooks.example.test/noust"
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)


class TestReadingAMoment:
    """One reading of ``notifications.muted_until``."""

    def test_no_pause_is_the_default(self) -> None:
        assert DEFAULT_CONFIG["notifications"]["muted_until"] == ""

    @pytest.mark.parametrize("value", ["", "   ", None, 42, "tomorrow", "2026-11-05T11:00:00"])
    def test_what_is_not_a_moment_with_its_zone_pauses_nothing(self, value: object) -> None:
        """A naive time is a different instant on every machine, and a typo must not silence a server."""
        assert parse_pause(value) is None
        assert is_paused(value, NOW) is False

    def test_a_browsers_utc_moment_is_read(self) -> None:
        """JavaScript writes ``Z``, which Python 3.10's ``fromisoformat`` does not know."""
        assert parse_pause("2026-11-05T11:30:00.000Z") == datetime(
            2026, 11, 5, 11, 30, tzinfo=timezone.utc
        )

    def test_a_moment_with_an_offset_is_read_in_utc(self) -> None:
        assert parse_pause("2026-11-06T09:00:00+01:00") == datetime(
            2026, 11, 6, 8, 0, tzinfo=timezone.utc
        )

    def test_the_pause_covers_the_time_before_its_end_and_not_after(self) -> None:
        until = (NOW + timedelta(hours=1)).isoformat()
        assert is_paused(until, NOW) is True
        assert is_paused(until, NOW + timedelta(hours=1)) is False
        assert is_paused(until, NOW + timedelta(hours=2)) is False


class TestWhatTheConfigurationAccepts:
    """``Config.set`` takes nothing the notifier would read differently."""

    def test_a_moment_is_stored_in_utc(self, config: Config) -> None:
        config.set("notifications.muted_until", "2026-11-06T09:00:00+01:00")
        assert config.get("notifications.muted_until") == "2026-11-06T08:00:00+00:00"

    def test_empty_clears_it(self, config: Config) -> None:
        config.set("notifications.muted_until", "2026-11-06T09:00:00+01:00")
        config.set("notifications.muted_until", "")
        assert config.get("notifications.muted_until") == ""

    @pytest.mark.parametrize("value", ["tomorrow", "2026-11-05T11:00:00", "11:00"])
    def test_a_value_that_is_no_moment_is_refused_with_the_way_to_write_one(
        self, config: Config, value: str
    ) -> None:
        with pytest.raises(ConfigError, match="muted_until") as raised:
            config.set("notifications.muted_until", value)
        assert "2026-11-06T09:00:00+01:00" in (raised.value.details or "")

    def test_the_validator_is_the_one_the_configuration_uses(self) -> None:
        assert validate_pause("2026-11-06T09:00:00Z") == "2026-11-06T09:00:00+00:00"


class TestWhatTheNotifierSends:
    """The pause is a reason to stay silent, and only until it ends."""

    def _ready(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)

    def test_a_pause_that_has_not_ended_sends_nothing(self, config: Config) -> None:
        self._ready(config)
        config.set(
            "notifications.muted_until",
            (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []

    def test_a_pause_that_has_ended_sends_again(self, config: Config) -> None:
        self._ready(config)
        config.set(
            "notifications.muted_until",
            (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert len(opener.requests) == 1

    def test_no_pause_sends_as_before(self, config: Config) -> None:
        self._ready(config)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert len(opener.requests) == 1

    def test_a_value_written_by_hand_that_nobody_can_read_silences_nothing(
        self, config: Config
    ) -> None:
        """The configuration refuses it on save; the notifier does not trust that it was asked."""
        self._ready(config)
        config._config["notifications"]["muted_until"] = "after the weekend"
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert len(opener.requests) == 1

    def test_the_test_button_ignores_the_pause_as_it_ignores_the_master_switch(
        self, config: Config
    ) -> None:
        """Trying a channel is how an operator learns it works, paused or not."""
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set(
            "notifications.muted_until",
            (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        opener = CapturingOpener()

        # No answer is the channel's: None means it delivered.
        assert Notifier(config, opener=opener).test_channel("webhook") is None
        assert len(opener.requests) == 1
