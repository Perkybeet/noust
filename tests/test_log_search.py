# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Searching the logs of every application at once (checklist G-13).

What is defended: the query means what it reads as (words are all required, a phrase
stays whole, ``-word`` excludes, ``OR`` offers a choice, fields narrow); the journal is
asked for exactly the applications that were allowed and nothing else; an entry of
another application, even one whose unit name starts the same, is never shown; every
line leaves masked; a search that examined all it was allowed says so; and a caller who
does not see the whole server cannot name a system unit.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.exceptions import ValidationError
from noust.core.redact import Scrubber
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.managers import log_search
from noust.managers.log_search import (
    LogSearch,
    fields_of,
    parse_moment,
    process_names,
)
from noust.managers.server import logquery
from noust.web.api import log_search as log_search_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

NOW = 1_800_000_000.0
SHOP = App(domain="shop.example.com", app_type="nodejs", port=3000)
BLOG = App(domain="blog.example.com", app_type="nodejs", port=3001)
STACK = App(domain="stack.example.com", app_type="docker-compose", port=3002)


def entry(
    message: str,
    unit: str = "shop-example-com.service",
    *,
    priority: int = 6,
    at: float = NOW - 60,
) -> str:
    return json.dumps(
        {
            "MESSAGE": message,
            "PRIORITY": str(priority),
            "_SYSTEMD_UNIT": unit,
            "__REALTIME_TIMESTAMP": str(int(at * 1_000_000)),
            "__CURSOR": f"s={unit};t={at}",
        }
    )


def journal(*entries: str) -> str:
    return "\n".join(entries) + "\n"


def search(
    runner: FakeRunner,
    text: str = "",
    apps: list[App] | None = None,
    **kwargs: Any,
) -> log_search.SearchResult:
    finder = LogSearch(runner, scrubber_for_app=lambda _domain: Scrubber([]), clock=lambda: NOW)
    return finder.search(text, apps if apps is not None else [SHOP], **kwargs)


def matched(text: str, *messages: str, apps: list[App] | None = None, **kwargs: Any) -> list[str]:
    """
    Args:
        text: A query.
        *messages: Lines of the shop's journal, newest first.
        apps: The applications searched.
        **kwargs: Further arguments of the search.

    Returns:
        The messages that the query keeps.
    """
    runner = FakeRunner()
    runner.script(
        ["journalctl"],
        stdout=journal(*(entry(m, at=NOW - 10 * i) for i, m in enumerate(messages, 1))),
    )
    return [line.message for line in search(runner, text, apps, **kwargs).lines]


class TestTheQuery:
    """The journal's grammar (tests/test_logquery.py defends the grammar itself)."""

    def test_words_are_all_required_and_case_does_not_matter(self) -> None:
        assert matched(
            "Timeout REFUSED", "Connect TIMEOUT: connection refused", "connect timeout"
        ) == ["Connect TIMEOUT: connection refused"]

    def test_a_quoted_phrase_stays_whole(self) -> None:
        assert matched(
            '"connection refused"', "error: Connection Refused by peer", "connection was refused"
        ) == ["error: Connection Refused by peer"]

    def test_a_word_with_a_minus_is_excluded(self) -> None:
        assert matched("error -healthcheck", "error in checkout", "error in healthcheck") == [
            "error in checkout"
        ]

    def test_or_offers_a_choice_and_groups_say_what_it_covers(self) -> None:
        lines = ("redis timeout", "nginx returned 502", "redis ok", "timeout elsewhere")

        assert matched("(redis OR nginx) (timeout OR 502)", *lines) == [
            "redis timeout",
            "nginx returned 502",
        ]

    def test_the_fields_narrow_and_are_not_words(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("boom", priority=3, at=NOW - 10),
                entry("boom quietly", priority=6, at=NOW - 20),
                entry("@app:shop.example.com", priority=3, at=NOW - 30),
            ),
        )

        result = search(runner, "@app:shop.example.com @level:ERR boom")

        assert [line.message for line in result.lines] == ["boom"]

    def test_a_unit_and_a_process_are_fields_too(self) -> None:
        runner = FakeRunner()
        raw = journal(
            entry("from the web", "shop-example-com.service"),
            entry("from the worker", "shop-example-com-worker@1.service"),
        )
        runner.script(["journalctl"], stdout=raw)

        result = search(
            runner,
            "@unit:shop-example-com-worker*",
            processes={"shop.example.com": {"worker"}},
        )

        assert [line.message for line in result.lines] == ["from the worker"]

    def test_the_process_that_logged_is_a_field(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout="\n".join(
                json.dumps(
                    {
                        "MESSAGE": message,
                        "PRIORITY": "6",
                        "_SYSTEMD_UNIT": "shop-example-com.service",
                        "_PID": pid,
                        "__REALTIME_TIMESTAMP": str(int((NOW - 10) * 1_000_000)),
                    }
                )
                for message, pid in (("mine", "4821"), ("other", "77"))
            ),
        )

        result = search(runner, "@pid:4821")

        assert [line.message for line in result.lines] == ["mine"]

    def test_an_empty_query_matches_everything(self) -> None:
        assert matched("", "anything", "at all") == ["anything", "at all"]

    @pytest.mark.parametrize(
        "text",
        ['"unclosed', "@level:loud", "@bogus:x", "@pid:abc", "x" * 501, "(a OR"],
    )
    def test_what_cannot_be_read_is_refused(self, text: str) -> None:
        with pytest.raises(ValidationError):
            search(FakeRunner(), text)

    def test_it_says_how_it_was_understood(self) -> None:
        result = search(FakeRunner(), "@level:err timeout -healthcheck", [])

        assert result.understood == "@level:err AND timeout AND NOT healthcheck"
        assert result.to_dict()["understood"] == result.understood

    def test_the_fields_a_query_mentions_are_found_anywhere_in_it(self) -> None:
        assert fields_of(logquery.parse("a (@level:err OR -@app:x) @unit:y")) == {
            "level",
            "app",
            "unit",
        }
        assert fields_of(logquery.parse("only words")) == set()


