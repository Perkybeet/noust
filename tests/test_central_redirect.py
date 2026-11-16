# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The plain HTTP port of a central that only says where the console is (item 199).

The store of a NAS opens an app at ``http://<host>:<declared port>``, and the console only speaks TLS,
so the icon opened a page that never loaded. A central in a container can listen on one more port
whose whole job is a ``308`` to the console over HTTPS. It is a new door in cleartext, so what it
accepts is an allowlist, what it answers is built from what was validated and never from an echo,
it reads no body, it sets no cookie, and nothing of the console is reachable through it.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

import pytest

from noust.central import redirect
from noust.core.exceptions import NoustError, ValidationError
from noust.core.net import ALL_INTERFACES

HTTPS_PORT = 8443


def request(
    target: str = "/",
    *,
    method: str = "GET",
    host: str | None = "nas.example.net:8442",
    version: str = "HTTP/1.1",
    extra: tuple[str, ...] = (),
) -> bytes:
    lines = [f"{method} {target} {version}"]
    if host is not None:
        lines.append(f"Host: {host}")
    lines.extend(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


def reply(raw: bytes, https_port: int = HTTPS_PORT) -> tuple[int, dict[str, str], bytes]:
    """Split what :func:`redirect.answer` returns into a status, its headers and the body."""
    out = redirect.answer(raw, https_port)
    assert out is not None, "the listener closed without a word"
    head, _, body = out.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(": ")
        assert name.lower() not in headers, f"{name} is sent twice"
        headers[name.lower()] = value
    return status, headers, body


class TestWhatItRedirectsTo:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("nas.example.net:8442", "https://nas.example.net:8443/"),
            ("nas.example.net", "https://nas.example.net:8443/"),
            ("NAS.Example.NET", "https://nas.example.net:8443/"),
            ("192.0.2.10:8442", "https://192.0.2.10:8443/"),
            ("[2001:db8::1]:8442", "https://[2001:db8::1]:8443/"),
            ("[2001:db8::1]", "https://[2001:db8::1]:8443/"),
            ("nas", "https://nas:8443/"),
        ],
    )
    def test_it_keeps_the_host_the_browser_asked_for_and_changes_the_port(
        self, host: str, expected: str
    ) -> None:
        status, headers, body = reply(request(host=host))

        assert status == 308
        assert headers["location"] == expected
        assert body == b""

    def test_the_port_is_the_one_the_console_has_from_outside(self) -> None:
        _, headers, _ = reply(request(), https_port=18443)

        assert headers["location"] == "https://nas.example.net:18443/"

    def test_the_default_port_of_https_is_not_written(self) -> None:
        _, headers, _ = reply(request("/login?next=%2Fapps"), https_port=443)

        assert headers["location"] == "https://nas.example.net/login?next=%2Fapps"

    @pytest.mark.parametrize(
        "target",
        [
            "/",
            "/login",
            "/apps/shop.example.com/logs?lines=50&follow=1",
            "/a/b/c.js?v=1",
            "/%2e%2e/etc/passwd",
            "/?q=%0d%0aSet-Cookie:%20x=1",
            "/?",
            "//other.example/x",
            "///other.example",
            "/api/system/health",
            "/assets/index-abc123.js",
        ],
    )
    def test_the_path_and_the_query_go_as_they_came(self, target: str) -> None:
        status, headers, _ = reply(request(target))

        assert status == 308
        assert headers["location"] == f"https://nas.example.net:{HTTPS_PORT}{target}"

    def test_a_path_that_starts_with_two_slashes_stays_on_the_host(self) -> None:
        _, headers, _ = reply(request("//other.example/x"))

        assert headers["location"].startswith("https://nas.example.net:8443//other.example")
        assert headers["location"].split("/")[2] == "nas.example.net:8443"

    def test_head_gets_the_same_answer_as_get(self) -> None:
        assert reply(request(method="HEAD"))[:2] == reply(request())[:2]

    def test_http_1_0_with_a_host_is_answered_too(self) -> None:
        assert reply(request(version="HTTP/1.0"))[0] == 308


