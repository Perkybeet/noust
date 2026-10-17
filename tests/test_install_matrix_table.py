"""
The installer's harness runs every release the platform table has a position on.

``docs/PLATFORMS.md`` says, per release, where Noust was run (``tested`` in
``managers/server/platforms.py``). That column is only true while a case of
``tests/integration/install_matrix.py`` exists for the release, with the status and the
repository the table gives it, and on the architectures it claims: the harness needs Docker and
the network, so it is not part of pytest, and this is what keeps it from drifting from the table.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from noust.managers.server.platforms import (
    INSTALL_AMD64,
    INSTALL_ARM64,
    PIP_ONLY,
    TARGETS,
    UNSUPPORTED,
    Target,
    find_target,
)

MATRIX = Path(__file__).resolve().parent / "integration" / "install_matrix.py"

#: Which ``ID`` of ``/etc/os-release`` an image's name stands for. The images are the
#: distributions' own; this is the one place the harness's names meet the table's.
IMAGE_IDS: dict[str, str] = {
    "debian": "debian",
    "ubuntu": "ubuntu",
    "fedora": "fedora",
    "opensuse/leap": "opensuse-leap",
    "opensuse/tumbleweed": "opensuse-tumbleweed",
    "rockylinux": "rocky",
    "almalinux": "almalinux",
    "redhat/ubi": "rhel",
    "quay.io/centos/centos": "centos",
    "oraclelinux": "ol",
    "archlinux": "arch",
    "alpine": "alpine",
}


def load_matrix() -> ModuleType:
    """
    Returns:
        The harness's module, imported by path (it is a script, not a package).
    """
    spec = importlib.util.spec_from_file_location("noust_it_install_matrix", MATRIX)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def identity_of(image: str) -> tuple[str, str]:
    """
    Args:
        image: A Docker image of the matrix, such as ``rockylinux:9`` or ``redhat/ubi10``.

    Returns:
        The ``ID`` and a ``VERSION_ID`` its ``/etc/os-release`` would say.
    """
    name, _, tag = image.partition(":")
    for prefix in sorted(IMAGE_IDS, key=len, reverse=True):
        if name == prefix or name.startswith(prefix):
            os_id = IMAGE_IDS[prefix]
            break
    else:
        pytest.fail(f"{image} is not an image whose distribution this test knows")
    digits = re.search(r"\d+(?:\.\d+)*", tag or name[len(prefix) :])
    if os_id in ("rocky", "almalinux", "ol", "rhel", "centos") and digits:
        # They say "9.5", never "9".
        return os_id, f"{digits.group(0)}.0"
    return os_id, digits.group(0) if digits else "rolling"


@pytest.fixture(scope="module")
def cases() -> list[tuple[Any, Target]]:
    """The matrix's cases, each with the row of the table its image falls on."""
    matrix = load_matrix()
    found: list[tuple[Any, Target]] = []
    for case in matrix.CASES:
        os_id, version_id = identity_of(case.image)
        target = find_target(os_id, version_id)
        assert target is not None, f"{case.image} ({os_id} {version_id}) falls on no row"
        found.append((case, target))
    return found


def test_every_image_of_the_matrix_says_what_the_table_says(
    cases: list[tuple[Any, Target]],
) -> None:
    """A case's status, repository and whether it installs are the row's."""
    for case, target in cases:
        assert case.status == target.status, f"{case.image}: {case.status} vs {target.status}"
        assert case.repo == (target.repo or "-"), f"{case.image}: repository {case.repo}"
        assert case.installs == (target.status != UNSUPPORTED), case.image


def test_every_row_of_the_table_has_a_case(cases: list[tuple[Any, Target]]) -> None:
    """A release the table has a position on is run by the harness, not left to a claim."""
    covered = {id(target) for _, target in cases}
    missing = [f"{t.os_id} {t.versions}" for t in TARGETS if id(t) not in covered]
    assert not missing, f"rows with no case in install_matrix.py: {missing}"


@pytest.mark.parametrize("claim", [INSTALL_AMD64, INSTALL_ARM64])
def test_what_the_table_says_was_run_has_a_case_on_that_architecture(
    cases: list[tuple[Any, Target]], claim: str
) -> None:
    """``tested`` is a promise: the row's case runs on the architecture it names."""
    platform = "linux/" + claim.split(":")[1]
    for target in TARGETS:
        if claim not in target.tested:
            continue
        runs = [case for case, row in cases if row is target]
        assert any(platform in case.platforms for case in runs), (
            f"{target.os_id} {target.versions} says {claim} but no case runs on {platform}"
        )


def test_a_pip_row_is_run_with_the_pip_method(cases: list[tuple[Any, Target]]) -> None:
    """The RHEL family has no package: its cases expect the pip method, not an apt/dnf one."""
    for case, target in cases:
        if target.status == PIP_ONLY:
            assert case.repo == "-", case.image
