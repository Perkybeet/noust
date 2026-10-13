# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A release that fixes a vulnerability says so, and an installation that is
behind it finds out (checklist L-43).

Coolify's advisories were published a median of 84 days after the fix, with
silent fixes among them. What Noust does instead is mark the release (its title
ends in ``(security release)``, which ``release.yml`` writes when a changelog
bullet starts with ``SECURITY:``), and have the update check, the CLI banner,
``noust health`` and the console say it plainly, with the advisory when there is
one.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from noust import __version__
from noust.core import package_index
from noust.core.package_index import SecurityRelease
from noust.core.update_checker import UpdateChecker, VersionCheck


def release(tag: str, name: str, body: str = "", **flags: bool) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "name": name,
        "body": body,
        "draft": False,
        "prerelease": False,
        **flags,
    }


def serve_releases(monkeypatch: pytest.MonkeyPatch, releases: Any) -> list[str]:
    """Answer GitHub's list of releases; return the URLs asked."""
    asked: list[str] = []

    def fetch(url: str, **kwargs: object) -> bytes:
        asked.append(url)
        if isinstance(releases, Exception):
            raise releases
        return json.dumps(releases).encode() if not isinstance(releases, bytes) else releases

    monkeypatch.setattr(package_index, "fetch", fetch)
    return asked


class TestTheMarker:
    @pytest.mark.parametrize(
        "name",
        [
            "Noust v3.3.2 (security release)",
            "Noust v3.3.2 (Security Release)",
            "v3.3.2 (security release) ",
        ],
    )
    def test_a_title_ending_in_security_release_is_one(self, name: str) -> None:
        assert package_index.is_security_release(name)

    @pytest.mark.parametrize(
        "name",
        ["Noust v3.3.2", "Noust security release notes v3.3.2", "", None, 7],
    )
    def test_nothing_else_is(self, name: object) -> None:
        assert not package_index.is_security_release(name)


class TestReadingTheReleases:
    def test_the_flagged_ones_are_returned_with_their_advisory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = serve_releases(
            monkeypatch,
            [
                release("v3.4.1", "Noust v3.4.1"),
                release(
                    "v3.4.0",
                    "Noust v3.4.0 (security release)",
                    "Fixes GHSA-abcd-1234-wxyz and a second thing.",
                ),
                release("v3.3.9", "Noust v3.3.9 (security release)"),
            ],
        )

        found = package_index.github_security_releases()

        assert found == [
            SecurityRelease("3.4.0", "GHSA-abcd-1234-wxyz"),
            SecurityRelease("3.3.9", None),
        ]
        assert asked and asked[0].startswith(
            "https://api.github.com/repos/Perkybeet/noust/releases"
        )

    def test_a_draft_and_a_prerelease_are_not_a_release_anyone_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serve_releases(
            monkeypatch,
            [
                release("v9.0.0", "Noust v9.0.0 (security release)", draft=True),
                release("v8.0.0", "Noust v8.0.0 (security release)", prerelease=True),
            ],
        )

        assert package_index.github_security_releases() == []

    @pytest.mark.parametrize(
        "answer",
        [OSError("no route"), b"not json", b'{"message": "rate limited"}', b'[1, "x", null]'],
    )
    def test_whatever_cannot_be_read_is_nothing_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch, answer: object
    ) -> None:
        serve_releases(monkeypatch, answer)

        assert package_index.github_security_releases() == []

    def test_a_tag_that_is_not_a_version_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serve_releases(monkeypatch, [release("nightly", "Nightly (security release)")])

        assert package_index.github_security_releases() == []


def checked(**fields: Any) -> VersionCheck:
    base = {
        "current": "3.3.1",
        "location": "/x",
        "method": "pip",
        "installable": "3.4.1",
        "published": "3.4.1",
        "checked_at": time.time(),
    }
    return VersionCheck(**{**base, **fields})


