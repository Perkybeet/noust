"""
The "Deploy to Noust" action (checklist A-22).

``packaging/github-action`` is a composite action over a POSIX sh script. The
script is run for real here, against a fake Noust on loopback, because what
matters is what it does at the edges: it never puts the token on a command
line, it refuses a plain-http server, it follows the job to its end and says
the server's own words when it fails, and it sets its outputs.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

ACTION = Path(__file__).resolve().parent.parent / "packaging" / "github-action"
TOKEN = "noust_tok_" + "a1B2" * 8


class FakeNoust:
    """A Noust that answers the two calls the action makes, and remembers what it was asked."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.deploy: tuple[int, dict[str, Any]] = (
            202,
            {
                "message": "Update job created",
                "results": [{"domain": "shop.example.com", "status": "queued", "job_id": "job1"}],
            },
        )
        self.jobs: list[dict[str, Any]] = [
            {"status": "running", "current_step": "Building"},
            {
                "status": "completed",
                "current_step": "Done",
                "deployment_id": 41,
                "result": {"deployment_id": 41},
            },
        ]
        self.headers_of_429: dict[str, str] = {}
        self.refusals_first = 0
        self.polled = 0

    def handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _reply(
                self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None
            ):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)

            def _record(self, body: dict[str, Any] | None) -> None:
                fake.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                self._record(body)
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._reply(401, {"error": "unauthorized"})
                if fake.refusals_first > 0:
                    fake.refusals_first -= 1
                    return self._reply(
                        429,
                        {"error": "queue_full", "detail": "The deploy queue is full"},
                        {"Retry-After": "1"},
                    )
                status, payload = fake.deploy
                self._reply(status, payload)

            def do_GET(self) -> None:
                self._record(None)
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._reply(401, {"error": "unauthorized"})
                step = min(fake.polled, len(fake.jobs) - 1)
                fake.polled += 1
                self._reply(200, {"id": "job1", **fake.jobs[step]})

        return Handler


