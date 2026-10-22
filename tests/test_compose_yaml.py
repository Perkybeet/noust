# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose document is read the way Docker Compose reads it, not the way YAML 1.1 does.

Found by deploying Gitea's template from Coolify's catalogue: ``- 22222:22`` (its SSH port) is a
number in base 60 to PyYAML, 1333342, and the document written back was refused by Compose
("Invalid containerPort: 1333342"). The same reading hid ``2222:22`` from the port reader of every
stack that published an unquoted SSH port, and turned ``restart: no`` into ``restart: false``.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
import yaml

from noust.core.compose_yaml import load_compose
from noust.deployers.compose_paste import analyze_document
from noust.deployers.helpers.compose_ports import parse_ports
from noust.recipes.coolify import convert


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("22222:22", "22222:22"),
        ("2222:22", "2222:22"),
        ("5353:53", "5353:53"),
        ("8080:80", "8080:80"),
        ("127.0.0.1:2222:22", "127.0.0.1:2222:22"),
        ("53:53/udp", "53:53/udp"),
        ("1:30", "1:30"),
    ],
)
def test_a_port_mapping_is_the_string_it_is_whatever_its_container_port(
    text: str, expected: str
) -> None:
    assert load_compose(f"ports:\n  - {text}\n") == {"ports": [expected]}


@pytest.mark.parametrize("word", ["no", "yes", "on", "off", "No", "Yes", "ON", "OFF", "y", "n"])
def test_the_words_of_yaml_1_1_are_strings(word: str) -> None:
    assert load_compose(f"restart: {word}\n") == {"restart": word}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("true", True),
        ("false", False),
        ("True", True),
        ("FALSE", False),
        ("3000", 3000),
        ("0", 0),
        ("-1", -1),
        ("0x10", 16),
        ("1.5", 1.5),
        ("~", None),
        ("null", None),
        ("hello", "hello"),
        ("'22222:22'", "22222:22"),
    ],
)
def test_the_scalars_compose_knows_are_what_they_were(text: str, expected: Any) -> None:
    assert load_compose(f"value: {text}\n") == {"value": expected}


def test_a_file_can_be_read_as_well_as_text() -> None:
    assert load_compose(io.StringIO("ports: [22222:22]\n")) == {"ports": ["22222:22"]}


def test_the_rest_of_the_program_keeps_reading_yaml_the_way_it_did() -> None:
    """The configuration and the manifests are not Compose: the loader is not changed for them."""
    assert yaml.safe_load("port: 22222:22\nswitch: no\n") == {"port": 1333342, "switch": False}


def test_what_cannot_be_read_is_still_a_yaml_error() -> None:
    with pytest.raises(yaml.YAMLError):
        load_compose("services: [unclosed\n")


# -- what is decided from what is read -------------------------------------------------------------


SSH = """\
services:
  git:
    image: gitea/gitea:1
    restart: no
    ports:
      - 127.0.0.1:3000:3000
      - 22222:22
"""


def test_an_unquoted_ssh_port_is_read_as_the_port_it_is() -> None:
    document = load_compose(SSH)

    mappings = parse_ports(document["services"]["git"]["ports"])

    assert [(m.host_port, m.container_port) for m in mappings] == [(3000, 3000), (22222, 22)]


def test_a_pasted_document_with_one_is_analysed_with_it() -> None:
    found = analyze_document(SSH)

    assert found.deployable
    assert [service.name for service in found.services] == ["git"]
    assert found.web == ["git"]


def test_a_converted_template_keeps_the_ports_restart_policy_and_words_it_was_given() -> None:
    """The document written back must be what Compose accepts, not a base 60 number."""
    template = (
        "# port: 3000\n"
        "services:\n"
        "  gitea:\n"
        "    image: gitea/gitea:1\n"
        "    restart: no\n"
        "    environment:\n"
        "      - SERVICE_URL_GITEA_3000\n"
        "    ports:\n"
        "      - 22222:22\n"
    )

    result = convert(template, domain="git.example.com", port=3001)

    written = yaml.safe_load(result.document)["services"]["gitea"]
    assert "22222:22" in written["ports"]
    assert "1333342" not in result.document
    assert written["restart"] == "no"  # kept: neither false nor replaced by the default
    assert "restart: unless-stopped" not in result.document
