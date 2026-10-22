# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Python client of the API, generated from its schema (checklist J-18).

``scripts/generate_sdk.py`` writes ``sdk/python/noust_sdk`` from ``panel/openapi.json``. These
tests hold three things: that what is committed is what the generator writes now, that it
covers every operation of the schema with the right verb and path, and that the client does
what it promises against the real application: the token in a header, the error contract
turned into exceptions, sudo mode and the rate limit told apart, a job waited for. The whole
package also passes mypy in strict mode, which is what a user's editor will ask of it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from noust.core.store import NoustStore
from tests.teams_support import World, build_world

ROOT = Path(__file__).parent.parent
SDK = ROOT / "sdk" / "python"
SCHEMA = ROOT / "panel" / "openapi.json"


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_sdk_script", ROOT / "scripts" / "generate_sdk.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_sdk_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sdk() -> Iterator[ModuleType]:
    """The package, imported from where it lives and forgotten afterwards."""
    sys.path.insert(0, str(SDK))
    try:
        import noust_sdk

        yield noust_sdk
    finally:
        sys.path.remove(str(SDK))
        for name in [n for n in sys.modules if n == "noust_sdk" or n.startswith("noust_sdk.")]:
            del sys.modules[name]


class TestWhatIsGenerated:
    def test_the_files_are_what_the_generator_writes_now(self) -> None:
        stale = [
            str(path.relative_to(ROOT))
            for path, text in load_generator().generate().items()
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]

        assert stale == [], f"run python scripts/generate_sdk.py: {stale}"

    def test_every_operation_of_the_api_is_a_method_with_its_own_verb_and_path(
        self, sdk: ModuleType
    ) -> None:
        document = json.loads(SCHEMA.read_text(encoding="utf-8"))
        wanted = {
            (verb.upper(), path): (
                operation.get("x-noust-permission"),
                bool(operation.get("x-noust-requires-elevation")),
            )
            for path, item in document["paths"].items()
            for verb, operation in item.items()
            if verb in ("get", "post", "put", "patch", "delete")
        }

        found = {(v, p): (perm, sudo) for v, p, perm, sudo in sdk.OPERATIONS.values()}

        assert found == wanted
        assert len(sdk.OPERATIONS) == len(wanted), "two methods for one operation"

    def test_each_listed_operation_is_reachable_from_the_client(self, sdk: ModuleType) -> None:
        client = sdk.Client("http://localhost", token="t", transport=lambda request: None)
        missing = [
            (group, name)
            for group, name in sdk.OPERATIONS
            if not callable(getattr(getattr(client, group, None), name, None))
        ]

        assert missing == []

    def test_the_whole_package_passes_mypy_in_strict_mode(self) -> None:
        from mypy import api

        out, err, status = api.run(
            [
                "--config-file",
                str(SDK / "pyproject.toml"),
                "--no-incremental",
                str(SDK / "noust_sdk"),
            ]
        )

        assert status == 0, out + err


def answer(
    status: int = 200, body: Any = None, headers: dict[str, str] | None = None
) -> Callable[[Any], Any]:
    """
    Args:
        status: What the fake server answers.
        body: The JSON body, or bytes for a file.
        headers: Its headers.

    Returns:
        A transport that records what it was asked and answers that.
    """

    def transport(request: Any) -> Any:
        from noust_sdk import Response

        transport.requests.append(request)  # type: ignore[attr-defined]
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return Response(status, {k.lower(): v for k, v in (headers or {}).items()}, raw)

    transport.requests = []  # type: ignore[attr-defined]
    return transport


