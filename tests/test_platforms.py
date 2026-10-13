# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The platform table, and the installer that carries a copy of it.

``noust.managers.server.platforms`` is the one statement of which releases
Noust installs on. ``packaging/install.sh`` cannot import it, so it carries a
generated copy, and ``docs/PLATFORMS.md`` shows the same rows: the tests below
fail when any of the three drifts, when the installer grows a habit this
project refuses (piping a download into a shell, a bashism in a ``#!/bin/sh``
file), and check what each distribution's real ``os-release`` is judged as.

The installer's own detection is tested where it can run on those machines, the
integration harness (``tests/integration/install_matrix.py``); a pytest run
executes no process, apart from the syntax checks at the end, which only parse.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from noust.managers.server import platforms
from noust.managers.server.host import HostPaths, OsRelease, read_os_release

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "packaging" / "install.sh"
DOCUMENT = ROOT / "docs" / "PLATFORMS.md"

#: What a distribution's ``/etc/os-release`` says, for the releases the table is about.
OS_RELEASES = {
    "debian12": 'ID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n',
    "debian13": 'ID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n',
    "debian11": 'ID=debian\nVERSION_ID="11"\nPRETTY_NAME="Debian GNU/Linux 11 (bullseye)"\n',
    "raspberrypi-os-64": 'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n',
    "raspbian-32": 'ID=raspbian\nID_LIKE=debian\nVERSION_ID="12"\nPRETTY_NAME="Raspbian GNU/Linux 12"\n',
    "ubuntu2204": 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.5 LTS"\n',
    "ubuntu2404": 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n',
    "ubuntu2510": 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="25.10"\nPRETTY_NAME="Ubuntu 25.10"\n',
    "pop2204": 'ID=pop\nID_LIKE="ubuntu debian"\nVERSION_ID="22.04"\nPRETTY_NAME="Pop!_OS 22.04 LTS"\n',
    "fedora42": 'ID=fedora\nVERSION_ID=42\nPRETTY_NAME="Fedora Linux 42 (Server Edition)"\n',
    "fedora43": 'ID=fedora\nVERSION_ID=43\nPRETTY_NAME="Fedora Linux 43 (Server Edition)"\n',
    "fedora44": 'ID=fedora\nVERSION_ID=44\nPRETTY_NAME="Fedora Linux 44"\n',
    "leap156": 'ID="opensuse-leap"\nID_LIKE="suse opensuse"\nVERSION_ID="15.6"\nPRETTY_NAME="openSUSE Leap 15.6"\n',
    "leap160": 'ID="opensuse-leap"\nID_LIKE="suse opensuse"\nVERSION_ID="16.0"\nPRETTY_NAME="openSUSE Leap 16.0"\n',
    "tumbleweed": 'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\nVERSION_ID="20261001"\nPRETTY_NAME="openSUSE Tumbleweed"\n',
    "rocky95": 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.5"\nPRETTY_NAME="Rocky Linux 9.5 (Blue Onyx)"\n',
    "alma94": 'ID="almalinux"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.4"\nPRETTY_NAME="AlmaLinux 9.4"\n',
    "rhel9": 'ID="rhel"\nID_LIKE="fedora"\nVERSION_ID="9.4"\nPRETTY_NAME="Red Hat Enterprise Linux 9.4"\n',
    "centos9": 'ID="centos"\nID_LIKE="rhel fedora"\nVERSION_ID="9"\nPRETTY_NAME="CentOS Stream 9"\n',
    "oracle9": 'ID="ol"\nID_LIKE="fedora"\nVERSION_ID="9.4"\nPRETTY_NAME="Oracle Linux Server 9.4"\n',
    "arch": 'ID=arch\nPRETTY_NAME="Arch Linux"\n',
    "alpine": 'ID=alpine\nVERSION_ID=3.20.0\nPRETTY_NAME="Alpine Linux v3.20"\n',
}


def release_of(tmp_path: Path, name: str) -> OsRelease:
    """
    Read one of the fake ``os-release`` files the way Noust reads the real one.

    Args:
        tmp_path: A temporary directory standing for ``/``.
        name: A key of :data:`OS_RELEASES`.

    Returns:
        What :func:`read_os_release` makes of it.
    """
    path = tmp_path / "etc" / "os-release"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OS_RELEASES[name], encoding="utf-8")
    return read_os_release(HostPaths(tmp_path))


# A date well inside every release's support, so the end-of-life check says nothing.
TODAY = date(2026, 10, 6)


