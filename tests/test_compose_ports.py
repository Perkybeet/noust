# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One reader of a Compose service's ports, and the decisions built on it.

``_get_primary_port``, ``_parse_compose_services`` and the nginx builder each
had their own reading of ``ports:``. The first split on ``:`` and took the
first piece, so ``127.0.0.1:3000:3000`` failed and fell back to 3000 - the
backend, in Proggest, whose site then proxied ``/`` to the API instead of the
web front.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from noust.deployers.docker_compose import parse_services
from noust.deployers.helpers.compose_ports import (
    PortMapping,
    is_headless_stack,
    parse_ports,
    published_port,
    web_root_service,
)

FIXTURES = Path(__file__).parent / "fixtures" / "compose"


def services_of(text: str) -> list:
    """The services of a compose document, as the deployer reads them."""
    return parse_services(yaml.safe_load(text))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3000", [PortMapping(None, None, 3000, "tcp")]),
        (3000, [PortMapping(None, None, 3000, "tcp")]),
        ("8080:80", [PortMapping(None, 8080, 80, "tcp")]),
        ("127.0.0.1:3000:3000", [PortMapping("127.0.0.1", 3000, 3000, "tcp")]),
        ("[::1]:3000:3000", [PortMapping("::1", 3000, 3000, "tcp")]),
        ("127.0.0.1::3000", [PortMapping("127.0.0.1", None, 3000, "tcp")]),
        (
            "3000-3002:3000-3002",
            [
                PortMapping(None, 3000, 3000, "tcp"),
                PortMapping(None, 3001, 3001, "tcp"),
                PortMapping(None, 3002, 3002, "tcp"),
            ],
        ),
        ("53:53/udp", [PortMapping(None, 53, 53, "udp")]),
        ("8443:443/tcp", [PortMapping(None, 8443, 443, "tcp")]),
        (
            {"target": 80, "published": "8080", "host_ip": "127.0.0.1"},
            [PortMapping("127.0.0.1", 8080, 80, "tcp")],
        ),
        ({"target": 53, "published": 53, "protocol": "udp"}, [PortMapping(None, 53, 53, "udp")]),
        ({"target": 80}, [PortMapping(None, None, 80, "tcp")]),
        ("${WEB_PORT:-8080}:80", [PortMapping(None, 8080, 80, "tcp")]),
    ],
)
def test_every_form_compose_accepts_is_read(value: object, expected: list[PortMapping]) -> None:
    assert parse_ports(value) == expected


def test_a_list_of_entries_is_read_in_order() -> None:
    assert parse_ports(["127.0.0.1:3001:3001", "9229"]) == [
        PortMapping("127.0.0.1", 3001, 3001, "tcp"),
        PortMapping(None, None, 9229, "tcp"),
    ]


@pytest.mark.parametrize("value", [None, [], "", "${PORT}:3000", "not-a-port", "70000:80", {}])
def test_what_cannot_be_read_yields_no_mapping(value: object) -> None:
    assert parse_ports(value) == []


def test_the_primary_port_skips_the_ip_compose_binds_to() -> None:
    services = services_of("services:\n  api:\n    ports: ['127.0.0.1:3000:3000']\n")

    assert published_port(services[0]) == 3000


def test_the_web_root_is_the_service_in_front_of_another_web_service() -> None:
    """In Proggest the frontend depends on the backend: '/' goes to the frontend."""
    services = parse_services(
        yaml.safe_load((FIXTURES / "proggest.docker-compose.prod.yml").read_text())
    )

    root = web_root_service(services)

    assert root == "frontend"
    assert published_port(next(s for s in services if s.name == root)) == 3001


def test_without_a_dependency_between_web_services_the_first_one_is_the_root() -> None:
    services = services_of(
        "services:\n"
        "  web:\n    ports: ['8080:80']\n"
        "  api:\n    ports: ['3001:3000']\n"
        "  db:\n    image: postgres\n"
    )

    assert web_root_service(services) == "web"


def test_in_a_chain_the_root_is_the_one_nothing_else_fronts() -> None:
    services = services_of(
        "services:\n"
        "  api:\n    ports: ['3000:3000']\n"
        "  bff:\n    ports: ['3100:3100']\n    depends_on: [api]\n"
        "  web:\n    ports: ['3200:3200']\n    depends_on:\n      bff:\n        condition: service_healthy\n"
    )

    assert web_root_service(services) == "web"


def test_a_stack_without_web_services_has_no_root() -> None:
    services = services_of("services:\n  worker:\n    build: .\n")

    assert web_root_service(services) is None


def test_the_licitaciones_worker_is_headless() -> None:
    """Owner item 57: the production worker publishes nothing and is not a web."""
    services = parse_services(
        yaml.safe_load((FIXTURES / "licitaciones.docker-compose.yml").read_text())
    )

    assert [s.name for s in services] == ["licitaciones-avisos"]
    assert is_headless_stack(services) is True


def test_a_stack_publishing_only_udp_is_headless() -> None:
    """Nothing to proxy HTTP to: a DNS server is not a web."""
    services = services_of("services:\n  dns:\n    image: coredns\n    ports: ['53:53/udp']\n")

    assert is_headless_stack(services) is True


def test_a_stack_with_a_published_port_is_not_headless() -> None:
    services = services_of(
        "services:\n  worker:\n    build: .\n  web:\n    image: nginx\n    ports: ['8080:80']\n"
    )

    assert is_headless_stack(services) is False


def test_a_port_that_cannot_be_read_is_not_taken_for_none() -> None:
    """``${PORT}:3000`` publishes something; calling the stack headless would drop its site."""
    services = services_of("services:\n  web:\n    image: nginx\n    ports: ['${PORT}:3000']\n")

    assert is_headless_stack(services) is False