class TestWhatTheAnswerIs:
    def test_a_redirect_has_no_body_no_cookie_and_is_not_kept(self) -> None:
        status, headers, body = reply(request())

        assert status == 308
        assert body == b""
        assert headers["content-length"] == "0"
        assert headers["connection"] == "close"
        assert headers["cache-control"] == "no-store"
        assert "set-cookie" not in headers
        assert set(headers) <= {"location", "content-length", "connection", "cache-control", "date"}

    def test_it_never_says_what_answers(self) -> None:
        _, headers, _ = reply(request())

        assert "server" not in headers and "x-powered-by" not in headers

    def test_a_request_never_makes_it_set_a_cookie_or_read_one(self) -> None:
        status, headers, _ = reply(request(extra=("Cookie: noust_session=abc",)))

        assert status == 308 and "set-cookie" not in headers


class TestWhatItRefuses:
    @pytest.mark.parametrize(
        "method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]
    )
    def test_only_get_and_head_are_redirected_and_the_rest_is_told_so(self, method: str) -> None:
        status, headers, body = reply(request(method=method))

        assert status == 405
        assert headers["allow"] == "GET, HEAD"
        assert body == b""
        assert "location" not in headers

    def test_a_body_is_never_asked_for_after_a_refusal(self) -> None:
        raw = request(method="POST", extra=("Content-Length: 5",)) + b"hello"

        status, _, _ = reply(raw)

        assert status == 405

    @pytest.mark.parametrize(
        "host",
        [
            None,
            "",
            " ",
            "evil.example/path",
            "evil.example@other.example",
            "user:pass@nas.example.net",
            "nas.example.net:8442:1",
            "nas.example.net:",
            "nas.example.net:99999",
            "nas.example.net:0",
            "nas.example.net:abc",
            "nas example.net",
            "nas.example.net\x00",
            "nas.example.net,other.example",
            "-nas.example.net",
            "nas-.example.net",
            "nas..example.net",
            ".nas.example.net",
            "nas.example.net.",
            "[::1",
            "::1",
            "[2001:db8::1%25eth0]",
            "[fe80::1%eth0]",
            "[nas.example.net]",
            "0x7f.1",
            "1.2.3",
            "999.1.1.1",
            "a" * 300 + ".example",
            "\uff4eas.example.net",
            "nas.example.net\\x",
            "nas.example.net#frag",
            "nas.example.net?x=1",
        ],
    )
    def test_a_host_that_is_not_a_name_or_an_address_gets_a_400_that_does_not_repeat_it(
        self, host: str | None
    ) -> None:
        raw = request(host=host)
        out = redirect.answer(raw, HTTPS_PORT)
        assert out is not None

        head, _, body = out.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 400 ")
        assert body == b""
        assert b"location" not in head.lower()
        if host and host.strip():
            assert host.encode("utf-8", "replace") not in out

    def test_two_hosts_are_ambiguous_and_get_a_400(self) -> None:
        raw = request(extra=("Host: other.example",))

        assert reply(raw)[0] == 400

    @pytest.mark.parametrize(
        "target",
        [
            "http://evil.example/",
            "https://evil.example/x",
            "//evil.example",
            "evil.example:443",
            "*",
            "@evil.example",
            "x",
            "",
            "/a b",
            "/\\evil.example",
            "/a#b",
            "/\x7f",
            "/é",
        ],
    )
    def test_only_a_path_is_a_target(self, target: str) -> None:
        raw = f"GET {target} HTTP/1.1\r\nHost: nas.example.net\r\n\r\n".encode()

        if target == "//evil.example":
            # A path of two slashes is a path: it stays under the host (above).
            assert reply(raw)[0] == 308
            return
        out = redirect.answer(raw, HTTPS_PORT)
        assert out is not None
        assert out.startswith(b"HTTP/1.1 400 ") or out.startswith(b"HTTP/1.1 414 ")
        assert b"evil.example" not in out

    def test_a_target_that_is_too_long_is_refused(self) -> None:
        status, _, _ = reply(request("/" + "a" * 5000))

        assert status == 414

    def test_too_many_header_fields_are_refused(self) -> None:
        raw = request(extra=tuple(f"X-{n}: 1" for n in range(200)))

        assert reply(raw)[0] == 431

    @pytest.mark.parametrize(
        "raw",
        [
            b"",
            b"\r\n\r\n",
            b"GET\r\n\r\n",
            b"GET /\r\n\r\n",
            b"GET / HTTP/1.1 extra\r\nHost: nas.example.net\r\n\r\n",
            b"GET /  HTTP/1.1\r\nHost: nas.example.net\r\n\r\n",
            b"get / HTTP/1.1\r\nHost: nas.example.net\r\n\r\n",
            b"GET / HTTP/1.1\nHost: nas.example.net\n\n",
            b"GET / HTTP/1.1\r\nHost: nas.example.net\r\n bad: fold\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost : nas.example.net\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost nas.example.net\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: nas.example.net\r\nX: a\x00b\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: nas.example.net\rX: y\r\n\r\n",
            b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03",
            b"GET / HTTP/2.0\r\nHost: nas.example.net\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: nas.example.net\r\n\xff: x\r\n\r\n",
        ],
    )
    def test_whatever_is_not_a_request_is_a_4xx_or_nothing_and_never_a_redirect(
        self, raw: bytes
    ) -> None:
        out = redirect.answer(raw, HTTPS_PORT)

        if out is not None:
            assert out[9:10] in (b"4", b"5"), out[:40]
            assert b"location" not in out.lower()

    def test_an_unknown_version_is_a_505(self) -> None:
        out = redirect.answer(b"GET / HTTP/2.0\r\nHost: nas.example.net\r\n\r\n", HTTPS_PORT)

        assert out is not None and out.startswith(b"HTTP/1.1 505 ")


