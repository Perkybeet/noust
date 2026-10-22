#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stand-in for Cloudflare's DNS API in front of a real BIND, for the certificate scenarios.

No test machine has an account at a DNS provider, and the checklist rows E-33 and E-34 are about
what happens between certbot and a DNS record, not about Cloudflare. So this serves the part of
the v4 API that Noust's Cloudflare client calls (zones, and the records of a zone: list, create,
read, change, delete) and keeps each record where the authority will look for it: it applies every
change to the BIND beside it by RFC 2136 (``nsupdate`` with the TSIG key), and BIND is what Pebble
asks. It answers like Cloudflare does (the ``success``/``errors``/``result`` envelope, a TXT value
returned inside quotes, 403 and its error code for a token it does not know) and records every
request, with the token left out, in ``/srv/requests.jsonl`` so a scenario can read the exact calls.

It speaks for one zone and one token, and does nothing else: it is not Cloudflare and proves
nothing about it. Standard library only.

Usage (the container's entrypoint runs it):
    provider.py --cert server.pem --key server.key --zone acme.test --tsig-key /etc/bind/noust.key
"""

from __future__ import annotations

import argparse
import json
import secrets
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

TOKEN_FILE = Path("/srv/token")
REQUESTS_LOG = Path("/srv/requests.jsonl")
API_ROOT = "/client/v4"

LOCK = threading.Lock()
RECORDS: dict[str, dict[str, object]] = {}
SETTINGS: dict[str, str] = {}


def nsupdate(commands: str) -> None:
    """
    Apply a dynamic update to the BIND beside this process.

    Args:
        commands: ``nsupdate`` input, without ``server`` and ``send``.

    Raises:
        RuntimeError: BIND refused the update.
    """
    script = f"server 127.0.0.1\n{commands}\nsend\n"
    proc = subprocess.run(
        ["nsupdate", "-k", SETTINGS["tsig_key"]],
        input=script,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"nsupdate failed: {proc.stderr.strip() or proc.stdout.strip()}")


def rdata(record: dict[str, object]) -> str:
    """The record as the update syntax writes it (a TXT value is quoted)."""
    content = str(record["content"])
    if record["type"] == "TXT":
        return '"' + content.replace('"', "") + '"'
    return content


def shown(record: dict[str, object]) -> dict[str, object]:
    """The record as the API prints it: a TXT value comes back inside quotes."""
    out = dict(record)
    if record["type"] == "TXT":
        out["content"] = '"' + str(record["content"]).replace('"', "") + '"'
    return out


class Handler(BaseHTTPRequestHandler):
    """One request of the API."""

    server_version = "cloudflare-stand-in"

    def log_message(self, format: str, *args: object) -> None:
        """Keep the access log off the console; the requests log is the record."""

    def _send(self, status: int, payload: dict[str, object]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _fail(self, status: int, code: int, message: str) -> None:
        self._send(
            status,
            {
                "success": False,
                "errors": [{"code": code, "message": message}],
                "messages": [],
                "result": None,
            },
        )

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length))
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _record_request(self, method: str, body: dict[str, object]) -> None:
        entry = {
            "time": time.time(),
            "method": method,
            "path": self.path,
            "authorized": self.headers.get("Authorization") == f"Bearer {SETTINGS['token']}",
            "user_agent": self.headers.get("User-Agent", ""),
            "body": body,
        }
        with REQUESTS_LOG.open("a") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _handle(self, method: str) -> None:
        body = self._body() if method in ("POST", "PATCH", "PUT") else {}
        self._record_request(method, body)
        if self.headers.get("Authorization") != f"Bearer {SETTINGS['token']}":
            self._fail(403, 9109, "Invalid access token")
            return
        url = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        path = url.path.removeprefix(API_ROOT)
        zone = SETTINGS["zone"]
        zone_id = SETTINGS["zone_id"]
        parts = [part for part in path.split("/") if part]
        with LOCK:
            if parts == ["zones"] and method == "GET":
                self._send(
                    200,
                    {
                        "success": True,
                        "errors": [],
                        "messages": [],
                        "result": [
                            {"id": zone_id, "name": zone, "account": {"id": "account-1"}},
                        ],
                        "result_info": {"page": 1, "per_page": 50, "total_pages": 1, "count": 1},
                    },
                )
                return
            if len(parts) >= 3 and parts[0] == "zones" and parts[2] == "dns_records":
                if parts[1] != zone_id:
                    self._fail(
                        404,
                        7003,
                        "Could not route to /zones/dns_records, perhaps your object identifier is invalid?",
                    )
                    return
                self._records(method, parts[3:], query, body)
                return
        self._fail(404, 7000, f"No route for that URI: {method} {url.path}")

    def _records(
        self, method: str, rest: list[str], query: dict[str, str], body: dict[str, object]
    ) -> None:
        zone_id = SETTINGS["zone_id"]
        if not rest:
            if method == "GET":
                found = [
                    shown(record)
                    for record in RECORDS.values()
                    if query.get("type") in (None, record["type"])
                    and query.get("name") in (None, record["name"])
                ]
                self._send(
                    200,
                    {
                        "success": True,
                        "errors": [],
                        "messages": [],
                        "result": found,
                        "result_info": {"page": 1, "per_page": 100, "total_pages": 1},
                    },
                )
                return
            if method == "POST":
                name = str(body.get("name", "")).lower()
                kind = str(body.get("type", ""))
                if not name.endswith(SETTINGS["zone"]) or not kind or "content" not in body:
                    self._fail(400, 9007, "Invalid request headers or body")
                    return
                record = {
                    "id": secrets.token_hex(16),
                    "zone_id": zone_id,
                    "zone_name": SETTINGS["zone"],
                    "type": kind,
                    "name": name,
                    "content": str(body["content"]).strip('"'),
                    "ttl": body.get("ttl", 1),
                    "proxied": bool(body.get("proxied", False)),
                    "comment": body.get("comment"),
                }
                ttl = 60 if record["ttl"] == 1 else record["ttl"]
                try:
                    nsupdate(f"update add {name} {ttl} {kind} {rdata(record)}")
                except RuntimeError as exc:
                    self._fail(500, 1000, str(exc))
                    return
                RECORDS[str(record["id"])] = record
                self._send(
                    200, {"success": True, "errors": [], "messages": [], "result": shown(record)}
                )
                return
        else:
            record_id = rest[0]
            record = RECORDS.get(record_id)
            if record is None:
                self._fail(404, 81044, "Record does not exist.")
                return
            if method == "GET":
                self._send(
                    200, {"success": True, "errors": [], "messages": [], "result": shown(record)}
                )
                return
            if method == "DELETE":
                try:
                    nsupdate(f"update delete {record['name']} {record['type']} {rdata(record)}")
                except RuntimeError as exc:
                    self._fail(500, 1000, str(exc))
                    return
                del RECORDS[record_id]
                self._send(
                    200,
                    {"success": True, "errors": [], "messages": [], "result": {"id": record_id}},
                )
                return
            if method == "PATCH":
                before = dict(record)
                record["content"] = str(body.get("content", record["content"])).strip('"')
                try:
                    nsupdate(
                        f"update delete {before['name']} {before['type']} {rdata(before)}\n"
                        f"update add {record['name']} 60 {record['type']} {rdata(record)}"
                    )
                except RuntimeError as exc:
                    self._fail(500, 1000, str(exc))
                    return
                self._send(
                    200, {"success": True, "errors": [], "messages": [], "result": shown(record)}
                )
                return
        self._fail(405, 10000, "Method not allowed")

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")


def main() -> int:
    """Serve the API over HTTPS on 443 until the container is stopped."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--zone", required=True)
    parser.add_argument("--tsig-key", required=True)
    args = parser.parse_args()

    SETTINGS.update(
        zone=args.zone,
        zone_id="zone-" + args.zone.replace(".", "-"),
        tsig_key=args.tsig_key,
        token="w6r-" + secrets.token_urlsafe(24),
    )
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(SETTINGS["token"])
    TOKEN_FILE.chmod(0o600)
    REQUESTS_LOG.write_text("")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    httpd = ThreadingHTTPServer(("0.0.0.0", 443), Handler)  # noqa: S104 - a test container
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    print("provider stand-in listening on 443", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
