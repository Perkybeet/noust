"""
Tests for the release script.

This is the one script that must not break at release time, and it did: the
replacement templates used ``\\1`` back-references, so setting version 1.0.0
produced ``\\1`` followed by ``1`` and the regex engine read it as group 11.
The failure only appears for versions starting with a digit that follows a
back-reference, which is to say the first major release.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_script", Path(__file__).parent.parent / "scripts/release.py"
)
release = importlib.util.module_from_spec(SPEC)
# Registered before execution: the module defines a dataclass, and dataclasses
# resolves its annotations through sys.modules at class creation time.
sys.modules["release_script"] = release
SPEC.loader.exec_module(release)


SECURITY = """# Security policy

## Supported versions

| Version | Security fixes |
|---|---|
{rows}

Fixes are released as a patch version of each supported line.

Contact: security@example.org, also in `.well-known/security.txt`.

## Reporting a vulnerability
"""

#: The table of a repository whose current line is 0.15 (the fixture's version).
SUPPORTED_ROWS = """| 0.15.x | Yes |
| 0.14.x | Yes, until 0.16.0 is released |
| 0.13.x and earlier, WASM 2.x included | No: upgrade to a supported line |"""

SECURITY_TXT = (
    "Contact: mailto:security@example.org\n"
    "Expires: 2027-02-01T00:00:00.000Z\n"
    f"Policy: {release.POLICY_URL}\n"
    "Canonical: https://example.org/.well-known/security.txt\n"
)

DEBIAN_ENTRY = (
    "{package} (0.15.8-1) unstable; urgency=medium\n\n  * Something\n\n"
    " -- A B <a@b>  Fri, 20 Mar 2026 14:00:00 +0000\n"
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """
    Build a miniature repository with every file the script rewrites.

    The product's Debian changelog still has its last entry under the old
    name, wasm, as the real one does until the first release as noust.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The repository root.
    """
    for directory in (
        "src/noust",
        "rpm",
        "obs",
        "packaging/transitional/wasm",
        "packaging/transitional/wasm-cli",
        "packaging/container",
    ):
        (tmp_path / directory).mkdir(parents=True)

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "noust"\nversion = "0.15.8"\n')
    (tmp_path / "setup.py").write_text('setup(\n    version="0.15.8",\n)\n')
    (tmp_path / "src/noust/__init__.py").write_text('_FALLBACK_VERSION = "0.15.8"\n')
    (tmp_path / "rpm/noust.spec").write_text(
        "Name:           noust\nVersion:        0.15.8\n%changelog\n"
    )
    (tmp_path / "obs/noust.dsc").write_text(
        "Version: 0.15.8-1\nFiles:\n 00000000000000000000000000000000 0 noust-0.15.8.tar.gz\n"
    )
    (tmp_path / "obs/debian.changelog").write_text(DEBIAN_ENTRY.format(package="wasm"))

    transitional = tmp_path / "packaging/transitional/wasm"
    (transitional / "wasm.spec").write_text(
        "Name:           wasm-cli\nVersion:        0.15.8\nRequires:       noust >= %{version}\n"
        "%changelog\n"
    )
    (transitional / "wasm.dsc").write_text(
        "Version: 0.15.8-1\nFiles:\n 00000000000000000000000000000000 0 wasm-0.15.8.tar.gz\n"
    )
    (transitional / "debian.changelog").write_text(DEBIAN_ENTRY.format(package="wasm"))
    (tmp_path / "packaging/transitional/wasm-cli/pyproject.toml").write_text(
        '[project]\nname = "wasm-cli"\nversion = "0.15.8"\ndependencies = [\n    "noust==0.15.8",\n]\n'
    )
    (tmp_path / "packaging/container/Dockerfile").write_text(
        "FROM python:3.12-slim-bookworm\nARG NOUST_VERSION=0.15.8\n"
    )
    (tmp_path / "packaging/container/compose.yaml").write_text(
        "services:\n  noust:\n    image: ghcr.io/perkybeet/noust:0.15.8\n"
    )

    (tmp_path / "SECURITY.md").write_text(SECURITY.format(rows=SUPPORTED_ROWS))
    (tmp_path / ".well-known").mkdir()
    (tmp_path / ".well-known/security.txt").write_text(SECURITY_TXT)

    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "_current_tag", lambda: None)
    # The fixture's security.txt is valid on this day, whatever day the suite runs.
    monkeypatch.setattr(release, "_today", lambda: date(2026, 10, 13))
    return tmp_path


@pytest.mark.parametrize("version", ["1.0.0", "1.2.3", "10.0.0", "0.16.0", "2.11.11"])
def test_every_file_gets_the_version(repo, version):
    """A version starting with a digit must not be read as a group reference."""
    assert release.bump(version, ["Released"]) == 0

    assert f'version = "{version}"' in (repo / "pyproject.toml").read_text()
    assert f'version="{version}"' in (repo / "setup.py").read_text()
    assert f'_FALLBACK_VERSION = "{version}"' in (repo / "src/noust/__init__.py").read_text()
    assert f"Version:        {version}" in (repo / "rpm/noust.spec").read_text()

    dsc = (repo / "obs/noust.dsc").read_text()
    assert f"Version: {version}-1" in dsc
    assert f"noust-{version}.tar.gz" in dsc


@pytest.mark.parametrize("version", ["1.0.0", "3.0.0", "10.0.0"])
def test_the_transitional_packages_and_the_image_follow(repo, version):
    """
    The transitional packages move with every release.

    wasm-cli on PyPI pins noust to its own version, so a transitional package
    left behind would hold `pip install -U wasm-cli` on an old noust.
    """
    assert release.bump(version, ["Released"]) == 0

    transitional = repo / "packaging/transitional/wasm"
    assert f"Version:        {version}" in (transitional / "wasm.spec").read_text()
    dsc = (transitional / "wasm.dsc").read_text()
    assert f"Version: {version}-1" in dsc
    assert f"wasm-{version}.tar.gz" in dsc

    pypi = (repo / "packaging/transitional/wasm-cli/pyproject.toml").read_text()
    assert f'version = "{version}"' in pypi
    assert f'    "noust=={version}",' in pypi

    assert f"ARG NOUST_VERSION={version}" in (repo / "packaging/container/Dockerfile").read_text()
    assert (
        f"image: ghcr.io/perkybeet/noust:{version}"
        in (repo / "packaging/container/compose.yaml").read_text()
    )


def test_the_indentation_of_setup_py_survives(repo):
    """The captured group is the indentation, and it has to come back."""
    release.bump("1.0.0", ["Released"])

    assert '    version="1.0.0",' in (repo / "setup.py").read_text()


def test_the_check_passes_after_a_bump(repo):
    """Whatever bump writes, check has to accept."""
    release.bump("1.0.0", ["Something changed"])

    assert release.check() == 0


def test_the_check_fails_on_drift(repo):
    """A file left behind is the failure this gate exists for."""
    release.bump("1.0.0", ["Something changed"])
    (repo / "setup.py").write_text('setup(\n    version="0.9.9",\n)\n')

    assert release.check() == 1


@pytest.mark.parametrize(
    "path",
    [
        "packaging/transitional/wasm-cli/pyproject.toml",
        "packaging/transitional/wasm/wasm.dsc",
        "packaging/container/Dockerfile",
    ],
)
def test_the_check_fails_when_a_transitional_file_drifts(repo, path):
    """The files added for the rename are held to the same rule as the rest."""
    release.bump("1.0.0", ["Something changed"])
    file = repo / path
    file.write_text(file.read_text().replace("1.0.0", "0.9.9"))

    assert release.check() == 1


def test_a_changelog_entry_lands_at_the_top(repo):
    """Debian reads the newest entry first."""
    release.bump("1.0.0", ["First thing", "Second thing"])

    first_line = (repo / "obs/debian.changelog").read_text().splitlines()[0]
    assert first_line.startswith("noust (1.0.0-1)")
    assert "First thing" in (repo / "obs/debian.changelog").read_text()
    assert "- First thing" in (repo / "rpm/noust.spec").read_text()


def test_the_first_release_under_the_new_name_says_so(repo):
    """
    The entry that follows one written as wasm opens with the rename, in both
    changelogs; the one after that does not repeat it.
    """
    release.bump("3.0.0", ["Fleet"])

    debian = (repo / "obs/debian.changelog").read_text()
    entry = debian.split("\n -- ", 1)[0]
    assert entry.index("Renamed from wasm") < entry.index("Fleet")
    assert "- Renamed from wasm" in (repo / "rpm/noust.spec").read_text()

    release.bump("3.0.1", ["A fix"])

    newest = (repo / "obs/debian.changelog").read_text().split("\n -- ", 1)[0]
    assert newest.startswith("noust (3.0.1-1)")
    assert "Renamed from wasm" not in newest


def test_the_transitional_changelogs_are_written_for_every_release(repo):
    """The transitional packages get their own entry, under their own names."""
    release.bump("3.0.0", ["Fleet"])

    transitional = repo / "packaging/transitional/wasm"
    debian = (transitional / "debian.changelog").read_text()
    assert debian.startswith("wasm (3.0.0-1)")
    assert "installs noust 3.0.0" in debian.split("\n -- ", 1)[0]
    assert "Fleet" not in debian
    spec = (transitional / "wasm.spec").read_text()
    assert "- 3.0.0-1\n- WASM is now Noust" in spec


def test_a_version_that_is_not_semver_is_refused(repo):
    """A tag like 'v1.0' would produce packages nobody can order."""
    assert release.bump("1.0", ["Released"]) == 1
    assert 'version = "0.15.8"' in (repo / "pyproject.toml").read_text()


# ---------------------------------------------------------------------------
# SECURITY.md lists the version being released as supported (checklist L-40)
# ---------------------------------------------------------------------------


def table(repo):
    """The three kinds of rows of the fixture's SECURITY.md, as written now."""
    return [
        line
        for line in (repo / "SECURITY.md").read_text().splitlines()
        if line.startswith("| ") and not line.startswith("| Version")
    ]


