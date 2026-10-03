# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which engines, in which versions, Noust installs (3.3, item 71).

What is pinned here:

- a version becomes the distribution's package when the release ships it,
  the upstream repository's otherwise, and a refusal naming where it can be
  had when nothing publishes it for the release, before apt is touched;
- MySQL and MariaDB, Redis and Valkey are separate choices, and one is
  refused while the other is installed;
- an upstream key is trusted only when it is exactly the pinned key;
- an install that names nothing does what 3.2 did.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.exceptions import DatabaseEngineError, DatabaseExistsError, ValidationError
from noust.core.fs import DryRunFileSystem, get_fs, set_fs
from noust.core.runner import DryRunRunner, FakeRunner, set_runner
from noust.managers.database import flavours
from noust.managers.database.engine_setup import install_catalog, plan_engine_install
from noust.managers.database.flavours import (
    PGDG_KEY,
    catalog,
    install_repository,
    plan_install,
    primary_fingerprints,
    resolve_flavour,
)
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager
from noust.managers.server.host import HostPaths, OsRelease


def release(distro: str, codename: str, pretty: str | None = None) -> OsRelease:
    """
    Build an os-release.

    Args:
        distro: ``ID``.
        codename: ``VERSION_CODENAME``.
        pretty: ``PRETTY_NAME``.

    Returns:
        The identity.
    """
    return OsRelease(id=distro, codename=codename, pretty_name=pretty or f"{distro} {codename}")


NOBLE = release("ubuntu", "noble", "Ubuntu 24.04.3 LTS")
BOOKWORM = release("debian", "bookworm", "Debian GNU/Linux 12 (bookworm)")
TRIXIE = release("debian", "trixie", "Debian GNU/Linux 13 (trixie)")
JAMMY = release("ubuntu", "jammy", "Ubuntu 22.04.5 LTS")


