# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A process out of file descriptors says so, where it says anything (issue 19).

The console of a central answered "Too many open files" to every request: a 500 with no name that
sent the operator to the logs. A request that fails with ``EMFILE`` or ``ENFILE``, at any depth of
its causes, is now a 503 named ``descriptors_exhausted`` that says what to do; the diagnosis of an
application says it instead of "could not complete this check"; and a central that asks a node
that has run out is told that, not "answered 503".
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import DescriptorsExhaustedError, ServiceError
from noust.core.runner import FakeRunner
from noust.managers import diagnose as diagnose_module
from noust.web.api.deps import NoustErrorRoute, install_error_handlers
from noust.web.server import create_app, get_token_manager
from tests.support.diagnose import (
    _app,
    _check,
    _diagnose,
    _fragment,
    _listening,
    _no_findings,
    _unit_state,
)
from tests.support.web_auth import bearer, make_config


def too_many() -> OSError:
    return OSError(errno.EMFILE, "Too many open files")


@pytest.fixture
def small() -> TestClient:
    """An application with the API's own error handlers and a few routes that fail as asked."""
    app = FastAPI()
    install_error_handlers(app)
    router = APIRouter(route_class=NoustErrorRoute)

    @router.get("/api/direct")
    def direct() -> None:
        raise too_many()

    @router.get("/api/wrapped")
    def wrapped() -> None:
        try:
            raise too_many()
        except OSError as exc:
            raise ServiceError("Failed to write the unit", details=str(exc)) from exc

    @router.get("/api/other")
    def other() -> None:
        raise FileNotFoundError(errno.ENOENT, "No such file")

    @app.get("/api/outside")
    def outside() -> None:
        raise too_many()

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


class TestWhatAnOperatorSees:
    @pytest.mark.parametrize("path", ["/api/direct", "/api/wrapped", "/api/outside"])
    def test_a_request_that_ran_out_of_files_is_a_503_that_names_it(
        self, small: TestClient, path: str
    ) -> None:
        response = small.get(path)

        assert response.status_code == 503
        body = response.json()
        assert body["error"] == "descriptors_exhausted"
        assert body["detail"].startswith("File descriptors exhausted")
        assert "noust web restart" in body["hint"]
        assert body["coded"] == {
            "code": "server.descriptors_exhausted",
            "params": {"command": "noust web restart"},
        }

    def test_another_error_of_the_same_kind_is_what_it_was(self, small: TestClient) -> None:
        response = small.get("/api/other")

        assert response.status_code == 500
        assert "descriptors" not in response.text

    def test_the_error_is_in_the_hierarchy_every_boundary_already_knows(self) -> None:
        from noust.core.exceptions import NoustError

        assert issubclass(DescriptorsExhaustedError, NoustError)

    def test_it_is_answered_by_the_real_application_too(
        self, sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.api import system as system_api

        def starved(**_: object) -> None:
            raise too_many()

        monkeypatch.setattr(system_api, "collect_health_report", starved)
        app = create_app(make_config(sandbox))
        client = TestClient(app, client=("testclient", 50000), raise_server_exceptions=False)

        response = client.get(
            "/api/system/health", headers=bearer(get_token_manager().generate_master_token())
        )

        assert response.status_code == 503, response.text
        assert response.json()["error"] == "descriptors_exhausted"


class TestWhatTheDiagnosisSays:
    def run_out(self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner):
        _fragment(runner)
        _unit_state(runner)
        _listening(runner, 3000)
        _no_findings(runner)

        def starved(ctx):  # type: ignore[no-untyped-def]
            raise too_many()

        # The list of probes holds the functions themselves, so the port's is replaced in it.
        monkeypatch.setattr(
            diagnose_module,
            "_PROBES",
            tuple(
                (name, starved if name == "port" else probe)
                for name, probe in diagnose_module._PROBES
            ),
        )
        return _diagnose(monkeypatch, runner, app=_app())

    def test_it_says_the_server_ran_out_and_not_that_a_check_could_not_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self.run_out(monkeypatch, FakeRunner())

        assert result.verdict == "degraded"
        assert result.probable_cause_coded is not None
        assert result.probable_cause_coded.code == "diagnose.cause.descriptors"
        assert "file descriptors" in (result.probable_cause or "").lower()
        assert "noust web restart" in result.probable_cause_coded.text + str(
            result.probable_cause_coded.hint
        )

    def test_the_check_that_could_not_run_is_still_listed_with_the_systems_words(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self.run_out(monkeypatch, FakeRunner())

        port = _check(result, "port")
        assert port.status == "skip" and "Too many open files" in port.evidence
        found = _check(result, "descriptors")
        assert found.status == "fail"
        assert found.coded.code == "diagnose.descriptors.exhausted"

    def test_a_diagnosis_that_was_not_starved_has_no_such_check(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
    ) -> None:
        _fragment(runner)
        _unit_state(runner)
        _listening(runner, 3000)
        _no_findings(runner)

        result = _diagnose(monkeypatch, runner, app=_app())

        assert "descriptors" not in [check.name for check in result.checks]
        assert result.probable_cause_coded is None or (
            result.probable_cause_coded.code != "diagnose.cause.descriptors"
        )
