# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The client of a server's API: addresses, retries, redirects and the error contract (J-12).

A token is a standing credential, so the client is strict about where it sends
one (HTTPS, or this machine) and what it does with a redirect (nothing). It is
patient only with what is safe to repeat. And a refusal keeps the server's
words: its error code, its hint and the output of the system underneath.
"""

from __future__ import annotations

import urllib.error

import pytest

from noust.client import ApiClient, RemoteError, normalise_url
from tests.client_support import Answer, Wire

TOKEN = "noust_tok_" + "x" * 40


def client(wire: Wire, sleeps: list[float] | None = None) -> ApiClient:
    """
    Args:
        wire: The scripted server.
        sleeps: Where the waits are recorded instead of slept.

    Returns:
        A client of ``https://noust.test`` over it.
    """
    log = sleeps if sleeps is not None else []
    return ApiClient("https://noust.test", TOKEN, opener=wire, sleep=log.append)


class TestAddresses:
    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("https://noust.test", "https://noust.test"),
            ("https://noust.test/", "https://noust.test"),
            ("noust.test", "https://noust.test"),
            ("https://noust.test:8443/console?x=1", "https://noust.test:8443"),
            ("http://localhost:8080", "http://localhost:8080"),
            ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
            ("http://[::1]:8080", "http://[::1]:8080"),
        ],
    )
    def test_an_address_is_reduced_to_scheme_host_and_port(self, typed: str, expected: str) -> None:
        assert normalise_url(typed) == expected

    def test_a_token_is_not_sent_in_clear_over_a_network(self) -> None:
        with pytest.raises(RemoteError, match="clear text") as caught:
            normalise_url("http://noust.example.com")

        assert "https://" in caught.value.details

    @pytest.mark.parametrize(
        "typed", ["https://user:pw@noust.test", "ftp://noust.test", "https://", ""]
    )
    def test_an_address_with_credentials_or_no_host_is_refused(self, typed: str) -> None:
        with pytest.raises(RemoteError):
            normalise_url(typed)

    def test_a_client_needs_a_token(self) -> None:
        with pytest.raises(RemoteError, match="no token"):
            ApiClient("https://noust.test", "   ")


class TestACall:
    def test_the_token_goes_in_one_header_and_nowhere_else(self) -> None:
        wire = Wire().on("GET", "/api/apps", Answer(body={"apps": []}))

        body = client(wire).get("/apps")

        assert body == {"apps": []}
        method, path, headers, _ = wire.seen[0]
        assert (method, path) == ("GET", "/api/apps")
        assert headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in path

    def test_a_query_leaves_out_what_is_none_and_spells_booleans(self) -> None:
        wire = Wire().on("GET", "/api/apps?status=running&unmask=false", Answer(body={}))

        client(wire).get("/apps", status="running", team=None, unmask=False)

        assert wire.seen[0][1] == "/api/apps?status=running&unmask=false"

    def test_a_full_api_path_is_not_prefixed_twice(self) -> None:
        wire = Wire().on("GET", "/api/jobs", Answer(body={}))

        client(wire).get("/api/jobs")

        assert wire.seen[0][1] == "/api/jobs"

    def test_a_body_is_sent_as_json(self) -> None:
        wire = Wire().on("POST", "/api/jobs/update", Answer(202, {"job": {"id": "abc"}}))

        client(wire).post("/jobs/update", {"domain": "shop.example.com", "force": True})

        _, _, headers, body = wire.seen[0]
        assert body == {"domain": "shop.example.com", "force": True}
        assert headers["content-type"] == "application/json"

    def test_an_answer_that_is_not_json_is_not_taken_for_one(self) -> None:
        wire = Wire().on("GET", "/api/apps", Answer(body="<html>proxy login</html>"))

        with pytest.raises(RemoteError, match="did not answer with JSON") as caught:
            client(wire).get("/apps")

        assert caught.value.code == "bad_response"


class TestRefusals:
    def test_the_servers_own_words_are_kept(self) -> None:
        wire = Wire().on(
            "POST",
            "/api/jobs/update",
            Answer(
                409,
                {
                    "error": "nothing_new",
                    "detail": "The branch has no commit the live build lacks",
                    "hint": "Use force to rebuild anyway",
                    "fields": None,
                    "output": "nginx: configuration file test failed\n",
                },
            ),
        )

        with pytest.raises(RemoteError) as caught:
            client(wire).post("/jobs/update", {"domain": "x"})

        error = caught.value
        assert (error.status, error.code) == (409, "nothing_new")
        assert error.message == "The branch has no commit the live build lacks"
        assert error.details == "Use force to rebuild anyway"
        assert error.output == "nginx: configuration file test failed\n"

    def test_a_contract_nested_in_detail_is_read_too(self) -> None:
        wire = Wire().on(
            "GET",
            "/api/apps/x",
            Answer(
                403,
                {
                    "detail": {
                        "error": "permission_denied",
                        "detail": "Needs apps.manage",
                        "hint": "Ask",
                    }
                },
            ),
        )

        with pytest.raises(RemoteError) as caught:
            client(wire).get("/apps/x")

        assert caught.value.code == "permission_denied"
        assert caught.value.message == "Needs apps.manage"

    def test_a_plain_text_error_still_says_something(self) -> None:
        wire = Wire().on("GET", "/api/apps", Answer(502, "Bad gateway"))

        with pytest.raises(RemoteError) as caught:
            client(wire, []).get("/apps")

        assert caught.value.status == 502

    def test_the_token_is_never_in_an_error(self) -> None:
        wire = Wire().on("GET", "/api/apps", Answer(401, {"detail": "Invalid credentials"}))

        with pytest.raises(RemoteError) as caught:
            client(wire).get("/apps")

        assert TOKEN not in str(caught.value)


class TestPatience:
    def test_a_read_is_tried_again_after_a_server_error(self) -> None:
        sleeps: list[float] = []
        wire = Wire().on(
            "GET", "/api/apps", Answer(503, {"detail": "starting"}), Answer(200, {"apps": []})
        )

        body = client(wire, sleeps).get("/apps")

        assert body == {"apps": []}
        assert len(wire.seen) == 2 and sleeps

    def test_a_read_gives_up_after_three_tries(self) -> None:
        wire = Wire().on("GET", "/api/apps", Answer(500, {"detail": "boom"}))

        with pytest.raises(RemoteError):
            client(wire).get("/apps")

        assert len(wire.seen) == 3

    def test_a_write_is_not_repeated_after_a_server_error(self) -> None:
        wire = Wire().on("POST", "/api/jobs/update", Answer(500, {"detail": "boom"}))

        with pytest.raises(RemoteError):
            client(wire).post("/jobs/update", {"domain": "x"})

        assert len(wire.seen) == 1

    def test_a_refused_for_being_busy_is_tried_again_whatever_it_was(self) -> None:
        sleeps: list[float] = []
        wire = Wire().on(
            "POST",
            "/api/jobs/update",
            Answer(429, {"detail": "slow down"}, {"Retry-After": "2"}),
            Answer(202, {"job": {"id": "abc"}}),
        )

        client(wire, sleeps).post("/jobs/update", {"domain": "x"})

        assert sleeps == [2.0]
        assert len(wire.seen) == 2

    def test_retry_after_is_honoured_but_bounded(self) -> None:
        sleeps: list[float] = []
        wire = Wire().on(
            "GET",
            "/api/apps",
            Answer(429, {}, {"Retry-After": "86400"}),
            Answer(200, {"apps": []}),
        )

        client(wire, sleeps).get("/apps")

        assert sleeps == [30.0]

    def test_a_dropped_connection_is_tried_again_for_a_read_only(self) -> None:
        wire = Wire().on(
            "GET", "/api/apps", urllib.error.URLError("reset"), Answer(200, {"apps": []})
        )
        assert client(wire).get("/apps") == {"apps": []}

        writes = Wire().on("POST", "/api/jobs/update", urllib.error.URLError("reset"))
        with pytest.raises(RemoteError, match="Cannot reach") as caught:
            client(writes).post("/jobs/update", {"domain": "x"})
        assert caught.value.code == "unreachable"
        assert len(writes.seen) == 1


class TestRedirects:
    def test_a_redirect_is_an_error_and_is_never_followed(self) -> None:
        wire = Wire().on(
            "GET", "/api/apps", Answer(302, {}, {"Location": "https://elsewhere.example/api/apps"})
        )

        with pytest.raises(RemoteError) as caught:
            client(wire).get("/apps")

        assert caught.value.status == 302
        assert len(wire.seen) == 1

    def test_the_real_opener_has_no_redirect_handler_that_follows(self) -> None:
        from noust.client.api import _NoRedirect

        assert _NoRedirect().redirect_request(object(), None, 302, "Found", {}, "https://x") is None  # type: ignore[call-arg]
