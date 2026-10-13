# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What changed in each release, inside the product (checklist N-24).

The Debian changelog is the one place a release's summary is written. It becomes the packaged
``changelog.json`` (what the console shows of the releases this installation has) and the GitHub
release's text (what it shows of a release it does not have yet, before the operator updates).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from noust.core import changelog
from noust.core.changelog import ACTION_MARKER, Entry, Item

ROOT = Path(__file__).resolve().parent.parent

DEBIAN = """\
noust (3.4.0-1) unstable; urgency=medium

  * First line of the release
  * Action required: run noust setup doctor after updating, then restart the console
  * A line that continues
    over a second line

 -- Yago Lopez Prado <yago.lopez.adeje@gmail.com>  Tue, 13 Oct 2026 10:00:00 +0000

wasm (3.4.0-1) unstable; urgency=medium

  * The transitional package, same version

 -- Yago Lopez Prado <yago.lopez.adeje@gmail.com>  Tue, 13 Oct 2026 10:00:00 +0000

noust (3.3.1-1) unstable; urgency=medium

  * Sudo mode lasts while you work

 -- Yago Lopez Prado <yago.lopez.adeje@gmail.com>  Mon, 05 Oct 2026 13:08:48 +0000

wasm (2.3.0-1) unstable; urgency=medium

  * An entry from before the rename

 -- Yago Lopez Prado <yago.lopez.adeje@gmail.com>  Sat, 28 Sep 2026 09:00:00 +0000
"""


def test_each_release_is_an_entry_with_its_day_and_its_lines() -> None:
    entries = changelog.parse_debian(DEBIAN)

    assert [(e.version, e.date) for e in entries] == [
        ("3.4.0", "2026-10-13"),
        ("3.3.1", "2026-10-05"),
        ("2.3.0", "2026-09-28"),
    ]
    assert [i.text for i in entries[0].items] == [
        "First line of the release",
        "run noust setup doctor after updating, then restart the console",
        "A line that continues over a second line",
    ]


def test_a_line_that_starts_with_the_marker_is_flagged_and_loses_it() -> None:
    entry = changelog.parse_debian(DEBIAN)[0]

    assert [i.action_required for i in entry.items] == [False, True, False]
    assert entry.action_required
    assert not changelog.parse_debian(DEBIAN)[1].action_required
    assert ACTION_MARKER == "Action required:"


def test_the_marker_is_read_without_regard_to_case() -> None:
    text = DEBIAN.replace("Action required:", "ACTION REQUIRED:")

    assert changelog.parse_debian(text)[0].items[1].action_required


def test_the_transitional_package_is_not_a_second_release() -> None:
    versions = [e.version for e in changelog.parse_debian(DEBIAN)]

    assert versions.count("3.4.0") == 1
    assert "The transitional package, same version" not in [
        i.text for e in changelog.parse_debian(DEBIAN) for i in e.items
    ]


def test_a_date_that_cannot_be_read_is_empty_not_an_error() -> None:
    text = DEBIAN.replace("Tue, 13 Oct 2026 10:00:00 +0000", "yesterday")

    assert changelog.parse_debian(text)[0].date == ""


def test_text_that_is_not_a_changelog_has_no_entries() -> None:
    assert changelog.parse_debian("nothing here\n") == []
    assert changelog.parse_debian("") == []


# -- a release that is not installed yet


BODY = """\
## Changes in v3.4.0

* First line of the release
* Action required: run noust setup doctor after updating
* A line that continues
  over a second line

## Installation

### One line, on a server
* not a change
"""


def test_the_notes_of_a_github_release_are_the_lines_under_its_changes_heading() -> None:
    entry = changelog.parse_release_body(BODY, "3.4.0")

    assert entry.version == "3.4.0"
    assert [i.text for i in entry.items] == [
        "First line of the release",
        "run noust setup doctor after updating",
        "A line that continues over a second line",
    ]
    assert entry.items[1].action_required


def test_a_release_description_without_the_section_has_no_lines() -> None:
    assert changelog.parse_release_body("Just some text.\n", "3.4.0").items == ()


def test_the_notes_stop_at_the_next_heading() -> None:
    entry = changelog.parse_release_body(BODY, "3.4.0")

    assert "not a change" not in [i.text for i in entry.items]


# -- ordering


def test_versions_are_ordered_as_numbers() -> None:
    entries = [Entry("3.9.0"), Entry("3.10.0"), Entry("3.2.1")]

    assert [e.version for e in changelog.since(entries, "3.2.1")] == ["3.10.0", "3.9.0"]
    assert changelog.since(entries, "3.10.0") == []


# -- the packaged file


def test_the_packaged_file_is_what_the_debian_changelog_says() -> None:
    wanted = changelog.render_from_debian((ROOT / "obs/debian.changelog").read_text())

    assert (ROOT / "src/noust/changelog.json").read_text() == wanted, (
        "run scripts/release.py --sync-changelog"
    )


def test_the_packaged_entries_load_newest_first() -> None:
    entries = changelog.packaged()

    assert entries
    keys = [changelog.version_key(e.version) for e in entries]
    assert keys == sorted(keys, reverse=True)
    assert all(e.items for e in entries)


def test_only_the_newest_entries_are_kept() -> None:
    many = [
        Entry(f"3.{n}.0", "2026-10-01", (Item("x"),)) for n in range(changelog.KEEP + 20, 0, -1)
    ]

    document = json.loads(changelog.render(many))

    assert len(document["entries"]) == changelog.KEEP
    assert document["entries"][0]["version"] == f"3.{changelog.KEEP + 20}.0"


def test_a_missing_or_broken_file_is_an_empty_history_not_a_failed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        def joinpath(self, name: str) -> Broken:
            return self

        def read_text(self, encoding: str = "utf-8") -> str:
            return "{not json"

    monkeypatch.setattr(changelog.resources, "files", lambda package: Broken())

    assert changelog.packaged() == []


# -- the release script writes it


@pytest.fixture(scope="module")
def release_script():
    spec = importlib.util.spec_from_file_location(
        "release_script_changelog", ROOT / "scripts/release.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_script_changelog"] = module
    spec.loader.exec_module(module)
    return module


def test_the_release_script_checks_the_packaged_file(release_script, capsys) -> None:
    assert release_script.packaged_changelog() == (ROOT / "src/noust/changelog.json").read_text()


def test_the_release_script_calls_a_stale_packaged_file_a_problem(
    release_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(release_script, "ROOT", tmp_path)
    (tmp_path / "obs").mkdir()
    (tmp_path / "obs/debian.changelog").write_text(DEBIAN)
    (tmp_path / "src/noust").mkdir(parents=True)
    (tmp_path / "src/noust/changelog.json").write_text("{}\n")

    assert release_script.sync_packaged_changelog() is True
    assert release_script.sync_packaged_changelog() is False
    assert (
        json.loads((tmp_path / "src/noust/changelog.json").read_text())["entries"][0]["version"]
        == "3.4.0"
    )
