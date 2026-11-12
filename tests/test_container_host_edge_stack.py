# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose stack deployed where there is no web server of Noust's to give it a site.

The stack's own document says what it publishes (a stack that brings its own file is not rewritten), so the
edge has nothing to change in it: it skips the two steps that would write a site and order a certificate, and
says where the stack is reached. Neither a web server manager nor a certificate manager is built, and what
the stack was deployed with is recorded as an ordinary deployment is. A server's deployment, which the tests
of its site hold, is untouched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from tests.support.docker_compose_sites import DOMAIN, certs, root, stack, store, web  # noqa: F401

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


@pytest.fixture(autouse=True)
def a_container_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    yield
    Config.reset_instance()


def test_no_site_and_no_certificate_are_made_and_no_manager_for_them_is_built(
    stack: DockerComposeDeployer,
    web,
    store: NoustStore,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("a container host has no web server or certificate to manage")

    monkeypatch.setattr(stack, "webserver_manager", refuse)
    monkeypatch.setattr(DockerComposeDeployer, "cert_manager", property(refuse))
    said: list[str] = []
    monkeypatch.setattr(stack.logger, "step", lambda number, total, text: said.append(text))

    stack.deploy()

    assert "Skipping Nginx (no web server on this machine)" in said
    assert "Skipping SSL (no web server on this machine)" in said
    assert not web.config_path(DOMAIN).exists()
    row = store.get_app(DOMAIN)
    assert row is not None
    assert (row.ssl_enabled, row.webserver) == (False, "")


def test_the_port_it_is_reached_on_is_said(
    stack: DockerComposeDeployer, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.script(["docker", "info", "--format", "{{.Name}}"], stdout="nas-oficina\n")
    notes: list[str] = []
    monkeypatch.setattr(stack.logger, "substep", notes.append)

    stack.deploy()

    assert (
        "Reached at http://nas-oficina:3001: Docker publishes it on port 3001 of the machine"
        in notes
    )


def test_a_server_makes_its_site_as_its_tests_hold(
    stack: DockerComposeDeployer, web, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOUST_CENTRAL_ROLE")
    monkeypatch.delenv("NOUST_DEPLOY_HERE")
    Config.reset_instance()
    stack.ssl = False

    stack.deploy()

    assert web.config_path(DOMAIN).exists()
