# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No error says English that Noust typed and no code stands for (item 151).

The console in Spanish showed "Application not found", "Invalid two-factor code." and the
firewall's refusal in English, because the mechanism that gives a sentence a code
(``noust.core.server_text``) was optional and nothing noticed an error that skipped it. These tests
make it the rule, in the way the project holds its other rules (``tests/test_layering.py``):

* a module may not raise more English-without-a-code than the table of
  ``tests/support/untranslated_baseline.py`` says, so a new ``raise`` in English fails here, and a
  module that is given its codes fails until the table says so, so it only goes down;
* the areas a person goes through when they sign in, hold a token, change the firewall, choose a
  role, take or restore a backup and deploy are at zero, with no table to hide in.

What is counted, and what is not, is in ``tests/support/untranslated_sentences.py``.
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path

import pytest

from tests.support.untranslated_baseline import UNTRANSLATED
from tests.support.untranslated_sentences import (
    ROOT,
    count_in,
    noust_error_names,
    untranslated_by_file,
)

#: The areas that are at zero, as the modules of the package (globs, forward slashes) that make
#: each. The ratchet cannot be what holds these: nothing in them is excused.
ZERO_AREAS: dict[str, tuple[str, ...]] = {
    "signing in": (
        "web/api/auth.py",
        "web/api/auth_accounts.py",
        "web/api/passkeys.py",
        "web/auth/*.py",
        "core/accounts/local_login.py",
        "core/accounts/passwords.py",
        "core/accounts/passkeys.py",
        "core/accounts/webauthn.py",
        "core/accounts/reset.py",
        "core/accounts/model.py",
    ),
    "tokens": ("web/auth/tokens.py", "web/api/token_activity.py"),
    "the firewall and SSH": (
        "managers/server/security*.py",
        "web/api/server/security.py",
        "web/api/access.py",
    ),
    "roles and permissions": ("web/permissions/*.py", "web/api/roles.py"),
    "backups and restores": (
        "managers/*backup*.py",
        "managers/*restore*.py",
        "managers/database/backup*.py",
        "core/backup*.py",
        "central/backup.py",
        "web/api/*backup*.py",
        "web/api/databases/backups.py",
    ),
    "deploying": (
        "deployers/lifecycle.py",
        "deployers/migrate.py",
        "deployers/base/*.py",
        "deployers/helpers/hooks.py",
        "deployers/helpers/health_gate.py",
        "deployers/helpers/release_build.py",
        "core/appdir/releases.py",
        "web/api/app_deploy.py",
        "web/api/deployments.py",
        "web/api/jobs.py",
    ),
    "the cases the review of the console in Spanish found": (
        "managers/http3.py",
        "managers/waf.py",
        "managers/server/storage.py",
        "managers/log_search.py",
        "managers/log_retention.py",
    ),
}


def _modules_of(patterns: tuple[str, ...]) -> list[str]:
    found = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if any(
            fnmatch.fnmatchcase(path.relative_to(ROOT).as_posix(), pattern) for pattern in patterns
        )
    }
    return sorted(found)


# -- what the detector counts ----------------------------------------------------------


def _count(source: str, extra: set[str] | None = None) -> int:
    return count_in(ast.parse(source), {"NoustError", "DeploymentError", *(extra or set())})


@pytest.mark.parametrize(
    "source",
    [
        'raise DeploymentError("Application not found")',
        'raise DeploymentError(f"Application not found: {domain}", details="Run it.")',
        'raise DeploymentError("Not " + "found")',
        'raise DeploymentError("Not found: {}".format(domain))',
        'raise HTTPException(status_code=404, detail="Not found")',
        'raise HTTPException(404, f"Not found: {name}")',
        'raise HTTPException(status_code=400, detail={"error": "x", "detail": "Not found"})',
        'raise DeploymentError(message="Not found")',
    ],
)
def test_an_error_whose_sentence_is_english_with_no_code_is_counted(source: str) -> None:
    assert _count(source) == 1


@pytest.mark.parametrize(
    "source",
    [
        'raise DeploymentError("Not found", coded=say("apps.not_found", domain=domain))',
        "raise DeploymentError.from_text(say('apps.not_found', domain=domain))",
        "raise DeploymentError(message)",
        "raise DeploymentError(str(exc))",
        'raise ValueError("a defect, not a sentence for a person")',
        'raise HTTPException(400, detail={"error": "x", "detail": "Not found", "coded": coded})',
        "raise HTTPException(status_code=404, detail=sentence.text)",
    ],
)
def test_what_has_a_code_or_is_not_a_sentence_of_noust_is_not_counted(source: str) -> None:
    assert _count(source) == 0


def test_a_class_that_derives_from_a_noust_error_is_one_too() -> None:
    tree = ast.parse("class A(NoustError): pass\nclass B(A): pass\nclass C(Exception): pass\n")

    names = noust_error_names({Path("x.py"): tree})

    assert {"NoustError", "A", "B"} <= names
    assert "C" not in names


# -- the ratchet -----------------------------------------------------------------------


def test_no_module_raises_more_english_than_it_did_and_the_table_lists_every_one_that_does() -> (
    None
):
    now = untranslated_by_file()

    worse = {
        name: (UNTRANSLATED.get(name, 0), count)
        for name, count in now.items()
        if count > UNTRANSLATED.get(name, 0)
    }
    assert not worse, (
        'These modules raise an error in English with no code (`raise X("...")` without `coded=`, or an '
        "`HTTPException` with a spelled detail). Give the sentence a code: add it to "
        "noust.core.server_text and raise `X.from_text(say(...))`:\n"
        + "\n".join(
            f"  {name}: {before} -> {after}" for name, (before, after) in sorted(worse.items())
        )
    )


def test_the_table_goes_down_with_the_tree() -> None:
    now = untranslated_by_file()

    better = {
        name: (before, now.get(name, 0))
        for name, before in UNTRANSLATED.items()
        if now.get(name, 0) < before
    }
    assert not better, (
        "These modules raise fewer errors in English than the table says: lower the figure in "
        "tests/support/untranslated_baseline.py (and drop the line at zero) so that it only goes down:\n"
        + "\n".join(
            f'    "{name}": {after},' if after else f'    (remove "{name}")'
            for name, (_, after) in sorted(better.items())
        )
    )


@pytest.mark.parametrize("area", sorted(ZERO_AREAS))
def test_the_areas_a_person_goes_through_say_nothing_in_english_without_a_code(area: str) -> None:
    modules = _modules_of(ZERO_AREAS[area])
    assert modules, f"{area}: no module matches {ZERO_AREAS[area]}"

    now = untranslated_by_file()

    assert {name: now[name] for name in modules if name in now} == {}


def test_no_area_is_listed_as_a_place_that_still_has_a_figure() -> None:
    zero = {name for patterns in ZERO_AREAS.values() for name in _modules_of(patterns)}

    assert sorted(zero & set(UNTRANSLATED)) == []