class TestTheSettings:
    def test_without_a_port_there_is_no_listener(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOUST_REDIRECT_PORT", raising=False)

        assert redirect.from_environment(console_port=8443, host=ALL_INTERFACES) is None

    def test_the_port_the_console_has_from_outside_defaults_to_its_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_REDIRECT_PORT", "8442")
        monkeypatch.delenv("NOUST_PUBLIC_HTTPS_PORT", raising=False)

        server = redirect.from_environment(console_port=8443, host=ALL_INTERFACES)

        assert server is not None
        assert (server.port, server.https_port) == (8442, 8443)

    def test_the_port_from_outside_can_be_another(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOUST_REDIRECT_PORT", "8442")
        monkeypatch.setenv("NOUST_PUBLIC_HTTPS_PORT", "18443")

        server = redirect.from_environment(console_port=8443, host=ALL_INTERFACES)

        assert server is not None and server.https_port == 18443

    @pytest.mark.parametrize(
        "value",
        ["0", "65536", "-1", "80.0", "8442 ", "0x20fa", "٨٤٤٢", "abc", "8_442", "+8442", ""],
    )
    def test_a_redirect_port_that_is_not_a_port_is_said_and_not_used(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("NOUST_REDIRECT_PORT", value)
        if value == "":
            assert redirect.from_environment(console_port=8443, host=ALL_INTERFACES) is None
            return

        with pytest.raises(ValidationError, match="NOUST_REDIRECT_PORT"):
            redirect.from_environment(console_port=8443, host=ALL_INTERFACES)

    @pytest.mark.parametrize("value", ["0", "65536", "abc", "-3"])
    def test_a_public_port_that_is_not_a_port_is_said_and_not_used(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("NOUST_REDIRECT_PORT", "8442")
        monkeypatch.setenv("NOUST_PUBLIC_HTTPS_PORT", value)

        with pytest.raises(ValidationError, match="NOUST_PUBLIC_HTTPS_PORT"):
            redirect.from_environment(console_port=8443, host=ALL_INTERFACES)

    def test_the_console_port_cannot_be_the_redirect_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_REDIRECT_PORT", "8443")

        with pytest.raises(ValidationError, match="NOUST_REDIRECT_PORT"):
            redirect.from_environment(console_port=8443, host=ALL_INTERFACES)


def one_exchange(port: int, payload: bytes, *, read_until_close: bool = True) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(payload)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = client.recv(4096)
            except (ConnectionResetError, TimeoutError):
                break
            if not chunk:
                break
            chunks.append(chunk)
            if not read_until_close:
                break
        return b"".join(chunks)


@pytest.fixture
def listener() -> Any:
    server = redirect.RedirectServer("127.0.0.1", 0, HTTPS_PORT)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.mark.allow_sockets
class TestOnARealSocket:
    def test_a_browser_that_opens_the_icon_is_sent_to_the_console(self, listener: Any) -> None:
        raw = one_exchange(listener.port, request("/", host=f"127.0.0.1:{listener.port}"))

        assert raw.startswith(b"HTTP/1.1 308 ")
        assert f"Location: https://127.0.0.1:{HTTPS_PORT}/\r\n".encode() in raw

    def test_it_answers_one_request_and_closes(self, listener: Any) -> None:
        raw = one_exchange(
            listener.port,
            request("/a", host="nas.example.net") + request("/b", host="nas.example.net"),
        )

        assert raw.count(b"HTTP/1.1 308") == 1 and b"/b" not in raw

    def test_a_body_that_never_comes_does_not_hold_it(self, listener: Any) -> None:
        raw = one_exchange(
            listener.port,
            request(method="POST", extra=("Content-Length: 100000",)),
        )

        assert raw.startswith(b"HTTP/1.1 405 ")

    def test_a_client_that_says_nothing_is_dropped_after_the_time_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(redirect, "TIME_LIMIT_SECONDS", 0.4)
        server = redirect.RedirectServer("127.0.0.1", 0, HTTPS_PORT)
        server.start()
        try:
            started = time.monotonic()
            raw = one_exchange(server.port, b"GET / HTTP/1.1\r\nHost: nas.exa")
            took = time.monotonic() - started

            assert raw == b""
            assert took < 3
        finally:
            server.stop()

    def test_a_head_larger_than_the_limit_is_refused_without_reading_on(
        self, listener: Any
    ) -> None:
        raw = one_exchange(listener.port, b"GET / HTTP/1.1\r\nX: " + b"a" * 20000)

        assert raw.startswith(b"HTTP/1.1 431 ")

    def test_more_connections_than_the_limit_are_closed_and_the_rest_still_answered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(redirect, "MAX_CONNECTIONS", 2)
        monkeypatch.setattr(redirect, "TIME_LIMIT_SECONDS", 2.0)
        server = redirect.RedirectServer("127.0.0.1", 0, HTTPS_PORT)
        server.start()
        holders = [socket.create_connection(("127.0.0.1", server.port)) for _ in range(2)]
        try:
            time.sleep(0.2)
            assert one_exchange(server.port, request()) == b""
        finally:
            for holder in holders:
                holder.close()
        time.sleep(0.3)
        try:
            assert one_exchange(server.port, request()).startswith(b"HTTP/1.1 308 ")
        finally:
            server.stop()

    def test_a_client_outside_the_allow_list_is_closed_on_without_a_word(self) -> None:
        server = redirect.RedirectServer(
            "127.0.0.1", 0, HTTPS_PORT, allow=lambda address: address != "127.0.0.1"
        )
        server.start()
        try:
            assert one_exchange(server.port, request()) == b""
        finally:
            server.stop()

    def test_nothing_of_the_console_answers_through_it(self, listener: Any) -> None:
        for target in (
            "/api/auth/session",
            "/health",
            "/assets/x.js",
            "/events",
            "/hooks/deploy/a",
        ):
            raw = one_exchange(listener.port, request(target))

            assert raw.startswith(b"HTTP/1.1 308 "), target
            assert b"\r\n\r\n" in raw and raw.endswith(b"\r\n\r\n"), target

    def test_stopping_frees_the_port_and_is_clean_twice(self) -> None:
        server = redirect.RedirectServer("127.0.0.1", 0, HTTPS_PORT)
        server.start()
        port = server.port

        server.stop()
        server.stop()

        with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
            pass

    def test_a_port_that_is_taken_is_an_error_the_caller_can_say(self) -> None:
        first = redirect.RedirectServer("127.0.0.1", 0, HTTPS_PORT)
        first.start()
        try:
            second = redirect.RedirectServer("127.0.0.1", first.port, HTTPS_PORT)
            with pytest.raises(NoustError, match=str(first.port)):
                second.start()
        finally:
            first.stop()

    def test_the_threads_it_started_end_with_it(self) -> None:
        before = threading.active_count()
        server = redirect.RedirectServer("127.0.0.1", 0, HTTPS_PORT)
        server.start()
        one_exchange(server.port, request())

        server.stop()
        time.sleep(0.2)

        assert threading.active_count() <= before
