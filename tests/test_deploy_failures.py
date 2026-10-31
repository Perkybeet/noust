# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A failed deployment leads with one of the catalog's sentences (``noust.core.deploy_failures``).

A job and a deployment keep their error as text, and a Spanish console showed its first line in
English: in the toast that says the job ended and in the Overview's list of what needs attention.
The first line is now a sentence with a code, carried by the exception and read back from the
stored text, and what follows it is another program's words, which are never read.
"""

from __future__ import annotations

import pytest

from noust.core import deploy_failures
from noust.core.deploy_failures import (
    app_unhealthy,
    coded,
    release_unhealthy,
    rollback_unhealthy,
    stack_went_back,
)
from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.server_text import CATALOG, ServerText, read_back, say, values_of


def _sample(code: str) -> ServerText:
    """The sentence of a code with a value for each placeholder (a number where one is a count)."""
    return say(
        code,
        **{
            name: 300 if name == "seconds" else {"release": "20261031-2"}.get(name, f"{name}-1")
            for name in sorted(values_of(code))
        },
    )


FAILURE_CODES = sorted(code for code in CATALOG if code.startswith("deploy_failure."))


def test_every_failure_sentence_of_the_catalog_can_be_read_back() -> None:
    """A sentence added to the catalog and not to the reader would reach the console in English."""
    assert sorted(deploy_failures._CODES) == FAILURE_CODES


@pytest.mark.parametrize("code", FAILURE_CODES)
def test_a_sentence_reads_back_as_the_sentence_that_said_it(code: str) -> None:
    sentence = _sample(code)

    assert coded(sentence.text) == sentence


@pytest.mark.parametrize("code", FAILURE_CODES)
def test_only_the_first_line_is_the_sentence_and_the_rest_is_left_alone(code: str) -> None:
    sentence = _sample(code)
    stored = f"{sentence.text}\n  Details: Health check attempts 1-15 failed: HTTP 502\nsecond"

    assert coded(stored) == sentence


def test_a_line_that_is_not_all_of_one_sentence_is_not_one() -> None:
    """Another program's words may contain ours, and are never taken for them."""
    for line in (
        "npm ERR! Release 20261031-2 did not pass its health check",
        "Release 20261031-2 did not pass its health check, said the proxy",
        "Release a b did not pass its health check",
        "Build failed: exit code 2",
        "certbot said no",
        "",
    ):
        assert coded(line) is None, line
    assert coded(None) is None


def test_a_stored_error_is_read_from_its_first_non_empty_line() -> None:
    assert coded("\n\nBuild failed\n  Details: x") == say("deploy_failure.build_failed")


def test_the_words_of_each_failure_are_the_ones_it_always_had() -> None:
    """A sentence moved into the catalog; what operators and scripts read did not change."""
    assert (
        release_unhealthy("r2", previous=None, restored=False, evidence="e").message
        == "Release r2 did not pass its health check"
    )
    assert (
        release_unhealthy("r2", previous="r1", restored=True, evidence="e").message
        == "Release r2 did not pass its health check; release r1 is active again"
    )
    assert release_unhealthy("r2", previous="r1", restored=False, evidence="e").message == (
        "Release r2 did not pass its health check; release r1 is active again "
        "but is not answering either"
    )
    assert app_unhealthy("shop.example.com", evidence="e").message == (
        "shop.example.com did not pass its health check"
    )
    assert rollback_unhealthy("shop.example.com", details="e").message == (
        "shop.example.com did not pass its health check after the rollback"
    )


def test_only_a_release_that_goes_back_and_answers_is_a_rollback() -> None:
    """The class says what happened to the application: serving the previous release, or down."""
    assert type(release_unhealthy("r2", previous=None, restored=False, evidence="e")) is (
        DeploymentError
    )
    assert type(release_unhealthy("r2", previous="r1", restored=True, evidence="e")) is (
        RolledBackError
    )
    assert type(release_unhealthy("r2", previous="r1", restored=False, evidence="e")) is (
        DeploymentError
    )


def test_the_error_keeps_the_evidence_verbatim_and_carries_its_code() -> None:
    error = release_unhealthy("r2", previous="r1", restored=True, evidence="probe: HTTP 502")

    assert error.details == "probe: HTTP 502"
    assert error.coded == say("deploy_failure.release_unhealthy_back", release="r2", previous="r1")
    # What a job stores is str(error): its first line reads back as the same sentence.
    assert coded(str(error)) == error.coded


@pytest.mark.parametrize(
    ("outcome", "code", "rolled_back"),
    [
        ("nothing_before", "deploy_failure.stack_nothing_before", False),
        ("not_restored", "deploy_failure.stack_not_restored", False),
        ("back", "deploy_failure.stack_back", True),
        ("back_down", "deploy_failure.stack_back_down", False),
    ],
)
def test_a_stack_that_failed_its_gate_says_how_it_ended(
    outcome: str, code: str, rolled_back: bool
) -> None:
    error = stack_went_back("shop.example.com", outcome, "evidence")  # type: ignore[arg-type]

    assert error.coded == say(code, domain="shop.example.com")
    assert isinstance(error, RolledBackError) is rolled_back
    assert error.message.startswith("The update of shop.example.com did not pass its health check")
    assert coded(str(error)) == error.coded


def test_a_value_is_one_word_and_a_number_where_the_sentence_counts() -> None:
    sentence = coded("Build did not finish within 2700s and was stopped")

    assert sentence == say("deploy_failure.build_timeout", seconds=2700)
    assert sentence is not None
    assert sentence.params == {"seconds": 2700}


def test_the_reader_is_the_same_for_any_table_of_sentences() -> None:
    assert read_back("Build failed", ["deploy_failure.build_failed"]) == say(
        "deploy_failure.build_failed"
    )
    assert read_back("Build failed", ["deploy_failure.install_failed"]) is None
