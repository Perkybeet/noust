# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
There is one answer to "where does this application answer", and a static site has one.

``answers_at`` is the answer: behind a site, the domain over HTTPS when a certificate covers it and over
HTTP when none does; without a site, the machine and the port. ``reached`` is what the address card and
``noust status`` give a person, and used to say "it serves no web" for any application with no port, which
is what a static site and a PHP site are: a site on a server that nginx serves by its name. It now asks the
same thing, so the card, the variables and the statuses of a code host cannot disagree.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.store import App
from noust.deployers.helpers import edge as edges

DOMAIN = "docs.example.com"


@pytest.fixture(autouse=True)
def a_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    monkeypatch.setattr("noust.deployers.helpers.edge.machine_name", lambda _runner: "nas-oficina")
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def container_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()


def app(**fields: object) -> App:
    return App(domain=DOMAIN, app_path=f"/var/www/apps/{DOMAIN}", **fields)  # type: ignore[arg-type]


#: What each application is reached at on a server: (what it is, its row, what it is reached as).
ON_A_SERVER = [
    (
        "a static site with a certificate",
        {"is_static": True, "port": None, "ssl_enabled": True},
        edges.Reached("site", "https://docs.example.com", None),
    ),
    (
        "a static site without one",
        {"is_static": True, "port": None, "ssl_enabled": False},
        edges.Reached("site", "http://docs.example.com", None),
    ),
    (
        "a PHP site, served by its pool behind the site",
        {"is_static": True, "app_type": "php", "port": None, "ssl_enabled": True},
        edges.Reached("site", "https://docs.example.com", None),
    ),
    (
        "an application with a port and a certificate",
        {"port": 3000, "ssl_enabled": True},
        edges.Reached("site", "https://docs.example.com", 3000),
    ),
    (
        "an application with a port and no certificate",
        {"port": 3000, "ssl_enabled": False},
        edges.Reached("site", "http://docs.example.com", 3000),
    ),
    (
        "a stack that publishes no port, which has no site",
        {"app_type": "docker-compose", "port": None},
        edges.Reached("none", None, None),
    ),
]

ON_A_CONTAINER_HOST = [
    (
        "an application with a port",
        {"app_type": "container", "port": 8081, "ssl_enabled": True},
        edges.Reached("port", "http://nas-oficina:8081", 8081),
    ),
    (
        "a stack with a port",
        {"app_type": "docker-compose", "port": 8000, "ssl_enabled": False},
        edges.Reached("port", "http://nas-oficina:8000", 8000),
    ),
    (
        "a static site, which nothing serves there",
        {"is_static": True, "port": None},
        edges.Reached("none", None, None),
    ),
    (
        "a stack that publishes no port",
        {"app_type": "docker-compose", "port": None},
        edges.Reached("none", None, None),
    ),
]


@pytest.mark.parametrize(
    ("row", "expected"), [case[1:] for case in ON_A_SERVER], ids=[case[0] for case in ON_A_SERVER]
)
def test_on_a_server(row: dict[str, object], expected: edges.Reached) -> None:
    assert edges.reached(app(**row)) == expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [case[1:] for case in ON_A_CONTAINER_HOST],
    ids=[case[0] for case in ON_A_CONTAINER_HOST],
)
def test_on_a_container_host(
    container_host: None, row: dict[str, object], expected: edges.Reached
) -> None:
    assert edges.reached(app(**row)) == expected


@pytest.mark.parametrize(
    "row", [case[1] for case in ON_A_SERVER[:5]], ids=[case[0] for case in ON_A_SERVER[:5]]
)
def test_the_card_never_says_a_different_thing_than_the_answer_the_variables_ask(
    row: dict[str, object],
) -> None:
    found = edges.reached(app(**row))
    port = row.get("port")

    assert found.url == edges.answers_at(
        DOMAIN, ssl=bool(row.get("ssl_enabled")), port=port if isinstance(port, int) else None
    )


def test_a_site_has_no_hint_for_the_nas_proxy() -> None:
    assert edges.proxy_hint(app(is_static=True, port=None)) is None