def test_a_patch_release_leaves_the_table_alone(repo):
    before = (repo / "SECURITY.md").read_text()

    assert release.bump("0.15.9", ["A fix"]) == 0

    assert (repo / "SECURITY.md").read_text() == before


def test_a_minor_release_adds_its_line_and_moves_the_two_before_it(repo):
    assert release.bump("0.16.0", ["A feature"]) == 0

    assert table(repo) == [
        "| 0.16.x | Yes |",
        "| 0.15.x | Yes, until 0.17.0 is released |",
        "| 0.14.x and earlier, WASM 2.x included | No: upgrade to a supported line |",
    ]


def test_a_major_release_does_the_same(repo):
    assert release.bump("1.0.0", ["A new major"]) == 0

    assert table(repo) == [
        "| 1.0.x | Yes |",
        "| 0.15.x | Yes, until 1.1.0 is released |",
        "| 0.14.x and earlier, WASM 2.x included | No: upgrade to a supported line |",
    ]


def test_the_text_around_the_table_is_untouched(repo):
    release.bump("0.16.0", ["A feature"])

    text = (repo / "SECURITY.md").read_text()
    assert text.startswith("# Security policy\n\n## Supported versions\n\n| Version |")
    assert "Fixes are released as a patch version of each supported line." in text
    assert text.endswith("## Reporting a vulnerability\n")


