# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a unit, a timer or a package script runs works under the ENS profile.

``security.profile: ens-medium`` requires ``--reason`` for every command that
changes something. Nobody types a reason into a unit file, a timer written by
3.0 or a package's maintainer script, so every command those run must be
either read-only or one of :data:`noust.cli.audit_policy.SYSTEM_ENTRY_POINTS`,
which are still audited but need no reason. These tests read every place such
a command is written - the systemd templates, the units Noust renders in
code, the Debian and RPM scripts and the container - and check each one.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import Context, NoustGroup, cli
from noust.core.audit import Actor, get_log

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "noust" / "templates" / "systemd"
DEBIAN_SCRIPTS = [ROOT / "obs" / f"debian.{name}" for name in ("postinst", "prerm", "postrm")]
RPM_SPEC = ROOT / "rpm" / "noust.spec"
DOCKERFILE = ROOT / "packaging" / "container" / "Dockerfile"

#: The scriptlets of an RPM spec; everything else in it (the changelog above
#: all) is prose that merely mentions commands.
RPM_SCRIPTLETS = ("%pre", "%post", "%preun", "%postun", "%pretrans", "%posttrans")

_NOUST_CALL = re.compile(r"(?:^|[\s;|&(!])(?:/usr/bin/)?noust\s+([^\n]*)")


def _strip_quoted(line: str) -> str:
    """Drop quoted text: what an echo says is not a command that runs."""
    return re.sub(r"\"[^\"]*\"|'[^']*'", " ", line)


def _resolve(words: list[str]) -> str | None:
    """The command path a sequence of words names in the real tree, if any."""
    command: click.Command = cli
    path: list[str] = []
    for word in words:
        if not isinstance(command, click.Group):
            break
        sub = command.get_command(click.Context(command), word)
        if sub is None:
            break
        command = sub
        path.append(sub.name or word)
    if isinstance(command, click.Group) or not path:
        return None
    return " ".join(path)


