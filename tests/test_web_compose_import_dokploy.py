# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Dokploy template converted over the API (checklist D-26).

``tests/test_web_compose_import.py`` pins the endpoint for Coolify's templates and
``tests/test_dokploy_template.py`` the conversion; here: that ``format: dokploy`` reaches the Dokploy
converter, that the files a template mounts come back beside the document, that the permission is
the one Coolify's conversion takes, and that what a template must not do is a 400 in words.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from tests.teams_support import World, build_world

COMPOSE = """\
services:
  web:
    image: nginx:1.27
    volumes:
      - ../files/site.conf:/etc/nginx/conf.d/site.conf:ro
"""

TEMPLATE = """\
[variables]
main_domain = "${domain}"
token = "${password:24}"

[config]
env = ["SITE=${main_domain}"]

[[config.domains]]
serviceName = "web"
port = 80
host = "${main_domain}"

[[config.mounts]]
filePath = "site.conf"
content = "server_name ${main_domain}; set $t ${token};"
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def post(world: World, body: dict[str, Any], who: str = "admin") -> Any:
    return world.client.post("/api/compose/import", json=body, headers=world.as_(who))


def test_a_dokploy_template_comes_back_with_its_document_values_and_files(world: World) -> None:
    response = post(
        world,
        {
            "format": "dokploy",
            "document": COMPOSE,
            "template": TEMPLATE,
            "domain": "site.example.com",
            "port": 18080,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "127.0.0.1:${PORT:-18080}:80" in body["document"]
    assert "./files/site.conf:/etc/nginx/conf.d/site.conf:ro" in body["document"]
    assert body["values"]["SITE"] == "site.example.com"
    assert set(body["files"]) == {"files/site.conf"}
    assert "server_name site.example.com;" in body["files"]["files/site.conf"]
    assert body["secret_files"] == ["files/site.conf"]
    assert (body["routed_service"], body["routed_port"]) == ("web", 80)
    assert body["analysis"]["deployable"] is True


def test_the_default_format_is_still_coolifys(world: World) -> None:
    body = post(world, {"document": "services:\n  a:\n    image: busybox\n"}).json()

    assert body["files"] == {}
    assert body["secret_files"] == []


def test_a_template_that_needs_the_domain_is_a_400_that_names_it(world: World) -> None:
    response = post(world, {"format": "dokploy", "document": COMPOSE, "template": TEMPLATE})

    assert response.status_code == 400
    assert "domain" in response.text


@pytest.mark.parametrize(
    "template",
    [
        '[[config.mounts]]\nfilePath = "../../etc/cron.d/x"\ncontent = "x"\n',
        "this is [not toml",
    ],
)
def test_a_template_that_cannot_be_converted_is_a_400_in_words(world: World, template: str) -> None:
    response = post(world, {"format": "dokploy", "document": COMPOSE, "template": template})

    assert response.status_code == 400, response.text


def test_a_format_nobody_knows_is_refused(world: World) -> None:
    assert post(world, {"format": "heroku", "document": COMPOSE}).status_code == 422


def test_the_permission_is_the_one_coolifys_conversion_takes(world: World) -> None:
    body = {
        "format": "dokploy",
        "document": COMPOSE,
        "template": TEMPLATE,
        "domain": "a.example.com",
    }

    assert post(world, body, "dana").status_code == 403


def test_the_biggest_template_of_the_catalogue_fits_the_request(world: World) -> None:
    """ChirpStack's template.toml is 283 KiB because it carries the files it mounts."""
    template = "[config]\nenv = []\n# " + "x" * (290 * 1024)

    response = post(world, {"format": "dokploy", "document": COMPOSE, "template": template})

    assert response.status_code == 200, response.text[:200]


def test_a_template_beyond_the_limit_is_refused_before_it_is_read(world: World) -> None:
    body = {"format": "dokploy", "document": COMPOSE, "template": "#" + "x" * (1100 * 1024)}

    assert post(world, body).status_code in (413, 422)
