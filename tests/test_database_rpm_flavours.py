# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Installing a database engine on the RPM families (checklist C-11).

What is pinned here, with the fake runner and a temporary system root; the same installs run
for real, in Rocky Linux and openSUSE Leap containers, in ``tests/integration/engines_run.py``:

- only the releases whose repositories were read are planned, and everything else is refused
  before a package manager is touched, with the way that does work;
- a version comes from AppStream's module stream, PGDG, MariaDB's or MongoDB's repository, and
  an upstream repository is trusted only through its pinned key;
- the packages, the module steps, the data directory command and the unit are the ones the
  real packages need, in order;
- an uninstall removes the packages that are exactly the flavour's, and the repository Noust
  added, and nothing a pattern could widen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.fs import get_fs
from noust.core.package_family import PackageFamily
from noust.core.runner import FakeRunner
from noust.managers.database import flavours
from noust.managers.database.engine_setup import install_catalog
from noust.managers.database.flavours import (
    MARIADB_KEY,
    MONGODB_KEYS,
    catalog,
    offered_versions,
    plan_install,
)
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager
from noust.managers.database.rpm_flavours import (
    PGDG_RPM_KEY,
    RpmRepository,
    default_rpm_plan,
    install_rpm_repository,
    installed_packages,
    key_file_of,
    repository_files,
    rpm_manager,
    rpm_release,
)
from noust.managers.server.host import HostPaths, OsRelease, Platform

from .test_database_flavours import colons


def release(identity: str, version: str, pretty: str, *like: str) -> OsRelease:
    """
    Build an os-release.

    Args:
        identity: ``ID``.
        version: ``VERSION_ID``.
        pretty: ``PRETTY_NAME``.
        *like: ``ID_LIKE`` words.

    Returns:
        The identity.
    """
    return OsRelease(id=identity, id_like=like, version_id=version, pretty_name=pretty)


ROCKY9 = release("rocky", "9.8", "Rocky Linux 9.8 (Blue Onyx)", "rhel", "centos", "fedora")
ALMA9 = release("almalinux", "9.6", "AlmaLinux 9.6", "rhel", "centos", "fedora")
RHEL9 = release("rhel", "9.4", "Red Hat Enterprise Linux 9.4", "fedora")
ROCKY8 = release("rocky", "8.10", "Rocky Linux 8.10", "rhel", "centos", "fedora")
RHEL10 = release("rhel", "10.0", "Red Hat Enterprise Linux 10.0", "fedora")
FEDORA = release("fedora", "42", "Fedora Linux 42")
LEAP16 = release("opensuse-leap", "16.0", "openSUSE Leap 16.0", "suse", "opensuse")
LEAP156 = release("opensuse-leap", "15.6", "openSUSE Leap 15.6", "suse", "opensuse")
TUMBLEWEED = release("opensuse-tumbleweed", "20261001", "openSUSE Tumbleweed", "suse", "opensuse")
NOBLE = release("ubuntu", "24.04", "Ubuntu 24.04 LTS", "debian")