class TestTheMoments:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("15m", NOW - 900), ("2h", NOW - 7_200), ("7d", NOW - 7 * 86_400), ("1w", NOW - 604_800)],
    )
    def test_a_time_ago(self, text: str, expected: float) -> None:
        assert parse_moment(text, NOW, 0) == expected

    def test_epoch_seconds_and_dates(self) -> None:
        assert parse_moment("1700000000", NOW, 0) == 1_700_000_000
        assert parse_moment(1_700_000_000, NOW, 0) == 1_700_000_000
        midnight = datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp()
        assert parse_moment("2026-10-16", NOW, 0) == midnight
        assert parse_moment("2026-10-16T08:30:00", NOW, 0) == midnight + 8.5 * 3_600

    def test_nothing_is_the_default(self) -> None:
        assert parse_moment(None, NOW, 42.0) == 42.0
        assert parse_moment("", NOW, 42.0) == 42.0

    def test_what_is_not_a_time_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not a time"):
            parse_moment("yesterday-ish", NOW, 0)


class TestWhatTheJournalIsAskedFor:
    def run(self, *, text: str = "", apps: list[App] | None = None, **kwargs: Any) -> list[str]:
        runner = FakeRunner()
        runner.script(["journalctl"], stdout="")
        search(runner, text, apps, **kwargs)
        return list(runner.calls[0])

    def test_argv_only_in_a_window_of_the_applications_that_were_allowed(self) -> None:
        argv = self.run(text="timeout", apps=[SHOP, BLOG], since="2h")

        assert argv[0] == "journalctl"
        assert "--output=json" in argv and "--reverse" in argv
        assert f"--since=@{int(NOW - 7_200)}" in argv and f"--until=@{int(NOW)}" in argv
        assert argv.count("-u") == 4
        for unit in ("shop-example-com*", "wasm-shop-example-com*", "blog-example-com*"):
            assert unit in argv
        assert "--grep=timeout" in argv and "--case-sensitive=false" in argv
        assert "--output-fields=MESSAGE,PRIORITY,_SYSTEMD_UNIT,_PID" in argv

    def test_the_narrowing_word_is_escaped_for_the_regular_expression(self) -> None:
        argv = self.run(text='"a.b(c)"')

        assert "--grep=a\\.b\\(c\\)" in argv

    def test_the_narrowing_word_is_one_every_line_has(self) -> None:
        def grep(text: str) -> list[str]:
            return [part for part in self.run(text=text) if part.startswith("--grep=")]

        assert grep("timeout redis") == ["--grep=timeout"]
        assert grep("(redis OR nginx) timeout") == ["--grep=timeout"]
        assert grep("redis OR nginx") == []
        assert grep("-nothing") == []
        assert grep("@level:err") == []

    def test_a_level_is_a_priority(self) -> None:
        assert "--priority=3" in self.run(level="err")
        assert "--priority=4" in self.run(text="@level:warning")
        assert "--priority=3" in self.run(text="boom @level:err @level:warning")

    def test_no_applications_means_no_command(self) -> None:
        runner = FakeRunner()

        result = search(runner, "x", [])

        assert runner.calls == [] and result.lines == []

    def test_a_compose_application_is_not_asked_of_the_journal(self) -> None:
        runner = FakeRunner()
        runner.script(["docker"], stdout="")

        search(runner, "x", [STACK])

        assert not any(call[0] == "journalctl" for call in runner.calls)


