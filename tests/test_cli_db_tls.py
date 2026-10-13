# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db instance tls|renew-tls|tls-ca`` and ``noust db link --tls`` (checklist C-25, C-26).

The commands are clients of the one TLS manager, like the console's API. What matters at this
level: nothing restarts before the operator has seen the consequences and said yes, ``--plan``
changes nothing, a mode the engine cannot serve is refused with why, and what a link did is
printed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import db as db_cli
from noust.core.config import Config
from noust.core.db_instance_store import READY, DbInstanceStore
from noust.core.fs import RealFileSystem
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.managers.database import tls_authority
from tests.database_migration_support import Lab, build_lab
from tests.database_move_support import DOMAIN, HOST
from tests.test_database_tls import TlsHost, UnprivilegedFs


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    monkeypatch.setattr(tls_authority, "authority_dir", lambda: tmp_path / "etc" / "noust" / "tls")
    write = RealFileSystem.write_text
    monkeypatch.setattr(
        RealFileSystem,
        "write_text",
        lambda self, path, content, *, mode=0o644, owner=None: write(
            self, path, content, mode=mode
        ),
    )
    Config.reset_instance()
    try:
        for built in build_lab(tmp_path, monkeypatch):
            monkeypatch.setattr(db_cli, "_service", lambda logger, built=built: built.service)
            monkeypatch.setattr(
                "noust.managers.database.managed.server_resources", built.managed._resources
            )
            host = TlsHost(tmp_path)
            set_runner(host)
            built.world.host = host
            built.managed._fs = UnprivilegedFs()
            built.managed.create("pg16", "postgresql", version="16", memory="1g")
            yield built
    finally:
        Config.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", *args], input=input)


class TestShowing:
    def test_an_instance_that_serves_nothing_says_so(self, lab: Lab, log: list[str]) -> None:
        result = run("instance", "tls", "pg16")

        assert result.exit_code == 0, result.output
        assert "off" in "\n".join(log)

    def test_the_status_is_json_for_scripts(self, lab: Lab) -> None:
        result = run("instance", "tls", "pg16", "--json")

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["mode"] == "off" and body["certificate"] is None

    def test_there_is_no_authority_until_an_instance_first_serves_tls(
        self, lab: Lab, log: list[str]
    ) -> None:
        result = run("instance", "tls-ca")

        assert result.exit_code == 0, result.output
        assert "No authority yet" in "\n".join(log)
        assert json.loads(run("instance", "tls-ca", "--json").output) == {"authority": None}


class TestChanging:
    def test_a_plan_says_what_restarts_and_changes_nothing(self, lab: Lab, log: list[str]) -> None:
        result = run("instance", "tls", "pg16", "--mode", "required", "--plan")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "off -> required" in text and "refused" in text
        assert lab.managed.get("pg16").tls_mode == "off"

    def test_it_asks_before_restarting_the_engine(self, lab: Lab, log: list[str]) -> None:
        result = run("instance", "tls", "pg16", "--mode", "optional", input="n\n")

        assert result.exit_code != 0
        assert lab.managed.get("pg16").tls_mode == "off"

    def test_yes_turns_it_on_and_the_certificate_is_the_authoritys(
        self, lab: Lab, log: list[str]
    ) -> None:
        result = run("instance", "tls", "pg16", "--mode", "optional", "--yes")

        assert result.exit_code == 0, result.output
        assert lab.managed.get("pg16").tls_mode == "optional"
        shown = run("instance", "tls", "pg16")
        assert shown.exit_code == 0
        text = "\n".join(log)
        assert "Certificate for" in text and "ca.crt" in text
        assert "PRIVATE KEY" not in text

    def test_the_authority_is_shown_once_it_exists(self, lab: Lab, log: list[str]) -> None:
        run("instance", "tls", "pg16", "--mode", "optional", "--yes")
        log.clear()

        result = run("instance", "tls-ca")

        assert result.exit_code == 0, result.output
        assert "Fingerprint" in "\n".join(log)

    def test_a_mode_the_engine_cannot_serve_is_refused_with_the_reason(
        self, lab: Lab, log: list[str]
    ) -> None:
        # MariaDB before 11.4 cannot refuse plaintext for the whole server.
        # The lab's service resolves PostgreSQL only: the record is all this needs.
        DbInstanceStore(lab.world.store).create(
            name="maria",
            flavour="mariadb",
            version="10.11",
            image="mariadb",
            digest="sha256:" + "c" * 64,
            host_port=3307,
            memory_limit_mb=512,
            created_by=None,
        )
        DbInstanceStore(lab.world.store).set_state("maria", READY)

        result = run("instance", "tls", "maria", "--mode", "required", "--yes")

        assert result.exit_code != 0
        assert "11.4" in "\n".join(log) + result.output
        assert lab.managed.get("maria").tls_mode == "off"

    def test_an_unknown_mode_is_refused_by_the_command_itself(self, lab: Lab) -> None:
        result = run("instance", "tls", "pg16", "--mode", "mandatory")

        assert result.exit_code == 2
        assert "off" in result.output and "required" in result.output

    def test_renewing_asks_first_too(self, lab: Lab, log: list[str]) -> None:
        run("instance", "tls", "pg16", "--mode", "optional", "--yes")
        signed = lab.world.host.issued

        refused = run("instance", "renew-tls", "pg16", input="n\n")
        renewed = run("instance", "renew-tls", "pg16", "--yes")

        assert refused.exit_code != 0 and renewed.exit_code == 0, renewed.output
        assert lab.world.host.issued > signed

    def test_renewing_an_instance_that_serves_nothing_says_how_to_turn_it_on(
        self, lab: Lab, log: list[str]
    ) -> None:
        result = run("instance", "renew-tls", "pg16", "--yes")

        assert result.exit_code != 0
        assert "noust db instance tls pg16 --mode optional" in result.exception.details


class TestLinking:
    def test_the_host_engine_is_linked_asking_for_encryption(
        self, lab: Lab, log: list[str]
    ) -> None:
        result = run("link", DOMAIN, "shop_db", "--engine", HOST, "--tls", "require")

        assert result.exit_code == 0, result.output
        assert "sslmode=require" in lab.env()["DATABASE_URL"]
        listed = run("links", DOMAIN)
        assert listed.exit_code == 0, listed.output
        assert "tls require" in listed.output

    def test_a_mode_that_cannot_be_kept_is_refused_and_the_application_is_untouched(
        self, lab: Lab, log: list[str]
    ) -> None:
        before = lab.env()

        result = run("link", DOMAIN, "shop_db", "--engine", HOST, "--tls", "verify-full")

        assert result.exit_code != 0
        assert "noust db move" in "\n".join(log) + result.output
        assert lab.env() == before

    def test_the_command_only_takes_the_modes_a_link_has(self, lab: Lab) -> None:
        result = run("link", DOMAIN, "shop_db", "--engine", HOST, "--tls", "optional")

        assert result.exit_code == 2
        assert "verify-full" in result.output
