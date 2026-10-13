# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A site's access and error logs, from the console and ``noust site logs``.

Checklist G-11. The web server's files are faked in a temporary directory. What is
asserted is that only the end of a file is read, that a link is not followed, that
a filter is plain text and that what leaves is masked.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import App, NoustStore
from noust.managers.site_logs import MAX_LINE_CHARS, MAX_LINES, TAIL_BYTES, SiteLogReader
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.permissions.registry import permission_for_route
from noust.web.server import create_app, get_token_manager

DOMAIN = "shop.example.com"
TOKEN = "ghp_" + "c" * 36


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    directory = tmp_path / "log" / "nginx"
    directory.mkdir(parents=True)
    return directory


def write(directory: Path, kind: str, *rows: str, domain: str = DOMAIN) -> Path:
    path = directory / f"{domain}.{kind}.log"
    path.write_text("\n".join(rows) + "\n")
    return path


def reader(directory: Path, **kwargs: Any) -> SiteLogReader:
    return SiteLogReader("nginx", directories=(directory,), **kwargs)


class TestReading:
    def test_the_last_lines_come_back_oldest_first(self, logs: Path) -> None:
        write(logs, "access", *[f"GET /page/{n} 200" for n in range(10)])

        log = reader(logs).read(DOMAIN, "access", lines=3)

        assert log.available
        assert log.lines == ["GET /page/7 200", "GET /page/8 200", "GET /page/9 200"]
        assert log.truncated and log.matched == 10

    def test_a_short_log_is_all_there_and_not_truncated(self, logs: Path) -> None:
        write(logs, "error", "boom")

        log = reader(logs).read(DOMAIN, "error")

        assert (log.lines, log.truncated, log.size_bytes) == (["boom"], False, 5)

    def test_a_filter_is_plain_text_without_regard_to_case(self, logs: Path) -> None:
        write(logs, "access", "GET /a 200", "GET /b 502 Bad Gateway", "POST /c 502 bad gateway")

        log = reader(logs).read(DOMAIN, "access", contains="BAD GATEWAY")

        assert log.lines == ["GET /b 502 Bad Gateway", "POST /c 502 bad gateway"]
        assert log.matched == 2

    def test_a_filter_is_never_a_pattern(self, logs: Path) -> None:
        write(logs, "access", "GET /a 200", "a(b")

        assert reader(logs).read(DOMAIN, "access", contains="(").lines == ["a(b"]
        assert reader(logs).read(DOMAIN, "access", contains=".*").lines == []

    def test_only_the_end_of_a_big_file_is_read(self, logs: Path) -> None:
        rows = [f"{n:08d} " + "x" * 90 for n in range(25_000)]  # about 2.4 MB
        path = write(logs, "access", *rows)
        assert path.stat().st_size > 2 * TAIL_BYTES

        started = time.monotonic()
        log = reader(logs).read(DOMAIN, "access", lines=2)

        assert time.monotonic() - started < 2.0
        assert log.lines[-1].startswith("00024999")
        assert log.truncated
        # The first row of the window began before it: it is dropped, not half shown.
        assert all(len(line) == 99 for line in log.lines)

    def test_the_number_of_lines_has_a_ceiling(self, logs: Path) -> None:
        write(logs, "access", *[f"row {n}" for n in range(MAX_LINES + 50)])

        assert len(reader(logs).read(DOMAIN, "access", lines=10**6).lines) == MAX_LINES

    def test_a_giant_line_is_cut(self, logs: Path) -> None:
        write(logs, "access", "z" * (MAX_LINE_CHARS * 3))

        (line,) = reader(logs).read(DOMAIN, "access").lines

        assert len(line) == MAX_LINE_CHARS and line.endswith("…")

    def test_bytes_that_are_not_text_do_not_break_it(self, logs: Path) -> None:
        (logs / f"{DOMAIN}.access.log").write_bytes(b"ok line\n\xff\xfe broken\nlast\n")

        lines = reader(logs).read(DOMAIN, "access").lines

        assert lines[0] == "ok line" and lines[-1] == "last"

    def test_every_line_is_masked(self, logs: Path) -> None:
        write(logs, "access", f'1.2.3.4 - - "GET /cb?token={TOKEN}&x=1 HTTP/1.1" 200')

        (line,) = (
            reader(logs, scrub=lambda text: text.replace("1.2.3.4", "ip"))
            .read(DOMAIN, "access")
            .lines
        )

        assert line.startswith("ip") and "1.2.3.4" not in line


