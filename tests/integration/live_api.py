# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the live tests of the API's clients share: a server to test against and tokens to test it with.

The Terraform provider (``terraform-provider-noust/``) and the Python SDK (``sdk/python``) are clients of
the HTTP API, and a client is only proved by a server that answers. The server is
``scripts/console_server.py``: the real application on a seeded, sandboxed machine, with
``NOUST_E2E_CODE_HOST`` set so that an application can really be deployed in it (a code host that serves
two small repositories, and a listener for the health gate). Nothing touches the machine it runs on.

The credentials are the part this module is about. The server prints the master token, which is
break-glass and owns nothing; a client is never given it. :func:`mint_tokens` uses it once to make an
account, as an operator would on a fresh server, and then does what an operator does with that account:
signs in, enrols an authenticator, confirms with a backup code (sudo mode) and issues API tokens narrowed
to the permissions the client needs. So a token here is exactly the credential a pipeline would hold, and
a test can prove that a token without ``allow_elevated`` is refused where sudo mode is asked and that one
without ``teams.manage`` cannot make a team.

Standard library only, so it also runs inside a bare Python container (``python -I live_api.py mint``).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import signal
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent

#: A token that may only look: what ``terraform plan`` needs to refresh a configuration it cannot change.
READ_ONLY_PERMISSIONS = ("apps.read", "teams.read", "backups.read", "databases.read", "fleet.read")

#: The profiles of token a client is tested with, and what each is for.
PROFILES = {
    "provider": "every permission the provider asks for, issued with allow_elevated: what a pipeline holds",
    "nosudo": "the same permissions, without allow_elevated: refused wherever sudo mode is asked",
    "read": "may look and nothing else: a plan-only credential",
}


class ApiError(RuntimeError):
    """The server refused a call a live test needed to succeed."""

    def __init__(self, method: str, path: str, status: int, body: Any) -> None:
        super().__init__(f"{method} {path}: {status} {json.dumps(body)[:300]}")
        self.status = status
        self.body = body


class Session:
    """A client of the API that keeps cookies, the way a browser or a person's script does."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    @property
    def csrf(self) -> str | None:
        """The CSRF token the server set when the account signed in."""
        return next((cookie.value for cookie in self.jar if cookie.name == "noust_csrf"), None)

    def call(
        self, method: str, path: str, body: Any = None, *, token: str | None = None
    ) -> tuple[int, Any]:
        """
        Make one call.

        Args:
            method: The verb.
            path: The route, from ``/api`` on.
            body: A JSON body, or None.
            token: A bearer token to authenticate with; the session's cookies otherwise.

        Returns:
            The status and the decoded body.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif self.csrf and method != "GET":
            headers["X-Noust-CSRF"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as answer:
                raw = answer.read()
                return answer.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw) if raw else None
            except ValueError:
                return error.code, raw.decode("utf-8", "replace")

    def must(self, method: str, path: str, body: Any = None, *, token: str | None = None) -> Any:
        """
        Make one call that has to succeed.

        Raises:
            ApiError: The server refused it.
        """
        status, answer = self.call(method, path, body, token=token)
        if status >= 400:
            raise ApiError(method, path, status, answer)
        return answer


def totp(secret: str, *, at: float | None = None) -> str:
    """
    Args:
        secret: A base32 TOTP secret (RFC 6238, SHA-1, 30 seconds, 6 digits).
        at: A UNIX time; now when omitted.

    Returns:
        The code an authenticator shows.
    """
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", int((at or time.time()) // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 0xF
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def mint_tokens(url: str, master_token: str, permissions: Sequence[str]) -> dict[str, str]:
    """
    Make an operator's account and issue the profiles of token a client is tested with.

    The master token is used for exactly one thing: creating the account. Everything after it is the account
    signing in, enrolling an authenticator, confirming with a backup code and issuing tokens, each narrowed
    to the permissions its profile names (:data:`PROFILES`), so no client ever holds the master token.

    Args:
        url: The server's address.
        master_token: The break-glass token the server printed.
        permissions: What the ``provider`` profile holds (``permissions.json``'s ``all``).

    Returns:
        Profile name to token, and ``account`` to the name of the account that issued them: an account
        that exists, which a test can make a member of a team.

    Raises:
        ApiError: The server refused a step.
    """
    session = Session(url)
    username = "operator-" + secrets.token_hex(3)
    password = secrets.token_urlsafe(24)
    session.must(
        "POST",
        "/api/auth/accounts",
        {"username": username, "password": password, "role": "admin"},
        token=master_token,
    )
    session.must("POST", "/api/auth/login", {"username": username, "password": password})
    enrolment = session.must("POST", "/api/auth/2fa/enroll")
    confirmed = session.must("POST", "/api/auth/2fa/confirm", {"code": totp(enrolment["secret"])})
    session.must("POST", "/api/auth/elevate", {"code": confirmed["backup_codes"][0]})

    wanted = {
        "provider": (sorted(set(permissions)), True),
        "nosudo": (sorted(set(permissions)), False),
        "read": (sorted(READ_ONLY_PERMISSIONS), False),
    }
    tokens: dict[str, str] = {}
    for profile, (granted, elevated) in wanted.items():
        issued = session.must(
            "POST",
            "/api/auth/tokens",
            {
                "name": f"live-{profile}-{secrets.token_hex(2)}",
                "scope": "admin",
                "permissions": granted,
                "allow_elevated": elevated,
            },
        )
        tokens[profile] = issued["token"]
    tokens["account"] = username
    return tokens


@dataclass
class Server:
    """A running console server and how to reach it."""

    url: str
    master_token: str


@contextlib.contextmanager
def native_server(*, code_host: bool = True, boot_timeout: float = 120.0) -> Iterator[Server]:
    """
    Start ``scripts/console_server.py`` on this machine and stop it afterwards.

    Args:
        code_host: Model the code host and the applications' listeners (``NOUST_E2E_CODE_HOST``), which
            a client that deploys needs.
        boot_timeout: Seconds to wait for the server to print its connection line.

    Yields:
        The server's address and master token.

    Raises:
        RuntimeError: The server did not start.
    """
    env = {
        **os.environ,
        "PYTHONPATH": f"{REPO / 'src'}{os.pathsep}{REPO}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if code_host:
        env["NOUST_E2E_CODE_HOST"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(REPO / "scripts" / "console_server.py")],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        deadline = time.monotonic() + boot_timeout
        line = ""
        while time.monotonic() < deadline:
            assert process.stdout is not None
            line = process.stdout.readline()
            if line.startswith("{"):
                break
            if process.poll() is not None:
                raise RuntimeError("console_server.py exited before it served")
        else:
            raise RuntimeError("console_server.py printed no connection line in time")
        details = json.loads(line)
        yield Server(url=details["url"], master_token=details["token"])
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _main(argv: Sequence[str]) -> int:
    """
    ``live_api.py mint``: read ``{"url", "token", "permissions"}`` from standard input and print the
    profiles' tokens as JSON. The master token goes through a pipe and is never an argument.
    """
    if list(argv) != ["mint"]:
        print("usage: live_api.py mint   (JSON on standard input)", file=sys.stderr)
        return 2
    request = json.load(sys.stdin)
    print(json.dumps(mint_tokens(request["url"], request["token"], request["permissions"])))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
