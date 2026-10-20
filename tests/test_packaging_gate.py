"""
The packages refuse to put 4.0 over a server whose data 3.x never moved.

``noust`` itself refuses to start on such a server (``noust.core.retired_names``), but a
package manager does not ask it: ``apt`` or ``dnf`` would unpack 4.0, delete the commands the
old units run and leave the data under names nothing reads. So the package's own pre-install
step, which runs before anything is unpacked, looks for the same two files and aborts the
install with the way out. These tests run that step against a directory standing in for ``/``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PREINST = ROOT / "obs" / "debian.preinst"
SPEC = ROOT / "rpm" / "noust.spec"

OLD = "wasm"


def deb_gate() -> str:
    """The Debian pre-install step, as a script that can be pointed at another root."""
    return PREINST.read_text(encoding="utf-8")


def rpm_gate() -> str:
    """The body of the spec's ``%pre``, as a script."""
    text = SPEC.read_text(encoding="utf-8")
    match = re.search(r"^%pre\n(.*?)^%post\b", text, re.MULTILINE | re.DOTALL)
    assert match is not None, "the spec has no %pre"
    return "#!/bin/sh\n" + match.group(1).replace("$1", "1")


def run(script: str, root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """
    Run a gate with every absolute path moved under ``root``.

    Args:
        script: The script's text.
        root: The directory that stands in for ``/``.
        tmp_path: Where the rewritten script is put.
        *args: The arguments dpkg would pass.

    Returns:
        The finished process.
    """
    rewritten = re.sub(r"(?<![\w/.])/(etc|var)/", f"{root}/\\1/", script)
    path = tmp_path / "gate.sh"
    path.write_text(rewritten, encoding="utf-8")
    return subprocess.run(
        ["/bin/sh", str(path), *args], capture_output=True, text=True, timeout=30, check=False
    )


@pytest.fixture(params=["deb", "rpm"])
def gate(request: pytest.FixtureRequest) -> tuple[str, tuple[str, ...]]:
    """Each package's gate and the arguments its manager passes on an upgrade."""
    if request.param == "deb":
        return deb_gate(), ("upgrade", "3.3.1-1")
    return rpm_gate(), ()


pytestmark = pytest.mark.allow_subprocess


def test_a_server_that_never_had_the_old_names_installs(
    gate: tuple[str, tuple[str, ...]], tmp_path: Path
) -> None:
    script, args = gate
    root = tmp_path / "root"
    (root / "etc/noust").mkdir(parents=True)

    assert run(script, root, tmp_path, *args).returncode == 0


def test_a_moved_server_installs(gate: tuple[str, tuple[str, ...]], tmp_path: Path) -> None:
    script, args = gate
    root = tmp_path / "root"
    (root / "etc/noust").mkdir(parents=True)
    (root / "etc/noust/config.yaml").write_text("x: 1\n")
    (root / "var/lib/noust").mkdir(parents=True)
    (root / f"etc/{OLD}").symlink_to(root / "etc/noust")
    (root / f"var/lib/{OLD}").symlink_to(root / "var/lib/noust")

    assert run(script, root, tmp_path, *args).returncode == 0


@pytest.mark.parametrize(
    ("directory", "marker"), [(f"etc/{OLD}", "config.yaml"), (f"var/lib/{OLD}", f"{OLD}.db")]
)
def test_data_still_under_the_old_names_stops_the_install(
    gate: tuple[str, tuple[str, ...]], tmp_path: Path, directory: str, marker: str
) -> None:
    script, args = gate
    root = tmp_path / "root"
    (root / directory).mkdir(parents=True)
    (root / directory / marker).write_text("data")

    result = run(script, root, tmp_path, *args)

    assert result.returncode != 0
    assert "3.3.1" in result.stderr
    assert str(root / directory) in result.stderr


def test_a_directory_of_something_else_does_not_stop_the_install(
    gate: tuple[str, tuple[str, ...]], tmp_path: Path
) -> None:
    """The name is also WebAssembly's."""
    script, args = gate
    root = tmp_path / "root"
    (root / f"var/lib/{OLD}/modules").mkdir(parents=True)

    assert run(script, root, tmp_path, *args).returncode == 0


def test_the_deb_gate_lets_dpkg_abort_and_ask_without_failing(tmp_path: Path) -> None:
    """``abort-upgrade`` is dpkg undoing an install: it must never fail."""
    root = tmp_path / "root"
    (root / f"etc/{OLD}").mkdir(parents=True)
    (root / f"etc/{OLD}/config.yaml").write_text("data")

    assert run(deb_gate(), root, tmp_path, "abort-upgrade", "3.3.1-1").returncode == 0