class TestWhatIsFound:
    def test_matching_lines_newest_first_with_their_level_and_application(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("db timeout while paying", priority=3, at=NOW - 300),
                entry("nothing wrong", at=NOW - 200),
                entry("redis timeout", priority=4, at=NOW - 100),
            ),
        )

        result = search(runner, "timeout")

        assert [(line.message, line.level) for line in result.lines] == [
            ("redis timeout", "warning"),
            ("db timeout while paying", "err"),
        ]
        assert result.lines[0].app == "shop.example.com" and result.lines[0].process == "web"
        assert (result.scanned, result.matched, result.truncated) == (3, 2, False)

    def test_another_applications_unit_is_never_shown_even_when_the_name_starts_alike(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("mine", "shop-example-com.service"),
                entry("theirs", "shop-example-com-au.service"),
                entry("not an app", "sshd.service"),
            ),
        )

        result = search(runner, "")

        assert [line.message for line in result.lines] == ["mine"]

    def test_a_side_process_belongs_to_its_application(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"], stdout=journal(entry("job done", "shop-example-com-worker@1.service"))
        )

        result = search(runner, "", processes={"shop.example.com": {"worker"}})

        assert (result.lines[0].app, result.lines[0].process) == ("shop.example.com", "worker")

    def test_the_limit_bounds_the_lines_and_not_the_count(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"], stdout=journal(*(entry(f"line {i}", at=NOW - i) for i in range(10)))
        )

        result = search(runner, "line", limit=3)

        assert len(result.lines) == 3 and result.matched == 10
        assert result.lines[0].message == "line 0"

    def test_the_scan_stops_at_its_limit_and_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(log_search, "SCAN_LIMIT", 4)
        runner = FakeRunner()
        runner.script(
            ["journalctl"], stdout=journal(*(entry(f"m{i}", at=NOW - i) for i in range(10)))
        )

        result = search(runner, "")

        assert result.truncated and result.scanned == 4 and result.matched == 4

    def test_a_binary_message_is_decoded_and_junk_lines_skipped(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout="not json\n"
            + json.dumps(
                {
                    "MESSAGE": list(b"bytes ok"),
                    "_SYSTEMD_UNIT": "shop-example-com.service",
                    "__REALTIME_TIMESTAMP": "5",
                }
            )
            + "\n[1,2]\n",
        )

        result = search(runner, "bytes")

        assert [line.message for line in result.lines] == ["bytes ok"]

    def test_a_failing_journal_is_a_note_and_not_an_exception(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"], stderr="Failed to open journal: Permission denied", exit_code=1
        )

        result = search(runner, "x")

        assert result.lines == []
        assert any("Permission denied" in note for note in result.notes)

    def test_app_in_the_query_narrows_the_applications(self) -> None:
        runner = FakeRunner()
        runner.script(["journalctl"], stdout="")

        search(runner, "@app:blog.example.com", [SHOP, BLOG])

        argv = list(runner.calls[0])
        assert "blog-example-com*" in argv and "shop-example-com*" not in argv

    def test_an_app_nobody_can_see_is_said(self) -> None:
        result = search(FakeRunner(), "@app:nope", [SHOP])

        assert any("nope" in note for note in result.notes)

    def test_app_is_judged_by_the_line_so_or_and_not_mean_what_they_say(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("shop boom", "shop-example-com.service", at=NOW - 10),
                entry("blog boom", "blog-example-com.service", at=NOW - 20),
            ),
        )

        either = search(
            runner, "boom (@app:shop.example.com OR @app:blog.example.com)", [SHOP, BLOG]
        )
        neither = search(runner, "boom -@app:shop.example.com", [SHOP, BLOG])

        assert [line.message for line in either.lines] == ["shop boom", "blog boom"]
        assert [line.message for line in neither.lines] == ["blog boom"]


class TestWhatIsShown:
    def test_credentials_colours_and_the_applications_own_secrets_are_gone(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry(
                    "\x1b[31mcalling https://api.example.com/?token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 "
                    "with password=hunter2-secret and hush-value\x1b[0m"
                )
            ),
        )
        finder = LogSearch(
            runner, scrubber_for_app=lambda _d: Scrubber(["hush-value"]), clock=lambda: NOW
        )

        (line,) = finder.search("calling", [SHOP]).lines

        assert "\x1b" not in line.message
        for secret in ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "hunter2-secret", "hush-value"):
            assert secret not in line.message, secret

    def test_a_word_hidden_by_the_mask_does_not_match(self) -> None:
        # The words are looked for in what is shown, so a search cannot be used to
        # probe for a secret the mask removes.
        runner = FakeRunner()
        runner.script(["journalctl"], stdout=journal(entry("the key is hush-value")))
        finder = LogSearch(
            runner, scrubber_for_app=lambda _d: Scrubber(["hush-value"]), clock=lambda: NOW
        )

        assert finder.search("hush-value", [SHOP]).lines == []


