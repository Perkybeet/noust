# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the account applications, their builds and their migrations run as.

A node had ``service_user: www-data`` and ``service_group: ''`` in
``/etc/noust/config.yaml``. systemd reads ``Group=`` empty as the user's primary
group, so its applications ran fine; ``noust app sandbox test`` crashed with a
raw ``ValueError`` traceback, because the empty string reached
:class:`~noust.core.runner.SandboxSpec` as a group name.

Pinned here:

- an empty ``service_user`` is the default, an empty ``service_group`` is the
  user's primary group, resolved by name, for every consumer at once;
- a missing ``service_group`` keeps the documented default;
- a group that does not exist, or a name that is not an account, is an
  actionable :class:`~noust.core.exceptions.ConfigError` naming the setting,
  never a traceback.
"""

from __future__ import annotations

import grp
import pwd
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from noust.core import config as config_module
from noust.core.config import Config, resolve_service_group, resolve_service_user
from noust.core.exceptions import ConfigError, NoustError
from noust.core.runner import FakeRunner, SandboxSpec
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.sandbox import BuildPhase, SandboxState
from noust.deployers.nodejs import NodeJSDeployer

DOMAIN = "acct.example.com"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point the configuration singleton at a sandboxed file that does not exist yet.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The configuration file path.
    """
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def accounts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Replace the account database with a known one: ``www-data`` (primary group
    ``www-data``) and ``deploy`` (primary group ``staff``).

    Returns:
        The users and groups, by name.
    """
    users = {
        "www-data": SimpleNamespace(pw_name="www-data", pw_uid=33, pw_gid=33),
        "deploy": SimpleNamespace(pw_name="deploy", pw_uid=1000, pw_gid=50),
    }
    groups = {
        "www-data": SimpleNamespace(gr_name="www-data", gr_gid=33),
        "staff": SimpleNamespace(gr_name="staff", gr_gid=50),
    }

    def getpwnam(name: str) -> Any:
        return users[name]

    def getgrnam(name: str) -> Any:
        return groups[name]

    def getgrgid(gid: int) -> Any:
        for entry in groups.values():
            if entry.gr_gid == gid:
                return entry
        raise KeyError(gid)

    monkeypatch.setattr(pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(grp, "getgrnam", getgrnam)
    monkeypatch.setattr(grp, "getgrgid", getgrgid)
    return {"users": users, "groups": groups}


def write_config(path: Path, tree: dict[str, Any]) -> None:
    """
    Write a configuration file the way an operator's server has it.

    Args:
        path: Where to write.
        tree: The YAML content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(tree))
    Config.reset_instance()


# ---------------------------------------------------------------------------
# The configuration: one interpretation of the two settings
# ---------------------------------------------------------------------------


class TestResolution:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_an_empty_user_is_the_default(self, value: Any) -> None:
        assert resolve_service_user(value) == "www-data"

    @pytest.mark.parametrize("value", [None, "", "  "])
    def test_an_empty_group_is_the_users_primary_group(
        self, value: Any, accounts: dict[str, Any]
    ) -> None:
        assert resolve_service_group(value, "www-data") == "www-data"
        assert resolve_service_group(value, "deploy") == "staff"

    def test_an_empty_group_for_an_unknown_user_is_named_after_the_user(
        self, accounts: dict[str, Any]
    ) -> None:
        """What useradd --user-group names it; nothing here can run as it anyway."""
        assert resolve_service_group("", "ghost") == "ghost"

    def test_a_set_group_is_kept(self, accounts: dict[str, Any]) -> None:
        assert resolve_service_group(" www-data ", "deploy") == "www-data"


