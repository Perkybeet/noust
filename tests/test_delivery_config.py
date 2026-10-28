# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The four ``deploy`` settings of 4.0: what they default to and what refuses them.

``noust config set deploy.max_concurrent_builds 3`` is how an operator sizes the
queue, so the keys are validated where every write passes, and read in one
place (:mod:`noust.core.delivery`).
"""

from __future__ import annotations

import pytest

from noust.core import delivery
from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.exceptions import ConfigError
from noust.core.store import MAX_DRAIN_SECONDS
from tests.support.delivery_config import (  # noqa: F401
    config,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


def test_the_defaults_say_what_the_documentation_says() -> None:
    deploy = DEFAULT_CONFIG["deploy"]

    assert deploy["zero_downtime"] is True
    assert deploy["drain_seconds"] == delivery.DEFAULT_DRAIN_SECONDS == 10
    assert deploy["max_concurrent_builds"] == delivery.DEFAULT_MAX_CONCURRENT_BUILDS == 2
    assert deploy["queue_limit"] == delivery.DEFAULT_QUEUE_LIMIT == 25
    assert delivery.DeliverySettings.read(Config.snapshot()) == delivery.DeliverySettings()


def test_the_config_s_drain_range_is_the_store_s() -> None:
    """The config module cannot import the store; this is what holds the two numbers together."""
    assert MAX_DRAIN_SECONDS == 300


@pytest.mark.parametrize(
    ("key", "value", "stored"),
    [
        ("deploy.max_concurrent_builds", "4", 4),
        ("deploy.max_concurrent_builds", "1", 1),
        ("deploy.queue_limit", "100", 100),
        ("deploy.drain_seconds", "0", 0),
        ("deploy.drain_seconds", "300", 300),
        ("deploy.zero_downtime", "off", False),
        ("deploy.zero_downtime", "true", True),
    ],
)
def test_a_valid_value_is_stored_as_what_it_means(
    config: Config, key: str, value: str, stored: object
) -> None:
    config.set(key, value)

    assert config.get(key) == stored


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("deploy.max_concurrent_builds", "0"),
        ("deploy.max_concurrent_builds", "17"),
        ("deploy.max_concurrent_builds", "many"),
        ("deploy.queue_limit", "0"),
        ("deploy.queue_limit", "1001"),
        ("deploy.drain_seconds", "-1"),
        ("deploy.drain_seconds", "301"),
        ("deploy.zero_downtime", "perhaps"),
    ],
)
def test_a_value_out_of_range_is_refused_where_it_is_written(
    config: Config, key: str, value: str
) -> None:
    with pytest.raises(ConfigError):
        config.set(key, value)


def test_the_queue_reads_a_change_without_a_restart(config: Config) -> None:
    """``current`` reads the file again every few seconds; ``refresh`` at once."""
    assert delivery.current(refresh=True).max_concurrent_builds == 2

    config.set("deploy.max_concurrent_builds", "3")
    assert config.save() is True

    assert delivery.current(refresh=True).max_concurrent_builds == 3


# ---------------------------------------------------------------------------
# The longest a deployment may take (checklist F-28)
# ---------------------------------------------------------------------------


def test_the_default_is_an_hour_and_zero_means_no_limit(config: Config) -> None:
    assert DEFAULT_CONFIG["deploy"]["max_minutes"] == delivery.DEFAULT_MAX_MINUTES == 60
    assert delivery.DeliverySettings().max_minutes == 60

    config.set("deploy.max_minutes", "0")

    assert delivery.DeliverySettings.read(config).max_minutes == 0
    assert delivery.DeliverySettings.read(config).max_seconds is None


def test_the_limit_is_read_with_the_other_two_from_one_place(config: Config) -> None:
    config.set("deploy.max_minutes", "15")
    config.set("deploy.max_concurrent_builds", "3")
    config.set("deploy.queue_limit", "7")

    settings = delivery.DeliverySettings.read(config)

    assert (settings.max_minutes, settings.max_concurrent_builds, settings.queue_limit) == (
        15,
        3,
        7,
    )
    assert settings.max_seconds == 900


@pytest.mark.parametrize("value", ["-1", "1441", "soon", "2.5"])
def test_a_time_limit_out_of_range_is_refused_where_it_is_written(
    config: Config, value: str
) -> None:
    with pytest.raises(ConfigError):
        config.set("deploy.max_minutes", value)


def test_a_hand_edited_file_with_nonsense_reads_as_the_default(config: Config) -> None:
    config._config["deploy"]["max_minutes"] = "soon"

    assert delivery.DeliverySettings.read(config).max_minutes == delivery.DEFAULT_MAX_MINUTES
