# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The built-in assistant with a model that is real, small and open (item 171).

Not collected by default (its name does not begin with ``test_``) and not part of the battery: it needs a
server of the protocol that answers, which ``tests/integration/assistant_run.py`` starts in a container and
names in ``NOUST_REAL_MODEL_URL``. Run by hand it is::

    NOUST_REAL_MODEL_URL=http://127.0.0.1:11434/v1 NOUST_REAL_MODEL=qwen2.5:3b \\
    NOUST_REAL_RESULTS=/tmp/results python -m pytest -p no:xdist tests/integration/assistant_real.py -s

What the scripted model of the battery cannot say, this does: **the real format** of the stream and of the
calls to tools of a real server, what the probe finds of the model, how long it takes and what it gets right.
The same scenarios, the same seeded console and the same oracles that no secret leaked and nothing was
executed without approval; but the model is the one that answers, so the cases are measured and not asserted:
a small model on a CPU is slow, and fails, and that is also a result.

Between the console and the server there is a **recording proxy**: it forwards every request and keeps what
was sent and a sample of what came back, which is how a real format is seen.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.fixtures import battery_environment  # noqa: F401
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.oracles import global_oracles
from tests.support.assistant_battery.scenario import SCENARIOS_DIR, load
from tests.support.assistant_lab import Lab, build_lab
from tests.support.scripted_model import Recorded

URL = os.environ.get("NOUST_REAL_MODEL_URL", "")
MODEL = os.environ.get("NOUST_REAL_MODEL", "")
RESULTS = Path(os.environ["NOUST_REAL_RESULTS"]) if os.environ.get("NOUST_REAL_RESULTS") else None
WAIT = float(os.environ.get("NOUST_REAL_WAIT", "900"))

pytestmark = [
    pytest.mark.allow_sockets,
    pytest.mark.skipif(not URL, reason="NOUST_REAL_MODEL_URL says where the model is"),
]

#: What each scenario is about, as the words that tell an answer found it: measured, never asserted.
CAUSE: dict[str, list[str]] = {
    "explain/explain-missing-variable": ["DATABASE_URL"],
    "explain/explain-port-in-use": ["3000"],
    "explain/explain-out-of-memory": ["memory"],
    "explain/explain-certificate-expired": ["expired"],
    "investigate/investigate-missing-variable": ["DATABASE_URL"],
    "investigate/investigate-out-of-memory": ["memory"],
    "investigate/investigate-no-evidence-intermittent": [],
}
ASKED = [s for s in os.environ.get("NOUST_REAL_SCENARIOS", "").split(",") if s] or list(CAUSE)

#: Words of a model that says it does not know.
UNSURE = re.compile(
    r"don't know|do not know|not sure|no evidence|cannot (tell|determine)|unable to", re.I
)