class TestTheNodeThatCrashed:
    def test_an_empty_group_reads_as_the_users_primary_group(
        self, config_path: Path, accounts: dict[str, Any]
    ) -> None:
        write_config(config_path, {"service_user": "www-data", "service_group": ""})

        assert Config().service_user == "www-data"
        assert Config().service_group == "www-data"

    def test_an_empty_user_and_group_read_as_the_defaults(
        self, config_path: Path, accounts: dict[str, Any]
    ) -> None:
        write_config(config_path, {"service_user": "", "service_group": ""})

        assert Config().service_user == "www-data"
        assert Config().service_group == "www-data"

    def test_another_users_empty_group_is_its_own_primary_group(
        self, config_path: Path, accounts: dict[str, Any]
    ) -> None:
        write_config(config_path, {"service_user": "deploy", "service_group": ""})

        assert Config().service_group == "staff"

    def test_a_missing_group_keeps_the_default(
        self, config_path: Path, accounts: dict[str, Any]
    ) -> None:
        write_config(config_path, {"service_user": "deploy"})

        assert Config().service_group == "www-data"

    def test_loading_rewrites_nothing(self, config_path: Path, accounts: dict[str, Any]) -> None:
        write_config(config_path, {"service_user": "www-data", "service_group": ""})
        before = config_path.read_bytes()

        assert Config().service_group == "www-data"
        assert config_path.read_bytes() == before

    def test_a_migration_runs_as_the_primary_group(
        self, tmp_path: Path, config_path: Path, accounts: dict[str, Any]
    ) -> None:
        """The deployer path of the traceback: _execution -> the sandbox spec."""
        write_config(config_path, {"service_user": "www-data", "service_group": ""})
        runner = FakeRunner()
        deployer = NodeJSDeployer(verbose=False, runner=runner)
        root = tmp_path / "apps" / "x"
        deployer.configure(DOMAIN, "https://example.com/x.git", app_path=root)
        deployer._layout = "inplace"
        deployer._sandbox_regime = SandboxState(domain=DOMAIN, mode="on")
        deployer._sandbox_cache = tmp_path / "cache"
        deployer._sandbox_tree = deployer.build_path

        spec = deployer._execution(BuildPhase.INSTALL)
        release = deployer._execution(BuildPhase.RELEASE)

        assert spec is not None and release is not None
        assert (spec.user, spec.group) == ("www-data", "www-data")
        assert (release.user, release.group) == ("www-data", "www-data")


# ---------------------------------------------------------------------------
# An unusable account is an actionable error, never a traceback
# ---------------------------------------------------------------------------


def _build_spec(user: str, group: str, tmp_path: Path) -> SandboxSpec:
    return build_sandbox.build_spec(
        app=None,
        app_name="x",
        phase=BuildPhase.BUILD,
        state=SandboxState(domain="x.example.com", mode="on"),
        user=user,
        group=group,
        build_path=tmp_path / "apps" / "x",
        apps_dir=tmp_path / "apps",
        cache=tmp_path / "cache" / "x",
        env_file=None,
        shared=None,
    )


class TestUnusableAccount:
    def test_a_group_that_does_not_exist_names_the_setting(
        self, tmp_path: Path, accounts: dict[str, Any]
    ) -> None:
        with pytest.raises(ConfigError) as caught:
            _build_spec("www-data", "nosuchgroup", tmp_path)

        text = str(caught.value)
        assert "nosuchgroup" in text
        assert "service_group" in text
        assert "noust config set service_group" in caught.value.details

    def test_a_migration_as_a_group_that_does_not_exist_is_refused_too(
        self, tmp_path: Path, accounts: dict[str, Any]
    ) -> None:
        with pytest.raises(ConfigError, match="service_group"):
            build_sandbox.release_spec(
                app_name="x",
                user="www-data",
                group="nosuchgroup",
                build_path=tmp_path / "apps" / "x",
                env_file=None,
            )

    def test_the_cache_is_not_handed_to_a_group_that_does_not_exist(
        self, tmp_path: Path, accounts: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import paths

        monkeypatch.setattr(paths, "BUILD_CACHE_DIR", tmp_path / "cache")
        runner = FakeRunner()

        with pytest.raises(ConfigError, match="service_group"):
            build_sandbox.ensure_cache_dir("x", user="www-data", group="nosuchgroup", runner=runner)

        assert not [call for call in runner.calls if call[0] == "chown"]

    @pytest.mark.parametrize(
        ("user", "group", "key"),
        [
            ("", "www-data", "service_user"),
            ("www-data", "", "service_group"),
            ("www data", "www-data", "service_user"),
        ],
    )
    def test_a_name_that_is_not_an_account_names_the_setting(
        self, tmp_path: Path, accounts: dict[str, Any], user: str, group: str, key: str
    ) -> None:
        with pytest.raises(ConfigError) as caught:
            _build_spec(user, group, tmp_path)

        assert key in str(caught.value)
        assert f"noust config set {key}" in caught.value.details

    def test_an_existing_group_passes(self, tmp_path: Path, accounts: dict[str, Any]) -> None:
        spec = _build_spec("deploy", "staff", tmp_path)

        assert (spec.user, spec.group) == ("deploy", "staff")

    def test_an_account_not_created_yet_is_left_to_systemd(
        self, tmp_path: Path, accounts: dict[str, Any]
    ) -> None:
        """noust-build before its first build (a rehearsal): nothing to look up yet."""
        spec = _build_spec("noust-build", "noust-build", tmp_path)

        assert spec.group == "noust-build"

    def test_the_spec_itself_refuses_with_a_noust_error(self) -> None:
        """Whoever builds a spec, an unusable account never surfaces as a ValueError."""
        with pytest.raises(NoustError) as caught:
            SandboxSpec(user="www-data", group="")

        assert "not an account name" in str(caught.value)
        assert caught.value.details
