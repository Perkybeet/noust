# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The four ``deploy`` settings of 3.4: what they default to and what refuses them.

``noust config set deploy.max_concurrent_builds 3`` is how an operator sizes the
queue, so the keys are validated where every write passes, and read in one
place (:mod:`noust.core.delivery`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import delivery
from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.exceptions import ConfigError
from noust.core.store import MAX_DRAIN_SECONDS


@pytest.fixture
def config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """
    A configuration file of the test's own, read fresh.

    Yields:
        The configuration.
    """
    path = sandbox / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    delivery.forget()
    monkeypatch.undo()  # the autouse stand-in of the settings is for the other tests
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    try:
        yield Config()
    finally:
        Config.reset_instance()
        delivery.forget()


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
