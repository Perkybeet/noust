# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The barrier before a release is announced (checklist N-30, owner items 80 and 81).

OBS builds one repository per distribution and publishes each when its own build ends,
so for a while after a tag some distributions serve the new version and others the old
one. ``noust.managers.server.publication`` reads what the package managers read, and
``scripts/obs_published.py`` waits on it. These tests hold the judgement: which
repositories are required, that a best-effort
release never blocks, and that the release workflow actually runs the gate.
"""

from __future__ import annotations

import gzip
import urllib.error
from pathlib import Path

import pytest

from noust.core import package_index
from noust.managers.server import platforms, publication
from scripts import obs_published

ROOT = Path(__file__).resolve().parent.parent
OBS = platforms.REPOSITORY_BASE


def reader_of(table: dict[tuple[str, str], str | None]):
    """
    Answer :func:`publication.read_package` from a table.

    Args:
        table: ``(repo, package)`` to the version served; absent keys serve nothing.

    Returns:
        A reader.
    """

    def read(family: str, repo: str, package: str) -> str | None:
        return table.get((repo, package))

    return read


def everything_at(version: str) -> dict[tuple[str, str], str | None]:
    """
    A table in which every repository serves the product at one version.

    Args:
        version: The version.

    Returns:
        The table.
    """
    table: dict[tuple[str, str], str | None] = {}
    for state in publication.repositories():
        for package in publication.PACKAGES[state.family]:
            table[(state.repo, package)] = version
    return table


# -- which repositories are asked


def test_the_repositories_come_from_the_table_of_releases_without_repeating_one() -> None:
    names = [state.repo for state in publication.repositories()]

    assert len(names) == len(set(names))
    assert "Debian_12" in names
    assert "xUbuntu_24.04" in names
    assert "Fedora_43" in names
    assert "openSUSE_Tumbleweed" in names
    # Fedora 44 installs from the Fedora 43 repository: one repository, two releases.
    fedora = next(state for state in publication.repositories() if state.repo == "Fedora_43")
    assert any("44" in release for release in fedora.releases)
    assert any("43" in release for release in fedora.releases)


def test_an_unsupported_or_pip_only_release_has_no_repository_to_wait_for() -> None:
    names = {state.repo for state in publication.repositories()}
    for target in platforms.TARGETS:
        if target.status in (platforms.UNSUPPORTED, platforms.PIP_ONLY):
            assert target.repo not in names or target.repo is None


def test_a_repository_is_required_when_a_supported_release_installs_from_it() -> None:
    states = {state.repo: state for state in publication.repositories()}

    assert states["Debian_12"].required
    assert states["Fedora_43"].required
    # Fedora 42 is best-effort and has a repository of its own: reported, never blocking.
    assert not states["Fedora_42"].required


# -- the judgement


def test_a_version_served_everywhere_is_ready() -> None:
    state = publication.check("3.4.0", reader=reader_of(everything_at("3.4.0")))

    assert state.ready
    assert not state.missing
    assert "safe to announce" in state.to_markdown()


def test_one_distribution_still_on_the_previous_version_holds_the_release_back() -> None:
    table = everything_at("3.4.0")
    table[("xUbuntu_24.04", "noust")] = "3.3.1"

    state = publication.check("3.4.0", reader=reader_of(table))

    assert not state.ready
    assert [repo.repo for repo in state.missing] == ["xUbuntu_24.04"]
    assert state.missing[0].lagging("3.4.0") == ["noust (serves 3.3.1)"]
    assert "`xUbuntu_24.04` still wait" in state.to_markdown()


def test_only_the_product_is_judged() -> None:
    assert publication.PACKAGES == {
        "apt": ("noust",),
        "dnf": ("noust",),
        "zypper": ("noust",),
    }


def test_a_repository_that_cannot_be_read_is_waiting_not_published() -> None:
    table = everything_at("3.4.0")
    table[("Debian_13", "noust")] = None

    state = publication.check("3.4.0", reader=reader_of(table))

    assert not state.ready
    assert "Debian_13" in {repo.repo for repo in state.missing}


def test_a_best_effort_release_that_lags_is_reported_but_never_blocks() -> None:
    table = everything_at("3.4.0")
    table[("Fedora_42", "noust")] = "3.3.1"

    state = publication.check("3.4.0", reader=reader_of(table))

    assert state.ready
    assert [repo.repo for repo in state.late] == ["Fedora_42"]
    assert "late (best effort, not blocking)" in state.to_markdown()


def test_a_newer_version_than_asked_is_not_the_version_asked() -> None:
    state = publication.check("3.4.0", reader=reader_of(everything_at("3.4.1")))

    assert not state.ready


# -- reading the real indexes


def serve(monkeypatch: pytest.MonkeyPatch, table: dict[str, bytes]) -> list[str]:
    asked: list[str] = []

    def fetch(url: str, **kwargs: object) -> bytes:
        asked.append(url)
        if url not in table:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        return table[url]

    monkeypatch.setattr(package_index, "fetch", fetch)
    return asked


PACKAGES_INDEX = (
    "Package: noust\nVersion: 3.4.0-1\nArchitecture: all\n\n"
    "Package: other\nVersion: 1.0-1\nArchitecture: all\n"
)


def test_a_deb_repository_is_read_from_its_flat_packages_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = serve(
        monkeypatch,
        {f"{OBS}/Debian_12/Packages.gz": gzip.compress(PACKAGES_INDEX.encode())},
    )

    assert publication.read_package("apt", "Debian_12", "noust") == "3.4.0"
    assert publication.read_package("apt", "Debian_12", "other") == "1.0"
    assert asked[0] == f"{OBS}/Debian_12/Packages.gz"


def test_a_deb_repository_falls_back_to_the_uncompressed_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, {f"{OBS}/Debian_12/Packages": PACKAGES_INDEX.encode()})

    assert publication.read_package("apt", "Debian_12", "noust") == "3.4.0"


def test_an_absent_index_is_none_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, {})

    assert publication.read_package("apt", "Debian_12", "noust") is None
    assert publication.read_package("dnf", "Fedora_43", "noust") is None


def test_an_rpm_repository_names_the_package_it_is_asked_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repomd = (
        b'<?xml version="1.0"?><repomd xmlns="http://linux.duke.edu/metadata/repo">'
        b'<data type="primary"><location href="repodata/primary.xml.gz"/></data></repomd>'
    )
    primary = (
        b'<?xml version="1.0"?><metadata xmlns="http://linux.duke.edu/metadata/common">'
        b'<package><name>noust</name><version ver="3.4.0" rel="1"/></package>'
        b'<package><name>other</name><version ver="1.0" rel="1"/></package></metadata>'
    )
    serve(
        monkeypatch,
        {
            f"{OBS}/Fedora_43/repodata/repomd.xml": repomd,
            f"{OBS}/Fedora_43/repodata/primary.xml.gz": gzip.compress(primary),
        },
    )

    assert publication.read_package("dnf", "Fedora_43", "noust") == "3.4.0"
    assert publication.read_package("dnf", "Fedora_43", "other") == "1.0"


# -- the script that waits


def test_a_tag_and_a_version_are_both_accepted() -> None:
    assert obs_published.parse_version("v3.4.0") == "3.4.0"
    assert obs_published.parse_version("3.4.0") == "3.4.0"


@pytest.mark.parametrize("text", ["", "latest", "v3", "3.x.1", "main"])
def test_anything_else_is_a_usage_error(text: str) -> None:
    with pytest.raises(ValueError, match="is not a version"):
        obs_published.parse_version(text)


class Clock:
    """A clock that only moves when the script sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_it_polls_until_the_last_distribution_has_the_version() -> None:
    clock = Clock()
    looks = {"n": 0}
    behind = everything_at("3.4.0")
    behind[("xUbuntu_22.04", "noust")] = "3.3.1"

    def check(version: str) -> publication.Publication:
        looks["n"] += 1
        return publication.check(
            version, reader=reader_of(behind if looks["n"] < 3 else everything_at("3.4.0"))
        )

    lines: list[str] = []
    state = obs_published.run(
        "3.4.0",
        wait_minutes=60,
        interval=120,
        check=check,
        sleep=clock.sleep,
        clock=clock,
        out=lines.append,
    )

    assert state.ready
    assert looks["n"] == 3
    assert clock.slept == [120, 120]
    assert any("waiting: xUbuntu_22.04" in line for line in lines)


