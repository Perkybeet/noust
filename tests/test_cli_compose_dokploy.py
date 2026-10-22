# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust compose import --from dokploy``: a Dokploy template folder in, a stack folder out (D-26).

The converter is pinned by ``tests/test_dokploy_template.py``. What is pinned here is the command:
that a folder of the catalogue (``docker-compose.yml`` and ``template.toml``) is read as one thing,
what is written and with which mode (the ``.env`` and a file holding a generated secret readable by
their owner only), that the JSON never carries a value, and that a path that leaves the folder
writes nothing at all.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import ValidationError

COMPOSE = """\
services:
  web:
    image: nginx:1.27
    volumes:
      - ../files/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ../files/README.md:/readme.md
"""

TEMPLATE = """\
[variables]
main_domain = "${domain}"
admin_token = "${password:20}"

[config]
env = ["SITE=${main_domain}", "TOKEN=${admin_token}"]

[[config.domains]]
serviceName = "web"
port = 80
host = "${main_domain}"

[[config.mounts]]
filePath = "/nginx/nginx.conf"
content = "server_name ${main_domain}; set $token ${admin_token};"

[[config.mounts]]
filePath = "README.md"
content = "Served on ${main_domain}"
"""


@pytest.fixture
def blueprint(tmp_path: Path) -> Path:
    folder = tmp_path / "blueprint"
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(COMPOSE)
    (folder / "template.toml").write_text(TEMPLATE)
    return folder


def run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(root_cli, ["compose", "import", *args])
    assert result.exit_code == code, f"{result.output} {result.exception!r}"
    return result.output


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_blueprint_folder_is_written_as_a_stack_folder(blueprint: Path, tmp_path: Path) -> None:
    out = tmp_path / "stack"

    run(
        str(blueprint),
        "--from",
        "dokploy",
        "--domain",
        "site.example.com",
        "--port",
        "3021",
        "--out",
        str(out),
    )

    assert (out / "docker-compose.yml").is_file()
    assert (out / ".env").is_file()
    assert (out / "files" / "nginx" / "nginx.conf").is_file()
    assert (out / "files" / "README.md").read_text() == "Served on site.example.com"
    assert (
        "./files/nginx/nginx.conf:/etc/nginx/nginx.conf:ro"
        in (out / "docker-compose.yml").read_text()
    )


def test_the_env_and_a_file_with_a_secret_are_private_and_the_other_is_not(
    blueprint: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stack"

    run(
        str(blueprint),
        "--from",
        "dokploy",
        "--domain",
        "site.example.com",
        "--port",
        "3021",
        "--out",
        str(out),
    )

    assert mode(out / ".env") == 0o600
    assert mode(out / "files" / "nginx" / "nginx.conf") == 0o600
    assert mode(out / "files" / "README.md") == 0o644
    assert mode(out / "docker-compose.yml") == 0o644


def test_the_generated_secret_is_in_the_env_and_the_file_alike(
    blueprint: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stack"

    run(
        str(blueprint),
        "--from",
        "dokploy",
        "--domain",
        "site.example.com",
        "--port",
        "3021",
        "--out",
        str(out),
    )

    env = dict(line.split("=", 1) for line in (out / ".env").read_text().splitlines())
    assert env["SITE"] == "site.example.com"
    assert f"set $token {env['TOKEN']};" in (out / "files" / "nginx" / "nginx.conf").read_text()


def test_the_json_names_the_files_and_never_carries_a_value(
    blueprint: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stack"

    output = run(
        str(blueprint),
        "--from",
        "dokploy",
        "--domain",
        "site.example.com",
        "--port",
        "3021",
        "--out",
        str(out),
        "--json",
    )

    document = json.loads(output)
    assert document["files"] == ["files/README.md", "files/nginx/nginx.conf"]
    assert document["deployable"] is True
    token = dict(line.split("=", 1) for line in (out / ".env").read_text().splitlines())["TOKEN"]
    assert token not in output


def test_a_compose_file_and_a_template_given_apart_are_read_together(
    blueprint: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stack"

    run(
        str(blueprint / "docker-compose.yml"),
        "--from",
        "dokploy",
        "--template",
        str(blueprint / "template.toml"),
        "--domain",
        "site.example.com",
        "--port",
        "3021",
        "--out",
        str(out),
    )

    assert (out / "files" / "README.md").is_file()


def test_without_out_nothing_is_written_and_the_document_is_printed(blueprint: Path) -> None:
    output = run(
        str(blueprint), "--from", "dokploy", "--domain", "site.example.com", "--port", "3021"
    )

    assert "# Converted by Noust from a Dokploy template." in output
    assert "127.0.0.1:${PORT:-3021}:80" in output
    assert "Nothing was written" in output


def test_a_folder_that_is_not_a_template_is_a_usage_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    output = run(str(empty), "--from", "dokploy", code=2)

    assert "no docker-compose.yml" in output


def test_a_path_that_leaves_the_folder_writes_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "evil"
    folder.mkdir()
    (folder / "docker-compose.yml").write_text("services:\n  a:\n    image: busybox\n")
    (folder / "template.toml").write_text(
        '[[config.mounts]]\nfilePath = "../../escape"\ncontent = "x"\n'
    )
    out = tmp_path / "stack"

    result = CliRunner().invoke(
        root_cli, ["compose", "import", str(folder), "--from", "dokploy", "--out", str(out)]
    )

    assert result.exit_code != 0
    assert not out.exists()
    assert not (tmp_path / "escape").exists()


def test_a_template_toml_with_coolify_is_a_usage_error(blueprint: Path) -> None:
    output = run(
        str(blueprint / "docker-compose.yml"),
        "--template",
        str(blueprint / "template.toml"),
        code=2,
    )

    assert "--template goes with --from dokploy" in output


def test_a_folder_given_for_a_coolify_template_is_a_usage_error(blueprint: Path) -> None:
    output = run(str(blueprint), code=2)

    assert "is a folder" in output


def test_a_template_that_needs_the_domain_says_so(blueprint: Path) -> None:
    result = CliRunner().invoke(
        root_cli, ["compose", "import", str(blueprint), "--from", "dokploy"]
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, ValidationError)
    assert result.exception.field == "domain"
