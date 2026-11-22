# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A diagnosis with no cause is not degraded: the verdict is composed by one rule, held to a table of cases.

Any minor warning used to leave the state at "degraded" with nothing under it to do: a line of the nginx
error log from last spring, a deployment that is not "succeeded", a kernel log that mentions memory and
no application. An operator who reads "degraded" looks for what to fix, so the word is only said with the
cause that says it. A warning that has one (a certificate past its renewal point, the instance that is
not serving, a unit in a state that is not running) says it with its own sentence and degrades; every
other warning is listed beside its check and does not change the verdict.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.server_text import say
from noust.managers import diagnose as module
from noust.managers.diagnose import Check

DOMAIN = "example.com"

CERT_SENTENCE = say("diagnose.certificate.expires_several", label=DOMAIN, days=12)
LEFTOVER_SENTENCE = say("diagnose.blue_green.leftover", unit="example-com", port=3001)
IDLE_SENTENCE = say(
    "diagnose.blue_green.idle_failed", serving="blue", port=3000, idle="green", idle_state="failed"
)
UNIT_SENTENCE = say(
    "diagnose.unit.state",
    unit_file="example-com.service",
    active_state="inactive",
    sub_state="dead",
)


def _check(name: str, status: str, sentence: Any = None) -> Check:
    said = sentence or say("diagnose.check.incomplete")
    return Check(name, status, said.text, "", said)  # type: ignore[arg-type]


def _decide(*checks: Check, **facts: dict[str, Any]) -> tuple[str, Any]:
    ctx = SimpleNamespace(domain=DOMAIN, app_name="example-com", app=None)
    verdict, cause = module._decide(ctx, tuple(checks), facts)  # type: ignore[arg-type]
    return verdict, cause


#: A case is: what the checks found, the facts they collected, the verdict and what says it.
#: ``cause`` is a sentence, ``"own"`` for the sentence of the warning's own check, or None.
CASES: list[tuple[str, tuple[Check, ...], dict[str, dict[str, Any]], str, Any]] = [
    ("nothing found", (_check("unit", "ok"), _check("disk", "ok")), {}, "healthy", None),
    (
        "a skipped probe says nothing",
        (_check("unit", "skip"), _check("certificate", "skip")),
        {},
        "healthy",
        None,
    ),
    (
        "a line of the nginx error log is only listed",
        (_check("nginx_log", "warn"), _check("unit", "ok")),
        {"nginx_log": {"matches": 3}},
        "healthy",
        None,
    ),
    (
        "a deployment that is not succeeded is only listed",
        (_check("last_deployment", "warn"),),
        {"last_deployment": {"failed": False}},
        "healthy",
        None,
    ),
    (
        "a kernel log with OOM and no application is only listed",
        (_check("oom", "warn"),),
        {"oom": {"found": True, "cannot_say": "no_file"}},
        "healthy",
        None,
    ),
    (
        "a certificate whose expiry cannot be read is only listed",
        (_check("certificate", "warn"),),
        {"certificate": {"found": True}},
        "healthy",
        None,
    ),
    (
        "a disk past 90 and under 95 percent is only listed",
        (_check("disk", "warn"),),
        {"disk": {"percent_used": 92.0}},
        "healthy",
        None,
    ),
    (
        "a certificate past its renewal point degrades with its own sentence",
        (_check("certificate", "warn", CERT_SENTENCE),),
        {"certificate": {"found": True, "needs_renewal": True, "expired": False}},
        "degraded",
        CERT_SENTENCE,
    ),
    (
        "the certificate that expired is down",
        (_check("certificate", "fail"),),
        {"certificate": {"found": True, "expired": True, "expiry": "2025-01-01"}},
        "down",
        "any",
    ),
    (
        "a unit left behind by blue/green degrades with its own sentence",
        (_check("blue_green", "warn", LEFTOVER_SENTENCE),),
        {"blue_green": {"leftover_unit": "example-com"}},
        "degraded",
        LEFTOVER_SENTENCE,
    ),
    (
        "an idle instance that failed degrades with its own sentence",
        (_check("blue_green", "warn", IDLE_SENTENCE),),
        {"blue_green": {"idle_failed": True}},
        "degraded",
        IDLE_SENTENCE,
    ),
    (
        "a unit in a state that is not running degrades with its own sentence",
        (_check("unit", "warn", UNIT_SENTENCE),),
        {
            "unit": {
                "exists": True,
                "active_state": "inactive",
                "sub_state": "dead",
                "odd_state": True,
            }
        },
        "degraded",
        UNIT_SENTENCE,
    ),
    (
        "a disk past 95 percent is degraded with its cause",
        (_check("disk", "fail"),),
        {"disk": {"percent_used": 97.0}},
        "degraded",
        "any",
    ),
    (
        "what degrades wins over what is only listed",
        (_check("nginx_log", "warn"), _check("certificate", "warn", CERT_SENTENCE)),
        {"nginx_log": {"matches": 1}, "certificate": {"needs_renewal": True}},
        "degraded",
        CERT_SENTENCE,
    ),
    (
        "a check that failed and no rule names is down without a cause",
        (_check("journal", "fail"), _check("nginx_log", "warn")),
        {},
        "down",
        None,
    ),
]


@pytest.mark.parametrize(
    ("checks", "facts", "verdict", "cause"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_the_verdict_of_each_case(
    checks: tuple[Check, ...], facts: dict[str, dict[str, Any]], verdict: str, cause: Any
) -> None:
    got_verdict, got_cause = _decide(*checks, **facts)

    assert got_verdict == verdict
    if cause == "any":
        assert got_cause is not None
    elif cause is None:
        assert got_cause is None
    else:
        assert got_cause is not None and got_cause.code == cause.code


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_a_diagnosis_with_no_cause_is_never_degraded(case: tuple[Any, ...]) -> None:
    _, checks, facts, _, _ = case

    verdict, cause = _decide(*checks, **facts)

    assert verdict != "degraded" or cause is not None


def test_every_warning_the_probes_can_raise_is_ruled_on() -> None:
    # A probe that gains a "warn" must say whether it degrades: the places it can be raised are these,
    # and each is either in the table of what degrades or among those only listed.
    import ast
    from pathlib import Path

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    raising: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_check":
            args = node.args
            if (
                len(args) >= 2
                and isinstance(args[0], ast.Constant)
                and isinstance(args[1], ast.Constant)
                and args[1].value == "warn"
            ):
                raising.add(str(args[0].value))

    degrading = {name for name, _fact in module._DEGRADING_WARNINGS}
    # What the verdict has a rule of its own for above (a worker with a port, a full disk, a kill the
    # kernel counted), whose other forms are listed.
    ruled_above = {"containers", "disk", "oom"}
    only_listed = {"nginx_log", "certificate", "last_deployment"}
    assert raising - degrading - ruled_above - only_listed == set(), (
        "a probe raises a warning that the verdict has no rule for"
    )
