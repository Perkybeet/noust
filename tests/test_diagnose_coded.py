# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust diagnose`` says is a sentence with a code, for the console to translate.

Every check's summary and the probable cause were English text a Spanish console showed as it
came. They are now entries of the one catalog (``noust.core.server_text``), asked for with
``say``, and the check and the cause carry the sentence beside the text: so the English the CLI
prints, the code the console translates by and its values are one thing. The evidence under a
check is what the system printed and is never coded. The rules of the diagnosis itself are in
``tests/test_diagnose.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.core.server_text import CATALOG
from noust.core.store import DeploymentRecord
from noust.managers import diagnose as diagnose_module
from noust.managers.diagnose import Diagnosis
from tests.support.diagnose import (
    DOMAIN,
    UNIT_FILE,
    _app,
    _check,
    _diagnose,
    _disk,
    _fragment,
    _http,
    _listening,
    _no_findings,
    _unit_state,
)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


def _healthy(runner: FakeRunner) -> None:
    _fragment(runner)
    _unit_state(runner)
    _listening(runner, 3000)
    _no_findings(runner)


def _said(diagnosis: Diagnosis, name: str) -> tuple[str, dict[str, object]]:
    """The code and values of a check's sentence."""
    coded = _check(diagnosis, name).coded
    assert coded is not None, name
    return coded.code, dict(coded.params)


def _is_one_thing(diagnosis: Diagnosis) -> None:
    """The English and the code of every sentence of a diagnosis are the same sentence."""
    for check in diagnosis.checks:
        assert check.coded is not None, f"{check.name} says {check.summary!r} with no code"
        assert check.summary == check.coded.text, check.name
        assert check.coded.code in CATALOG
    if diagnosis.probable_cause is None:
        assert diagnosis.probable_cause_coded is None
    else:
        assert diagnosis.probable_cause_coded is not None
        assert diagnosis.probable_cause == diagnosis.probable_cause_coded.text


# -- the checks ------------------------------------------------------------------------------


def test_a_healthy_app_says_every_check_with_a_code(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)
    runner.script(
        ["certbot", "certificates"],
        stdout=(
            f"  Certificate Name: {DOMAIN}\n    Domains: {DOMAIN}\n"
            "    Expiry Date: 2026-06-01 00:00:00+00:00 (VALID: 30 days)\n"
        ),
    )

    result = _diagnose(
        monkeypatch,
        runner,
        app=_app(),
        deployments=[DeploymentRecord(domain=DOMAIN, status="success")],
    )

    _is_one_thing(result)
    assert _said(result, "unit") == ("diagnose.unit.active", {"unit_file": UNIT_FILE})
    assert _said(result, "port") == (
        "diagnose.port.listening_process",
        {"port": 3000, "processes": "node (pid 100)"},
    )
    assert _said(result, "http_direct") == (
        "diagnose.http_direct.status",
        {"status": "200", "port": 3000},
    )
    assert _said(result, "http_nginx") == (
        "diagnose.http_nginx.status",
        {"status": "200", "domain": DOMAIN},
    )
    assert _said(result, "last_deployment")[0] == "diagnose.last_deployment.succeeded"
    assert _said(result, "oom")[0] == "diagnose.oom.none"
    assert _said(result, "certificate")[0] == "diagnose.certificate.valid_several"
    assert _said(result, "disk")[0] == "diagnose.disk.usage"
    # What the sentence says in English is what the diagnosis always said.
    assert _check(result, "port").summary == "Port 3000 is listening (node (pid 100))"
    assert _check(result, "unit").summary == f"{UNIT_FILE} is active (running)"


