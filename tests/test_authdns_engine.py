# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Knot DNS as Noust runs it (4.1, item 104).

The runner is scripted, so what is pinned is the exact commands: what is asked of the package
manager on each family, that a configuration is checked before it replaces the working one and
is put back when Knot does not take it, that a Knot with zones of its own is not taken over, and
how Knot's own words about zones and keys are read.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.managers.authdns.engine import BACKUP_SUFFIX, KnotEngine
from noust.managers.authdns.engine import EngineError as Failure
from noust.managers.authdns.knotconf import CONFIG_MARKER
from noust.managers.server.errors import ConfirmationRequiredError, UnsupportedHostError
from noust.managers.server.host import PackageFamily

NEW = f"# {CONFIG_MARKER}\nserver:\n    listen: [ 203.0.113.10@53 ]\n"
SAMPLE = "# a sample\nserver:\n    rundir: /run/knot\nzone:\n#  - domain: example.com\n"


@pytest.fixture
def paths_(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(config=tmp_path / "etc" / "knot.conf", zones=tmp_path / "var" / "zones")


@pytest.fixture
def engine(runner: FakeRunner, fs: Any, paths_: SimpleNamespace) -> KnotEngine:
    return KnotEngine(
        runner,
        fs,
        config_path=paths_.config,
        zones_dir=paths_.zones,
        group=None,
        sleep=lambda _: None,
    )


def platform(family: PackageFamily, os_id: str, version: str = "12", like: str = "") -> Any:
    return SimpleNamespace(
        family=family,
        os=SimpleNamespace(id=os_id, version_id=version, id_like=like),
        why_updates_unsupported=lambda: "",
    )


def calls(runner: FakeRunner) -> list[list[str]]:
    return [list(call) for call in runner.calls]


class TestTheVersion:
    def test_it_is_read_from_the_program(self, runner: FakeRunner, engine: KnotEngine) -> None:
        runner.script(["knotd", "--version"], stdout="knotd, Knot DNS 3.4.6\n")

        assert engine.version() == (3, 4, 6)
        assert engine.require_version() == (3, 4, 6)

    def test_an_older_one_is_refused_with_the_reason(
        self, runner: FakeRunner, engine: KnotEngine
    ) -> None:
        runner.script(["knotd", "--version"], stdout="knotd, Knot DNS 3.0.9\n")

        with pytest.raises(Failure) as raised:
            engine.require_version()

        assert "needs 3.1 or newer" in raised.value.message
        assert "catalog zones" in raised.value.details

    def test_no_program_is_no_version(self, runner: FakeRunner, engine: KnotEngine) -> None:
        runner._known_programs = set()

        assert engine.version() is None and engine.installed() is False
        with pytest.raises(Failure):
            engine.require_version()


class TestInstalling:
    def test_apt_installs_the_package_and_the_version_is_checked(
        self, runner: FakeRunner, engine: KnotEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.authdns.engine.detect_platform",
            lambda _runner: platform(PackageFamily.APT, "debian"),
        )
        runner._known_programs = {"apt-get"}
        runner.script(["knotd", "--version"], stdout="knotd, Knot DNS 3.4.6\n")
        real_run = runner.run

        def run(argv: Any, **kwargs: Any) -> Any:
            result = real_run(argv, **kwargs)
            if list(argv)[:2] == ["apt-get", "install"] and runner._known_programs is not None:
                runner._known_programs |= {"knotd", "knotc", "keymgr"}
            return result

        monkeypatch.setattr(runner, "run", run)
        output: list[str] = []

        version = engine.install(on_output=output.append)

        assert version == (3, 4, 6)
        assert ["apt-get", "install", "-y", "knot"] in calls(runner)
        assert output[0] == "$ apt-get install -y knot"

    def test_a_machine_that_has_it_installs_nothing(
        self, runner: FakeRunner, engine: KnotEngine
    ) -> None:
        runner.script(["knotd", "--version"], stdout="knotd, Knot DNS 3.3.4\n")

        assert engine.install() == (3, 3, 4)
        assert not any(call[0] in ("apt-get", "dnf", "zypper") for call in calls(runner))

    @pytest.mark.parametrize(
        ("family", "os_id", "expected"),
        [
            (PackageFamily.APT, "ubuntu", [["apt-get", "install", "-y", "knot"]]),
            (
                PackageFamily.ZYPPER,
                "opensuse-leap",
                [["zypper", "--non-interactive", "install", "knot"]],
            ),
            (PackageFamily.DNF, "fedora", [["dnf", "install", "-y", "knot"]]),
        ],
    )
    def test_each_family_has_its_command(
        self,
        engine: KnotEngine,
        monkeypatch: pytest.MonkeyPatch,
        family: PackageFamily,
        os_id: str,
        expected: list[list[str]],
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.authdns.engine.detect_platform",
            lambda _runner: platform(family, os_id),
        )

        assert engine.install_plan() == (expected, False)

    def test_a_rebuild_of_rhel_gets_knot_from_epel_and_only_with_a_yes(
        self, runner: FakeRunner, engine: KnotEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.authdns.engine.detect_platform",
            lambda _runner: platform(PackageFamily.DNF, "almalinux", "9.4", "rhel centos fedora"),
        )
        runner._known_programs = {"dnf"}

        commands, needs_epel = engine.install_plan()
        with pytest.raises(ConfirmationRequiredError) as raised:
            engine.install()

        assert needs_epel is True
        assert commands == [
            ["dnf", "install", "-y", "epel-release"],
            ["dnf", "install", "-y", "knot"],
        ]
        assert raised.value.required["epel"] is True
        assert not any(call[0] == "dnf" for call in calls(runner))

    def test_rhel_itself_is_explained_not_guessed(
        self, engine: KnotEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.authdns.engine.detect_platform",
            lambda _runner: platform(PackageFamily.DNF, "rhel", "9.4"),
        )

        with pytest.raises(UnsupportedHostError) as raised:
            engine.install_plan()

        assert "codeready-builder-for-rhel-9" in raised.value.details

    def test_a_machine_with_no_package_manager_is_unsupported(
        self, engine: KnotEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.authdns.engine.detect_platform",
            lambda _runner: platform(PackageFamily.NONE, "alpine"),
        )

        with pytest.raises(UnsupportedHostError):
            engine.install_plan()

    def test_a_failed_install_carries_the_package_managers_words(
        self, runner: FakeRunner, engine: KnotEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.authdns.engine.detect_platform",
            lambda _runner: platform(PackageFamily.ZYPPER, "opensuse-leap"),
        )
        runner._known_programs = {"zypper"}
        runner.script(["zypper"], stderr="Package 'knot' not found.", exit_code=104)

        with pytest.raises(Failure) as raised:
            engine.install()

        assert "Installing failed" in raised.value.message
        assert "not found" in (raised.value.output or "")


class TestAConfiguration:
    def test_a_clean_machine_gets_it_checked_then_replaced_then_started(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        runner.script(["systemctl", "is-active"], stdout="inactive\n", exit_code=3)

        engine.apply_config(NEW)

        candidate = str(paths_.config) + ".noust-new"
        seen = calls(runner)
        assert ["knotc", "-c", candidate, "conf-check"] in seen
        assert ["systemctl", "enable", "knot"] in seen and ["systemctl", "restart", "knot"] in seen
        assert seen.index(["knotc", "-c", candidate, "conf-check"]) < seen.index(
            ["systemctl", "restart", "knot"]
        )
        assert paths_.config.read_text() == NEW
        assert (paths_.config.stat().st_mode & 0o777) == 0o640
        assert not Path(candidate).exists()

    def test_a_running_server_is_reloaded_not_restarted(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        runner.script(["systemctl", "is-active"], stdout="active\n")

        engine.apply_config(NEW)

        seen = calls(runner)
        assert ["knotc", "-c", str(paths_.config), "-b", "reload"] in seen
        assert not any(call[:2] == ["systemctl", "restart"] for call in seen)

    def test_a_change_of_addresses_restarts_it(
        self, runner: FakeRunner, engine: KnotEngine
    ) -> None:
        runner.script(["systemctl", "is-active"], stdout="active\n")

        engine.apply_config(NEW, restart=True)

        assert ["systemctl", "restart", "knot"] in calls(runner)

    def test_the_packages_sample_is_kept_beside_it_once(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        paths_.config.parent.mkdir(parents=True)
        paths_.config.write_text(SAMPLE)
        runner.script(["systemctl", "is-active"], stdout="active\n")

        engine.apply_config(NEW)
        engine.apply_config(NEW + "# again\n")

        backup = Path(str(paths_.config) + BACKUP_SUFFIX)
        assert backup.read_text() == SAMPLE
        assert paths_.config.read_text().endswith("# again\n")

    def test_a_knot_that_serves_zones_of_its_own_is_not_taken_over(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        paths_.config.parent.mkdir(parents=True)
        paths_.config.write_text("zone:\n  - domain: customer.example\n    file: x\n")

        with pytest.raises(Failure) as raised:
            engine.apply_config(NEW)

        assert "zones of its own" in raised.value.message
        assert "customer.example" in raised.value.details
        assert paths_.config.read_text().startswith("zone:")
        assert not any(call[0] in ("systemctl", "knotc") for call in calls(runner))

    def test_a_configuration_knot_refuses_replaces_nothing(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        paths_.config.parent.mkdir(parents=True)
        paths_.config.write_text(SAMPLE)
        runner.script(["knotc"], stderr="error: config, line 3 (invalid item)", exit_code=1)

        with pytest.raises(Failure) as raised:
            engine.apply_config(NEW)

        assert "did not accept the configuration" in raised.value.message
        assert "invalid item" in (raised.value.output or "")
        assert paths_.config.read_text() == SAMPLE
        assert not Path(str(paths_.config) + ".noust-new").exists()

    def test_a_reload_knot_refuses_puts_the_previous_file_back(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        paths_.config.parent.mkdir(parents=True)
        paths_.config.write_text(f"# {CONFIG_MARKER}\nold\n")
        runner.script(["systemctl", "is-active"], stdout="active\n")
        runner.script(
            ["knotc", "-c", str(paths_.config), "-b", "reload"], stderr="failed", exit_code=1
        )

        with pytest.raises(Failure) as raised:
            engine.apply_config(NEW)

        assert "did not accept the new configuration" in raised.value.message
        assert paths_.config.read_text() == f"# {CONFIG_MARKER}\nold\n"

    def test_a_server_that_will_not_start_leaves_no_configuration_behind(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        runner.script(["systemctl", "is-active"], stdout="inactive\n", exit_code=3)
        runner.script(["systemctl", "restart"], stderr="Job failed", exit_code=1)

        with pytest.raises(Failure) as raised:
            engine.apply_config(NEW)

        assert "would not start" in raised.value.message
        assert "journalctl -u knot" in raised.value.details
        assert not paths_.config.exists()

    def test_a_server_that_never_answers_is_an_error_not_a_hang(
        self, runner: FakeRunner, engine: KnotEngine
    ) -> None:
        runner.script(["systemctl", "is-active"], stdout="inactive\n", exit_code=3)
        runner.script(["knotc", "-c"], stderr="not running", exit_code=1)
        runner.script(
            ["knotc", "-c", str(engine.config_path) + ".noust-new", "conf-check"], stdout="ok"
        )

        with pytest.raises(Failure) as raised:
            engine.apply_config(NEW)

        assert "never answered" in raised.value.message

    def test_removing_it_puts_the_samples_back_and_takes_noust_zone_files_only(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        paths_.config.parent.mkdir(parents=True)
        paths_.config.write_text(SAMPLE)
        runner.script(["systemctl", "is-active"], stdout="active\n")
        engine.apply_config(NEW)
        engine.prepare_zones_dir()
        engine.write_zone("example.com", "; Generated by Noust (noust dns zone)\n$ORIGIN x.\n")
        other = paths_.zones / "yours.example.zone"
        other.write_text("$ORIGIN yours.example.\n")

        engine.restore_config()

        assert paths_.config.read_text() == SAMPLE
        assert not engine.zone_file("example.com").exists() and other.exists()
        assert ["systemctl", "disable", "--now", "knot"] in calls(runner)


class TestZoneFiles:
    def test_a_file_that_says_the_same_is_not_written_again(
        self, engine: KnotEngine, fs: Any
    ) -> None:
        engine.prepare_zones_dir()

        assert engine.write_zone("example.com", "; Generated by Noust\nx\n") is True
        assert engine.write_zone("example.com", "; Generated by Noust\nx\n") is False
        assert engine.write_zone("example.com", "; Generated by Noust\ny\n") is True
        assert [c for c in fs.changes if c[0] == "write" and c[1].name == "example.com.zone"] != []

    def test_a_zone_file_is_for_the_server_to_read_and_nobody_else(
        self, engine: KnotEngine
    ) -> None:
        engine.prepare_zones_dir()
        engine.write_zone("example.com", "; Generated by Noust\n")

        assert (engine.zone_file("example.com").stat().st_mode & 0o777) == 0o640


class TestAskingKnot:
    def test_a_reload_names_the_zones_whose_file_is_applied_again(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        engine.reload(["example.com"])

        assert calls(runner) == [
            ["knotc", "-c", str(paths_.config), "-b", "reload"],
            ["knotc", "-c", str(paths_.config), "-b", "zone-reload", "example.com."],
        ]

    def test_a_reload_knot_does_not_take_says_the_zone_is_still_served(
        self, runner: FakeRunner, engine: KnotEngine
    ) -> None:
        runner.script(["knotc"], stderr="error: [example.com.] (failed)", exit_code=1)

        with pytest.raises(Failure) as raised:
            engine.reload()

        assert "still served as it was" in raised.value.details
        assert "(failed)" in (raised.value.output or "")

    def test_the_status_of_the_zones_is_read_from_knots_lines(
        self, runner: FakeRunner, engine: KnotEngine
    ) -> None:
        runner.script(
            ["knotc"],
            stdout=(
                "[example.com.] role: master | serial: 1793266851 | catalog: @noust-catalog.invalid. "
                "| re-sign: +12D12h23m\n"
                "[noust-catalog.invalid.] role: master | serial: 1793266882 | catalog: generate\n"
                "[example.net.] role: slave | serial: none | refresh: +1h59m\n"
                "garbage that is not a zone\n"
            ),
        )

        status = engine.zone_status()

        assert status["example.com"].serial == 1793266851
        assert status["example.com"].catalog == "noust-catalog.invalid."
        assert status["example.com"].role == "master"
        assert status["noust-catalog.invalid"].catalog == "generate"
        assert status["example.net"].serial is None and status["example.net"].role == "slave"

    def test_knot_that_does_not_answer_has_no_status(
        self, runner: FakeRunner, engine: KnotEngine
    ) -> None:
        runner.script(["knotc"], stderr="error: failed to connect", exit_code=1)

        assert engine.zone_status() == {}

    def test_the_ds_records_are_read_for_the_registrar(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        runner.script(
            ["keymgr"],
            stdout=(
                "example.com. DS 9431 13 2 EBE5D6007FDF87E3B70FBC2E1AFB0CFF87E66A0D8F70CAE380D4352472E83665\n"
                "example.com. DS 9431 13 4 70312a16f2ae94d9c57b4f14e19eca31\n"
            ),
        )

        records = engine.ds_records("example.com")

        assert calls(runner) == [["keymgr", "-c", str(paths_.config), "example.com.", "ds"]]
        assert [(r.key_tag, r.algorithm, r.digest_type) for r in records] == [
            (9431, 13, 2),
            (9431, 13, 4),
        ]
        assert (
            records[0].digest == "ebe5d6007fdf87e3b70fbc2e1afb0cff87e66a0d8f70cae380d4352472e83665"
        )
        assert records[0].text.startswith("9431 13 2 ebe5d600")

    def test_a_zone_with_no_key_has_no_ds(self, runner: FakeRunner, engine: KnotEngine) -> None:
        runner.script(["keymgr"], stderr="error: no keys", exit_code=1)

        assert engine.ds_records("example.com") == []

    def test_telling_knot_the_parent_has_the_ds(
        self, runner: FakeRunner, engine: KnotEngine, paths_: SimpleNamespace
    ) -> None:
        engine.confirm_ds_published("example.com")

        assert calls(runner) == [
            ["knotc", "-c", str(paths_.config), "zone-ksk-submitted", "example.com."]
        ]

    def test_knot_refusing_that_is_explained(self, runner: FakeRunner, engine: KnotEngine) -> None:
        runner.script(["knotc"], stderr="error: no key", exit_code=1)

        with pytest.raises(Failure) as raised:
            engine.confirm_ds_published("example.com")

        assert "no key of example.com waiting" in raised.value.message