def colons(*fingerprints: str) -> str:
    """
    Render what ``gpg --show-keys --with-colons`` prints for some keys.

    Args:
        *fingerprints: One primary fingerprint per key.

    Returns:
        The output, with a subkey after each primary key.
    """
    lines: list[str] = []
    for fingerprint in fingerprints:
        lines += [
            f"pub:-:4096:1:{fingerprint[-16:]}:1318537154:::-:::scSC::::::23::0:",
            f"fpr:::::::::{fingerprint}:",
            "uid:-::::1483540543::690A::Signing Key::::::::::0:",
            "sub:-:4096:1:AAAABBBBCCCCDDDD:1318537154::::::e::::::23:",
            "fpr:::::::::0000000000000000000000000000AAAABBBBCCCCDDDD:",
        ]
    return "\n".join(lines) + "\n"


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    A system root on Ubuntu 24.04 with an empty apt configuration.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper.

    Returns:
        The root.
    """
    (tmp_path / "etc" / "apt" / "sources.list.d").mkdir(parents=True)
    (tmp_path / "usr" / "share" / "keyrings").mkdir(parents=True)
    (tmp_path / "etc" / "os-release").write_text(
        'ID=ubuntu\nVERSION_CODENAME=noble\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path


# ------------------------------------------------------------------ plans


@pytest.mark.parametrize(
    ("os_release", "version", "source", "packages"),
    [
        (NOBLE, None, "distribution", ("postgresql", "postgresql-contrib")),
        (NOBLE, "16", "distribution", ("postgresql-16",)),
        (NOBLE, "17", "upstream", ("postgresql-17",)),
        (NOBLE, "14", "upstream", ("postgresql-14",)),
        (BOOKWORM, "15", "distribution", ("postgresql-15",)),
        (BOOKWORM, "18", "upstream", ("postgresql-18",)),
        (TRIXIE, "17", "distribution", ("postgresql-17",)),
    ],
)
def test_a_postgresql_major_comes_from_where_it_is_published(
    os_release: OsRelease, version: str | None, source: str, packages: tuple[str, ...]
) -> None:
    plan = plan_install("postgresql", version, os_release)

    assert plan.source == source
    assert plan.package_sets == (packages,)
    assert (plan.repository is not None) == (source == "upstream")


def test_pgdg_line_names_the_release_and_the_pinned_keyring() -> None:
    plan = plan_install("postgresql", "17", JAMMY)

    assert plan.repository is not None
    assert plan.repository.key == PGDG_KEY
    assert plan.repository.line() == (
        "deb [signed-by=/usr/share/keyrings/noust-pgdg.gpg] "
        "https://apt.postgresql.org/pub/repos/apt jammy-pgdg main\n"
    )


def test_an_unsupported_postgresql_major_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        plan_install("postgresql", "12", NOBLE)

    assert excinfo.value.field == "version"
    assert "14, 15, 16, 17, 18" in excinfo.value.details


def test_a_release_pgdg_does_not_publish_for_is_refused_with_what_it_does() -> None:
    with pytest.raises(ValidationError) as excinfo:
        plan_install("postgresql", "17", release("ubuntu", "focal", "Ubuntu 20.04.6 LTS"))

    assert "Ubuntu 20.04.6 LTS" in excinfo.value.message
    assert "Ubuntu 24.04 (noble)" in excinfo.value.details


@pytest.mark.parametrize(
    ("os_release", "version", "source", "url"),
    [
        (NOBLE, None, "distribution", None),
        (NOBLE, "10.11", "distribution", None),
        (NOBLE, "11.4", "upstream", "https://deb.mariadb.org/11.4/ubuntu noble main"),
        (BOOKWORM, "11.8", "upstream", "https://deb.mariadb.org/11.8/debian bookworm main"),
        (TRIXIE, "11.8", "distribution", None),
        (JAMMY, "10.11", "upstream", "https://deb.mariadb.org/10.11/ubuntu jammy main"),
    ],
)
def test_a_mariadb_release_comes_from_where_it_is_published(
    os_release: OsRelease, version: str | None, source: str, url: str | None
) -> None:
    plan = plan_install("mariadb", version, os_release)

    assert plan.source == source
    assert plan.package_sets == (("mariadb-server",),)
    if url is not None:
        assert plan.repository is not None and url in plan.repository.line()


def test_mariadb_11_4_on_trixie_is_refused_before_apt() -> None:
    with pytest.raises(ValidationError) as excinfo:
        plan_install("mariadb", "11.4", TRIXIE)

    assert "MariaDB 11.4 publishes no packages" in excinfo.value.message
    assert "11.8" in excinfo.value.details


def test_mysql_is_the_distribution_s_and_debian_has_none() -> None:
    assert plan_install("mysql", None, NOBLE).package_sets == (("mysql-server",),)
    assert plan_install("mysql", "8.0", NOBLE).version == "8.0"

    with pytest.raises(ValidationError) as excinfo:
        plan_install("mysql", None, BOOKWORM)
    assert "noust db install mariadb" in excinfo.value.details

    with pytest.raises(ValidationError):
        plan_install("mysql", "8.4", NOBLE)


def test_valkey_is_an_explicit_choice_where_the_release_ships_it() -> None:
    assert plan_install("valkey", None, NOBLE).package_sets == (("valkey-server",),)
    assert plan_install("valkey", None, TRIXIE).version == "8.1"

    with pytest.raises(ValidationError) as excinfo:
        plan_install("valkey", None, BOOKWORM)
    assert "noust db install redis" in excinfo.value.details


@pytest.mark.parametrize(
    ("os_release", "series", "allowed"),
    [
        (JAMMY, "7.0", True),
        (BOOKWORM, "7.0", True),
        (NOBLE, "7.0", False),
        (NOBLE, "8.0", True),
        (TRIXIE, "8.0", False),
    ],
)
def test_mongodb_series_follow_what_mongodb_publishes(
    os_release: OsRelease, series: str, allowed: bool
) -> None:
    if not allowed:
        with pytest.raises(ValidationError, match=f"MongoDB {series} publishes no packages"):
            plan_install("mongodb", series, os_release)
        return
    plan = plan_install("mongodb", series, os_release)
    assert plan.repository is not None
    assert f"mongodb-org/{series}" in plan.repository.line()
    assert plan.repository.key == flavours.MONGODB_KEYS[series]


def test_an_unknown_release_still_gets_the_distribution_s_packages() -> None:
    questing = release("ubuntu", "questing")

    assert plan_install("postgresql", None, questing).source == "distribution"
    assert plan_install("valkey", None, questing).package_sets == (("valkey-server",),)
    with pytest.raises(ValidationError):
        plan_install("postgresql", "17", questing)


@pytest.mark.parametrize(
    ("typed", "engine", "flavour", "version", "expected"),
    [
        ("mysql", "mysql", None, None, None),
        ("mariadb", "mysql", None, None, "mariadb"),
        ("maria", "mysql", None, None, "mariadb"),
        ("mysql", "mysql", None, "8.0", "mysql"),
        ("mysql", "mysql", "mariadb", None, "mariadb"),
        ("valkey", "redis", None, None, "valkey"),
        ("redis", "redis", None, None, None),
        ("pg", "postgresql", None, "17", "postgresql"),
    ],
)
def test_the_flavour_follows_what_was_typed(
    typed: str, engine: str, flavour: str | None, version: str | None, expected: str | None
) -> None:
    assert resolve_flavour(typed, engine, flavour=flavour, version=version) == expected


def test_a_flavour_of_another_engine_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        resolve_flavour("postgresql", "postgresql", flavour="mariadb")

    assert excinfo.value.field == "flavour"


# ---------------------------------------------------------------- catalog


def test_the_catalog_offers_what_this_release_can_have() -> None:
    entries = {entry.flavour: entry for entry in catalog(BOOKWORM, {}, apt=True)}

    postgres = entries["postgresql"]
    assert postgres.installable
    assert [v.version for v in postgres.versions] == ["14", "15", "16", "17", "18"]
    assert [v.version for v in postgres.versions if v.default] == ["15"]
    assert {v.version: v.source for v in postgres.versions}["15"] == "distribution"
    assert entries["mysql"].blocked == "not_available"
    assert entries["valkey"].blocked == "not_available"
    assert [v.version for v in entries["mongodb"].versions] == ["7.0", "8.0"]
    assert [v.version for v in entries["mariadb"].versions] == ["10.11", "11.4", "11.8"]


def test_the_catalog_never_offers_the_other_flavour_of_an_installed_engine() -> None:
    entries = {entry.flavour: entry for entry in catalog(NOBLE, {"mysql": "mariadb"}, apt=True)}

    assert entries["mariadb"].installed and entries["mariadb"].blocked == "installed"
    assert entries["mysql"].blocked == "conflict"
    assert "/var/lib/mysql" in (entries["mysql"].reason or "")
    assert entries["postgresql"].installable


def test_the_catalog_without_apt_installs_nothing() -> None:
    entries = catalog(NOBLE, {}, apt=False)

    assert all(entry.blocked == "no_apt" and not entry.versions for entry in entries)


def test_the_service_catalog_reads_the_distribution_and_the_engines(
    host: Path, runner: FakeRunner
) -> None:
    runner.only_knows("apt-get", "mysql", "mariadb")
    managers = [PostgresManager(), MySQLManager()]

    built = install_catalog(managers)

    assert built["distribution"] == {
        "id": "ubuntu",
        "codename": "noble",
        "name": "Ubuntu 24.04.3 LTS",
        "known": True,
    }
    by_name = {entry["flavour"]: entry for entry in built["flavours"]}
    assert by_name["mariadb"]["installed"] is True
    assert by_name["mysql"]["blocked"] == "conflict"
    assert by_name["postgresql"]["installable"] is True


# --------------------------------------------------------------- conflicts


def test_mysql_is_refused_while_mariadb_is_installed(host: Path, runner: FakeRunner) -> None:
    runner.only_knows("apt-get", "mysql", "mariadb")
    manager = MySQLManager()

    with pytest.raises(DatabaseExistsError) as excinfo:
        plan_engine_install(manager, "mysql", version="8.0")

    assert "MariaDB is installed" in excinfo.value.message
    assert "/var/lib/mysql" in excinfo.value.details
    assert not [call for call in runner.calls if call[0] == "apt-get"]


def test_valkey_is_refused_while_redis_is_installed(host: Path, runner: FakeRunner) -> None:
    runner.only_knows("apt-get", "redis-cli", "redis-server")

    with pytest.raises(DatabaseExistsError, match="Redis is installed"):
        plan_engine_install(RedisManager(), "valkey")


def test_the_installed_flavour_asked_again_is_already_installed(
    host: Path, runner: FakeRunner
) -> None:
    runner.only_knows("apt-get", "mysql", "mariadb")

    decided = plan_engine_install(MySQLManager(), "mariadb")

    assert decided.already_installed and decided.plan is None


# ----------------------------------------------------------------- install


def test_a_pgdg_install_verifies_the_key_then_adds_the_source_then_installs(
    host: Path, runner: FakeRunner
) -> None:
    runner.only_knows("apt-get")
    runner.script(["curl"], stdout="-----BEGIN PGP PUBLIC KEY BLOCK-----\n")
    runner.script(["gpg"], stdout=colons(PGDG_KEY.fingerprint))
    manager = PostgresManager()

    manager.install(plan_install("postgresql", "17", flavours.distribution()))

    programs = [call[0] for call in runner.calls]
    assert programs[:3] == ["curl", "gpg", "gpg"]
    assert "--show-keys" in runner.calls[1]
    assert runner.calls[2][-3:-1] == ("-o", str(host / "usr/share/keyrings/noust-pgdg.gpg"))
    assert runner.calls[3] == ("apt-get", "update")
    assert runner.calls[4] == ("apt-get", "install", "-y", "postgresql-17")
    assert runner.calls[5:7] == [
        ("systemctl", "enable", "postgresql"),
        ("systemctl", "start", "postgresql"),
    ]
    source = (host / "etc/apt/sources.list.d/noust-pgdg.list").read_text()
    assert "noble-pgdg main" in source and "signed-by=/usr/share/keyrings/noust-pgdg.gpg" in source


@pytest.mark.parametrize(
    "served",
    [
        colons("0" * 40),
        colons(PGDG_KEY.fingerprint, "1" * 40),
        "",
    ],
    ids=["another-key", "a-bundle-with-a-second-key", "nothing"],
)
def test_a_key_that_is_not_exactly_the_pinned_one_is_refused(
    host: Path, runner: FakeRunner, served: str
) -> None:
    runner.only_knows("apt-get")
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=served)

    with pytest.raises(DatabaseEngineError, match="not the one Noust trusts"):
        PostgresManager().install(plan_install("postgresql", "17", flavours.distribution()))

    assert not [call for call in runner.calls if call[0] == "apt-get"]
    assert not (host / "etc/apt/sources.list.d/noust-pgdg.list").exists()
    assert not [call for call in runner.calls if "--dearmor" in call]


def test_a_repository_the_operator_already_configured_is_used_as_it_is(
    host: Path, runner: FakeRunner
) -> None:
    (host / "etc/apt/sources.list.d/pgdg.list").write_text(
        "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] "
        "https://apt.postgresql.org/pub/repos/apt noble-pgdg main\n"
    )
    repository = plan_install("postgresql", "17", NOBLE).repository
    assert repository is not None

    install_repository(repository, runner=runner, fs=get_fs())

    assert runner.calls == []
    assert not (host / "etc/apt/sources.list.d/noust-pgdg.list").exists()


def test_a_rehearsal_downloads_nothing_and_writes_nothing(host: Path) -> None:
    inner = FakeRunner()
    rehearsal = DryRunRunner(inner)
    set_runner(rehearsal)
    set_fs(DryRunFileSystem())
    try:
        inner.only_knows("apt-get")
        PostgresManager().install(plan_install("postgresql", "17", flavours.distribution()))
    finally:
        set_runner(None)
        set_fs(None)

    assert inner.calls == []
    assert [call[0] for call in rehearsal.skipped][:2] == ["curl", "apt-get"]
    assert not (host / "etc/apt/sources.list.d/noust-pgdg.list").exists()


def test_an_install_naming_nothing_does_what_3_2_did(host: Path, runner: FakeRunner) -> None:
    runner.only_knows("apt-get", "mysqldump")
    runner.script(["apt-get", "install", "-y", "mariadb-server"], exit_code=100, stderr="no")
    manager = MySQLManager()

    manager.install()

    installs = [call for call in runner.calls if call[:2] == ("apt-get", "install")]
    assert installs == [
        ("apt-get", "install", "-y", "mariadb-server"),
        ("apt-get", "install", "-y", "mysql-server"),
    ]


def test_mysql_chosen_installs_mysql_and_never_mariadb(host: Path, runner: FakeRunner) -> None:
    runner.only_knows("apt-get")

    MySQLManager().install(plan_install("mysql", None, NOBLE))

    installs = [call for call in runner.calls if call[:2] == ("apt-get", "install")]
    assert installs == [("apt-get", "install", "-y", "mysql-server")]
    assert ("systemctl", "enable", "mysql") in runner.calls


def test_valkey_chosen_runs_as_valkey(host: Path, runner: FakeRunner) -> None:
    runner.only_knows("apt-get")
    manager = RedisManager()

    manager.install(plan_install("valkey", None, NOBLE))

    assert ("apt-get", "install", "-y", "valkey-server") in runner.calls
    assert ("systemctl", "start", "valkey-server") in runner.calls
    assert manager.DISPLAY_NAME == "Valkey"


def test_mongodb_7_on_jammy_uses_its_own_key(
    host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    (host / "etc" / "os-release").write_text("ID=ubuntu\nVERSION_CODENAME=jammy\n")
    runner.only_knows("apt-get", "mongosh", "mongod")
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=colons(flavours.MONGODB_KEYS["7.0"].fingerprint))
    monkeypatch.setattr(MongoDBManager, "_post_install", lambda self: None)

    MongoDBManager().install(plan_install("mongodb", "7.0", flavours.distribution()))

    assert runner.calls[0][-1] == "https://www.mongodb.org/static/pgp/server-7.0.asc"
    source = (host / "etc/apt/sources.list.d/mongodb-org-7.0.list").read_text()
    assert "jammy/mongodb-org/7.0 multiverse" in source


def test_primary_fingerprints_ignore_subkeys() -> None:
    assert primary_fingerprints(colons("A" * 40, "B" * 40)) == ["A" * 40, "B" * 40]


def test_the_install_job_passes_the_choice_to_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.managers.database import service as service_module
    from noust.web import jobs

    seen: dict[str, object] = {}

    class FakeService:
        def manager(self, engine: str) -> SimpleNamespace:
            return SimpleNamespace(DISPLAY_NAME="MariaDB")

        def install_engine(self, engine: str, *, flavour=None, version=None):
            seen.update(engine=engine, flavour=flavour, version=version)
            return SimpleNamespace(to_dict=lambda: {"version": "11.4.8"})

    monkeypatch.setattr(service_module, "DatabaseService", FakeService)
    context = SimpleNamespace(set_metadata=lambda *a: None, update=lambda *a: None)
    monkeypatch.setattr(jobs, "_require_context", lambda job_context: context)

    result = jobs.database_engine_job("mysql", "install", flavour="mariadb", version="11.4")

    assert seen == {"engine": "mysql", "flavour": "mariadb", "version": "11.4"}
    assert result["installed"] == {"version": "11.4.8"}
