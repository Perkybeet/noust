#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sign in to the console as an account and enrol its authenticator, the way a person does.

Runs inside the integration container with Noust's own interpreter, so that the code its authenticator
would show is computed by Noust's TOTP and not by a second copy of it. It makes the three calls a
browser makes after the password: sign in, ask for a secret, confirm it with a code. The password
comes on the standard input, never in an argument.

Usage:
    /opt/noust/bin/python -I account_enrol.py URL USERNAME     (the password on standard input)

Prints ``{"secret": ..., "backup_codes": [...]}``.
"""

from __future__ import annotations

import http.cookiejar
import json
import sys
import urllib.request

from noust.core.totp import totp_now


def main() -> None:
    url, username = sys.argv[1].rstrip("/"), sys.argv[2]
    password = sys.stdin.readline().rstrip("\n")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path: str, body: dict[str, str] | None = None) -> dict[str, object]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        csrf = next((cookie.value for cookie in jar if cookie.name == "noust_csrf"), None)
        if csrf:
            headers["X-Noust-CSRF"] = csrf
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url + path, data=data, headers=headers, method="POST")
        with opener.open(request, timeout=30) as answer:
            raw = answer.read()
        parsed: dict[str, object] = json.loads(raw) if raw else {}
        return parsed

    call("/api/auth/login", {"username": username, "password": password})
    secret = str(call("/api/auth/2fa/enroll")["secret"])
    confirmed = call("/api/auth/2fa/confirm", {"code": totp_now(secret)})
    print(json.dumps({"secret": secret, "backup_codes": confirmed["backup_codes"]}))


if __name__ == "__main__":
    main()