class TestTheStacksContainers:
    def stack_runner(self, logs: str, *, level_note: bool = False) -> FakeRunner:
        runner = FakeRunner()
        runner.script(["docker", "ps"], stdout="abc123|web|running\n")
        runner.script(["docker", "logs"], stdout=logs)
        return runner

    def test_what_the_containers_printed_is_searched_too(self) -> None:
        runner = self.stack_runner(
            "2026-10-16T08:00:00.500000000Z GET /boom 500 timeout\n"
            "2026-10-16T08:00:01.000000000Z all fine\n"
        )

        result = search(runner, "timeout", [STACK], since="2026-10-16", until="2026-10-17")

        (line,) = result.lines
        assert (line.source, line.unit, line.app, line.level) == (
            "container",
            "web",
            "stack.example.com",
            None,
        )
        assert line.ts == datetime(2026, 10, 16, 8, tzinfo=timezone.utc).timestamp() + 0.5
        logs = next(c for c in runner.calls if c[:2] == ("docker", "logs"))
        assert "--timestamps" in logs and f"--tail={log_search.CONTAINER_TAIL}" in logs

    def test_a_level_leaves_the_containers_out_and_says_so(self) -> None:
        runner = self.stack_runner("2026-10-16T08:00:00Z error x\n")

        result = search(runner, "error", [STACK], level="err")

        assert result.lines == []
        assert any("no levels" in note for note in result.notes)

    def test_a_level_named_in_the_query_does_the_same(self) -> None:
        runner = self.stack_runner("2026-10-16T08:00:00Z error x\n")

        result = search(runner, "error @level:err", [STACK])

        assert result.lines == []
        assert any("no levels" in note for note in result.notes)
        assert not any(call[:2] == ("docker", "logs") for call in runner.calls)

    def test_the_service_and_the_application_are_fields_of_a_containers_line(self) -> None:
        runner = self.stack_runner("2026-10-16T08:00:00Z hello\n")

        mine = search(runner, "@app:stack.example.com @unit:web", [STACK])
        another = search(runner, "@app:other.example.com", [STACK])
        other_service = search(runner, "@unit:worker", [STACK])

        assert [line.message for line in mine.lines] == ["hello"]
        assert another.lines == [] and other_service.lines == []

    def test_a_stack_that_cannot_be_listed_is_a_note(self) -> None:
        runner = FakeRunner(default_exit_code=1)

        result = search(runner, "x", [STACK])

        assert any("stack.example.com" in note for note in result.notes)


class TestTheLimits:
    def test_the_window_must_have_an_end_after_its_start(self) -> None:
        with pytest.raises(ValidationError, match="ends before"):
            search(FakeRunner(), "", since="1h", until="2h")

    def test_the_window_has_a_longest(self) -> None:
        with pytest.raises(ValidationError, match="longer than the journal"):
            search(FakeRunner(), "", since="500d")

    @pytest.mark.parametrize("limit", [0, 1001])
    def test_the_limit_has_bounds(self, limit: int) -> None:
        with pytest.raises(ValidationError, match="limit"):
            search(FakeRunner(), "", limit=limit)

    def test_a_level_must_be_one(self) -> None:
        with pytest.raises(ValidationError, match="not a level"):
            search(FakeRunner(), "", level="loud")

    def test_a_system_unit_is_not_reachable_from_here(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("Accepted", "sshd.service"), entry("mine", "shop-example-com.service")
            ),
        )

        result = search(runner, "@unit:sshd.service")

        # The Server page's search is for the machine's own units; this one reads the
        # applications' and judges every line again, so the unit is a filter and no door.
        argv = list(runner.calls[0])
        assert "sshd.service" not in argv
        assert result.lines == []


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class TestTheProcessNames:
    def test_only_apps_that_run_as_units_have_any(self, store: NoustStore) -> None:
        store.create_app(SHOP)

        assert process_names([SHOP, STACK]) == {"shop.example.com": set()}