@pytest.mark.parametrize(
    ("name", "machine", "status", "repo"),
    [
        ("debian12", "x86_64", platforms.SUPPORTED, "Debian_12"),
        ("debian12", "aarch64", platforms.SUPPORTED, "Debian_12"),
        ("debian13", "x86_64", platforms.SUPPORTED, "Debian_13"),
        ("raspberrypi-os-64", "aarch64", platforms.SUPPORTED, "Debian_12"),
        ("ubuntu2204", "x86_64", platforms.SUPPORTED, "xUbuntu_22.04"),
        ("ubuntu2404", "aarch64", platforms.SUPPORTED, "xUbuntu_24.04"),
        ("ubuntu2510", "x86_64", platforms.UNSUPPORTED, None),
        ("fedora42", "x86_64", platforms.BEST_EFFORT, "Fedora_42"),
        ("fedora43", "x86_64", platforms.SUPPORTED, "Fedora_43"),
        ("fedora44", "x86_64", platforms.BEST_EFFORT, "Fedora_43"),
        ("leap156", "x86_64", platforms.BEST_EFFORT, "15.6"),
        ("leap160", "aarch64", platforms.SUPPORTED, "16.0"),
        ("tumbleweed", "x86_64", platforms.SUPPORTED, "openSUSE_Tumbleweed"),
        ("rocky95", "x86_64", platforms.PIP_ONLY, None),
        ("alma94", "aarch64", platforms.PIP_ONLY, None),
        ("rhel9", "x86_64", platforms.PIP_ONLY, None),
        ("oracle9", "x86_64", platforms.PIP_ONLY, None),
        ("centos9", "x86_64", platforms.PIP_ONLY, None),
        ("debian11", "x86_64", platforms.UNSUPPORTED, None),
        ("arch", "x86_64", platforms.UNSUPPORTED, None),
        ("alpine", "x86_64", platforms.UNSUPPORTED, None),
    ],
)
def test_each_release_is_judged_by_its_own_os_release(
    tmp_path: Path, name: str, machine: str, status: str, repo: str | None
) -> None:
    """A release is found by what it says it is, and the repository comes with it."""
    verdict = platforms.assess(release_of(tmp_path, name), machine, today=TODAY)

    assert verdict.status == status
    if verdict.target is not None:
        assert verdict.target.repo == repo
    assert verdict.installable == (status != platforms.UNSUPPORTED)


def test_a_derivative_is_judged_as_its_parent_and_never_better_than_best_effort(
    tmp_path: Path,
) -> None:
    """Pop!_OS 22.04 is Ubuntu 22.04's packages on a system nobody tested."""
    verdict = platforms.assess(release_of(tmp_path, "pop2204"), "x86_64", today=TODAY)

    assert verdict.status == platforms.BEST_EFFORT
    assert verdict.derivative_of == "ubuntu"
    assert verdict.target is not None
    assert verdict.target.repo == "xUbuntu_22.04"
    assert any("derivative" in reason for reason in verdict.reasons)


@pytest.mark.parametrize("machine", ["armv7l", "armhf", "i686", "ppc64le", "s390x", ""])
def test_a_machine_that_is_not_amd64_or_arm64_is_refused_whatever_it_runs(
    tmp_path: Path, machine: str
) -> None:
    """A 32-bit Raspberry Pi OS is Debian 12 on an architecture nothing was built for."""
    verdict = platforms.assess(release_of(tmp_path, "debian12"), machine, today=TODAY)

    assert verdict.status == platforms.UNSUPPORTED
    assert not verdict.installable
    assert any("architecture" in reason for reason in verdict.reasons)


def test_a_32_bit_raspberry_pi_os_is_not_a_declared_release(tmp_path: Path) -> None:
    """Its ID is raspbian: only the 64-bit image, which says debian, is Debian 12."""
    verdict = platforms.assess(release_of(tmp_path, "raspbian-32"), "armv7l", today=TODAY)

    assert verdict.status == platforms.UNSUPPORTED


