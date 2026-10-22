# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A source archive is fetched by this server as root from a URL somebody typed,
so its host, and the host of every redirect, is judged by the same guard as the
notifier's and the backups' (checklist L-21; :mod:`noust.core.outbound`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

from noust.core.config import Config
from noust.core.exceptions import SourceError
from noust.managers import source_manager as sm


@pytest.fixture(autouse=True)
def config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the opener with one that records what it was asked to open."""
    urls: list[str] = []

    class Opener:
        def open(self, url: str, timeout: int = 0) -> Any:
            urls.append(url)
            return object()

    monkeypatch.setattr(sm, "_build_opener", lambda: Opener())
    return urls


class TestTheDownload:
    def test_a_public_host_is_fetched(self, opened: list[str]) -> None:
        sm._open_url("https://downloads.example.com/app.tar.gz")

        assert opened == ["https://downloads.example.com/app.tar.gz"]

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/api/apps",
            "http://[::1]/x",
            "http://localhost/x",
            "http://artifacts.private.test/app.tar.gz",
            "http://10.0.0.5/app.tar.gz",
        ],
    )
    def test_this_machines_own_network_is_not_a_source(self, url: str, opened: list[str]) -> None:
        with pytest.raises(SourceError) as raised:
            sm._open_url(url)

        assert "security.allow_private_hosts" in str(raised.value.details)
        assert opened == []  # refused before a request exists

    def test_the_operator_can_name_an_internal_artifact_server(
        self, opened: list[str], config: Config
    ) -> None:
        config.set("security.allow_private_hosts", ["artifacts.private.test"])

        sm._open_url("http://artifacts.private.test/app.tar.gz")

        assert opened == ["http://artifacts.private.test/app.tar.gz"]

    def test_a_public_name_that_resolves_inside_is_refused(
        self, opened: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.core.outbound.resolve_host", lambda host: ("10.1.1.1",))

        with pytest.raises(SourceError, match=re.escape("10.1.1.1")):
            sm._open_url("https://cdn.example.com/app.tar.gz")

        assert opened == []


class TestTheRedirects:
    def follow(self, target: str) -> Any:
        handler = sm._SafeRedirectHandler()
        request = Request("https://downloads.example.com/app.tar.gz")
        return handler.redirect_request(request, None, 302, "Found", {}, target)

    def test_a_redirect_to_a_public_host_is_followed(self) -> None:
        assert self.follow("https://cdn.example.com/app.tar.gz") is not None

    @pytest.mark.parametrize(
        "target",
        ["https://169.254.169.254/latest", "https://127.0.0.1/", "https://nas.private.test/x"],
    )
    def test_a_redirect_into_the_machines_network_is_refused(self, target: str) -> None:
        with pytest.raises(SourceError, match="Refusing to download"):
            self.follow(target)