class TestTheApi:
    @pytest.fixture
    def client(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("timeout paying", priority=3),
                entry("blog timeout", "blog-example-com.service", priority=4),
            ),
        )
        self.runner = runner
        store.create_app(SHOP)
        store.create_app(BLOG)
        monkeypatch.setattr("noust.managers.log_search.get_runner", lambda: runner)
        app = FastAPI()
        app.include_router(log_search_api.router, prefix="/api/logs")
        install_error_handlers(app)
        app.dependency_overrides[get_current_session] = lambda: {
            "session_id": "t",
            "type": "master",
        }
        return TestClient(app, raise_server_exceptions=True)

    def test_it_searches_every_application_the_caller_sees(self, client: TestClient) -> None:
        body = client.get("/api/logs/search", params={"q": "timeout", "since": "1d"}).json()

        assert {line["app"] for line in body["lines"]} == {"shop.example.com", "blog.example.com"}
        assert sorted(body["apps"]) == ["blog.example.com", "shop.example.com"]
        assert body["matched"] == 2 and body["truncated"] is False

    def test_apps_narrow_it(self, client: TestClient) -> None:
        body = client.get(
            "/api/logs/search", params={"q": "timeout", "app": "shop.example.com", "since": "1d"}
        ).json()

        assert body["apps"] == ["shop.example.com"]
        assert "blog-example-com*" not in list(self.runner.calls[-1])

    def test_a_query_that_cannot_be_read_is_a_400_saying_where_and_the_syntax(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/logs/search", params={"q": 'boom "open'})

        assert response.status_code == 400
        body = json.dumps(response.json())
        assert "not closed" in body and "character 6" in body and "@unit:" in body

    def test_it_says_how_the_query_was_understood(self, client: TestClient) -> None:
        body = client.get("/api/logs/search", params={"q": "timeout -blog", "since": "1d"}).json()

        assert body["understood"] == "timeout AND NOT blog"

    def test_a_limit_out_of_range_is_a_422(self, client: TestClient) -> None:
        assert client.get("/api/logs/search", params={"limit": 5000}).status_code == 422

    def test_a_system_unit_is_not_a_way_in_for_anyone(self, client: TestClient) -> None:
        body = client.get(
            "/api/logs/search", params={"q": "@unit:sshd.service", "since": "1d"}
        ).json()

        assert body["lines"] == []
        assert "sshd.service" not in list(self.runner.calls[-1])

    def test_the_permissions_and_the_scope_are_declared(self) -> None:
        from noust.web.permissions import registry
        from noust.web.permissions.scopes import INSTANCE, SCOPED, rule_for

        table = registry.route_map()
        assert str(table[("GET", "/api/logs/search")]) == "apps.read"
        assert str(table[("PUT", "/api/logs/retention")]) == "server.manage"
        assert rule_for("GET", "/api/logs/search").scoping == SCOPED
        assert rule_for("GET", "/api/logs/retention").scoping == INSTANCE


class TestTheCommand:
    def test_it_prints_lines_newest_first(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.create_app(SHOP)
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("first timeout", at=NOW - 20), entry("second timeout", at=NOW - 10)
            ),
        )
        monkeypatch.setattr("noust.managers.log_search.get_runner", lambda: runner)
        monkeypatch.setattr("noust.managers.log_search.time.time", lambda: NOW)

        result = CliRunner().invoke(root_cli, ["log", "search", "timeout", "--since", "1d"])

        assert result.exit_code == 0, result.output
        assert result.output.index("second timeout") < result.output.index("first timeout")
        assert "shop.example.com" in result.output

    def test_not_leaves_a_word_out_and_a_phrase_stays_whole(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.create_app(SHOP)
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout=journal(
                entry("connection refused by redis"),
                entry("connection refused by healthcheck"),
                entry("connection reset"),
            ),
        )
        monkeypatch.setattr("noust.managers.log_search.get_runner", lambda: runner)
        monkeypatch.setattr("noust.managers.log_search.time.time", lambda: NOW)

        result = CliRunner().invoke(
            root_cli, ["log", "search", "connection refused", "--not", "healthcheck", "--json"]
        )

        assert result.exit_code == 0, (result.output, result.exception)
        body = json.loads(result.output)
        assert [line["message"] for line in body["lines"]] == ["connection refused by redis"]

    def test_nothing_found_says_how_much_was_looked_at(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.create_app(SHOP)
        runner = FakeRunner()
        runner.script(["journalctl"], stdout=journal(entry("calm")))
        monkeypatch.setattr("noust.managers.log_search.get_runner", lambda: runner)
        monkeypatch.setattr("noust.managers.log_search.time.time", lambda: NOW)

        result = CliRunner().invoke(root_cli, ["log", "search", "boom"])

        assert "Nothing matched in 1 entries" in result.output