@pytest.mark.parametrize(
    ("spelling", "arch"),
    [
        ("x86_64", "amd64"),
        ("amd64", "amd64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        (" AArch64 \n", "arm64"),
        ("armv7l", "armv7l"),
    ],
)
def test_architectures_are_named_once(spelling: str, arch: str) -> None:
    """uname and dpkg spell the same processor differently."""
    assert platforms.normalise_arch(spelling) == arch


def test_a_supported_release_past_its_end_of_life_is_best_effort(tmp_path: Path) -> None:
    """Debian 12 after its LTS ends is still installable, and no longer promised."""
    verdict = platforms.assess(release_of(tmp_path, "debian12"), "x86_64", today=date(2028, 7, 1))

    assert verdict.status == platforms.BEST_EFFORT
    assert any("stopped receiving security updates" in reason for reason in verdict.reasons)


def test_the_table_has_no_two_rows_for_one_release() -> None:
    """The first match wins, so an overlap would make one of the rows dead text."""
    seen: dict[tuple[str, str], str] = {}
    for target in platforms.TARGETS:
        for pattern in target.versions:
            key = (target.os_id, pattern)
            assert key not in seen, f"{target.name} and {seen[key]} both cover {key}"
            seen[key] = target.name


def test_every_row_is_coherent() -> None:
    """A package row has a repository, a pip row has none, every status is declared."""
    for target in platforms.TARGETS:
        assert target.status in platforms.STATUSES, target.name
        assert target.family in ("apt", "dnf", "zypper", "pacman", "apk"), target.name
        if target.status in (platforms.SUPPORTED, platforms.BEST_EFFORT):
            assert target.repo, f"{target.name} has no repository"
            assert target.repository_url == f"{platforms.REPOSITORY_BASE}/{target.repo}/"
        else:
            assert target.repo is None, f"{target.name} names a repository it cannot use"
        if target.status != platforms.SUPPORTED:
            assert target.note, f"{target.name} is not simply supported and does not say why"


def test_a_supported_row_has_been_installed_on_every_architecture_it_claims() -> None:
    """``supported`` means the harness ran the published package there, not that it should work."""
    for target in platforms.TARGETS:
        if target.status == platforms.SUPPORTED:
            assert target.tested, f"{target.name} is declared supported and was never run"


def test_the_rhel_family_is_pip_only_until_obs_builds_for_it() -> None:
    """The one gap the table admits: no OBS target exists, so no package does."""
    rhel_family = [
        t for t in platforms.TARGETS if t.os_id in ("rocky", "almalinux", "rhel", "centos")
    ]

    assert {t.os_id for t in rhel_family} == {"rocky", "almalinux", "rhel", "centos"}
    assert all(t.status == platforms.PIP_ONLY for t in rhel_family)


# ---------------------------------------------------------------------------
# The installer carries the table, and says what the table says
# ---------------------------------------------------------------------------


def test_the_installer_carries_the_current_table() -> None:
    """Edit TARGETS, run scripts/sync_install_targets.py: the script is never behind."""
    text = INSTALLER.read_text(encoding="utf-8")

    assert platforms.replace_installer_block(text) == text


def test_the_platform_document_shows_the_current_table() -> None:
    """The document and the code are the same table."""
    text = DOCUMENT.read_text(encoding="utf-8")
    begin = "<!-- BEGIN TARGETS (generated: scripts/sync_install_targets.py) -->"
    end = "<!-- END TARGETS -->"
    shown = text[text.index(begin) + len(begin) : text.index(end)]

    assert shown.strip() == platforms.markdown_table().strip()


def test_the_installer_pins_the_fingerprint_the_code_knows() -> None:
    """One fingerprint, in one place in the code and one in the script."""
    text = INSTALLER.read_text(encoding="utf-8")

    assert f'REPO_KEY_FPR_PINNED="{platforms.REPOSITORY_KEY_FINGERPRINT}"' in text
    assert re.fullmatch(r"[0-9A-F]{40}", platforms.REPOSITORY_KEY_FINGERPRINT)


def test_the_installer_downloads_only_from_https_and_runs_nothing_it_fetched() -> None:
    """The point of the script: nothing it downloads is ever executed."""
    text = INSTALLER.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))

    assert not re.search(r"\b(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", code)
    assert not re.search(r"\|\s*(sudo\s+)?(ba|z|da)?sh\b", code)
    assert "eval " not in code
    # The one plain-http address in the script is the loopback console it tells the
    # operator to open through their SSH tunnel.
    assert not re.search(r"http://(?!127\.0\.0\.1)", code)
    assert "--proto '=https'" in code
    assert "--https-only" in code


def test_the_installer_refuses_a_key_with_another_fingerprint_before_writing_anything() -> None:
    """The comparison comes before the first file the repository needs is written."""
    text = INSTALLER.read_text(encoding="utf-8")
    apt = text[text.index("setup_apt_repository() {") : text.index("setup_dnf_repository() {")]

    assert apt.index("fetch_key") < apt.index("install -m 644")
    assert 'if [ "$found" != "$KEY_FPR" ]' in text


#: What a ``#!/bin/sh`` script may not use: each is a bash feature that dash or busybox lack.
BASHISMS = {
    r"\[\[": "[[ ]]",
    r"^\s*local\s": "local",
    r"^\s*source\s": "source",
    r"\bfunction\s+\w+\s*\(": "the function keyword",
    r"\becho\s+-e": "echo -e",
    r"\$'": "$'..'",
    r"<<<": "here-strings",
    r"\$\{[A-Za-z_]+//": "${var//pattern}",
    r"\$\{[A-Za-z_]+:[0-9]": "${var:offset}",
    r"\bdeclare\s": "declare",
    r"\bsetopt\b|\bshopt\b": "shell options",
    r"=\(": "arrays",
    r"\[\s[^\]]*\s==\s": "== inside [ ]",
}