@pytest.fixture
def noust() -> Iterator[tuple[FakeNoust, str]]:
    fake = FakeNoust()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def run(
    url: str, tmp_path: Path, **overrides: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Run the script as the action does: inputs in the environment, outputs in a file."""
    output = tmp_path / "github-output"
    output.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "NOUST_URL": url,
        "NOUST_TOKEN": TOKEN,
        "NOUST_APP": "shop.example.com",
        "NOUST_WAIT": "true",
        "NOUST_TIMEOUT": "20",
        "NOUST_POLL_INTERVAL": "1",
        "NOUST_RETRY_DELAY": "1",
        "GITHUB_OUTPUT": str(output),
        **overrides,
    }
    done = subprocess.run(
        ["sh", str(ACTION / "deploy.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return done, outputs


pytestmark = [pytest.mark.allow_subprocess, pytest.mark.allow_sockets]


class TestTheActionFile:
    def load(self) -> dict[str, Any]:
        return yaml.safe_load((ACTION / "action.yml").read_text())

    def test_it_is_a_composite_action_with_the_inputs_a_pipeline_needs(self) -> None:
        action = self.load()

        assert action["runs"]["using"] == "composite"
        inputs = action["inputs"]
        for name in ("url", "token", "app"):
            assert inputs[name]["required"] is True
        for name in ("commit", "tag", "image-tag", "pull-request", "force", "no-cache", "wait"):
            assert name in inputs
        assert {"status", "job-id", "deployment-id", "results"} <= set(action["outputs"])

    def test_every_input_reaches_the_script_through_the_environment(self) -> None:
        action = self.load()
        (step,) = action["runs"]["steps"]

        assert step["shell"] == "sh"
        # Inputs are never pasted into the command's text, which would be shell injection.
        assert "inputs." not in step["run"]
        for name in action["inputs"]:
            assert f"inputs.{name}" in " ".join(str(v) for v in step["env"].values())

    def test_the_outputs_come_from_the_step(self) -> None:
        for name, output in self.load()["outputs"].items():
            assert output["value"] == f"${{{{ steps.deploy.outputs.{name} }}}}"

    def test_the_script_is_posix_sh_that_parses(self) -> None:
        script = ACTION / "deploy.sh"
        assert script.read_text().startswith("#!/bin/sh")
        done = subprocess.run(
            ["sh", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stderr

    def test_the_token_is_never_an_argument(self) -> None:
        text = (ACTION / "deploy.sh").read_text()
        assert "--config -" in text
        assert "Authorization: Bearer %s" in text
        assert '-H "Authorization' not in text and '--header "Authorization' not in text


class TestADeploy:
    def test_it_asks_for_the_application_and_follows_the_job_to_the_end(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust

        done, outputs = run(url, tmp_path)

        assert done.returncode == 0, done.stderr + done.stdout
        assert fake.requests[0]["method"] == "POST"
        assert fake.requests[0]["path"] == "/api/apps/shop.example.com/deploy"
        assert fake.requests[0]["body"] == {}
        assert [r["path"] for r in fake.requests[1:]] == ["/api/jobs/job1"] * 2
        assert outputs["status"] == "success"
        assert outputs["job-id"] == "job1"
        assert outputs["deployment-id"] == "41"
        assert json.loads(outputs["results"]) == [
            {
                "domain": "shop.example.com",
                "status": "success",
                "job_id": "job1",
                "deployment_id": 41,
            }
        ]
        assert "Building" in done.stdout and "deployed" in done.stdout

    def test_what_was_asked_is_what_is_sent(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust

        done, _ = run(
            url,
            tmp_path,
            NOUST_COMMIT="abc1234",
            NOUST_FORCE="true",
            NOUST_NO_CACHE="true",
            NOUST_WAIT="false",
        )

        assert done.returncode == 0, done.stderr
        assert fake.requests[0]["body"] == {"commit": "abc1234", "force": True, "no_cache": True}
        assert len(fake.requests) == 1

    def test_an_image_tag_and_a_pull_request(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        run(url, tmp_path, NOUST_IMAGE_TAG="1.4.2", NOUST_WAIT="false")
        run(url, tmp_path, NOUST_PULL_REQUEST="12", NOUST_WAIT="false")

        assert fake.requests[0]["body"] == {"image_tag": "1.4.2"}
        assert fake.requests[1]["body"] == {"pull_request": 12}

    def test_several_applications_use_the_batch_call(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.deploy = (
            202,
            {
                "message": "2 of 2 applications queued",
                "results": [
                    {"domain": "a.example.com", "status": "queued", "job_id": "job1"},
                    {"domain": "b.example.com", "status": "nothing_new", "detail": "same"},
                ],
            },
        )

        done, outputs = run(url, tmp_path, NOUST_APP="a.example.com, b.example.com\n")

        assert done.returncode == 0, done.stderr
        assert fake.requests[0]["path"] == "/api/deploy"
        assert fake.requests[0]["body"] == {"apps": ["a.example.com", "b.example.com"]}
        results = {r["domain"]: r["status"] for r in json.loads(outputs["results"])}
        assert results == {"a.example.com": "success", "b.example.com": "nothing-new"}
        assert outputs["status"] == "success"

    def test_nothing_new_is_not_a_failure(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.deploy = (
            200,
            {
                "message": "Nothing new to deploy",
                "results": [
                    {"domain": "shop.example.com", "status": "nothing_new", "detail": "same commit"}
                ],
            },
        )

        done, outputs = run(url, tmp_path)

        assert done.returncode == 0
        assert outputs["status"] == "nothing-new"
        assert "nothing new" in done.stdout
        assert len(fake.requests) == 1

    def test_a_queue_that_is_full_is_waited_out(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.refusals_first = 2

        done, outputs = run(url, tmp_path)

        assert done.returncode == 0, done.stderr
        assert outputs["status"] == "success"
        assert [r["method"] for r in fake.requests].count("POST") == 3


class TestAFailure:
    def test_the_servers_own_words_are_shown_and_the_step_fails(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.jobs = [
            {
                "status": "failed",
                "current_step": "Waiting for the new release to answer",
                "error": "The new release did not answer on port 3000.\nprobe 1: connection refused\njournal: boom",
            }
        ]

        done, outputs = run(url, tmp_path)

        assert done.returncode == 1
        assert outputs["status"] == "failed"
        assert "probe 1: connection refused" in done.stdout + done.stderr
        assert "journal: boom" in done.stdout + done.stderr

    def test_github_gets_an_annotation_and_a_group(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.jobs = [{"status": "failed", "error": "first line\nsecond line"}]

        done, _ = run(url, tmp_path, GITHUB_ACTIONS="true")

        assert "::error title=Noust deploy of shop.example.com::first line" in done.stdout
        assert "::group::" in done.stdout and "second line" in done.stdout

    def test_a_cancelled_deployment_fails_the_step_too(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.jobs = [{"status": "cancelled"}]

        done, outputs = run(url, tmp_path)

        assert done.returncode == 1 and outputs["status"] == "cancelled"

    def test_waiting_too_long_says_the_deploy_carries_on(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.jobs = [{"status": "running", "current_step": "Building"}]

        done, outputs = run(url, tmp_path, NOUST_TIMEOUT="2")

        assert done.returncode == 1
        assert outputs["status"] == "timeout"
        assert "carries on on the server" in done.stdout + done.stderr

    def test_a_token_the_server_refuses(self, noust: tuple[FakeNoust, str], tmp_path: Path) -> None:
        _, url = noust

        done, _ = run(url, tmp_path, NOUST_TOKEN=TOKEN + "x")

        assert done.returncode == 1
        assert "401" in done.stderr and "--scope deploy" in done.stderr

    def test_an_application_the_server_does_not_have(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        fake, url = noust
        fake.deploy = (404, {"detail": "Application not found: shop.example.com"})

        done, _ = run(url, tmp_path)

        assert done.returncode == 1 and "404" in done.stderr


class TestWhatItRefuses:
    def test_a_server_it_would_send_the_token_to_in_the_clear(self, tmp_path: Path) -> None:
        done, _ = run("http://noust.example.com", tmp_path)
        assert done.returncode == 1 and "plain http" in done.stderr

    def test_a_url_with_credentials(self, tmp_path: Path) -> None:
        done, _ = run("https://user:pass@noust.example.com", tmp_path)
        assert done.returncode == 1 and "credentials" in done.stderr

    @pytest.mark.parametrize("token", ['abc"def', "abc\ndef", "abc def", "a;b"])
    def test_a_token_that_could_break_out_of_the_header(
        self, noust: tuple[FakeNoust, str], tmp_path: Path, token: str
    ) -> None:
        fake, url = noust

        done, _ = run(url, tmp_path, NOUST_TOKEN=token)

        assert done.returncode == 1 and fake.requests == []

    @pytest.mark.parametrize("app", ["../etc", "-oX", "a b.example.com", ".hidden", "a;b"])
    def test_an_application_that_is_not_a_domain(
        self, noust: tuple[FakeNoust, str], tmp_path: Path, app: str
    ) -> None:
        fake, url = noust

        done, _ = run(url, tmp_path, NOUST_APP=app)

        assert done.returncode == 1 and fake.requests == []

    def test_a_boolean_that_is_not_one(self, noust: tuple[FakeNoust, str], tmp_path: Path) -> None:
        done, _ = run(noust[1], tmp_path, NOUST_FORCE="yes")
        assert done.returncode == 1 and "FORCE must be true or false" in done.stderr

    def test_the_token_is_not_in_what_it_prints(
        self, noust: tuple[FakeNoust, str], tmp_path: Path
    ) -> None:
        done, _ = run(noust[1], tmp_path)
        assert TOKEN not in done.stdout + done.stderr