def test_it_gives_up_when_the_wait_is_over_and_returns_what_it_last_saw() -> None:
    clock = Clock()
    behind = everything_at("3.4.0")
    behind[("Debian_13", "noust")] = "3.3.1"

    state = obs_published.run(
        "3.4.0",
        wait_minutes=5,
        interval=120,
        check=lambda v: publication.check(v, reader=reader_of(behind)),
        sleep=clock.sleep,
        clock=clock,
        out=lambda line: None,
    )

    assert not state.ready
    assert sum(clock.slept) <= 5 * 60


def test_it_asks_once_when_it_is_not_told_to_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    looks = {"n": 0}
    real = publication.check

    def check(version: str) -> publication.Publication:
        looks["n"] += 1
        return real(version, reader=reader_of({}))

    monkeypatch.setattr(publication, "check", check)

    assert obs_published.main(["v3.4.0"], env={}) == 1
    assert looks["n"] == 1


def test_the_exit_status_says_whether_it_is_safe_to_announce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setattr(
        publication, "check", lambda v: publication.Publication(v, tuple(_published(v)))
    )

    assert obs_published.main(["3.4.0"], env={"GITHUB_STEP_SUMMARY": str(summary)}) == 0
    assert "safe to announce" in summary.read_text()
    assert "safe to announce" in capsys.readouterr().out


def _published(version: str) -> list[publication.RepositoryState]:
    table = everything_at(version)
    return [
        publication.RepositoryState(
            state.repo,
            state.family,
            state.releases,
            state.required,
            {name: table[(state.repo, name)] for name in publication.PACKAGES[state.family]},
        )
        for state in publication.repositories()
    ]


def test_a_malformed_version_is_exit_status_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert obs_published.main(["next"], env={}) == 2
    assert "is not a version" in capsys.readouterr().err


# -- the workflow actually runs it


def test_the_release_workflow_waits_for_every_distribution_before_it_says_it_is_complete() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["obs-published"]

    assert "publish-obs" in job["needs"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "scripts/obs_published.py" in commands
    assert "--wait" in commands
