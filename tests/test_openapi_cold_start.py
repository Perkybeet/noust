# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for how the API's schema is built the first time it is asked for.

FastAPI builds the document once and keeps it, but takes no lock while it does:
two requests arriving on a cold console each built the whole of it. The document
of this API is over a thousand operations and takes seconds to build, so the
second request doubled the wait of both, and a page that fetches it (the
console's API reference, a central reading a node's elevation map) waited for
twice the work. The console now builds it once, on the first request or before
anyone asks, and every caller after that gets the one it built.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import fastapi.applications
import pytest
from fastapi import FastAPI

from noust.web.api import openapi as openapi_module
from noust.web.api.openapi import install_openapi, warm_up
from scripts.console_server.settings_pages import warm_api_schema

CALLERS = 4


def _app() -> FastAPI:
    """
    Returns:
        A small application with the schema installed the way ``create_app`` does.
    """
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ping": "pong"}

    install_openapi(app)
    return app


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """
    FastAPI's own schema builder, counted and made slow enough for callers to overlap.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        One entry per build, which is the thread that made it.
    """
    made: list[int] = []
    real = fastapi.applications.get_openapi

    def build(*args: Any, **kwargs: Any) -> dict[str, Any]:
        made.append(threading.get_ident())
        # What lets the others arrive while this one is still building; the
        # assertions below wait for the threads, not for this.
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(fastapi.applications, "get_openapi", build)
    return made


def _ask_together(ask: Callable[[], dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Call something from several threads that all start at once.

    Args:
        ask: What each thread calls.

    Returns:
        What each of them got.
    """
    answers: list[dict[str, Any]] = []
    gate = threading.Barrier(CALLERS)

    def run() -> None:
        gate.wait()
        answers.append(ask())

    threads = [threading.Thread(target=run) for _ in range(CALLERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return answers


class TestOneBuildForEveryoneWhoAsksAtOnce:
    def test_requests_that_arrive_together_on_a_cold_application_share_one_build(
        self, builds: list[int]
    ) -> None:
        app = _app()

        answers = _ask_together(app.openapi)

        assert len(builds) == 1
        assert len(answers) == CALLERS
        # The very same document, not four equal ones.
        assert all(answer is answers[0] for answer in answers)

    def test_a_later_request_gets_what_was_built_and_builds_nothing(
        self, builds: list[int]
    ) -> None:
        app = _app()
        first = app.openapi()

        assert app.openapi() is first
        assert len(builds) == 1


class TestTheDocumentIsAnnotatedOncePerBuild:
    @pytest.fixture
    def annotated(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        """
        What ``annotate_version``, the last step, was given.

        Args:
            monkeypatch: Patching helper, scoped to the test.

        Returns:
            One entry per document annotated.
        """
        seen: list[dict[str, Any]] = []
        real = openapi_module.annotate_version

        def record(schema: dict[str, Any]) -> dict[str, Any]:
            seen.append(schema)
            return real(schema)

        monkeypatch.setattr(openapi_module, "annotate_version", record)
        return seen

    def test_asking_again_does_not_walk_every_route_again(
        self, annotated: list[dict[str, Any]]
    ) -> None:
        app = _app()

        first = app.openapi()
        app.openapi()
        app.openapi()

        assert len(annotated) == 1
        assert first["info"]["x-noust-version"]

    def test_a_document_rebuilt_after_a_route_is_added_is_annotated_again(
        self, annotated: list[dict[str, Any]]
    ) -> None:
        app = _app()
        app.openapi()

        @app.get("/added")
        def added() -> dict[str, str]:
            return {}

        # FastAPI's documented way to say the routes changed.
        app.openapi_schema = None
        rebuilt = app.openapi()

        assert "/added" in rebuilt["paths"]
        assert len(annotated) == 2
        assert rebuilt["info"]["x-noust-version"]


class TestWarmingUp:
    def test_the_document_is_built_before_anyone_asks_for_it(self, builds: list[int]) -> None:
        app = _app()

        thread = warm_up(app)
        thread.join()

        assert app.openapi_schema is not None
        app.openapi()
        assert len(builds) == 1

    def test_a_request_that_comes_during_the_warm_up_waits_for_it_and_builds_nothing(
        self, builds: list[int]
    ) -> None:
        app = _app()

        thread = warm_up(app)
        answer = app.openapi()
        thread.join()

        assert answer is app.openapi()
        assert len(builds) == 1

    def test_the_warm_up_is_a_thread_that_does_not_keep_the_process_alive(self) -> None:
        app = _app()

        thread = warm_up(app)
        thread.join()

        assert thread.daemon is True

    def test_a_build_that_fails_is_reported_by_the_thread_and_the_next_request_sees_it_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reported: list[BaseException | None] = []
        monkeypatch.setattr(threading, "excepthook", lambda args: reported.append(args.exc_value))

        def broken(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise ValueError("a model the schema cannot describe")

        monkeypatch.setattr(fastapi.applications, "get_openapi", broken)
        app = _app()

        warm_up(app).join()

        # Not swallowed: the thread's own report is the log, and nothing was kept
        # that would hide the failure from whoever asks next.
        assert [str(error) for error in reported] == ["a model the schema cannot describe"]
        with pytest.raises(ValueError, match="cannot describe"):
            app.openapi()


class TestTheConsoleServerWarmsItBeforeItIsReady:
    """
    The end-to-end suite starts one console server per worker, and its first test pays for
    whatever the server has not built yet. The schema takes seconds on an idle machine and more
    than a test's ten-second budget on a loaded one, so the page that lists it (Settings > API
    reference) failed on a runner with two cores whenever it came first.
    """

    def test_the_schema_is_built_by_the_time_the_warm_up_returns(self) -> None:
        app = _app()
        assert app.openapi_schema is None

        warm_api_schema(app)

        assert app.openapi_schema is not None

    def test_what_a_request_gets_afterwards_is_what_it_built(self) -> None:
        app = _app()

        warm_api_schema(app)

        assert app.openapi() is app.openapi_schema
        assert "/ping" in app.openapi()["paths"]
