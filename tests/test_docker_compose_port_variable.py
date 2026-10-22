# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stack that publishes its web service on ``${PORT}`` is published where its site proxies to.

The site goes to the port the application was given (the one allocated, or ``--port``); a Compose
document that publishes on ``127.0.0.1:${PORT:-80}:80`` published on its default, 80 for a
template converted from Coolify's catalogue, which nginx holds, or any other number the site did
not know about: the deployment failed with "address already in use", or came up behind a 502.
Found by deploying a converted template with a real Docker. ``PORT`` is the one value Noust owns
in a stack's ``.env`` (a recipe does the same), written when the document reads it and the
operator has not set it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers.env_manager import EnvManager

STACK = """\
services:
  web:
    image: example/web:1
    ports:
      - 127.0.0.1:${PORT:-80}:80
"""


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


def deployer_for(
    tmp_path: Path, store: NoustStore, document: str = STACK, *, port: int | None = 3020
) -> DockerComposeDeployer:
    root = tmp_path / "app"
    root.mkdir()
    (root / "docker-compose.yml").write_text(document)
    deployer = DockerComposeDeployer(runner=FakeRunner())
    deployer.configure(
        "stack.example.com", "https://example.com/stack.git", app_path=root, port=port
    )
    deployer._discover_compose_file()
    deployer._parse_compose_services()
    return deployer


def env_text(tmp_path: Path) -> str:
    """The file without the lines Noust owns in it (what it says about the deployment)."""
    text = (tmp_path / "app" / ".env").read_text()
    kept = [
        line
        for line in text.splitlines()
        if not line.startswith("NOUST_") and line != EnvManager.MANAGED_COMMENT
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n" if kept else ""


def test_the_variable_the_document_publishes_on_gets_the_port_the_site_proxies_to(
    tmp_path: Path, store: NoustStore
) -> None:
    deployer = deployer_for(tmp_path, store)

    deployer._configure_environment()

    assert env_text(tmp_path) == "PORT=3020\n"
    assert (tmp_path / "app" / ".env").stat().st_mode & 0o777 == 0o600


def test_the_operators_own_value_and_the_rest_of_the_file_are_left_alone(
    tmp_path: Path, store: NoustStore
) -> None:
    deployer = deployer_for(tmp_path, store)
    (tmp_path / "app" / ".env").write_text("# mine\nSECRET=abc\nPORT=4000\n")

    deployer._configure_environment()

    assert env_text(tmp_path) == "# mine\nSECRET=abc\nPORT=4000\n"


def test_an_existing_file_without_the_variable_keeps_its_lines_and_its_secrets(
    tmp_path: Path, store: NoustStore
) -> None:
    deployer = deployer_for(tmp_path, store)
    (tmp_path / "app" / ".env").write_text("# generated\nSERVICE_PASSWORD_DB=hunter2")

    deployer._configure_environment()

    assert env_text(tmp_path) == "# generated\nSERVICE_PASSWORD_DB=hunter2\nPORT=3020\n"


def test_a_document_that_does_not_publish_on_the_variable_gets_nothing(
    tmp_path: Path, store: NoustStore
) -> None:
    deployer = deployer_for(
        tmp_path,
        store,
        "services:\n  web:\n    image: example/web:1\n    ports:\n      - '127.0.0.1:3000:3000'\n"
        "    environment:\n      - PORT=${PORT:-3000}\n",
    )

    deployer._configure_environment()

    assert env_text(tmp_path) == ""


def test_without_a_port_to_proxy_to_nothing_is_written(tmp_path: Path, store: NoustStore) -> None:
    deployer = deployer_for(tmp_path, store, port=None)

    deployer._configure_environment()

    assert env_text(tmp_path) == ""


def test_the_variable_in_the_long_form_of_a_port_counts_too(
    tmp_path: Path, store: NoustStore
) -> None:
    deployer = deployer_for(
        tmp_path,
        store,
        "services:\n  web:\n    image: example/web:1\n    ports:\n"
        "      - target: 80\n        published: ${PORT}\n        host_ip: 127.0.0.1\n",
    )

    deployer._configure_environment()

    assert env_text(tmp_path) == "PORT=3020\n"