@pytest.fixture
def rpm_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    A system root of Rocky Linux 9 with empty repository directories.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper.

    Returns:
        The root.
    """
    for directory in ("etc/yum.repos.d", "etc/zypp/repos.d", "etc/pki/rpm-gpg"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "etc" / "os-release").write_text(
        'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.8"\n'
        'PRETTY_NAME="Rocky Linux 9.8 (Blue Onyx)"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path


def on_platform(
    monkeypatch: pytest.MonkeyPatch, family: PackageFamily, os_release: OsRelease
) -> None:
    """
    Make the database managers see a machine of one package manager.

    Args:
        monkeypatch: Patching helper.
        family: Its package manager.
        os_release: Its distribution.
    """
    platform = Platform(family=family, program=family.value, os=os_release)
    monkeypatch.setattr("noust.managers.database.base.detect_platform", lambda *_a, **_k: platform)


# ------------------------------------------------------------ which release


@pytest.mark.parametrize(
    ("os_release", "manager"),
    [
        (ROCKY9, "dnf"),
        (ALMA9, "dnf"),
        (RHEL9, "dnf"),
        (FEDORA, "dnf"),
        (LEAP16, "zypper"),
        (LEAP156, "zypper"),
        (TUMBLEWEED, "zypper"),
        (NOBLE, None),
        (release("debian", "12", "Debian 12"), None),
        (OsRelease(), None),
    ],
)
def test_the_package_manager_follows_the_family(os_release: OsRelease, manager: str | None) -> None:
    assert rpm_manager(os_release) == manager


@pytest.mark.parametrize(
    ("os_release", "key"),
    [
        (ROCKY9, "el9"),
        (ALMA9, "el9"),
        (RHEL9, "el9"),
        (LEAP16, "leap-16.0"),
        (LEAP156, "leap-15.6"),
    ],
)
def test_the_releases_the_tables_were_read_from_are_known(os_release: OsRelease, key: str) -> None:
    found = rpm_release(os_release)

    assert found is not None and found.key == key


@pytest.mark.parametrize("os_release", [ROCKY8, RHEL10, FEDORA, TUMBLEWEED, NOBLE])
def test_a_release_nobody_read_the_repositories_of_is_not_planned_by_the_rpm_tables(
    os_release: OsRelease,
) -> None:
    assert rpm_release(os_release) is None


@pytest.mark.parametrize("os_release", [ROCKY8, RHEL10, FEDORA, TUMBLEWEED])
def test_the_refusal_of_a_release_names_the_instance_that_works(os_release: OsRelease) -> None:
    with pytest.raises(ValidationError) as caught:
        plan_install("postgresql", None, os_release)

    assert os_release.pretty_name in caught.value.message
    assert "noust db instance create NAME --engine postgresql" in caught.value.details
    assert "noust db status postgresql" in caught.value.details


# --------------------------------------------------------------- the plans


def test_the_plain_package_of_el9_resets_the_module_and_creates_the_cluster() -> None:
    plan = plan_install("postgresql", None, ROCKY9)

    assert plan.family == "dnf" and plan.source == "distribution"
    assert plan.version == "13"
    assert plan.package_sets == (("postgresql-server", "postgresql-contrib"),)
    # Reset, not enabled: a stream an earlier install enabled outlives its packages.
    assert plan.module == ("postgresql", None)
    assert plan.initialize == ("postgresql-setup", "--initdb")
    assert plan.unit == "postgresql" and plan.rpm_repository is None


def test_a_module_stream_of_appstream_is_enabled_before_the_packages() -> None:
    plan = plan_install("postgresql", "16", ROCKY9)

    assert plan.module == ("postgresql", "16") and plan.source == "distribution"
    assert plan.rpm_repository is None


def test_a_redis_stream_is_named_by_its_major() -> None:
    assert plan_install("redis", "7.2", ROCKY9).module == ("redis", "7")
    assert plan_install("redis", None, ROCKY9).version == "6.2"


def test_valkey_has_no_module_and_no_data_directory_step() -> None:
    plan = plan_install("valkey", None, ROCKY9)

    assert plan.module is None and plan.initialize == ()
    assert plan.package_sets == (("valkey",),) and plan.unit == "valkey"


def test_a_postgresql_major_appstream_lacks_comes_from_pgdg_with_the_pinned_key() -> None:
    plan = plan_install("postgresql", "17", ROCKY9)

    assert plan.source == "upstream" and plan.family == "dnf"
    assert plan.package_sets == (("postgresql17-server", "postgresql17-contrib"),)
    assert plan.rpm_repository is not None
    assert plan.rpm_repository.key == PGDG_RPM_KEY
    assert plan.rpm_repository.baseurl == (
        "https://download.postgresql.org/pub/repos/yum/17/redhat/rhel-9-$basearch"
    )
    # AppStream's module and PGDG's packages would both claim the name "postgresql".
    assert plan.disabled_modules == ("postgresql",)
    assert plan.unit == "postgresql-17"
    assert plan.initialize == ("/usr/pgsql-17/bin/postgresql-17-setup", "initdb")


def test_mariadb_eleven_four_comes_from_mariadbs_own_repository() -> None:
    plan = plan_install("mariadb", "11.4", ROCKY9)

    assert plan.rpm_repository is not None
    assert plan.rpm_repository.key == MARIADB_KEY
    assert plan.rpm_repository.baseurl == "https://rpm.mariadb.org/11.4/rhel/9/$basearch"
    assert plan.package_sets == (("MariaDB-server", "MariaDB-client"),)
    assert plan.disabled_modules == ("mariadb",)


@pytest.mark.parametrize("series", sorted(MONGODB_KEYS))
def test_mongodb_comes_from_its_repository_with_the_key_of_its_series(series: str) -> None:
    plan = plan_install("mongodb", series, ROCKY9)

    assert plan.rpm_repository is not None
    assert plan.rpm_repository.key == MONGODB_KEYS[series]
    assert plan.rpm_repository.baseurl.endswith(f"/mongodb-org/{series}/$basearch/")
    assert plan.package_sets == (("mongodb-org",),) and plan.unit == "mongod"


@pytest.mark.parametrize(
    ("flavour", "version", "offered"),
    [
        ("postgresql", "12", "13, 15, 16"),
        ("mariadb", "10.6", "10.5, 10.11, 11.8, 11.4"),
        ("mysql", "5.7", "8.0, 8.4"),
        ("mongodb", "4.4", "7.0, 8.0"),
    ],
)
def test_a_version_the_release_cannot_be_given_is_refused_with_what_it_can(
    flavour: str, version: str, offered: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        plan_install(flavour, version, ROCKY9)

    assert caught.value.field == "version"
    assert "Rocky Linux 9.8" in caught.value.message
    assert offered.split(", ")[0] in caught.value.details
    assert "noust db instance create" in caught.value.details


def test_leap_names_the_major_it_installs_even_when_none_is_asked_for() -> None:
    newest = plan_install("postgresql", None, LEAP16)
    older = plan_install("postgresql", "16", LEAP16)

    assert newest.family == "zypper" and newest.version == "18"
    # Never the meta packages: they ask for any "postgresql-server-implementation", and on
    # Leap 15.6 zypper installs 16 for the one that says 18.
    assert newest.package_sets == (("postgresql18-server", "postgresql18-contrib"),)
    assert older.package_sets == (("postgresql16-server", "postgresql16-contrib"),)
    assert older.module is None and older.rpm_repository is None and older.unit == "postgresql"
    assert plan_install("postgresql", None, LEAP156).package_sets == newest.package_sets


def test_leap_gives_the_mariadb_it_ships_and_no_other() -> None:
    assert plan_install("mariadb", None, LEAP16).version == "11.8"
    assert plan_install("mariadb", None, LEAP156).version == "10.11"
    with pytest.raises(ValidationError, match="is not available"):
        plan_install("mariadb", "11.4", LEAP16)


@pytest.mark.parametrize("flavour", ["mongodb", "mysql", "redis", "valkey"])
def test_leap_refuses_what_it_has_no_repository_for_before_anything_is_touched(
    flavour: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        plan_install(flavour, None, LEAP16)

    assert "openSUSE Leap 16.0" in caught.value.message
    assert f"noust db instance create NAME --engine {flavour}" in caught.value.details


def test_the_install_that_names_nothing_tries_mariadb_then_redis_on_the_rpm_families() -> None:
    assert default_rpm_plan("mysql", ROCKY9).flavour == "mariadb"
    assert default_rpm_plan("redis", ROCKY9).flavour == "redis"
    assert default_rpm_plan("postgresql", LEAP16).family == "zypper"
    # Leap has no Redis: the refusal of the first flavour is the one that explains.
    with pytest.raises(ValidationError, match="Redis"):
        default_rpm_plan("redis", LEAP16)


def test_the_plan_reports_its_package_manager_and_module() -> None:
    described = plan_install("redis", "7.2", ROCKY9).to_dict()

    assert described["package_manager"] == "dnf"
    assert described["module"] == "redis:7"
    assert described["repository"] is None
    assert plan_install("postgresql", "17", ROCKY9).to_dict()["repository"] == (
        "download.postgresql.org"
    )


# ---------------------------------------------------------------- the catalog


def test_el9_offers_each_version_with_where_it_comes_from() -> None:
    offered = {v.version: v for v in offered_versions("postgresql", ROCKY9)}

    assert set(offered) == {"13", "14", "15", "16", "17", "18"}
    assert offered["13"].default and offered["13"].source == "distribution"
    assert offered["17"].source == "upstream"
    assert [v.version for v in offered_versions("mongodb", ROCKY9)] == ["7.0", "8.0"]
    assert offered_versions("mongodb", LEAP16) == ()


def test_the_catalog_of_a_dnf_machine_installs_what_the_release_can_be_given() -> None:
    entries = {entry.flavour: entry for entry in catalog(ROCKY9, {}, apt=True)}

    assert entries["postgresql"].installable and entries["valkey"].installable
    assert entries["mongodb"].installable


def test_the_service_catalog_says_the_package_manager_and_knows_a_covered_release(
    rpm_host: Path, runner: FakeRunner
) -> None:
    runner.only_knows("dnf", "rpm", "psql")

    built = install_catalog([PostgresManager()])

    assert built["package_manager"] == "dnf" and built["apt"] is True
    assert built["distribution"]["known"] is True
    assert built["distribution"]["id"] == "rocky"


def test_without_any_package_manager_nothing_is_installable(
    rpm_host: Path, runner: FakeRunner
) -> None:
    runner.only_knows("psql")

    built = install_catalog([PostgresManager()])

    assert built["package_manager"] == "none" and built["apt"] is False


# ------------------------------------------------------------ the repository


def test_a_repository_definition_trusts_the_verified_key_alone() -> None:
    plan = plan_install("postgresql", "17", ROCKY9)
    assert plan.rpm_repository is not None

    text = plan.rpm_repository.render()

    assert text.startswith("# Generated by Noust.")
    assert "[noust-pgdg17]" in text
    assert "gpgcheck=1" in text
    assert "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-noust-pgdg17" in text
    assert "skip_if_unavailable=False" in text and "type=rpm-md" not in text


def test_mariadbs_repository_is_not_hidden_by_the_appstream_module() -> None:
    """dnf compares names without regard to case: "MariaDB-server" is filtered out by "mariadb"."""
    plan = plan_install("mariadb", "11.4", ROCKY9)
    assert plan.rpm_repository is not None

    assert "module_hotfixes=1" in plan.rpm_repository.render()
    pgdg = plan_install("postgresql", "17", ROCKY9).rpm_repository
    assert pgdg is not None and "module_hotfixes" not in pgdg.render()


def test_zypper_reads_its_metadata_as_rpm_md() -> None:
    repository = RpmRepository(
        name="example",
        alias="example1",
        key=PGDG_RPM_KEY,
        baseurl="https://example.test/",
        manager="zypper",
        directory=Path("/etc/zypp/repos.d"),
    )

    assert "type=rpm-md" in repository.render()
    assert repository.sources == Path("/etc/zypp/repos.d/noust-example1.repo")


@pytest.mark.parametrize("alias", ["", "A", "a b", "a/b", "../x", "a\nb", "x" * 60, "1abc"])
def test_an_alias_that_cannot_name_a_file_and_a_section_is_refused(alias: str) -> None:
    with pytest.raises(ValueError, match="cannot be a repository alias"):
        RpmRepository(
            name="x",
            alias=alias,
            key=PGDG_RPM_KEY,
            baseurl="https://example.test/",
            manager="dnf",
            directory=Path("/etc/yum.repos.d"),
        )


def test_a_verified_key_is_written_before_the_definition_names_it(
    rpm_host: Path, runner: FakeRunner
) -> None:
    runner.script(["curl"], stdout="-----BEGIN PGP PUBLIC KEY BLOCK-----\n")
    runner.script(["gpg"], stdout=colons(PGDG_RPM_KEY.fingerprint))
    repository = plan_install("postgresql", "17", flavours.distribution()).rpm_repository
    assert repository is not None

    created = install_rpm_repository(repository, runner=runner, fs=get_fs())

    export = next(call for call in runner.calls if "--export" in call)
    assert "--armor" in export  # a gpgkey= is read armoured, an apt keyring is not
    assert export[-1] == PGDG_RPM_KEY.fingerprint
    assert str(rpm_host / "etc/pki/rpm-gpg/RPM-GPG-KEY-noust-pgdg17") in export
    written = (rpm_host / "etc/yum.repos.d/noust-pgdg17.repo").read_text()
    assert "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-noust-pgdg17" in written
    assert rpm_host / "etc/yum.repos.d/noust-pgdg17.repo" in created
    assert not any(call[0] == "rpm" for call in runner.calls), "dnf reads gpgkey= itself"


def test_zypper_is_told_to_trust_the_verified_key(rpm_host: Path, runner: FakeRunner) -> None:
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=colons(PGDG_RPM_KEY.fingerprint))
    repository = RpmRepository(
        name="example",
        alias="example1",
        key=PGDG_RPM_KEY,
        baseurl="https://example.test/",
        manager="zypper",
        directory=Path("/etc/zypp/repos.d"),
    )

    install_rpm_repository(repository, runner=runner, fs=get_fs())

    imports = [call for call in runner.calls if call[:2] == ("rpm", "--import")]
    assert imports == [
        ("rpm", "--import", str(rpm_host / "etc/pki/rpm-gpg/RPM-GPG-KEY-noust-example1"))
    ]
    assert (rpm_host / "etc/zypp/repos.d/noust-example1.repo").exists()


@pytest.mark.parametrize(
    "served",
    [colons("0" * 40), colons(PGDG_RPM_KEY.fingerprint, "1" * 40), ""],
    ids=["another-key", "a-bundle-with-a-second-key", "nothing"],
)
def test_a_key_that_is_not_exactly_the_pinned_one_adds_nothing_to_dnf(
    rpm_host: Path, runner: FakeRunner, served: str
) -> None:
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=served)
    repository = plan_install("postgresql", "17", flavours.distribution()).rpm_repository
    assert repository is not None

    with pytest.raises(DatabaseEngineError, match="not the one Noust trusts") as caught:
        install_rpm_repository(repository, runner=runner, fs=get_fs())

    assert "Nothing was added to dnf" in str(caught.value.details)
    assert not (rpm_host / "etc/yum.repos.d/noust-pgdg17.repo").exists()
    assert not [call for call in runner.calls if "--export" in call]


def test_a_failed_zypper_import_takes_the_key_back_out(rpm_host: Path, runner: FakeRunner) -> None:
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=colons(PGDG_RPM_KEY.fingerprint))
    runner.script(["rpm", "--import"], exit_code=1, stderr="import failed")
    repository = RpmRepository(
        name="example",
        alias="example1",
        key=PGDG_RPM_KEY,
        baseurl="https://example.test/",
        manager="zypper",
        directory=Path("/etc/zypp/repos.d"),
    )
    # The fake gpg writes nothing, so the key file is made as gpg would have.
    key_file = rpm_host / "etc/pki/rpm-gpg/RPM-GPG-KEY-noust-example1"
    key_file.write_text("key")

    with pytest.raises(DatabaseEngineError, match="Failed to trust the signing key"):
        install_rpm_repository(repository, runner=runner, fs=get_fs())

    assert not (rpm_host / "etc/zypp/repos.d/noust-example1.repo").exists()


# ----------------------------------------------------- the install, in order


def run_install(
    manager: PostgresManager | MySQLManager | RedisManager | MongoDBManager,
    flavour: str,
    version: str | None,
    os_release: OsRelease,
) -> None:
    """
    Install a plan the way the CLI does.

    Args:
        manager: The engine's manager.
        flavour: The flavour.
        version: The version asked for.
        os_release: The distribution.
    """
    manager.install(plan_install(flavour, version, os_release))


def test_a_dnf_install_runs_the_modules_the_refresh_the_packages_and_the_cluster(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm")
    runner.script(["psql", "--version"], stdout="psql (PostgreSQL) 16.9\n")
    manager = PostgresManager()

    run_install(manager, "postgresql", "16", ROCKY9)

    steps = [call for call in runner.calls if call[0] in {"dnf", "postgresql-setup", "systemctl"}]
    assert steps == [
        ("dnf", "-y", "module", "reset", "postgresql"),
        ("dnf", "-y", "module", "enable", "postgresql:16"),
        ("dnf", "-q", "-y", "makecache", "--refresh"),
        ("dnf", "-y", "install", "postgresql-server", "postgresql-contrib"),
        ("postgresql-setup", "--initdb"),
        ("systemctl", "enable", "postgresql"),
        ("systemctl", "start", "postgresql"),
    ]
    assert not [call for call in runner.calls if call[0] == "apt-get"]


def test_pgdg_on_el9_disables_the_module_and_runs_the_majors_own_setup_and_unit(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm")
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=colons(PGDG_RPM_KEY.fingerprint))
    runner.script(["psql", "--version"], stdout="psql (PostgreSQL) 17.5\n")
    manager = PostgresManager()

    run_install(manager, "postgresql", "17", ROCKY9)

    steps = [
        call
        for call in runner.calls
        if call[0] in {"dnf", "systemctl"} or "postgresql-17-setup" in call[0]
    ]
    assert steps == [
        ("dnf", "-y", "module", "disable", "postgresql"),
        ("dnf", "-q", "-y", "makecache", "--refresh"),
        ("dnf", "-y", "install", "postgresql17-server", "postgresql17-contrib"),
        ("/usr/pgsql-17/bin/postgresql-17-setup", "initdb"),
        ("systemctl", "enable", "postgresql-17"),
        ("systemctl", "start", "postgresql-17"),
    ]
    assert (rpm_host / "etc/yum.repos.d/noust-pgdg17.repo").exists()
    assert manager.service_unit() == "postgresql-17"


def test_a_data_directory_that_exists_already_is_not_a_failure(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm")
    runner.script(["psql", "--version"], stdout="psql (PostgreSQL) 13.20\n")
    runner.script(["postgresql-setup"], exit_code=1, stderr="Data directory is not empty!")

    run_install(PostgresManager(), "postgresql", None, ROCKY9)

    assert ("systemctl", "start", "postgresql") in runner.calls


def test_any_other_failure_to_create_the_data_directory_stops_the_install(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm")
    runner.script(["postgresql-setup"], exit_code=1, stderr="initdb: no space left")

    with pytest.raises(DatabaseEngineError, match="data directory") as caught:
        run_install(PostgresManager(), "postgresql", None, ROCKY9)

    assert "no space left" in str(caught.value.details)
    assert ("systemctl", "start", "postgresql") not in runner.calls


def test_a_repository_dnf_cannot_read_is_taken_back_out(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm")
    runner.script(["curl"], stdout="key")
    runner.script(["gpg"], stdout=colons(PGDG_RPM_KEY.fingerprint))
    runner.script(
        ["dnf", "-q", "-y", "makecache"], exit_code=1, stderr="Failed to download metadata"
    )
    (rpm_host / "etc/pki/rpm-gpg/RPM-GPG-KEY-noust-pgdg17").write_text("key")

    with pytest.raises(DatabaseEngineError, match="update the package metadata") as caught:
        run_install(PostgresManager(), "postgresql", "17", ROCKY9)

    assert "Failed to download metadata" in str(caught.value.details)
    assert "Noust removed what it had just added" in str(caught.value.details)
    assert not (rpm_host / "etc/yum.repos.d/noust-pgdg17.repo").exists()
    assert not [call for call in runner.calls if call[:3] == ("dnf", "-y", "install")]


def test_a_zypper_install_has_no_module_step_and_runs_with_zypper(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.ZYPPER, LEAP16)
    runner.only_knows("zypper", "rpm")
    runner.script(["psql", "--version"], stdout="psql (PostgreSQL) 16.9\n")

    run_install(PostgresManager(), "postgresql", "16", LEAP16)

    programs = [call[0] for call in runner.calls]
    assert "apt-get" not in programs
    assert not [call for call in runner.calls if call[:2] == ("dnf", "-y")]
    installs = [call for call in runner.calls if call[0] == "zypper" and "install" in call]
    assert len(installs) == 1
    assert installs[0][-2:] == ("postgresql16-server", "postgresql16-contrib")
    assert ("systemctl", "start", "postgresql") in runner.calls


def test_the_package_manager_the_plan_needs_is_checked_first(
    rpm_host: Path, runner: FakeRunner
) -> None:
    runner.only_knows("psql")  # no apt-get, dnf or zypper

    with pytest.raises(DatabaseEngineError, match="which this system does not have") as caught:
        PostgresManager().install(plan_install("postgresql", None, ROCKY9))

    assert "noust db instance create NAME --engine postgresql" in str(caught.value.details)
    assert runner.calls == []


def test_a_version_dnf_picked_over_the_plans_is_an_error_naming_dnf(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm")
    runner.script(["psql", "--version"], stdout="psql (PostgreSQL) 13.20\n")

    with pytest.raises(DatabaseEngineError, match=r"dnf installed 13\.20") as caught:
        run_install(PostgresManager(), "postgresql", "16", ROCKY9)

    assert "dnf info postgresql-server" in str(caught.value.details)
    assert "dnf remove postgresql-server" in str(caught.value.details)


def test_the_engine_that_names_nothing_gets_the_first_flavour_the_release_has(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm", "mysqldump")
    runner.script(["mysql", "--version"], stdout="mysql  Ver 15.1 Distrib 10.5.27-MariaDB\n")

    MySQLManager().install()

    installs = [call for call in runner.calls if call[:3] == ("dnf", "-y", "install")]
    assert installs == [("dnf", "-y", "install", "mariadb-server")]


def test_mysql_is_driven_by_the_unit_its_package_brought(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    units = rpm_host / "usr/lib/systemd/system"
    units.mkdir(parents=True)
    (units / "mysqld.service").write_text("")
    runner.only_knows("mysql", "mysqld")

    assert MySQLManager().service_unit() == "mysqld"


def test_mysql_keeps_its_unit_on_debian(rpm_host: Path, runner: FakeRunner) -> None:
    units = rpm_host / "usr/lib/systemd/system"
    units.mkdir(parents=True)
    (units / "mysql.service").write_text("")
    (units / "mysqld.service").write_text("")
    runner.only_knows("mysql", "mysqld")

    assert MySQLManager().service_unit() == "mysql"


def test_postgresql_is_driven_by_the_newest_versioned_unit_when_there_is_no_plain_one(
    rpm_host: Path, runner: FakeRunner
) -> None:
    units = rpm_host / "usr/lib/systemd/system"
    units.mkdir(parents=True)
    for name in ("postgresql-9.service", "postgresql-17.service", "postgresql-16.service"):
        (units / name).write_text("")

    assert PostgresManager().service_unit() == "postgresql-17"


# ----------------------------------------------------------------- uninstall


@pytest.mark.parametrize(
    ("flavour", "listing", "expected"),
    [
        (
            "postgresql",
            "postgresql\npostgresql-server\npostgresql17-server\npostgresql17-libs\nbash\n"
            "postgresql-private-libs\nlibpq\npostgresql-jdbc\n",
            [
                "postgresql",
                "postgresql-private-libs",
                "postgresql-server",
                "postgresql17-libs",
                "postgresql17-server",
            ],
        ),
        (
            "mariadb",
            "mariadb-server\nmariadb-backup\nMariaDB-server\nlibmariadb\n",
            ["MariaDB-server", "mariadb-server"],
        ),
        ("redis", "redis\nredis-doc\nredispatcher\n", ["redis"]),
        (
            "mongodb",
            "mongodb-org\nmongodb-org-server\nmongodb-mongosh\nmongodb-org-tools\n",
            ["mongodb-org", "mongodb-org-server"],
        ),
        ("unknown", "postgresql\n", []),
    ],
)
def test_only_the_packages_that_are_exactly_the_flavours_are_removed(
    flavour: str, listing: str, expected: list[str]
) -> None:
    assert installed_packages(flavour, listing) == expected


def test_a_dnf_uninstall_removes_exactly_the_listed_packages_and_the_repository_noust_added(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm", "psql")
    runner.script(
        ["rpm", "-qa"],
        stdout="postgresql17-server\npostgresql17-contrib\nbash\npostgresql17-libs\n",
    )
    runner.script(["psql", "--version"], stdout="psql (PostgreSQL) 17.5\n")
    repo = rpm_host / "etc/yum.repos.d/noust-pgdg17.repo"
    repo.write_text("# Generated by Noust.\n[noust-pgdg17]\n")
    key = rpm_host / "etc/pki/rpm-gpg/RPM-GPG-KEY-noust-pgdg17"
    key.write_text("key")
    (rpm_host / "etc/yum.repos.d/operators.repo").write_text("[operator]\n")

    PostgresManager().uninstall()

    assert (
        "dnf",
        "-y",
        "remove",
        "postgresql17-contrib",
        "postgresql17-libs",
        "postgresql17-server",
    ) in runner.calls
    assert not any(call[0] == "apt-get" for call in runner.calls)
    assert not repo.exists() and not key.exists()
    assert (rpm_host / "etc/yum.repos.d/operators.repo").exists()


def test_an_uninstall_with_no_package_of_the_flavour_removes_nothing(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.DNF, ROCKY9)
    runner.only_knows("dnf", "rpm")
    runner.script(["rpm", "-qa"], stdout="bash\ncoreutils\n")

    with pytest.raises(DatabaseEngineError, match="No package of PostgreSQL is installed"):
        PostgresManager().uninstall()

    assert not [call for call in runner.calls if call[:2] == ("dnf", "-y")]


def test_a_zypper_uninstall_uses_zypper(
    rpm_host: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, PackageFamily.ZYPPER, LEAP16)
    runner.only_knows("zypper", "rpm")
    runner.script(["rpm", "-qa"], stdout="postgresql16-server\npostgresql16-contrib\n")

    PostgresManager().uninstall()

    assert (
        "zypper",
        "--non-interactive",
        "remove",
        "postgresql16-contrib",
        "postgresql16-server",
    ) in runner.calls


def test_the_repository_files_of_a_flavour_are_the_ones_noust_wrote(rpm_host: Path) -> None:
    mine = rpm_host / "etc/yum.repos.d/noust-pgdg17.repo"
    mine.write_text("# Generated by Noust.\n")
    (rpm_host / "etc/yum.repos.d/noust-pgdg16.repo").write_text("[hand-written-lookalike]\n")
    (rpm_host / "etc/yum.repos.d/noust-mariadb114.repo").write_text("# Generated by Noust.\n")
    zypper = rpm_host / "etc/zypp/repos.d/noust-pgdg18.repo"
    zypper.write_text("# Generated by Noust.\n")

    assert repository_files("postgresql", host=HostPaths(rpm_host)) == [mine, zypper]
    assert repository_files("redis", host=HostPaths(rpm_host)) == []
    assert key_file_of(mine) == Path("/etc/pki/rpm-gpg/RPM-GPG-KEY-noust-pgdg17")


def test_a_purge_removes_the_directories_of_the_rpm_packages_too() -> None:
    assert "/var/lib/pgsql" in PostgresManager.PURGE_PATHS
    assert "/var/lib/mongo" in MongoDBManager.PURGE_PATHS
    assert (
        "/var/lib/valkey" in RedisManager.PURGE_PATHS and "/etc/valkey" in RedisManager.PURGE_PATHS
    )
