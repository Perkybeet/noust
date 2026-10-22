# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The post-receive hook of a push repository (checklist A-23).

The hook is a script of the standard library written by Noust; the tests run its text as a module
with a fake git and a fake console, so what is pinned is what a push sends and says: a signed
GitHub-shaped push event that the console's own parser reads (skip markers, watch paths), the
branch filter, that a deleted branch and a tag deploy nothing, what the pusher is told for every
answer the console can give, and that the hook never fails a push.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from noust.core.forge_events import parse_push_change
from noust.pushdeploy import hook

DOMAIN = "shop.example.com"
SECRET = "s3cret-value"
OLD = "1" * 40
NEW = "2" * 40
ZERO = "0" * 40


def load(python: str = "/usr/bin/python3") -> ModuleType:
    """
    Run the hook's text as a module.

    Args:
        python: The interpreter named on its first line.

    Returns:
        The module, whose ``main`` is not run.
    """
    module = ModuleType("noust_push_hook")
    module.__dict__["__file__"] = "/repo/hooks/post-receive"
    # The hook's own text, written by this package: what these tests are about.
    exec(compile(hook.render_hook(python), "post-receive", "exec"), module.__dict__)  # noqa: S102
    return module


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / hook.CONFIG_NAME
    path.write_text(
        hook.HookConfig(
            domain=DOMAIN,
            url=f"http://127.0.0.1:8080/hooks/deploy/{DOMAIN}",
            secret=SECRET,
            branch="main",
        ).render()
    )
    return path