def test_a_missing_unit_is_a_check_and_a_cause_each_with_its_code(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _no_findings(runner)

    result = _diagnose(monkeypatch, runner, app=_app())

    _is_one_thing(result)
    assert _said(result, "unit")[0] == "diagnose.unit.missing"
    assert result.probable_cause_coded is not None
    assert result.probable_cause_coded.code == "diagnose.cause.no_unit"
    assert result.probable_cause_coded.params == {"unit": "example-com"}


def test_a_crash_loop_says_the_exit_status_and_blames_the_kernel_when_it_was_oom(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _fragment(runner)
    _unit_state(runner, active="activating", sub="auto-restart", exec_status="137", restarts="6")
    runner.script(
        ["journalctl", "-u", UNIT_FILE, "-n", "50", "--no-pager", "-o", "short-iso"], stdout=""
    )
    runner.script(["certbot", "certificates"], stdout="")
    oom = ["journalctl", "-k", "--since", "-7d", "--grep", "oom", "-o", "short-iso"]

    runner.script(oom, stdout="")
    plain = _diagnose(monkeypatch, runner, app=_app())
    runner.script(oom, stdout="host kernel: Out of memory: Killed process 100 (node)\n")
    killed = _diagnose(monkeypatch, runner, app=_app())

    _is_one_thing(plain)
    _is_one_thing(killed)
    assert _said(plain, "unit") == (
        "diagnose.unit.restarting",
        {"unit_file": UNIT_FILE, "restarts": 6},
    )
    assert plain.probable_cause_coded is not None
    assert plain.probable_cause_coded.code == "diagnose.cause.crash_loop"
    assert plain.probable_cause_coded.params == {"status": "137"}
    assert killed.probable_cause_coded is not None
    assert killed.probable_cause_coded.code == "diagnose.cause.oom"
    assert _said(killed, "oom")[0] == "diagnose.oom.found"


def test_a_unit_that_failed_says_its_result(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _fragment(runner)
    _unit_state(runner, active="failed", sub="failed", result="exit-code", exec_status="2")
    _listening(runner, 3000)
    _no_findings(runner)

    result = _diagnose(monkeypatch, runner, app=_app())

    _is_one_thing(result)
    assert _said(result, "unit") == (
        "diagnose.unit.failed",
        {"unit_file": UNIT_FILE, "result": "exit-code"},
    )
    assert result.probable_cause_coded is not None
    assert result.probable_cause_coded.code == "diagnose.cause.unit_failed"
    assert result.probable_cause_coded.params == {"result": "exit-code", "status": "2"}


def test_a_port_nothing_listens_on_and_one_it_listens_on_elsewhere(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _fragment(runner)
    _unit_state(runner)
    _no_findings(runner)
    runner.script(["ss", "-ltnpH"], stdout="")

    silent = _diagnose(monkeypatch, runner, app=_app())

    _is_one_thing(silent)
    assert _said(silent, "port") == ("diagnose.port.silent", {"port": 3000})
    assert silent.probable_cause_coded is not None
    assert silent.probable_cause_coded.code == "diagnose.cause.port_silent"

    runner.script(["systemctl", "show", "-p", "MainPID", UNIT_FILE], stdout="MainPID=200\n")
    _listening(runner, 4000, pid="200")
    elsewhere = _diagnose(monkeypatch, runner, app=_app())

    _is_one_thing(elsewhere)
    assert _said(elsewhere, "port") == (
        "diagnose.port.elsewhere",
        {"actual_port": 4000, "recorded_port": 3000},
    )
    assert elsewhere.probable_cause_coded is not None
    assert elsewhere.probable_cause_coded.code == "diagnose.cause.port_elsewhere"


def test_nginx_that_cannot_reach_a_healthy_app_says_what_it_answered(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)

    refused = _diagnose(monkeypatch, runner, app=_app(), http_get=_http(nginx=(502, None)))
    silent = _diagnose(
        monkeypatch, runner, app=_app(), http_get=_http(nginx=(None, "connection refused"))
    )

    _is_one_thing(refused)
    _is_one_thing(silent)
    assert refused.probable_cause_coded is not None
    assert refused.probable_cause_coded.code == "diagnose.cause.nginx_status"
    assert refused.probable_cause_coded.params == {"status": "502", "domain": DOMAIN}
    assert _said(silent, "http_nginx")[0] == "diagnose.http_nginx.unreachable"
    assert silent.probable_cause_coded is not None
    assert silent.probable_cause_coded.code == "diagnose.cause.nginx_unreachable"
    assert silent.probable_cause_coded.params["error"] == "connection refused"


def test_an_app_that_does_not_answer_directly_says_so(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)

    result = _diagnose(
        monkeypatch, runner, app=_app(), http_get=_http(direct=(None, "connection refused"))
    )

    _is_one_thing(result)
    assert _said(result, "http_direct") == ("diagnose.http_direct.unreachable", {"port": 3000})


def test_a_disk_nearly_full_says_how_full(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)

    result = _diagnose(monkeypatch, runner, app=_app(), disk_usage=_disk(97.0))

    _is_one_thing(result)
    code, values = _said(result, "disk")
    assert code == "diagnose.disk.usage" and values["percent"] == 97
    assert result.probable_cause_coded is not None
    assert result.probable_cause_coded.code == "diagnose.cause.disk_full"
    assert result.probable_cause_coded.params == {"percent": 97}


def test_a_certificate_says_how_long_it_has_left_in_the_singular_or_the_plural(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)

    def certificate(expiry: str) -> Diagnosis:
        runner.script(
            ["certbot", "certificates"],
            stdout=(
                f"  Certificate Name: {DOMAIN}\n    Domains: {DOMAIN}\n"
                f"    Expiry Date: {expiry} 00:00:00+00:00 (VALID: 1 days)\n"
            ),
        )
        return _diagnose(monkeypatch, runner, app=_app())

    soon = certificate("2026-01-02")
    later = certificate("2026-01-11")
    past = certificate("2025-12-30")

    for result in (soon, later, past):
        _is_one_thing(result)
    assert _said(soon, "certificate") == (
        "diagnose.certificate.expires_one",
        {"label": DOMAIN},
    )
    assert _said(later, "certificate") == (
        "diagnose.certificate.expires_several",
        {"label": DOMAIN, "days": 10},
    )
    assert _said(past, "certificate")[0] == "diagnose.certificate.expired"
    assert past.probable_cause_coded is not None
    assert past.probable_cause_coded.code == "diagnose.cause.certificate_expired"


def test_a_failed_deployment_says_why_and_a_cancelled_one_says_who(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)

    failed = _diagnose(
        monkeypatch,
        runner,
        app=_app(),
        deployments=[
            DeploymentRecord(domain=DOMAIN, status="failed", error="npm install exited 1")
        ],
    )
    cancelled = _diagnose(
        monkeypatch,
        runner,
        app=_app(),
        deployments=[
            DeploymentRecord(
                domain=DOMAIN,
                status="failed",
                cancelled_at="2026-01-01T00:00:00+00:00",
                cancelled_by="ana",
            )
        ],
    )
    nothing = _diagnose(monkeypatch, runner, app=_app(), deployments=[])

    for result in (failed, cancelled, nothing):
        _is_one_thing(result)
    assert _said(failed, "last_deployment") == (
        "diagnose.last_deployment.failed_with",
        {"error": "npm install exited 1"},
    )
    assert _said(cancelled, "last_deployment") == (
        "diagnose.last_deployment.cancelled_by",
        {"actor": "ana"},
    )
    assert _said(nothing, "last_deployment")[0] == "diagnose.last_deployment.none"


def test_a_static_site_says_why_it_has_no_unit_in_each_check_that_skips(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _no_findings(runner)

    result = _diagnose(monkeypatch, runner, app=_app(is_static=True, port=None))

    _is_one_thing(result)
    assert _said(result, "unit")[0] == "diagnose.unit.skipped_static"
    assert _said(result, "port")[0] == "diagnose.port.skipped_static"
    assert _said(result, "journal")[0] == "diagnose.journal.skipped_static"
    assert _said(result, "http_direct")[0] == "diagnose.http_direct.no_port"


def test_a_probe_that_raises_is_a_sentence_too_with_its_error_as_evidence(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)

    def raising(_url: str, _headers: dict) -> tuple[int | None, str | None]:
        raise ValueError("boom")

    result = _diagnose(monkeypatch, runner, app=_app(), http_get=raising)

    _is_one_thing(result)
    assert _said(result, "http_direct")[0] == "diagnose.check.incomplete"
    assert "boom" in _check(result, "http_direct").evidence


def test_the_offer_to_update_without_a_cut_is_a_sentence(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    _healthy(runner)
    monkeypatch.setattr(diagnose_module, "could_update_without_downtime", lambda *a, **k: True)

    result = _diagnose(monkeypatch, runner, app=_app())

    _is_one_thing(result)
    assert _said(result, "zero_downtime")[0] == "diagnose.zero_downtime.could"


# -- nothing is left uncoded -----------------------------------------------------------------


def _calls(name: str) -> list[ast.Call]:
    tree = ast.parse(Path(diagnose_module.__file__).read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def test_no_check_is_made_with_a_sentence_of_its_own() -> None:
    # A check is only ever made by ``_check`` from a catalog sentence, so a probe added later
    # cannot ship English the console cannot translate: its summary would have to be a literal.
    direct = _calls("Check")
    assert len(direct) == 1, "only _check builds a Check"
    for call in _calls("_check"):
        sentence = call.args[2]
        assert isinstance(sentence, ast.Call | ast.Name | ast.IfExp | ast.BoolOp), ast.dump(call)
        assert not isinstance(sentence, ast.Constant | ast.JoinedStr)


def test_every_sentence_of_the_diagnosis_is_asked_for_by_a_code_that_exists() -> None:
    asked = {
        call.args[0].value
        for call in _calls("say")
        if call.args and isinstance(call.args[0], ast.Constant)
    }

    assert asked
    assert all(code in CATALOG for code in asked), sorted(asked - set(CATALOG))