def _calls_in_shell(text: str) -> list[tuple[str, bool]]:
    """Every ``noust ...`` a shell script runs, and whether it passes --reason."""
    found: list[tuple[str, bool]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # A reason is quoted, so look for it before the quotes go.
        has_reason = "--reason" in line
        for match in _NOUST_CALL.finditer(_strip_quoted(line)):
            words = re.findall(r"[a-z][a-z0-9-]*", match.group(1).split(">")[0])
            path = _resolve(words)
            if path is not None:
                found.append((path, has_reason))
    return found


def _rpm_scriptlets(text: str) -> str:
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("%") and not line.startswith(("%{", "%%")):
            section = line.split()[0]
            if section in RPM_SCRIPTLETS or section in {
                "%changelog",
                "%files",
                "%install",
                "%build",
                "%prep",
                "%check",
                "%description",
                "%package",
            }:
                inside = section in RPM_SCRIPTLETS
                continue
        if inside:
            kept.append(line)
    return "\n".join(kept)


def _template_calls() -> list[tuple[str, str, bool]]:
    calls: list[tuple[str, str, bool]] = []
    for template in sorted(TEMPLATES.glob("*.j2")):
        for line in template.read_text().splitlines():
            if not line.startswith("ExecStart=") or "esc.line(noust)" not in line:
                continue
            rest = line.split("}}", 1)[1]
            words = [word for word in rest.split() if not word.startswith("{{")]
            words = words[: next((i for i, w in enumerate(words) if w.startswith("-")), None)]
            path = _resolve(words)
            assert path is not None, f"{template.name}: no command in {line!r}"
            calls.append((template.name, path, "--reason" in line))
    return calls


def _rendered_unit_calls() -> list[tuple[str, str, bool]]:
    """The units Noust renders in code rather than from a template."""
    from noust.cli.commands import web as web_commands
    from noust.monitor.process_monitor import ProcessMonitor

    calls: list[tuple[str, str, bool]] = []
    monitor = ProcessMonitor.__new__(ProcessMonitor)
    monitor._noust_executable = lambda: "/usr/bin/noust"  # type: ignore[method-assign]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(web_commands, "_noust_executable", lambda: "/usr/bin/noust")
    try:
        web_exec = web_commands._service_exec_start(web_commands.StartOptions())
    finally:
        monkey.undo()
    for name, unit in (
        ("noust-monitor.service", monitor._unit_content()),
        ("noust-web.service", f"ExecStart={web_exec}"),
    ):
        for line in unit.splitlines():
            if line.startswith("ExecStart="):
                argv = shlex.split(line.removeprefix("ExecStart="))
                words = argv[1 : next((i for i, w in enumerate(argv) if w.startswith("-")), None)]
                path = _resolve(words)
                assert path is not None, f"{name}: no command in {line!r}"
                calls.append((name, path, "--reason" in argv))
    return calls


def _every_call() -> list[tuple[str, str, bool]]:
    calls = _template_calls() + _rendered_unit_calls()
    for script in DEBIAN_SCRIPTS:
        calls += [(script.name, path, why) for path, why in _calls_in_shell(script.read_text())]
    rpm = _rpm_scriptlets(RPM_SPEC.read_text())
    calls += [("noust.spec", path, why) for path, why in _calls_in_shell(rpm)]
    cmd = re.search(r"^CMD\s+(\[.*\])", DOCKERFILE.read_text(), re.MULTILINE)
    assert cmd is not None
    container = _resolve(re.findall(r'"([^"]+)"', cmd.group(1)))
    assert container is not None
    calls.append(("Dockerfile", container, False))
    return calls


def _leaf(path: str) -> click.Command:
    command: click.Command = cli
    for word in path.split():
        assert isinstance(command, click.Group)
        found = command.get_command(click.Context(command), word)
        assert found is not None
        command = found
    return command


class TestEveryUnitAndPackageCommand:
    def test_the_parsers_find_what_is_known_to_be_there(self) -> None:
        paths = {path for _, path, _ in _every_call()}
        for expected in (
            "monitor run",
            "web start",
            "central run",
            "backup run-schedule",
            "preview sweep",
            "db backup-run",
            "migrate-from-wasm",
            "config upgrade",
            "config clean",
            "monitor install",
            "monitor autoenable",
            "web stop",
        ):
            assert expected in paths, expected

    def test_each_is_read_only_a_declared_entry_point_or_given_a_reason(self) -> None:
        """A unit line that carries --reason (the database backup timers) needs no exemption."""
        stranded = sorted(
            f"{where}: noust {path}"
            for where, path, why in _every_call()
            if not audit_policy.is_read_only(_leaf(path), path)
            and path not in audit_policy.SYSTEM_ENTRY_POINTS
            and not (why and where.endswith((".j2", ".service")))
        )
        assert not stranded, f"would exit 2 under ens-medium: {stranded}"

    def test_the_package_scripts_give_a_reason_where_they_change_something(self) -> None:
        """Harmless on the standard profile, and on record under the ENS one."""
        missing = sorted(
            f"{where}: noust {path}"
            for where, path, why in _every_call()
            if where.startswith(("debian.", "noust.spec"))
            and not why
            and not audit_policy.is_read_only(_leaf(path), path)
        )
        assert not missing, missing

    def test_every_declared_entry_point_exists(self) -> None:
        for path in audit_policy.SYSTEM_ENTRY_POINTS:
            assert not isinstance(_leaf(path), click.Group), path


SYSTEMD = Actor(kind="system", id="4f2a", name="systemd", via="systemd")


@click.group("fake", cls=NoustGroup)
def fake() -> None:
    """Stands in for the tree."""


@fake.group("preview", cls=NoustGroup)
def preview() -> None:
    """Stands in for 'noust preview'."""


@preview.command("sweep")
def sweep() -> None:
    """Stands in for 'noust preview sweep'."""
    click.echo("swept")


@preview.command("delete")
def delete() -> None:
    """A change an operator makes."""
    click.echo("deleted")


def _events(action: str) -> list[dict[str, Any]]:
    return [e for e in reversed(get_log().read(limit=1000)) if e["action"] == action]


class TestEntryPointsUnderTheEnsProfile:
    @pytest.fixture(autouse=True)
    def _ens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(audit_policy, "security_profile", lambda: "ens-medium")
        monkeypatch.setattr(audit_policy.audit, "cli_actor", lambda: SYSTEMD)

    def test_an_entry_point_runs_without_a_reason_and_is_on_record(self) -> None:
        result = CliRunner().invoke(fake, ["preview", "sweep"], obj=Context())
        assert result.exit_code == 0, result.output
        assert "swept" in result.output
        [done] = _events("cli.command")
        assert done["resource"] == "preview sweep"
        assert done["who"]["kind"] == "system"
        assert done["details"]["entry_point"] is True
        assert "reason" not in done["details"]

    def test_a_reason_given_to_one_is_still_recorded(self) -> None:
        result = CliRunner().invoke(
            fake, ["preview", "sweep", "--reason", "package upgrade"], obj=Context()
        )
        assert result.exit_code == 0
        assert _events("cli.command")[0]["details"]["reason"] == "package upgrade"

    def test_any_other_change_still_needs_one(self) -> None:
        result = CliRunner().invoke(fake, ["preview", "delete"], obj=Context())
        assert result.exit_code == 2
        assert "deleted" not in result.output