def test_the_installer_is_posix_sh() -> None:
    """It runs where only /bin/sh exists: a minimal container, dash, busybox."""
    text = INSTALLER.read_text(encoding="utf-8")

    assert text.startswith("#!/bin/sh\n")
    assert "\nset -eu\n" in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for pattern, name in BASHISMS.items():
        assert not re.search(pattern, code, re.MULTILINE), f"install.sh uses {name}"


@pytest.mark.allow_subprocess
@pytest.mark.parametrize("shell", ["sh", "dash"])
def test_the_installer_parses(shell: str) -> None:
    """``-n`` reads the script and runs none of it."""
    program = shutil.which(shell)
    if program is None:
        pytest.skip(f"{shell} is not installed")

    done = subprocess.run(
        [program, "-n", str(INSTALLER)], capture_output=True, text=True, timeout=30, check=False
    )

    assert done.returncode == 0, done.stderr


@pytest.mark.allow_subprocess
def test_the_installer_is_shellcheck_clean() -> None:
    """CI runs the same check; here it runs when shellcheck is on the machine."""
    program = shutil.which("shellcheck")
    if program is None:
        pytest.skip("shellcheck is not installed (CI runs it)")

    done = subprocess.run(
        [program, "-s", "sh", str(INSTALLER)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr


# ---------------------------------------------------------------------------
# noust setup doctor says where the machine stands
# ---------------------------------------------------------------------------


def doctor_says(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, machine: str) -> str:
    """Run the doctor's platform section for one fake machine and return what it printed."""
    import io

    from noust.cli.commands import setup as setup_cmd
    from noust.core.logger import Logger

    verdict = platforms.assess(release_of(tmp_path, name), machine, today=TODAY)
    monkeypatch.setattr(platforms, "assess_this_machine", lambda *_a, **_k: verdict)
    stream = io.StringIO()
    warnings = setup_cmd._report_platform(Logger(stream=stream, no_color=True))
    return f"{warnings}\n{stream.getvalue()}"


def test_the_doctor_is_content_with_a_supported_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    said = doctor_says(monkeypatch, tmp_path, "ubuntu2404", "aarch64")

    assert said.startswith("0\n")
    assert "Ubuntu 24.04.3 LTS, arm64: supported" in said


def test_the_doctor_warns_about_a_platform_nobody_tested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    said = doctor_says(monkeypatch, tmp_path, "leap156", "x86_64")

    assert said.startswith("1\n")
    assert "best effort" in said
    assert "docs/PLATFORMS.md" in said


def test_the_doctor_names_a_platform_the_table_does_not_know(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    said = doctor_says(monkeypatch, tmp_path, "arch", "x86_64")

    assert said.startswith("1\n")
    assert "not a platform Noust declares support for" in said


def test_the_doctor_is_not_alarmed_by_a_machine_that_is_pip_only_by_design(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    said = doctor_says(monkeypatch, tmp_path, "rocky95", "x86_64")

    assert said.startswith("0\n")
    assert "installed with pip, there is no package for it" in said


def test_the_installer_installs_the_console_packages_the_code_names() -> None:
    """The RPM families only Suggest them, so the script names them; one list, two spellings."""
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"for name in ([a-z ]+); do\n\s+packages=", text)

    assert match is not None
    assert tuple(match.group(1).split()) == platforms.CONSOLE_PACKAGES


def test_arch_and_alpine_are_refused_with_their_own_reason_and_not_the_generic_one(
    tmp_path: Path,
) -> None:
    arch = platforms.assess(release_of(tmp_path, "arch"), "x86_64", today=TODAY)
    alpine = platforms.assess(release_of(tmp_path, "alpine"), "x86_64", today=TODAY)

    assert arch.status == alpine.status == platforms.UNSUPPORTED
    assert arch.target is not None and "pacman" in arch.target.note
    assert any("no pacman support" in reason for reason in arch.reasons)
    assert alpine.target is not None and "systemd" in alpine.target.note
    assert not any("not a release Noust declares support for" in r for r in arch.reasons)


def test_oracle_linux_is_installed_like_the_rhel_rebuilds(tmp_path: Path) -> None:
    verdict = platforms.assess(release_of(tmp_path, "oracle9"), "x86_64", today=TODAY)

    assert verdict.status == platforms.PIP_ONLY
    assert verdict.installable is True
    assert verdict.target is not None and verdict.target.family == "dnf"
    assert "Rocky and AlmaLinux" in verdict.target.note
