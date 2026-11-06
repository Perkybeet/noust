# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``record_install``: the one place that writes down what Noust installed (item 118).

Every installer ends by calling it, so what the ledger holds does not depend on which button, command
or recipe brought the piece. What is pinned here is what it writes (the packages, the files Noust
added, who and through which installer), that it never claims a file that is not Noust's, and that a
ledger that cannot be written does not undo an install that worked.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from noust.core.audit.actor import Actor
from noust.core.audit.context import bind as bind_audit_context
from noust.core.software_ledger import SoftwareLedger
from noust.managers.server import components
from noust.managers.server.host import HostPaths
from noust.managers.server.software_record import marks_present, record_install


@pytest.fixture
def host(tmp_path: Path) -> HostPaths:
    for directory in ("etc/apt/sources.list.d", "usr/share/keyrings", "etc/yum.repos.d"):
        (tmp_path / directory).mkdir(parents=True)
    return HostPaths(tmp_path)


def write(host: HostPaths, path: str) -> None:
    host.at(path).write_text("x")


def test_it_writes_the_packages_the_files_noust_added_and_the_way(host: HostPaths) -> None:
    write(host, "/etc/apt/sources.list.d/noust-cloudflared.list")
    write(host, "/usr/share/keyrings/noust-cloudflared.gpg")

    record_install("cloudflared", via="cloudflared", packages=["cloudflared"], host=host)

    row = SoftwareLedger().get("cloudflared")
    assert row is not None
    assert row.packages == ("cloudflared",) and row.via == "cloudflared"
    assert row.repository_files == (
        "/etc/apt/sources.list.d/noust-cloudflared.list",
        "/usr/share/keyrings/noust-cloudflared.gpg",
    )


def test_files_that_are_not_noust_s_are_never_claimed(host: HostPaths) -> None:
    write(host, "/etc/apt/sources.list.d/cloudflared.list")  # Cloudflare's own instructions
    write(host, "/etc/apt/sources.list.d/operators.list")

    record_install("cloudflared", via="cloudflared", packages=["cloudflared"], host=host)

    row = SoftwareLedger().get("cloudflared")
    assert row is not None and row.repository_files == ()


def test_the_files_may_be_named_by_the_installer(host: HostPaths) -> None:
    record_install(
        "cloudflared",
        via="package",
        repository_files=["/etc/apt/sources.list.d/noust-cloudflared.list"],
        host=host,
    )

    row = SoftwareLedger().get("cloudflared")
    assert row is not None
    assert row.repository_files == ("/etc/apt/sources.list.d/noust-cloudflared.list",)


def test_it_names_who_acted_as_the_audit_trail_does() -> None:
    with bind_audit_context(actor=Actor(kind="cli", name="root"), correlation_id="c1"):
        record_install("chrony", via="package", packages=["chrony"])

    row = SoftwareLedger().get("chrony")
    assert row is not None and row.actor == "cli:root"


def test_a_component_the_registry_does_not_have_is_not_recorded_and_does_not_raise() -> None:
    record_install("not_a_component", via="package", packages=["x"])

    assert SoftwareLedger().all() == {}


def test_a_ledger_that_cannot_be_written_does_not_undo_the_install(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        def record(self, *args: Any, **kwargs: Any) -> None:
            raise sqlite3.OperationalError("database is locked")

    with caplog.at_level("WARNING"):
        record_install("chrony", via="package", packages=["chrony"], ledger=Broken())  # type: ignore[arg-type]

    assert "database is locked" in caplog.text


def test_a_value_the_ledger_refuses_is_logged_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        record_install("chrony", via="Not An Identifier", packages=["chrony"])

    assert SoftwareLedger().all() == {}
    assert "chrony" in caplog.text


def test_the_marks_found_are_those_of_the_component_only(host: HostPaths) -> None:
    write(host, "/etc/apt/sources.list.d/noust-pgdg.list")
    write(host, "/etc/yum.repos.d/noust-pgdg17.repo")
    write(host, "/etc/apt/sources.list.d/noust-cloudflared.list")
    postgresql = components.component("postgresql")
    assert postgresql is not None

    found = [str(path.relative_to(host.root)) for path in marks_present(postgresql, host)]

    assert found == ["etc/apt/sources.list.d/noust-pgdg.list", "etc/yum.repos.d/noust-pgdg17.repo"]


class FakeEngine:
    """An engine manager, as far as the common install path looks at one."""

    ENGINE_NAME = "postgresql"
    DISPLAY_NAME = "PostgreSQL"

    def __init__(self) -> None:
        self.installed_with: list[object] = []

    def install(self, plan: object) -> None:
        self.installed_with.append(plan)

    def get_version(self) -> str:
        return "16.3"

    def support(self, version: str | None) -> Any:
        return type("Notice", (), {"status": "supported", "message": ""})()

    def warnings(self) -> list[str]:
        return []


def test_an_engine_is_written_down_by_the_path_every_install_of_one_takes() -> None:
    from noust.managers.database.engine_setup import EnginePlan, install_engine

    engine = FakeEngine()
    plan = object()

    install_engine(EnginePlan(engine, None, plan, None))  # type: ignore[arg-type]

    assert engine.installed_with == [plan]
    row = SoftwareLedger().get("postgresql")
    assert row is not None and row.via == "database_engine"


def test_an_engine_that_was_there_already_is_not_written_down() -> None:
    from noust.managers.database.engine_setup import EnginePlan, install_engine

    install_engine(EnginePlan(FakeEngine(), None, None, "postgresql"))  # type: ignore[arg-type]

    assert SoftwareLedger().get("postgresql") is None


def test_a_rehearsal_writes_nothing_in_the_ledger() -> None:
    from noust.core.fs import DryRunFileSystem, set_fs

    set_fs(DryRunFileSystem())
    try:
        record_install("chrony", via="package", packages=["chrony"])
    finally:
        set_fs(None)

    assert SoftwareLedger().all() == {}
