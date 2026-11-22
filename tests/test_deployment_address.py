# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``NOUST_URL`` says where the application answers on this machine, not where its name would (item 130).

The variable every application is given about its own deployment used to be ``https://<domain>``
whatever the machine was. On the central of a NAS that deploys on its own machine there is no site and no
certificate, nothing answers to the application's name, and the application was told to build its links,
its redirects and its OAuth callbacks on an address that does not exist. Where an application answers is
one answer (:func:`noust.deployers.helpers.edge.answers_at`), which the end of a deployment, the recipes'
``{{ url }}`` and the statuses a forge shows already ask: the variable asks it too.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.store import App, NoustStore
from noust.core.variables.predefined import Deployment
from noust.deployers.helpers.deployment_variables import describe_deployment
from noust.deployers.helpers.env_variables import deployment_for

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()
    Config.reset_instance()


@pytest.fixture
def container_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    monkeypatch.setattr("noust.deployers.helpers.edge.machine_name", lambda _runner: "nas-oficina")
    Config.reset_instance()


def described(store: NoustStore, *, ssl: bool = True, port: int | None = 8081) -> Deployment:
    return describe_deployment(
        store, DOMAIN, row=None, ssl=ssl, branch="main", commit="abc1234", release="r1", port=port
    )


class TestDescribedForADeploy:
    def test_on_a_server_it_is_the_domain_over_https(self, store: NoustStore) -> None:
        assert described(store).url == f"https://{DOMAIN}"
        assert described(store).environment()["NOUST_URL"] == f"https://{DOMAIN}"

    def test_a_site_with_no_certificate_is_over_http(self, store: NoustStore) -> None:
        assert described(store, ssl=False).url == f"http://{DOMAIN}"

    def test_on_a_machine_with_no_site_it_is_the_machine_and_the_port(
        self, store: NoustStore, container_host: None
    ) -> None:
        deployment = described(store)

        assert deployment.url == "http://nas-oficina:8081"
        assert deployment.environment()["NOUST_URL"] == "http://nas-oficina:8081"
        assert deployment.fields()["URL"] == "http://nas-oficina:8081"
        assert deployment.fields()["DOMAIN"] == DOMAIN

    def test_with_no_site_and_no_port_there_is_nothing_better_than_the_name(
        self, store: NoustStore, container_host: None
    ) -> None:
        assert described(store, port=None).url == f"https://{DOMAIN}"

    def test_a_site_answers_at_its_domain_whatever_port_its_process_listens_on(
        self, store: NoustStore
    ) -> None:
        assert described(store, port=3000).url == f"https://{DOMAIN}"


class TestDescribedFromARow:
    def row(self, store: NoustStore, **fields: object) -> App:
        return store.create_app(
            App(domain=DOMAIN, app_type="nodejs", app_path=f"/var/www/apps/{DOMAIN}", **fields)
        )

    def test_on_a_machine_with_no_site_it_is_the_machine_and_the_port(
        self, store: NoustStore, container_host: None
    ) -> None:
        app = self.row(store, port=8081)

        assert deployment_for(app).url == "http://nas-oficina:8081"

    def test_on_a_server_it_follows_the_row_certificate(self, store: NoustStore) -> None:
        app = self.row(store, port=3000, ssl_enabled=False)

        assert deployment_for(app).url == f"http://{DOMAIN}"


class TestWhatAnAddressMeansToTheDeployment:
    def test_an_address_that_was_given_is_the_url(self) -> None:
        deployment = Deployment(domain=DOMAIN, address="http://nas-oficina:8081")

        assert deployment.url == "http://nas-oficina:8081"

    def test_with_none_it_is_what_it_always_was(self) -> None:
        assert Deployment(domain=DOMAIN, ssl=False).url == f"http://{DOMAIN}"
        assert Deployment(domain=DOMAIN).url == f"https://{DOMAIN}"