class TestTheCheck:
    def test_a_check_without_a_security_release_is_not_one(self) -> None:
        assert not checked().security_update

    def test_a_newer_security_release_is_one(self) -> None:
        check = checked(security="3.4.0", advisory="GHSA-abcd-1234-wxyz")

        assert check.security_update
        assert check.advisory_url == (
            "https://github.com/Perkybeet/noust/security/advisories/GHSA-abcd-1234-wxyz"
        )

    def test_one_the_installation_already_runs_is_not(self) -> None:
        assert not checked(current="3.4.0", security="3.4.0").security_update
        assert not checked(current="3.5.0", security="3.4.0").security_update

    def test_without_an_advisory_the_release_notes_are_pointed_at(self) -> None:
        check = checked(security="3.4.0")

        assert check.advisory_url == "https://github.com/Perkybeet/noust/releases/tag/v3.4.0"

    def test_it_survives_the_cache(self) -> None:
        check = checked(security="3.4.0", advisory="GHSA-abcd-1234-wxyz")

        assert VersionCheck.from_cache(check.to_cache()) == check

    def test_a_cache_from_before_the_fields_existed_still_reads(self) -> None:
        data = checked().to_cache()
        del data["security"], data["advisory"]

        restored = VersionCheck.from_cache(data)

        assert restored is not None and restored.security is None

    def test_a_cache_with_the_wrong_type_is_no_cache(self) -> None:
        data = checked().to_cache()
        data["security"] = 3

        assert VersionCheck.from_cache(data) is None


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[dict[str, Any]]:
    """A check whose network is a dictionary."""
    monkeypatch.setattr(UpdateChecker, "CACHE_FILE", tmp_path / "update-check.json")
    state: dict[str, Any] = {"published": "99.0.0", "security": [], "asked": 0}
    monkeypatch.setattr(
        UpdateChecker, "_detect_installation_method", classmethod(lambda cls: "pip")
    )
    monkeypatch.setattr(
        UpdateChecker,
        "_fetch_offer",
        classmethod(
            lambda cls, method: package_index.Offer(repository=state["published"], index=None)
        ),
    )
    monkeypatch.setattr(
        UpdateChecker, "_fetch_published_version", classmethod(lambda cls: state["published"])
    )

    def security(cls: type[UpdateChecker]) -> list[SecurityRelease]:
        state["asked"] += 1
        return state["security"]

    monkeypatch.setattr(UpdateChecker, "_fetch_security_releases", classmethod(security))
    yield state


class TestTheUpdateCheckAsksOnlyWhenThereIsSomethingNewer:
    def test_the_newest_flagged_release_above_the_running_one_is_recorded(
        self, sources: dict[str, Any]
    ) -> None:
        sources["security"] = [
            SecurityRelease("98.0.0", "GHSA-aaaa-bbbb-cccc"),
            SecurityRelease("97.0.0", None),
            SecurityRelease("0.0.1", None),
        ]

        check = UpdateChecker.check()

        assert check.security == "98.0.0" and check.advisory == "GHSA-aaaa-bbbb-cccc"
        assert check.security_update

    def test_a_flagged_release_older_than_the_running_one_is_not_news(
        self, sources: dict[str, Any]
    ) -> None:
        sources["security"] = [SecurityRelease("0.0.1", None)]

        assert UpdateChecker.check().security is None

    def test_nothing_is_asked_when_nothing_newer_exists(self, sources: dict[str, Any]) -> None:
        sources["published"] = __version__

        UpdateChecker.check()

        assert sources["asked"] == 0

    def test_a_list_that_cannot_be_read_costs_nothing(self, sources: dict[str, Any]) -> None:
        sources["security"] = []

        check = UpdateChecker.check()

        assert check.security is None and check.state == "update_available"

    def test_it_is_cached_with_the_rest(self, sources: dict[str, Any]) -> None:
        sources["security"] = [SecurityRelease("98.0.0", None)]
        UpdateChecker.check()

        UpdateChecker.check()

        assert sources["asked"] == 1


class TestWhatTheOperatorSees:
    def test_the_banner_says_security(self, capsys: pytest.CaptureFixture[str]) -> None:
        check = checked(security="3.4.0", advisory="GHSA-abcd-1234-wxyz")

        UpdateChecker._show_update_message(check)

        shown = capsys.readouterr().err
        assert "security release" in shown.lower()
        assert "GHSA-abcd-1234-wxyz" in shown
        assert "3.4.1" in shown

    def test_the_banner_of_an_ordinary_update_does_not(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        UpdateChecker._show_update_message(checked())

        assert "security" not in capsys.readouterr().err.lower()