class TestWhatIsNotThere:
    def test_a_site_without_a_log_says_where_it_looked(self, logs: Path) -> None:
        log = reader(logs).read(DOMAIN, "error")

        assert not log.available and log.path == str(logs / f"{DOMAIN}.error.log")
        assert log.lines == []

    def test_a_link_is_not_followed(self, logs: Path, tmp_path: Path) -> None:
        secret = tmp_path / "shadow"
        secret.write_text("root:$6$hash\n")
        os.symlink(secret, logs / f"{DOMAIN}.access.log")

        log = reader(logs).read(DOMAIN, "access")

        assert not log.available and "root:" not in "".join(log.lines)

    def test_a_kind_that_is_not_one_is_refused(self, logs: Path) -> None:
        with pytest.raises(ValueError, match="access and error"):
            reader(logs).read(DOMAIN, "../../etc/passwd")

    def test_a_domain_cannot_name_another_directory(self, logs: Path, tmp_path: Path) -> None:
        (tmp_path / "log" / "outside.access.log").write_text("not yours\n")

        for name in ("../outside", "a/../outside", "x/y", "..", "a..b", ""):
            with pytest.raises(ValueError, match="not a site's name"):
                reader(logs).read(name, "access")


class TestTheApi:
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[NoustStore]:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        try:
            yield instance
        finally:
            instance.close()
            NoustStore.reset_instance()

    @pytest.fixture
    def client(
        self, tmp_path: Path, store: NoustStore, logs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> TestClient:
        monkeypatch.setattr(
            "noust.monitor.plan.ACCESS_LOG_DIRS", {"nginx": (logs,), "apache": (logs,)}
        )
        store.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000))
        app = create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
        signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
        token = get_token_manager().generate_master_token()
        response = signed_in.post("/api/auth/login", json={"token": token})
        assert response.status_code == 200, response.text
        signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
        return signed_in

    def test_the_end_of_the_access_log_with_its_secrets_masked(
        self, client: TestClient, logs: Path
    ) -> None:
        write(logs, "access", "GET / 200", f'"GET /cb?token={TOKEN}" 200')

        body = client.get(f"/api/apps/{DOMAIN}/site-logs").json()

        assert body["available"] and body["kind"] == "access"
        assert body["lines"][0] == "GET / 200"
        assert TOKEN not in body["lines"][1] and "***" in body["lines"][1]

    def test_the_error_log_and_a_filter(self, client: TestClient, logs: Path) -> None:
        write(logs, "error", "connect() failed (111: Connection refused)", "worker started")

        body = client.get(
            f"/api/apps/{DOMAIN}/site-logs", params={"kind": "error", "contains": "refused"}
        ).json()

        assert body["lines"] == ["connect() failed (111: Connection refused)"]

    def test_a_site_whose_server_wrote_nothing_is_not_an_error(self, client: TestClient) -> None:
        response = client.get(f"/api/apps/{DOMAIN}/site-logs")

        assert response.status_code == 200
        assert response.json()["available"] is False

    def test_an_unknown_application_and_a_bad_kind(self, client: TestClient) -> None:
        assert client.get("/api/apps/nope.example.com/site-logs").status_code == 404
        assert (
            client.get(f"/api/apps/{DOMAIN}/site-logs", params={"kind": "debug"}).status_code == 400
        )
        assert (
            client.get(f"/api/apps/{DOMAIN}/site-logs", params={"lines": 5000}).status_code == 422
        )

    def test_reading_needs_the_permission_to_read_the_application(self) -> None:
        assert permission_for_route("GET", "/api/apps/{domain}/site-logs") == "apps.read"


class TestTheCommand:
    @pytest.fixture
    def run(self, tmp_path: Path, logs: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
        NoustStore.reset_instance()
        store = NoustStore(tmp_path / "noust.db")
        store.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000))
        monkeypatch.setattr(
            "noust.monitor.plan.ACCESS_LOG_DIRS", {"nginx": (logs,), "apache": (logs,)}
        )
        runner = CliRunner()
        yield lambda *args: runner.invoke(root_cli, ["site", "logs", *args])
        NoustStore.reset_instance()

    def test_it_prints_the_lines(self, run, logs: Path) -> None:  # type: ignore[no-untyped-def]
        write(logs, "access", "GET /a 200", f"GET /cb?token={TOKEN} 200")

        result = run(DOMAIN)

        assert result.exit_code == 0, result.output
        assert "GET /a 200" in result.output
        assert TOKEN not in result.output

    def test_json_and_the_error_log(self, run, logs: Path) -> None:  # type: ignore[no-untyped-def]
        write(logs, "error", "upstream timed out")

        result = run(DOMAIN, "--kind", "error", "--json")

        assert json.loads(result.output)["lines"] == ["upstream timed out"]

    def test_a_missing_log_is_said_not_failed(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run(DOMAIN)

        assert result.exit_code == 0 and "has not written" in result.output

    def test_an_unknown_application_fails(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("nope.example.com")

        assert result.exit_code == 1 and "not found" in result.output
