# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust domain list`` and ``noust domain expiry`` say when the registration of a domain expires (item 173).

What the registries said is kept in the store (:mod:`noust.core.domain_registrations`); these read it
and ask nobody, except ``noust domain expiry --refresh``, which is a person asking, says what it sends
and to whom before it does, and is audited as the change it is.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.commands import domain as domain_module
from noust.core.config import Config
from noust.core.domain_registrations import (
    NO_RDAP,
    OK,
    Registration,
    RegistrationStore,
)
from noust.core.store import DomainRecord, NoustStore

APP = "shop.example.com"
PRIMARY = DomainRecord(id=1, app_id=1, domain=APP, kind="primary", created_at="2026-09-25")
ALIAS = DomainRecord(id=2, app_id=1, domain="www.shop.example.com", kind="alias", created_at="t1")
SPANISH = DomainRecord(id=3, app_id=1, domain="tienda.example.es", kind="alias", created_at="t2")


def invoke(args: list[str]) -> Result:
    return CliRunner().invoke(root_cli, args)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def soon(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=1)).isoformat(
        timespec="seconds"
    )


def kept(store: NoustStore) -> RegistrationStore:
    records = RegistrationStore(store)
    now = datetime.now(timezone.utc)
    records.save(
        Registration(
            domain="example.com",
            status=OK,
            expires_at=soon(21),
            rdap_base="https://rdap.verisign.com/com/v1/",
            checked_at=now.isoformat(timespec="seconds"),
            next_check_at=soon(1),
        )
    )
    records.remember_registrable(APP, "example.com", now)
    records.remember_registrable("www.shop.example.com", "example.com", now)
    records.save(
        Registration(
            domain="example.es",
            status=NO_RDAP,
            expires_at=None,
            rdap_base=None,
            checked_at=now.isoformat(timespec="seconds"),
            next_check_at=soon(30),
            detail="This registry does not publish the expiry (the registry of .es has no RDAP service).",
        )
    )
    records.remember_registrable("tienda.example.es", "example.es", now)
    return records


@pytest.fixture
def domains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(domain_module, "list_domains", lambda app: [PRIMARY, ALIAS, SPANISH])
    monkeypatch.setattr(domain_module, "bindings", lambda app: [])


class TestDomainList:
    def test_the_json_of_each_name_carries_what_its_registry_said(
        self, store: NoustStore, domains: None
    ) -> None:
        kept(store)

        result = invoke(["domain", "list", APP, "--json"])

        items = {item["domain"]: item for item in json.loads(result.output)["items"]}
        assert items[APP]["registration"]["domain"] == "example.com"
        assert items[APP]["registration"]["expires_at"].startswith(soon(21)[:10])
        assert items[APP]["registration"]["days_left"] in (21, 22)
        assert items[APP]["registration"]["registry"] == "rdap.verisign.com"
        assert items["tienda.example.es"]["registration"]["status"] == NO_RDAP
        assert items["tienda.example.es"]["registration"]["days_left"] is None

    def test_a_name_never_looked_at_has_none(self, store: NoustStore, domains: None) -> None:
        result = invoke(["domain", "list", APP, "--json"])

        items = json.loads(result.output)["items"]
        assert all(item["registration"] is None for item in items)

    def test_the_table_has_the_column_only_when_something_is_known(
        self, store: NoustStore, domains: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.logger import Logger

        tables: list[tuple[list[Any], list[Any]]] = []
        monkeypatch.setattr(
            Logger,
            "table",
            lambda self, headers, rows, justify=None: tables.append((headers, rows)),
        )

        invoke(["domain", "list", APP])
        kept(store)
        invoke(["domain", "list", APP])

        assert tables[0][0] == ["Domain", "Kind", "Port", "Certificate", "Added"]
        assert tables[1][0] == ["Domain", "Kind", "Port", "Certificate", "Added", "Registration"]
        cells = [row[-1] for row in tables[1][1]]
        assert cells[0].startswith(soon(21)[:10]) and "days" in cells[0]
        assert "does not publish the expiry" in cells[2]


class TestDomainExpiry:
    def test_it_lists_what_is_known_without_asking_anyone(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kept(store)
        asked: list[Any] = []
        monkeypatch.setattr(
            "noust.managers.domain_registration.DomainRegistrations.check_names",
            lambda self, names, force=False: asked.append(names) or [],
        )

        result = invoke(["domain", "expiry", "--json"])

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert [item["domain"] for item in body["items"]] == ["example.com", "example.es"]
        assert body["enabled"] is False
        assert asked == []

    def test_it_says_nothing_is_asked_while_the_switch_is_off_and_how_to_turn_it_on(
        self, store: NoustStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from noust.core.logger import Logger

        lines: list[str] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                Logger, "_write", lambda self, message, newline=True: lines.append(message)
            )
            invoke(["domain", "expiry"])

        text = "\n".join(lines)
        assert "monitor.domain_expiry" in text
        assert "noust config set monitor.domain_expiry true" in text

    def test_with_the_switch_on_it_says_the_check_is_daily(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch, real_config: Config
    ) -> None:
        from noust.core.logger import Logger

        real_config.set("monitor.domain_expiry", True)
        lines: list[str] = []
        monkeypatch.setattr(
            Logger, "_write", lambda self, message, newline=True: lines.append(message)
        )

        invoke(["domain", "expiry"])

        assert "once a day" in "\n".join(lines)

    def test_refresh_asks_now_for_the_names_of_the_application_and_says_what_it_sends(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.logger import Logger

        asked: list[tuple[list[str], bool]] = []

        def fake(self: Any, names: Any, force: bool = False) -> list[Registration]:
            asked.append((list(names), force))
            return kept(store).all()

        monkeypatch.setattr(
            "noust.managers.domain_registration.DomainRegistrations.check_names", fake
        )
        monkeypatch.setattr(domain_module, "list_domains", lambda app: [PRIMARY, ALIAS])
        lines: list[str] = []
        monkeypatch.setattr(
            Logger, "_write", lambda self, message, newline=True: lines.append(message)
        )

        result = invoke(["domain", "expiry", APP, "--refresh"])

        assert result.exit_code == 0, result.output
        assert asked == [([APP, "www.shop.example.com"], True)]
        text = "\n".join(lines)
        assert "goes to the registry" in text

    def test_refresh_without_an_application_asks_about_every_name_served(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[list[str]] = []
        monkeypatch.setattr(
            "noust.managers.domain_registration.DomainRegistrations.check_names",
            lambda self, names, force=False: asked.append(list(names)) or [],
        )
        monkeypatch.setattr(
            "noust.managers.domain_registration.served_names", lambda store=None: ["a.example.com"]
        )

        invoke(["domain", "expiry", "--refresh"])

        assert asked == [["a.example.com"]]

    def test_listing_is_not_an_audited_change_and_asking_is(self) -> None:
        command = domain_module.cli.commands["expiry"]

        assert command.read_only({"refresh": False}) is True  # type: ignore[attr-defined,operator]
        assert command.read_only({"refresh": True}) is False  # type: ignore[attr-defined,operator]