def test_two_minor_releases_in_a_row_keep_two_lines(repo):
    release.bump("0.16.0", ["A feature"])
    release.bump("0.17.0", ["Another"])

    assert table(repo) == [
        "| 0.17.x | Yes |",
        "| 0.16.x | Yes, until 0.18.0 is released |",
        "| 0.15.x and earlier, WASM 2.x included | No: upgrade to a supported line |",
    ]


def test_a_release_with_no_table_row_to_retire_is_fine(repo):
    (repo / "SECURITY.md").write_text(SECURITY.format(rows="| 0.15.x | Yes |"))

    assert release.bump("0.16.0", ["A feature"]) == 0

    assert table(repo) == ["| 0.16.x | Yes |", "| 0.15.x | Yes, until 0.17.0 is released |"]


def released(repo):
    """
    Make the fixture a consistent release, so what a check reports is SECURITY.md's.

    Args:
        repo: The fixture's root.
    """
    assert release.bump("0.15.8", ["Released"]) == 0
    assert release.check() == 0


def test_the_check_passes_on_a_table_that_lists_the_version(repo):
    released(repo)

    assert release.check() == 0


def test_the_check_fails_when_the_table_does_not_list_the_version(repo, capsys):
    released(repo)
    (repo / "SECURITY.md").write_text(
        SECURITY.format(rows="| 0.14.x | Yes |\n| 0.13.x | Yes, until 0.15.0 is released |")
    )

    assert release.check() == 1

    output = capsys.readouterr().out
    assert "SECURITY.md" in output
    assert "0.15.x" in output
    assert "supported" in output


def test_the_check_fails_when_the_line_is_listed_as_unsupported(repo, capsys):
    released(repo)
    (repo / "SECURITY.md").write_text(
        SECURITY.format(rows="| 0.16.x | Yes |\n| 0.15.x | No: upgrade |")
    )

    assert release.check() == 1

    assert "0.15.x" in capsys.readouterr().out


def test_the_check_fails_when_an_until_has_passed(repo, capsys):
    """'Yes, until 0.15.0 is released' is not true of 0.15.1."""
    released(repo)
    (repo / "SECURITY.md").write_text(
        SECURITY.format(rows="| 0.16.x | Yes |\n| 0.15.x | Yes, until 0.15.0 is released |")
    )

    assert release.check() == 1

    assert "until 0.15.0" in capsys.readouterr().out


def test_the_check_fails_without_the_file(repo, capsys):
    released(repo)
    (repo / "SECURITY.md").unlink()

    assert release.check() == 1

    assert "SECURITY.md" in capsys.readouterr().out


def test_the_check_accepts_a_line_that_is_supported_until_a_later_release(repo):
    released(repo)
    (repo / "SECURITY.md").write_text(
        SECURITY.format(rows="| 0.16.x | Yes |\n| 0.15.x | Yes, until 0.17.0 is released |")
    )

    assert release.check() == 0


def test_a_bump_then_the_check_agree_for_a_run_of_releases(repo):
    for version in ("0.15.9", "0.16.0", "0.16.1", "0.17.0", "1.0.0", "1.0.1", "1.1.0"):
        assert release.bump(version, ["x"]) == 0, version
        assert release.check() == 0, version


def test_the_real_security_policy_lists_the_current_line():
    """The repository's own SECURITY.md, read as the gate reads it."""
    assert release.security_problem(release.source_of_truth()) is None


def test_the_check_names_a_version_it_cannot_place_instead_of_crashing(repo, capsys):
    (repo / "pyproject.toml").write_text('[project]\nname = "noust"\nversion = "one"\n')

    assert release.check() == 1

    assert "SECURITY.md: cannot tell which line 'one' belongs to" in capsys.readouterr().out
