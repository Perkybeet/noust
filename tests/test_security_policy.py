# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a researcher reaches the project: SECURITY.md, ``.well-known/security.txt``
(RFC 9116) and the list of scanner exceptions (checklist L-40, L-41, L-31).

``scripts/release.py --check`` runs the same checks in CI against today's
date; here the date is fixed so the suite does not start failing on the day a
file expires.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_script_policy", Path(__file__).parent.parent / "scripts/release.py"
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules["release_script_policy"] = release
SPEC.loader.exec_module(release)

ROOT = Path(__file__).parent.parent
TODAY = date(2026, 10, 13)

POLICY = """# Security policy

## Reporting a vulnerability

- GitHub: <https://github.com/Perkybeet/noust/security/advisories/new>
- email: security@example.org

The machine-readable form is `.well-known/security.txt`.
"""

SECURITY_TXT = """# How to report a vulnerability in Noust
Contact: https://github.com/Perkybeet/noust/security/advisories/new
Contact: mailto:security@example.org
Expires: 2027-03-01T00:00:00.000Z
Preferred-Languages: en, es
Canonical: https://raw.githubusercontent.com/Perkybeet/noust/main/.well-known/security.txt
Policy: https://github.com/Perkybeet/noust/blob/main/SECURITY.md
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".well-known").mkdir()
    (tmp_path / "SECURITY.md").write_text(POLICY, encoding="utf-8")
    (tmp_path / ".well-known" / "security.txt").write_text(SECURITY_TXT, encoding="utf-8")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    return tmp_path


def write_txt(repo: Path, text: str) -> None:
    (repo / ".well-known" / "security.txt").write_text(text, encoding="utf-8")


class TestSecurityTxt:
    def test_a_complete_file_has_no_problem(self, repo: Path) -> None:
        assert release.security_txt_problems(TODAY) == []

    def test_a_missing_file_is_a_problem(self, repo: Path) -> None:
        (repo / ".well-known" / "security.txt").unlink()

        problems = release.security_txt_problems(TODAY)

        assert problems and ".well-known/security.txt: missing" in problems[0]

    @pytest.mark.parametrize("field", ["Contact", "Expires", "Policy", "Canonical"])
    def test_every_field_the_policy_depends_on_is_required(self, repo: Path, field: str) -> None:
        lines = [line for line in SECURITY_TXT.splitlines() if not line.startswith(field + ":")]
        write_txt(repo, "\n".join(lines) + "\n")

        problems = release.security_txt_problems(TODAY)

        assert any(field in problem for problem in problems)

    def test_an_expired_file_is_a_problem_naming_the_date(self, repo: Path) -> None:
        problems = release.security_txt_problems(date(2027, 3, 2))

        assert any("expired" in problem and "2027-03-01" in problem for problem in problems)

    def test_an_expiry_more_than_a_year_away_is_a_problem(self, repo: Path) -> None:
        write_txt(repo, SECURITY_TXT.replace("2027-03-01", "2028-03-01"))

        assert any("more than a year" in p for p in release.security_txt_problems(TODAY))

    def test_an_expiry_that_is_not_a_date_is_a_problem(self, repo: Path) -> None:
        write_txt(repo, SECURITY_TXT.replace("2027-03-01T00:00:00.000Z", "next spring"))

        assert any("Expires" in p and "not a" in p for p in release.security_txt_problems(TODAY))

    def test_a_contact_the_policy_does_not_list_is_a_problem(self, repo: Path) -> None:
        write_txt(repo, SECURITY_TXT + "Contact: mailto:someone-else@example.org\n")

        problems = release.security_txt_problems(TODAY)

        assert any("someone-else@example.org" in problem for problem in problems)

    def test_a_policy_url_that_is_not_the_repositorys_is_a_problem(self, repo: Path) -> None:
        write_txt(repo, SECURITY_TXT.replace("blob/main/SECURITY.md", "blob/main/OTHER.md"))

        assert any("Policy" in p for p in release.security_txt_problems(TODAY))

    def test_the_policy_must_point_at_the_file(self, repo: Path) -> None:
        (repo / "SECURITY.md").write_text(POLICY.replace(".well-known/security.txt", ""))

        assert any("security.txt" in p for p in release.security_txt_problems(TODAY))

    def test_an_encryption_key_needs_a_fingerprint_in_the_policy(self, repo: Path) -> None:
        write_txt(repo, SECURITY_TXT + "Encryption: https://example.org/key.asc\n")

        assert any("fingerprint" in p for p in release.security_txt_problems(TODAY))

        fingerprint = "D9FA 6122 D7F1 54D7 5A58 8A50 1945 D169 A62C FF65"
        (repo / "SECURITY.md").write_text(POLICY + f"\nFingerprint: {fingerprint}\n")
        assert release.security_txt_problems(TODAY) == []

    def test_a_comment_is_not_a_field(self, repo: Path) -> None:
        write_txt(repo, SECURITY_TXT + "# Encryption: https://example.org/key.asc\n")

        assert release.security_txt_problems(TODAY) == []


class TestRefreshingTheExpiry:
    def test_a_release_renews_the_expiry_six_months_ahead(self) -> None:
        refreshed = release.refreshed_security_txt(SECURITY_TXT, date(2026, 10, 13))

        assert "Expires: 2027-04-11T00:00:00.000Z" in refreshed

    def test_only_the_expiry_changes(self) -> None:
        refreshed = release.refreshed_security_txt(SECURITY_TXT, date(2026, 10, 13))

        before = [line for line in SECURITY_TXT.splitlines() if not line.startswith("Expires:")]
        after = [line for line in refreshed.splitlines() if not line.startswith("Expires:")]
        assert before == after

    def test_a_file_without_an_expiry_gets_one(self) -> None:
        text = "\n".join(
            line for line in SECURITY_TXT.splitlines() if not line.startswith("Expires:")
        )

        assert "Expires: 2027-04-11" in release.refreshed_security_txt(
            text + "\n", date(2026, 10, 13)
        )

    def test_a_renewed_file_passes_the_check_of_that_day(self, repo: Path) -> None:
        today = date(2026, 10, 13)
        write_txt(repo, release.refreshed_security_txt(SECURITY_TXT, today))

        assert release.security_txt_problems(today) == []

    def test_releasing_renews_the_file_on_disk(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(release, "_today", lambda: date(2026, 10, 13))

        assert release._advanced_security_txt() is not None
        assert "2027-04-11" in release._advanced_security_txt()  # type: ignore[operator]


class TestTheRepositoryItself:
    """The files committed here, against a fixed date inside their validity."""

    def test_security_txt_is_valid_and_agrees_with_the_policy(self) -> None:
        # Real ROOT: no fixture.
        problems = release.security_txt_problems(date(2026, 10, 14))

        assert problems == []

    def test_the_private_advisory_form_comes_before_an_address(self) -> None:
        text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

        assert text.index("security/advisories/new") < text.index("**email**")

    def test_the_address_the_owner_still_has_to_choose_is_marked_in_both_files(self) -> None:
        for name in ("SECURITY.md", ".well-known/security.txt"):
            text = (ROOT / name).read_text(encoding="utf-8")

            assert "OWNER-TODO(security-address)" in text, name


class TestScannerExceptions:
    def entry(self, repo: Path, body: str) -> list[str]:
        (repo / ".trivyignore").write_text(body, encoding="utf-8")
        return release.trivyignore_problems(TODAY)

    def test_no_file_and_no_entries_are_fine(self, repo: Path) -> None:
        assert release.trivyignore_problems(TODAY) == []
        assert self.entry(repo, "# only comments\n\n") == []

    def test_an_entry_with_a_future_expiry_is_fine(self, repo: Path) -> None:
        body = "# expires: 2026-12-31 the base image has no fix yet\nCVE-2026-1111\n"

        assert self.entry(repo, body) == []

    def test_an_expired_entry_is_a_problem_naming_the_advisory(self, repo: Path) -> None:
        body = "# expires: 2026-10-12 waiting for the base image\nCVE-2026-1111\n"

        problems = self.entry(repo, body)

        assert any("CVE-2026-1111" in p and "expired" in p for p in problems)

    def test_an_entry_that_does_not_say_when_it_ends_is_a_problem(self, repo: Path) -> None:
        problems = self.entry(repo, "CVE-2026-1111\n")

        assert any("CVE-2026-1111" in p and "expires" in p for p in problems)

    def test_a_reason_is_required(self, repo: Path) -> None:
        problems = self.entry(repo, "# expires: 2026-12-31\nCVE-2026-1111\n")

        assert any("CVE-2026-1111" in p and "reason" in p for p in problems)

    def test_a_comment_only_covers_the_entry_right_after_it(self, repo: Path) -> None:
        body = "# expires: 2026-12-31 one reason\nCVE-2026-1111\nCVE-2026-2222\n"

        problems = self.entry(repo, body)

        assert any("CVE-2026-2222" in p for p in problems)
        assert not any("CVE-2026-1111" in p for p in problems)

    def test_the_committed_file_has_no_unexplained_entry(self) -> None:
        assert release.trivyignore_problems(date(2026, 10, 14)) == []