class TestTheRequest:
    def test_the_token_is_a_header_and_never_part_of_the_address(self, sdk: ModuleType) -> None:
        fake = answer(body={"apps": []})
        client = sdk.Client("https://noust.example.com", token="noust_tok_secret", transport=fake)

        client.applications.list_apps(team="acme")

        request = fake.requests[0]  # type: ignore[attr-defined]
        assert request.headers["Authorization"] == "Bearer noust_tok_secret"
        assert "noust_tok_secret" not in request.url
        assert request.url == "https://noust.example.com/api/apps?team=acme"

    def test_what_is_none_is_left_out_and_a_boolean_is_spelled_the_way_the_api_reads_it(
        self, sdk: ModuleType
    ) -> None:
        fake = answer(body={})
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)

        client.applications.list_apps(team=None, tag="prod")
        client.request("GET", "/api/x", query={"unmask": True, "dry": False, "n": 3})

        first, second = fake.requests  # type: ignore[attr-defined]
        assert urllib.parse.urlsplit(first.url).query == "tag=prod"
        assert urllib.parse.parse_qs(urllib.parse.urlsplit(second.url).query) == {
            "unmask": ["true"],
            "dry": ["false"],
            "n": ["3"],
        }

    def test_a_path_value_is_quoted_so_it_cannot_change_the_route(self, sdk: ModuleType) -> None:
        fake = answer(body={})
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)

        client.applications.get_app("a/b?c#d")

        assert fake.requests[0].url.endswith("/api/apps/a%2Fb%3Fc%23d")  # type: ignore[attr-defined]

    def test_a_body_is_json(self, sdk: ModuleType) -> None:
        fake = answer(202, {"job_id": "j1"})
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)

        client.applications.create_app({"domain": "a.example", "source": "s"})

        request = fake.requests[0]  # type: ignore[attr-defined]
        assert request.method == "POST"
        assert json.loads(request.body) == {"domain": "a.example", "source": "s"}
        assert request.headers["Content-Type"] == "application/json"

    def test_a_file_comes_back_as_bytes(self, sdk: ModuleType) -> None:
        document = json.loads(SCHEMA.read_text(encoding="utf-8"))
        files = []
        for (group, name), (verb, path, *_rest) in sdk.OPERATIONS.items():
            operation = document["paths"][path][verb.lower()]
            for code, reply in operation["responses"].items():
                content = reply.get("content") or {}
                if code.startswith("2") and content and "application/json" not in content:
                    files.append((group, name, operation))
        assert files, "the API has downloads"
        group, name, operation = files[0]
        fake = answer(200, b"PK\x03\x04data")
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)
        parameters = operation.get("parameters", [])
        positional = ["x" for p in parameters if p["in"] == "path"]
        required = {p["name"]: "x" for p in parameters if p["in"] == "query" and p.get("required")}

        assert getattr(getattr(client, group), name)(*positional, **required) == b"PK\x03\x04data"


class TestWhatTheServerRefuses:
    def test_the_error_contract_becomes_an_exception_that_keeps_its_words(
        self, sdk: ModuleType
    ) -> None:
        fake = answer(
            400,
            {
                "error": "validation_error",
                "detail": "Port 70000 is not valid",
                "hint": "Use 1 to 65535",
                "fields": {"port": "too big"},
                "output": "nginx: [emerg] bad port",
            },
        )
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)

        with pytest.raises(sdk.APIError) as caught:
            client.applications.create_app({"domain": "a.example"})

        error = caught.value
        assert (error.status, error.code) == (400, "validation_error")
        assert error.detail == "Port 70000 is not valid" and error.hint == "Use 1 to 65535"
        assert error.output == "nginx: [emerg] bad port"
        assert "Use 1 to 65535" in str(error)

    def test_an_error_raised_as_an_http_exception_is_read_too(self, sdk: ModuleType) -> None:
        fake = answer(
            409,
            {"detail": {"error": "nothing_new", "detail": "No new commits", "hint": "--force"}},
        )
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)

        with pytest.raises(sdk.APIError) as caught:
            client.applications.get_app("a.example")

        assert (caught.value.code, caught.value.detail) == ("nothing_new", "No new commits")

    def test_sudo_mode_is_its_own_class(self, sdk: ModuleType) -> None:
        fake = answer(403, {"error": "elevation_required", "detail": "Confirm it's you"})
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)

        with pytest.raises(sdk.ElevationRequired):
            client.applications.delete_app("a.example")

    def test_the_rate_limit_says_how_long_to_wait_and_a_change_is_not_repeated(
        self, sdk: ModuleType
    ) -> None:
        fake = answer(429, {"error": "rate_limited", "detail": "Slow down"}, {"Retry-After": "7"})
        client = sdk.Client("https://noust.example.com", token="t", transport=fake)

        with pytest.raises(sdk.RateLimited) as caught:
            client.applications.create_app({"domain": "a.example"})

        assert caught.value.retry_after == 7.0
        assert len(fake.requests) == 1  # type: ignore[attr-defined]

    def test_a_read_is_repeated_after_a_gateway_error_and_then_succeeds(
        self, sdk: ModuleType
    ) -> None:
        from noust_sdk import Response

        calls = []

        def flaky(request: Any) -> Any:
            calls.append(request)
            if len(calls) < 3:
                return Response(503, {"retry-after": "1"}, b"{}")
            return Response(200, {}, b'{"apps": []}')

        client = sdk.Client("https://noust.example.com", token="t", transport=flaky, retries=2)
        slept: list[float] = []
        client._sleep = slept.append  # type: ignore[assignment]

        assert client.applications.list_apps() == {"apps": []}
        assert len(calls) == 3 and slept == [1.0, 1.0]

    def test_a_server_that_does_not_answer_json_is_said_so(self, sdk: ModuleType) -> None:
        client = sdk.Client(
            "https://noust.example.com", token="t", transport=answer(200, b"<html>login</html>")
        )

        with pytest.raises(sdk.ConnectionFailed, match="did not answer JSON"):
            client.applications.list_apps()


