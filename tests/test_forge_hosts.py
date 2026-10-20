# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which kind of code host an address is: its name, the well-known ones, and what the operator says."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.exceptions import ConfigError
from noust.integrations.forges import hosts


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    yield Config()
    Config.reset_instance()


@pytest.mark.parametrize(
    ("host", "kind"),
    [
        ("gitlab.com", "gitlab"),
        ("gitlab.example.com", "gitlab"),
        ("bitbucket.org", "bitbucket"),
        ("codeberg.org", "forgejo"),
        ("gitea.com", "gitea"),
        ("gitea.example.com", "gitea"),
        ("forgejo.example.com", "forgejo"),
        ("gogs.example.com", "gogs"),
        ("GITLAB.Example.COM", "gitlab"),
        ("git.example.com", None),
        ("", None),
        (None, None),
    ],
)
def test_an_address_says_what_it_is_when_it_says_it(
    config: Config, host: str | None, kind: str | None
) -> None:
    assert hosts.kind_of_host(host, config) == kind


def test_the_operator_names_the_hosts_an_address_does_not(config: Config) -> None:
    config.set("integrations.forge_hosts", ["git.example.com=gitea", "Code.Example.com=forgejo"])

    assert hosts.kind_of_host("git.example.com", config) == "gitea"
    assert hosts.kind_of_host("code.example.com", config) == "forgejo"


def test_what_the_operator_says_wins_over_what_the_name_suggests(config: Config) -> None:
    config.set("integrations.forge_hosts", ["gitea.example.com=forgejo"])

    assert hosts.kind_of_host("gitea.example.com", config) == "forgejo"


def test_a_comma_separated_string_will_do(config: Config) -> None:
    config.set("integrations.forge_hosts", "a.example.com=gogs, b.example.com=gitlab")

    assert config.get("integrations.forge_hosts") == ["a.example.com=gogs", "b.example.com=gitlab"]


@pytest.mark.parametrize(
    "bad",
    [
        ["nokind"],
        ["git.example.com=svn"],
        ["=gitea"],
        ["https://git.example.com=gitea"],
        ["a b=gitea"],
        ["git.example.com:3000=gitea"],
        ["git.example.com=github"],
    ],
)
def test_what_is_not_host_equals_kind_is_refused(config: Config, bad: list[str]) -> None:
    with pytest.raises(ConfigError):
        config.set("integrations.forge_hosts", bad)


def test_a_hand_edit_gone_wrong_does_not_stop_a_deployment(config: Config) -> None:
    class Broken:
        def get(self, key: str, default: object = None) -> object:
            return ["not-valid"]

    assert hosts.declared(Broken()) == {}
    assert hosts.kind_of_host("gitlab.example.com", Broken()) == "gitlab"


def test_the_gitea_family_shares_an_api() -> None:
    assert [hosts.api_family(kind) for kind in ("gitea", "forgejo", "gogs", "gitlab", None)] == [
        "gitea",
        "gitea",
        "gitea",
        "gitlab",
        None,
    ]
