# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The registry of the server's software (``managers/server/components.py``, item 118).

It is data, and the tests hold it to what makes it one definition (rule 3): an id the ledger and the
API can carry, a package named once, a sentence for everything a person reads, the names of the
engines' repositories taken from the table that writes them, and the pieces Noust is built on in
the list of those that are never removed.
"""

from __future__ import annotations

import re

import pytest

from noust.core import dependencies
from noust.core.server_text import CATALOG
from noust.managers.database import flavours
from noust.managers.database.registry import DatabaseRegistry
from noust.managers.server import components
from noust.managers.server.components import COMPONENTS, Installer, Severity
from noust.managers.server.host import PackageFamily
from noust.validators.package import validate_package

FAMILIES = [PackageFamily.APT, PackageFamily.DNF, PackageFamily.ZYPPER]


class TestWhatTheRegistryIs:
    def test_every_id_is_what_the_ledger_and_the_api_carry(self) -> None:
        for item in COMPONENTS:
            assert re.fullmatch(r"[a-z][a-z0-9_]{0,39}", item.id), item.id

    def test_no_id_is_twice(self) -> None:
        ids = [item.id for item in COMPONENTS]

        assert len(ids) == len(set(ids))
        assert components.ids() == tuple(ids)

    def test_the_registry_is_the_allow_list_of_ids(self) -> None:
        assert components.component("nginx") is not None
        assert components.component("nginx; rm -rf /") is None
        assert components.component("") is None

    def test_the_pieces_noust_is_built_on_are_never_removed(self) -> None:
        never = {item.id for item in COMPONENTS if item.severity is Severity.NEVER}

        assert never == {"noust", "systemd", "sshd", "firewall", "git", "curl", "gnupg"}

    def test_a_never_says_why_and_nothing_else_does(self) -> None:
        for item in COMPONENTS:
            if item.severity is Severity.NEVER:
                assert item.never_reason in CATALOG, item.id
                assert item.dependents is None, item.id
            else:
                assert item.never_reason is None, item.id

    def test_what_is_removed_only_while_nothing_depends_on_it_can_say_who_does(self) -> None:
        for item in COMPONENTS:
            if item.severity is Severity.CRITICAL_WHILE:
                assert item.dependents is not None, item.id


class TestEveryWordAPersonReads:
    def test_every_component_has_a_sentence_for_what_it_is_for(self) -> None:
        for item in COMPONENTS:
            assert item.purpose == f"software.{item.id}.purpose"
            assert item.purpose in CATALOG, item.id

    def test_no_sentence_has_a_value_to_put_in_because_the_page_says_it_as_it_is(self) -> None:
        for item in COMPONENTS:
            assert "{" not in CATALOG[item.purpose], item.id
            if item.never_reason:
                assert "{" not in CATALOG[item.never_reason], item.id


class TestPackages:
    def test_a_package_is_a_name_never_a_pattern_or_an_option(self) -> None:
        for item in COMPONENTS:
            for family in FAMILIES:
                for name in item.candidates_on(family):
                    assert validate_package(name) == name, (item.id, name)

    def test_a_package_belongs_to_one_component(self) -> None:
        for family in FAMILIES:
            seen: dict[str, str] = {}
            for item in COMPONENTS:
                for name in item.candidates_on(family):
                    assert seen.setdefault(name, item.id) == item.id, (family, name)

    def test_who_owns_a_package_is_answered_from_the_same_lists(self) -> None:
        assert components.owner_of("nginx-core", PackageFamily.APT).id == "nginx"  # type: ignore[union-attr]
        assert components.owner_of("noust", PackageFamily.DNF).id == "noust"  # type: ignore[union-attr]
        assert components.owner_of("libreoffice", PackageFamily.APT) is None

    def test_git_curl_and_rclone_are_the_packages_the_installation_check_names(self) -> None:
        by_id = {item.id: item for item in COMPONENTS}
        for name, dependency in (
            ("git", dependencies.SYSTEM_DEPENDENCIES[0]),
            ("curl", dependencies.SYSTEM_DEPENDENCIES[1]),
            ("rclone", dependencies.RCLONE_DEPENDENCY),
        ):
            assert by_id[name].packages_on(PackageFamily.APT) == tuple(
                (dependency.install_apt or "").split()
            )

    def test_php_is_the_packages_of_the_php_dependency(self) -> None:
        php = components.component("php")

        assert php is not None
        assert php.packages_on(PackageFamily.APT) == tuple(
            (dependencies.PHP_FPM_DEPENDENCY.install_apt or "").split()
        )
        assert "php8.3-fpm" in php.candidates_on(PackageFamily.APT)
        # Every minor an exact name: a package manager is never given `php*-fpm`.
        assert not [n for n in php.candidates_on(PackageFamily.APT) if "*" in n]

    def test_nginx_removal_takes_the_package_that_carries_the_binary(self) -> None:
        nginx = components.component("nginx")

        assert nginx is not None
        assert {"nginx", "nginx-core"} <= set(nginx.candidates_on(PackageFamily.APT))

    def test_the_stream_module_is_not_a_package_on_opensuse(self) -> None:
        stream = components.component("nginx_stream_module")

        assert stream is not None
        assert stream.packages_on(PackageFamily.APT) == ("libnginx-mod-stream",)
        assert stream.packages_on(PackageFamily.DNF) == ("nginx-mod-stream",)
        assert not stream.installable_on(PackageFamily.ZYPPER)
        assert stream.installable_on(PackageFamily.APT)


class TestHowItIsInstalled:
    def test_a_system_with_no_package_manager_installs_nothing(self) -> None:
        for item in COMPONENTS:
            assert not item.installable_on(PackageFamily.NONE), item.id

    def test_docker_is_installed_where_noust_knows_how_and_the_waf_on_apt_only(self) -> None:
        docker, waf = components.component("docker"), components.component("waf")

        assert docker is not None and waf is not None
        assert [f for f in FAMILIES if docker.installable_on(f)] == [
            PackageFamily.APT,
            PackageFamily.ZYPPER,
        ]
        assert [f for f in FAMILIES if waf.installable_on(f)] == [PackageFamily.APT]

    def test_a_component_installed_by_the_package_manager_has_a_package_to_name(self) -> None:
        for item in COMPONENTS:
            if item.installer is Installer.PACKAGE:
                assert any(item.packages_on(family) for family in FAMILIES), item.id

    def test_an_engine_names_its_manager_and_that_manager_exists(self) -> None:
        engines = {
            item.engine for item in COMPONENTS if item.installer is Installer.DATABASE_ENGINE
        }

        assert engines == {"postgresql", "mysql", "redis", "mongodb"}
        assert engines <= set(DatabaseRegistry.list_engines())
        for item in COMPONENTS:
            assert (item.engine is not None) == (item.installer is Installer.DATABASE_ENGINE)


class TestTheMarksNoustLeaves:
    def test_an_absolute_path_each(self) -> None:
        for item in COMPONENTS:
            for mark in item.marks:
                assert mark.startswith("/") and "\n" not in mark and ".." not in mark, mark

    def test_the_engines_apt_marks_are_the_files_the_flavours_table_writes(self) -> None:
        postgresql = components.component("postgresql")
        mysql = components.component("mysql")

        assert postgresql is not None and mysql is not None
        for flavour, item in (("postgresql", postgresql), ("mariadb", mysql), ("mysql", mysql)):
            for path in flavours.APT_REPOSITORY_FILES[flavour]:
                assert str(path) in item.marks, (flavour, path)

    def test_cloudflared_marks_what_its_installer_writes_on_every_family(self) -> None:
        cloudflared = components.component("cloudflared")

        assert cloudflared is not None
        assert "/etc/apt/sources.list.d/noust-cloudflared.list" in cloudflared.marks
        assert "/usr/share/keyrings/noust-cloudflared.gpg" in cloudflared.marks
        assert "/etc/yum.repos.d/noust-cloudflared.repo" in cloudflared.marks
        assert "/etc/zypp/repos.d/noust-cloudflared.repo" in cloudflared.marks
        assert "/etc/pki/rpm-gpg/RPM-GPG-KEY-noust-cloudflared" in cloudflared.marks

    def test_every_mark_is_a_file_noust_names_in_a_repository_directory(self) -> None:
        for item in COMPONENTS:
            for mark in item.marks:
                name = mark.rsplit("/", 1)[1]
                assert name.startswith(("noust-", "RPM-GPG-KEY-noust-")), mark


@pytest.mark.parametrize("family", FAMILIES)
def test_what_a_removal_looks_for_includes_what_an_install_brings(family: PackageFamily) -> None:
    for item in COMPONENTS:
        assert set(item.packages_on(family)) <= set(item.candidates_on(family))