class RecordingProxy:
    """
    Forwards what the console asks to the real server, and keeps what went and a sample of what came back.

    It answers the few things of ``ScriptedModel`` the harness reads (``base_url``, ``requests``,
    ``chat_requests``, ``everything_received``) so that the same lab serves both.
    """

    def __init__(self, target: str) -> None:
        parts = urllib.parse.urlsplit(target)
        self.host, self.port = parts.hostname or "127.0.0.1", parts.port or 80
        self.requests: list[Recorded] = []
        self.responses: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    # The harness scripts a model; a real one needs no script.
    def script(self, *replies: Any) -> RecordingProxy:
        return self

    def unscript(self) -> None:
        return None

    def pending(self) -> int:
        return 0

    def chat_requests(self) -> list[Recorded]:
        return [r for r in self.requests if r.path.split("?", 1)[0].endswith("/chat/completions")]

    def everything_received(self) -> str:
        parts: list[str] = []
        for request in self.requests:
            parts.append(request.path)
            parts.extend(f"{k}: {v}" for k, v in request.headers.items() if k != "authorization")
            parts.append(request.raw.decode("utf-8", errors="replace"))
        return "\n".join(parts)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def __enter__(self) -> RecordingProxy:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                proxy._forward(self)

            def do_POST(self) -> None:
                proxy._forward(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def _forward(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        headers = {k.lower(): v for k, v in handler.headers.items()}
        with self._lock:
            self.requests.append(Recorded(handler.command, handler.path, headers, raw, body))
        started = time.monotonic()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=WAIT)
        forwarded = {
            k: v
            for k, v in headers.items()
            if k in ("content-type", "accept", "authorization", "user-agent")
        }
        try:
            connection.request(handler.command, handler.path, body=raw or None, headers=forwarded)
            answer = connection.getresponse()
        except OSError as exc:
            handler.send_response(502)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            self.responses.append({"path": handler.path, "status": 502, "error": str(exc)})
            return
        handler.send_response(answer.status)
        for name, value in answer.getheaders():
            if name.lower() in ("content-type", "cache-control"):
                handler.send_header(name, value)
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        kept = bytearray()
        total = 0
        try:
            while True:
                chunk = answer.read1(8192) if hasattr(answer, "read1") else answer.read(8192)
                if not chunk:
                    break
                total += len(chunk)
                if len(kept) < 6000:
                    kept += chunk
                handler.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                handler.wfile.flush()
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            handler.close_connection = True
        finally:
            connection.close()
        self.responses.append(
            {
                "path": handler.path.split("?", 1)[0],
                "status": answer.status,
                "content_type": answer.getheader("content-type", ""),
                "bytes": total,
                "seconds": round(time.monotonic() - started, 1),
                "head": kept.decode("utf-8", errors="replace"),
            }
        )


@pytest.fixture(autouse=True)
def patience(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    With ``NOUST_REAL_PATIENT`` the client waits as long as a model on a CPU needs.

    The product waits 30 seconds for the probe's reading and 60 for a turn's, which is right for a service and
    too little for a small model on three cores: the run as the product ships it is measured too, without this.
    """
    if not os.environ.get("NOUST_REAL_PATIENT"):
        return
    from noust.core.assistant import probe as probe_module
    from noust.core.assistant.client import ProviderClient
    from noust.web.assistant import loop as loop_module

    def patient_for_the_probe(base_url: str, model: str, **options: Any) -> ProviderClient:
        return ProviderClient(
            base_url, model, **{**options, "read_timeout": 540.0, "total_timeout": 900.0}
        )

    def patient_for_a_turn(settings: Any, key: str) -> ProviderClient:
        return ProviderClient(
            settings.base_url,
            settings.model,
            api_key=key,
            read_timeout=540.0,
            total_timeout=1200.0,
            max_retries=1,
        )

    monkeypatch.setattr(probe_module, "ProviderClient", patient_for_the_probe)
    monkeypatch.setattr(loop_module, "default_client", patient_for_a_turn)


@pytest.fixture
def real_model() -> Iterator[RecordingProxy]:
    """A recording proxy in front of the model."""
    with RecordingProxy(URL) as proxy:
        yield proxy


@pytest.fixture
def lab(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, real_model: RecordingProxy
) -> Iterator[Lab]:
    """The console, served, with the real model as its provider."""
    yield from build_lab(sandbox, monkeypatch, real_model)  # type: ignore[arg-type]


@pytest.fixture
def harness(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """The harness over the lab, with the real model's name and the patience a CPU asks for."""
    return Harness(lab, monkeypatch, model_name=MODEL, wait_seconds=WAIT, steps_per_turn=6)


def record_row(row: dict[str, Any]) -> None:
    """Append what a scenario found to the results folder, when there is one."""
    if RESULTS is None:
        return
    RESULTS.mkdir(parents=True, exist_ok=True)
    name = str(row["scenario"]).replace("/", "--")
    (RESULTS / f"{name}.json").write_text(
        json.dumps(row, indent=1, sort_keys=True), encoding="utf-8"
    )


def test_the_probe_says_what_the_model_can_do(harness: Harness, real_model: RecordingProxy) -> None:
    started = time.monotonic()
    scenario = load(SCENARIOS_DIR / "explain" / "explain-missing-variable.yaml")
    harness.prepare(scenario)
    seconds = round(time.monotonic() - started, 1)
    settings = harness.lab.marta.get("/api/assistant/settings").json()
    record_row(
        {
            "scenario": "probe",
            "seconds": seconds,
            "calls_tools": settings.get("calls_tools"),
            "probe": settings.get("probe"),
            "requests": [
                {"method": r.method, "path": r.path.split("?", 1)[0]} for r in real_model.requests
            ],
            "responses": real_model.responses[:6],
        }
    )
    assert settings["enabled"] is True


@pytest.mark.parametrize("name", ASKED)
def test_the_model_on(harness: Harness, real_model: RecordingProxy, name: str) -> None:
    family, _, stem = name.partition("/")
    scenario = load(SCENARIOS_DIR / family / f"{stem}.yaml")
    harness.prepare(scenario)
    real_model.requests.clear()
    real_model.responses.clear()

    started = time.monotonic()
    try:
        record = harness.play(scenario, [])
    except AssertionError as exc:
        # A turn that does not end in the time given is what a model too slow for the machine does: it is
        # a row of the results, not a crash of the harness.
        record_row(
            {
                "scenario": name,
                "seconds": round(time.monotonic() - started, 1),
                "state": "did not end",
                "error": str(exc)[:200],
                "steps": [],
                "steps_failed": 0,
                "doors": [],
                "oracles": [],
            }
        )
        raise
    seconds = round(time.monotonic() - started, 1)

    turn = record.turn
    text = str(turn.get("text", ""))
    words = CAUSE.get(name, [])
    problems = global_oracles(record)
    chats = [r for r in real_model.responses if r["path"].endswith("/chat/completions")]
    record_row(
        {
            "scenario": name,
            "seconds": seconds,
            "state": turn.get("state"),
            "stop": (turn.get("stop") or {}).get("code"),
            "error": (turn.get("error") or {}).get("code"),
            "steps": [s["tool"] for s in turn.get("steps", [])],
            "steps_failed": sum(1 for s in turn.get("steps", []) if s.get("state") == "failed"),
            "doors": record.extra.get("doors", []),
            "requests": record.model_requests,
            "tools_offered": record.first_request_tools is not None,
            "proposals": len(turn.get("proposals", [])),
            "usage": {k: (turn.get("usage") or {}).get(k) for k in ("steps", "tokens", "spend")},
            "cause_found": bool(words) and all(w.lower() in text.lower() for w in words),
            "says_it_does_not_know": bool(UNSURE.search(text)),
            "answer": text[:500],
            "oracles": problems,
            "executed_in_turn": sum(1 for r in record.receipts if r["phase"] == "turn"),
            "seconds_per_request": [c["seconds"] for c in chats],
            "stream_sample": next((c["head"][:1500] for c in chats), ""),
            "tool_call_sample": next(
                (c["head"][:1500] for c in chats if "tool_calls" in c["head"]),
                "",
            ),
        }
    )
    assert problems == []