class FakeGit:
    """What the hook asks git: the commits of a push and the files each touched."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] == "log":
            return (
                f"{'a' * 40}\x1fsecond: fix the cart\n\nmore text\x1e"
                f"{'b' * 40}\x1ffirst [skip ci]\x1e"
            )
        if args[0] == "rev-list":
            return "2\n"
        if args[0] == "diff-tree":
            sha = args[-1]
            if sha == "a" * 40:
                return "M\tsrc/cart.py\nA\tsrc/new.py\nD\told.py\nR100\told_name.py\tnew_name.py\n"
            return "M\tREADME.md\n"
        return ""


@pytest.fixture
def run(config_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    """Run the hook on some pushed lines; return what it sent, what it said and its exit code."""

    def go(lines: list[str], answer: tuple[int | None, dict[str, Any]] = (202, {"job_id": "abc"})):
        module = load()
        git = FakeGit()
        sent: list[tuple[dict[str, Any], bytes]] = []

        def deliver(config: dict[str, Any], body: bytes) -> tuple[int | None, dict[str, Any]]:
            sent.append((config, body))
            return answer

        monkeypatch.setattr(module, "git", git)
        monkeypatch.setattr(module, "deliver", deliver)
        code = module.main(lines, str(config_file))
        return sent, capsys.readouterr().out, code, git

    return go


class TestWhatIsSent:
    def test_a_push_to_the_deployed_branch_is_a_push_event_the_console_reads(self, run) -> None:
        sent, out, code, _ = run([f"{OLD} {NEW} refs/heads/main"])

        assert code == 0
        assert len(sent) == 1
        payload = json.loads(sent[0][1])
        assert payload["ref"] == "refs/heads/main"
        assert (payload["before"], payload["after"]) == (OLD, NEW)
        assert payload["repository"]["full_name"] == DOMAIN
        assert payload["total_commits_count"] == 2
        assert payload["head_commit"]["id"] == "a" * 40
        change = parse_push_change("github", payload)
        assert change.messages == ("second: fix the cart\n\nmore text", "first [skip ci]")
        assert change.files is not None
        assert set(change.files) == {
            "src/cart.py",
            "src/new.py",
            "old.py",
            "old_name.py",
            "new_name.py",
            "README.md",
        }
        assert "queued the update of shop.example.com (job abc)" in out

    def test_a_branch_that_is_new_lists_what_is_reachable_and_not_a_range(self, run) -> None:
        _, _, _, git = run([f"{ZERO} {NEW} refs/heads/main"])
        log = next(call for call in git.calls if call[0] == "log")
        assert log[-1] == NEW and ".." not in log[-1]

    def test_an_update_lists_the_range(self, run) -> None:
        _, _, _, git = run([f"{OLD} {NEW} refs/heads/main"])
        log = next(call for call in git.calls if call[0] == "log")
        assert log[-1] == f"{OLD}..{NEW}"
        assert "-n" in log and log[log.index("-n") + 1] == "20"

    def test_the_commit_files_are_read_with_renames_and_merges_in_mind(self, run) -> None:
        _, _, _, git = run([f"{OLD} {NEW} refs/heads/main"])
        diff = next(call for call in git.calls if call[0] == "diff-tree")
        assert "-m" in diff and "--root" in diff and "--name-status" in diff


class TestTheSignature:
    def test_it_signs_what_it_sends_with_the_applications_secret_in_the_way_the_console_checks(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = load()
        seen: list[urllib.request.Request] = []

        class Answer(io.BytesIO):
            status = 202

            def __enter__(self) -> Answer:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def opener(request: urllib.request.Request, timeout: int, context: object = None):
            seen.append(request)
            return Answer(b'{"job_id": "j1", "status": "pending"}')

        monkeypatch.setattr(module.urllib.request, "urlopen", opener)
        config = json.loads(config_file.read_text())
        body = b'{"ref": "refs/heads/main"}'

        status, answer = module.deliver(config, body)

        assert (status, answer["job_id"]) == (202, "j1")
        request = seen[0]
        assert request.full_url == config["url"] and request.get_method() == "POST"
        headers = {key.lower(): value for key, value in request.header_items()}
        expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert headers["x-hub-signature-256"] == f"sha256={expected}"
        assert headers["x-github-event"] == "push"
        assert len(headers["x-github-delivery"]) == 36
        assert headers["content-type"] == "application/json"

    def test_every_push_has_a_delivery_id_of_its_own(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = load()
        ids: list[str] = []

        def opener(request: urllib.request.Request, timeout: int, context: object = None):
            ids.append(dict(request.header_items())["X-github-delivery"])
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(module.urllib.request, "urlopen", opener)
        config = json.loads(config_file.read_text())
        module.deliver(config, b"{}")
        module.deliver(config, b"{}")
        assert len(set(ids)) == 2

    def test_https_to_the_console_on_the_loopback_address_does_not_ask_for_a_certificate_name(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = load()
        contexts: list[object] = []

        def opener(request: urllib.request.Request, timeout: int, context: object = None):
            contexts.append(context)
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(module.urllib.request, "urlopen", opener)
        config = json.loads(config_file.read_text())
        module.deliver({**config, "url": "https://127.0.0.1:8443/hooks/deploy/x"}, b"{}")
        module.deliver(config, b"{}")
        assert contexts[0] is not None and contexts[1] is None

    def test_an_error_status_is_an_answer_and_a_dead_console_is_none(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = load()
        config = json.loads(config_file.read_text())

        def refused(request: urllib.request.Request, timeout: int, context: object = None):
            raise urllib.error.HTTPError(
                config["url"], 404, "Not Found", {}, io.BytesIO(b'{"detail": "Not found"}')
            )

        monkeypatch.setattr(module.urllib.request, "urlopen", refused)
        assert module.deliver(config, b"{}") == (404, {"detail": "Not found"})

        def dead(request: urllib.request.Request, timeout: int, context: object = None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(module.urllib.request, "urlopen", dead)
        status, answer = module.deliver(config, b"{}")
        assert status is None and "connection refused" in answer["error"]


class TestWhatIsNotSent:
    def test_another_branch_is_stored_and_says_how_to_deploy_it(self, run) -> None:
        sent, out, code, _ = run([f"{OLD} {NEW} refs/heads/feature"])
        assert sent == [] and code == 0
        assert "feature was stored" in out
        assert "deploys main only" in out
        assert f"noust push enable {DOMAIN} --branch feature" in out

    def test_a_deleted_branch_deploys_nothing(self, run) -> None:
        sent, out, code, _ = run([f"{OLD} {ZERO} refs/heads/main"])
        assert sent == [] and out == "" and code == 0

    def test_a_tag_deploys_nothing(self, run) -> None:
        sent, out, _, _ = run([f"{ZERO} {NEW} refs/tags/v1.0"])
        assert sent == [] and out == ""

    def test_a_line_that_is_not_three_words_is_skipped(self, run) -> None:
        sent, _, code, _ = run(["", "garbage", "a b c d"])
        assert sent == [] and code == 0

    def test_several_refs_in_one_push_are_each_looked_at(self, run) -> None:
        sent, _, _, _ = run([f"{OLD} {NEW} refs/heads/feature", f"{OLD} {NEW} refs/heads/main"])
        assert len(sent) == 1


class TestWhatThePusherIsTold:
    @pytest.mark.parametrize(
        ("status", "answer", "expected"),
        [
            (202, {"job_id": "j7"}, "queued the update of shop.example.com (job j7)"),
            (200, {"reason": "branch"}, "nothing was deployed: branch"),
            (200, {"reason": "skip", "detail": "a [skip ci] marker"}, "a [skip ci] marker"),
            (429, {}, "the deploy queue is full"),
            (401, {}, "noust push refresh shop.example.com"),
            (404, {}, "noust push refresh shop.example.com"),
            (500, {}, "HTTP 500"),
            (
                None,
                {"error": "connection refused"},
                "the console is not reachable (connection refused)",
            ),
        ],
    )
    def test_each_answer_of_the_console(
        self, run, status: int | None, answer: dict, expected: str
    ) -> None:
        _, out, code, _ = run([f"{OLD} {NEW} refs/heads/main"], (status, answer))
        assert expected in out
        assert code == 0
        if status not in (200, 202):
            assert f"noust update {DOMAIN}" in out or "noust push refresh" in out

    def test_a_console_that_cannot_be_reached_says_the_code_is_stored(self, run) -> None:
        _, out, _, _ = run([f"{OLD} {NEW} refs/heads/main"], (None, {"error": "x"}))
        assert "The code is stored" in out or "the code is stored" in out.lower()


class TestItNeverFailsAPush:
    def test_settings_that_cannot_be_read(self, tmp_path: Path, capsys, monkeypatch) -> None:
        module = load()
        assert module.main([f"{OLD} {NEW} refs/heads/main"], str(tmp_path / "missing.json")) == 0
        assert "noust push refresh" in capsys.readouterr().out

    def test_settings_that_are_not_what_noust_writes(self, tmp_path: Path, capsys) -> None:
        module = load()
        bad = tmp_path / "bad.json"
        bad.write_text('{"domain": "x"}')
        assert module.main([f"{OLD} {NEW} refs/heads/main"], str(bad)) == 0
        assert "noust push refresh" in capsys.readouterr().out

    def test_anything_that_goes_wrong_while_building_the_event(
        self, config_file: Path, capsys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = load()

        def boom(*args: str) -> str:
            raise RuntimeError("git exploded")

        monkeypatch.setattr(module, "git", boom)
        assert module.main([f"{OLD} {NEW} refs/heads/main"], str(config_file)) == 0
        out = capsys.readouterr().out
        assert "RuntimeError: git exploded" in out

    def test_a_git_that_cannot_run_gives_an_event_without_commits(
        self, config_file: Path, capsys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = load()
        sent: list[bytes] = []
        monkeypatch.setattr(module, "git", lambda *args: "")
        monkeypatch.setattr(
            module, "deliver", lambda config, body: (sent.append(body), (202, {"job_id": "j"}))[1]
        )
        assert module.main([f"{OLD} {NEW} refs/heads/main"], str(config_file)) == 0
        payload = json.loads(sent[0])
        assert payload["commits"] == [] and payload["head_commit"] is None


class TestTheScript:
    def test_it_is_a_python_script_named_for_the_interpreter(self) -> None:
        text = hook.render_hook("/usr/bin/python3")
        assert text.startswith("#!/usr/bin/python3\n")
        compile(text, "post-receive", "exec")

    def test_it_uses_the_standard_library_alone(self) -> None:
        text = hook.render_hook("/usr/bin/python3")
        imports = {
            line.split()[1].split(".")[0]
            for line in text.splitlines()
            if line.startswith(("import ", "from "))
        }
        assert imports <= {
            "hashlib",
            "hmac",
            "json",
            "os",
            "ssl",
            "subprocess",
            "sys",
            "urllib",
            "uuid",
        }

    def test_it_runs_main_only_as_a_script(self) -> None:
        # Loading it as a module (as these tests do) sends nothing.
        module = load()
        assert hasattr(module, "main") and module.__name__ == "noust_push_hook"

    def test_the_settings_are_round_tripped_and_never_trusted(self) -> None:
        config = hook.HookConfig(DOMAIN, "http://127.0.0.1:1/hooks/deploy/x", "sec", "main")
        assert hook.read_config(config.render()) == config
        for bad in ("", "[]", "not json", '{"domain": "x"}', "null"):
            assert hook.read_config(bad) is None