class TestTheAddress:
    def test_plain_http_is_for_loopback_only_since_the_token_would_cross_in_clear(
        self, sdk: ModuleType
    ) -> None:
        for fine in ("http://localhost:8080", "http://127.0.0.1:8080", "https://noust.example.com"):
            sdk.Client(fine, token="t", transport=answer())
        with pytest.raises(ValueError, match="in clear"):
            sdk.Client("http://noust.example.com", token="t", transport=answer())

    @pytest.mark.parametrize("url", ["", "ftp://x", "noust.example.com", "https://"])
    def test_what_is_not_an_http_address_is_refused(self, sdk: ModuleType, url: str) -> None:
        with pytest.raises(ValueError):
            sdk.Client(url, token="t", transport=answer())

    def test_the_environment_holds_both_so_a_pipeline_passes_neither_as_an_argument(
        self, sdk: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_URL", "https://noust.example.com")
        monkeypatch.setenv("NOUST_TOKEN", "from-the-environment")
        fake = answer(body={})

        sdk.Client.from_environment(transport=fake).applications.list_apps()

        assert fake.requests[0].headers["Authorization"] == "Bearer from-the-environment"  # type: ignore[attr-defined]
        monkeypatch.delenv("NOUST_URL")
        with pytest.raises(ValueError, match="NOUST_URL"):
            sdk.Client.from_environment()

    def test_a_redirect_is_not_followed_since_it_would_carry_the_token(
        self, sdk: ModuleType
    ) -> None:
        from noust_sdk.client import _NoRedirect

        assert _NoRedirect().redirect_request() is None


class TestWaitingForAJob:
    def jobs(self, sdk: ModuleType, *states: str) -> tuple[Any, list[Any]]:
        from noust_sdk import Response

        seen: list[Any] = []

        def transport(request: Any) -> Any:
            seen.append(request)
            state = states[min(len(seen), len(states)) - 1]
            return Response(
                200, {}, json.dumps({"id": "j1", "state": state, "error": "boom"}).encode()
            )

        client = sdk.Client("https://noust.example.com", token="t", transport=transport)
        client._sleep = lambda seconds: None  # type: ignore[assignment]
        return client, seen

    def test_it_follows_the_job_to_its_end_from_the_answer_that_queued_it(
        self, sdk: ModuleType
    ) -> None:
        client, seen = self.jobs(sdk, "queued", "running", "succeeded")

        job = client.wait_for_job({"job_id": "j1", "status": "pending"})

        assert job["state"] == "succeeded" and len(seen) == 3
        assert seen[0].url.endswith("/api/jobs/j1")

    def test_a_job_that_failed_raises_with_what_the_server_said(self, sdk: ModuleType) -> None:
        client, _ = self.jobs(sdk, "running", "failed")

        with pytest.raises(sdk.JobFailed, match="boom") as caught:
            client.wait_for_job("j1")

        assert caught.value.job["state"] == "failed"

    def test_it_can_return_the_failed_job_instead(self, sdk: ModuleType) -> None:
        client, _ = self.jobs(sdk, "failed")

        assert client.wait_for_job("j1", raise_on_failure=False)["state"] == "failed"

    def test_a_wait_that_runs_out_returns_the_job_still_running(self, sdk: ModuleType) -> None:
        client, _ = self.jobs(sdk, "running")

        assert client.wait_for_job("j1", timeout=0)["state"] == "running"

    def test_something_that_is_not_a_job_is_refused(self, sdk: ModuleType) -> None:
        client, _ = self.jobs(sdk, "running")

        with pytest.raises(ValueError, match="no id"):
            client.wait_for_job({"message": "nothing queued"})


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def through(world: World) -> Callable[[Any], Any]:
    """
    Args:
        world: The console under test.

    Returns:
        A transport that hands each request to the real application.
    """

    def send(request: Any) -> Any:
        from noust_sdk import Response

        parts = urllib.parse.urlsplit(request.url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        reply = world.client.request(
            request.method, target, headers=request.headers, content=request.body
        )
        return Response(
            reply.status_code, {k.lower(): v for k, v in reply.headers.items()}, reply.content
        )

    return send


class TestAgainstTheRealApplication:
    def client(self, sdk: ModuleType, world: World, who: str) -> Any:
        token = world.as_(who)["Authorization"].removeprefix("Bearer ")
        return sdk.Client("http://localhost", token=token, transport=through(world))

    def test_a_team_member_lists_only_the_applications_of_its_team(
        self, sdk: ModuleType, world: World
    ) -> None:
        mine = self.client(sdk, world, "alice").applications.list_apps()
        everything = self.client(sdk, world, "admin").applications.list_apps()

        assert {a["domain"] for a in mine["apps"]} == {"shop.acme.example"}
        assert {a["domain"] for a in everything["apps"]} == {
            "shop.acme.example",
            "blog.globex.example",
        }

    def test_the_servers_refusal_arrives_as_an_exception_with_its_words(
        self, sdk: ModuleType, world: World
    ) -> None:
        viewer = self.client(sdk, world, "dana")

        with pytest.raises(sdk.APIError) as caught:
            viewer.applications.create_app({"domain": "new.acme.example", "source": "s"})

        assert caught.value.status == 403
        assert caught.value.code == "permission_denied"
        assert "apps.manage" in caught.value.detail

    def test_what_the_caller_cannot_see_is_not_found_rather_than_forbidden(
        self, sdk: ModuleType, world: World
    ) -> None:
        with pytest.raises(sdk.APIError) as caught:
            self.client(sdk, world, "alice").applications.get_app("blog.globex.example")

        assert caught.value.status == 404

    def test_a_token_that_cannot_confirm_sudo_mode_is_told_so(
        self, sdk: ModuleType, world: World
    ) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("alice")
        issued = get_token_manager().create_api_token(
            "no-sudo", "admin", None, owner=account, allow_elevated=False
        )
        client = sdk.Client("http://localhost", token=issued["token"], transport=through(world))

        with pytest.raises(sdk.ElevationRequired) as caught:
            client.applications.delete_app("shop.acme.example")

        assert caught.value.code == "elevation_required"

    def test_the_token_is_not_in_what_an_error_says(self, sdk: ModuleType, world: World) -> None:
        token = world.as_("dana")["Authorization"].removeprefix("Bearer ")
        client = sdk.Client("http://localhost", token=token, transport=through(world))

        with pytest.raises(sdk.APIError) as caught:
            client.applications.create_app({"domain": "x.acme.example", "source": "s"})

        assert token not in str(caught.value) and token not in repr(caught.value.body)
