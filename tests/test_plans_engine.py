# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The engine that makes a plan: the code of an operation, run inside a rehearsal of its own.

What it must never do is the content of most of these tests: execute a command that is not a
declared probe, leave a file or a row behind, let another request's thread rehearse, or show a
secret. Every one is held with an executor that fails the test, not with an assertion on what the
operation was meant to do.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import NoustError
from noust.core.fs import get_fs, is_rehearsal
from noust.core.plans import engine
from noust.core.plans.recorder import PROBE_TIMEOUT_CAP
from noust.core.redact import Scrubber
from noust.core.runner import (
    CommandResult,
    FakeRunner,
    get_runner,
    is_read_only,
    set_runner,
)
from noust.core.store import App, NoustStore

CANARY = "canary-5d2e8a7f1c4b9036"


class OnlyLooks(FakeRunner):
    """
    The runner a plan's probes reach: it fails the test on anything that is not declared to only look.
    """

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        assert is_read_only(argv), f"a plan executed {list(argv)}"
        return super().run(argv, **kwargs)

    def stream(self, argv: Any, **kwargs: Any) -> CommandResult:
        assert is_read_only(argv), f"a plan streamed {list(argv)}"
        return super().stream(argv, **kwargs)

    def start(self, argv: Any, **kwargs: Any) -> Any:
        assert is_read_only(argv), f"a plan started {list(argv)}"
        return super().start(argv, **kwargs)

    def capture_to_file(self, argv: Any, destination: Path, **kwargs: Any) -> CommandResult:
        raise AssertionError(f"a plan captured {list(argv)} to {destination}")


@pytest.fixture
def machine() -> OnlyLooks:
    """The process's runner: the one the plan's probes use, and nothing else may."""
    runner = OnlyLooks()
    set_runner(runner)
    yield runner
    set_runner(None)


def test_what_changes_is_noted_in_order_and_never_executed(machine: OnlyLooks) -> None:
    def operation() -> str:
        runner = get_runner()
        runner.run(["systemctl", "is-active", "shop"], timeout=5)
        runner.run(["systemctl", "restart", "shop"], timeout=30)
        runner.stream(["npm", "ci"], on_line=lambda line: None, timeout=900, cwd=Path("/srv/shop"))
        return "done"

    plan = engine.rehearse(operation)

    assert plan.result == "done" and plan.error is None
    assert [(c.argv, c.probe) for c in plan.commands] == [
        (("systemctl", "is-active", "shop"), True),
        (("systemctl", "restart", "shop"), False),
        (("npm", "ci"), False),
    ]
    assert plan.commands[2].cwd == "/srv/shop"
    assert machine.calls == [("systemctl", "is-active", "shop")]


def test_a_probe_gets_the_machines_real_answer_so_the_operation_decides_on_facts(
    machine: OnlyLooks,
) -> None:
    machine.script(["systemctl", "is-active", "shop"], stdout="failed\n", exit_code=3)

    plan = engine.rehearse(
        lambda: get_runner().run(["systemctl", "is-active", "shop"], timeout=5).stdout
    )

    assert plan.result == "failed\n"


def test_a_command_that_changes_something_answers_success_and_nothing_else(
    machine: OnlyLooks,
) -> None:
    plan = engine.rehearse(
        lambda: get_runner().run(["systemctl", "restart", "shop"], timeout=30).exit_code
    )

    assert plan.result == 0


def test_a_sandboxed_build_is_a_command_that_runs_as_the_sandbox_account(
    machine: OnlyLooks,
) -> None:
    from noust.core.runner import SandboxSpec

    release = Path("/srv/shop/releases/r1")
    spec = SandboxSpec(user="noust-build", working_dir=release, writable_paths=(release,))

    plan = engine.rehearse(
        lambda: get_runner().run(["npm", "run", "build"], timeout=900, sandbox=spec)
    )

    assert plan.commands[0].argv == ("npm", "run", "build")
    assert plan.commands[0].runs_as == "sandbox:noust-build"
    assert machine.calls == []


def test_a_dump_is_a_command_and_the_file_it_would_write(
    machine: OnlyLooks, tmp_path: Path
) -> None:
    target = tmp_path / "dump.sql.gz"

    plan = engine.rehearse(
        lambda: get_runner().capture_to_file(
            ["pg_dump", "shop"], target, compress=True, timeout=600
        )
    )

    assert plan.commands[0].writes_to == str(target)
    assert not target.exists()


def test_a_secret_never_reaches_a_command_of_the_plan(machine: OnlyLooks) -> None:
    def operation() -> None:
        get_runner().run(
            ["git", "clone", f"https://deploy:{CANARY}@git.example.com/shop.git"], timeout=60
        )
        get_runner().run(["mysql", f"--password={CANARY}"], timeout=60, secrets=[CANARY])
        get_runner().run(["tool", "--flag", f"token-{CANARY}"], timeout=60, secrets=[CANARY])

    plan = engine.rehearse(operation, scrub=Scrubber([CANARY]).scrub)

    assert CANARY not in repr(plan.commands)
    assert all(CANARY not in command.text for command in plan.commands)


def test_the_files_it_would_write_are_noted_with_their_diff_and_nothing_is_written(
    machine: OnlyLooks, tmp_path: Path
) -> None:
    unit = tmp_path / "shop.service"
    unit.write_text("[Service]\nExecStart=/bin/old\n")
    fresh = tmp_path / "new.conf"

    def operation() -> None:
        get_fs().write_text(unit, "[Service]\nExecStart=/bin/new\n")
        get_fs().write_text(fresh, "a = 1\n")

    plan = engine.rehearse(operation)

    changed = {item.path: item for item in plan.files}
    assert changed[str(unit)].change == "modify"
    assert [(line.kind, line.text) for line in changed[str(unit)].lines] == [
        ("same", "[Service]"),
        ("del", "ExecStart=/bin/old"),
        ("add", "ExecStart=/bin/new"),
    ]
    assert changed[str(fresh)].change == "create"
    assert unit.read_text() == "[Service]\nExecStart=/bin/old\n" and not fresh.exists()


def test_the_last_content_written_to_a_path_is_the_one_the_plan_shows(tmp_path: Path) -> None:
    target = tmp_path / "x.conf"

    def operation() -> None:
        get_fs().write_text(target, "first\n")
        get_fs().write_text(target, "second\n")

    plan = engine.rehearse(operation)

    assert [(f.path, [line.text for line in f.lines]) for f in plan.files] == [
        (str(target), ["second"])
    ]


def test_a_file_made_and_deleted_inside_the_operation_is_no_change(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"

    def operation() -> None:
        get_fs().write_text(scratch, "x")
        get_fs().remove(scratch)

    assert engine.rehearse(operation).files == ()


def test_deleting_a_file_that_exists_is_a_change_and_the_file_stays(tmp_path: Path) -> None:
    victim = tmp_path / "old.conf"
    victim.write_text("keep me\n")

    plan = engine.rehearse(lambda: get_fs().remove(victim))

    assert [(f.path, f.change) for f in plan.files] == [(str(victim), "delete")]
    assert victim.read_text() == "keep me\n"


def test_a_directory_a_move_a_copy_and_a_link_each_say_what_they_are(tmp_path: Path) -> None:
    source = tmp_path / "releases" / "r1"
    source.mkdir(parents=True)

    def operation() -> None:
        get_fs().make_dir(tmp_path / "shared")
        get_fs().move(source, tmp_path / "releases" / "r2")
        get_fs().copy_tree(source, tmp_path / "copy")
        get_fs().symlink(tmp_path / "releases" / "r2", tmp_path / "current")

    plan = engine.rehearse(operation)

    assert {
        (Path(f.path).name, f.change, Path(f.destination).name if f.destination else None)
        for f in plan.files
    } == {
        ("shared", "mkdir", None),
        ("r1", "move", "r2"),
        ("r1", "copy", "copy"),
        ("current", "link", "r2"),
    }


def test_a_dotenv_the_operation_writes_shows_names_and_no_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"KEEP=1\nDB_PASSWORD={CANARY}\n")

    plan = engine.rehearse(
        lambda: get_fs().write_text(
            env, f"KEEP=1\nDB_PASSWORD={CANARY}-rotated\nADDED=x\n", mode=0o600
        )
    )

    assert plan.files[0].shown == "names"
    assert [(line.kind, line.text) for line in plan.files[0].lines] == [
        ("del", "DB_PASSWORD"),
        ("add", "DB_PASSWORD"),
        ("add", "ADDED"),
    ]
    assert CANARY not in repr(plan.files)


def test_a_symlink_in_the_tree_is_never_followed_to_show_what_it_points_at(tmp_path: Path) -> None:
    """A repository is untrusted: a link it plants must not turn a plan into a way to read files."""
    private = tmp_path / "private.txt"
    private.write_text(f"{CANARY}\n")
    tree = tmp_path / "release"
    tree.mkdir()
    (tree / "settings.txt").symlink_to(private)
    (tree / "dir").symlink_to(tmp_path)

    def operation() -> None:
        get_fs().write_text(tree / "settings.txt", "harmless\n")
        get_fs().write_text(tree / "dir" / "private.txt", "harmless\n")

    plan = engine.rehearse(operation)

    assert plan.files and all(item.lines == () for item in plan.files)
    assert all(item.reason == "unreadable" for item in plan.files)
    assert CANARY not in repr(plan.files)
    assert private.read_text() == f"{CANARY}\n"


def test_the_store_is_left_as_it_was(tmp_path: Path) -> None:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    try:

        def operation() -> None:
            store.create_app(App(domain="shop.example.com", app_type="nodejs", port=3000))

        engine.rehearse(operation)

        assert store.get_app("shop.example.com") is None
    finally:
        NoustStore.reset_instance()


def test_inside_the_plan_the_code_is_in_a_rehearsal_and_outside_it_is_not(
    machine: OnlyLooks,
) -> None:
    inside = engine.rehearse(is_rehearsal)

    assert inside.result is True
    assert not is_rehearsal()


def test_a_refusal_of_the_operation_is_the_plans_to_report_with_what_ran_before_it(
    machine: OnlyLooks,
) -> None:
    def operation() -> None:
        get_runner().run(["systemctl", "is-active", "shop"], timeout=5)
        raise NoustError("There is no room on the disk")

    plan = engine.rehearse(operation)

    assert (
        isinstance(plan.error, NoustError) and plan.error.message == "There is no room on the disk"
    )
    assert [c.argv for c in plan.commands] == [("systemctl", "is-active", "shop")]


def test_a_defect_in_the_operation_is_not_swallowed(machine: OnlyLooks) -> None:
    def operation() -> None:
        raise AttributeError("a method that does not exist")

    with pytest.raises(AttributeError):
        engine.rehearse(operation)


def test_asking_a_repository_is_said(machine: OnlyLooks) -> None:
    machine.script(["git", "ls-remote"], stdout="abc\trefs/heads/main\n")

    plan = engine.rehearse(
        lambda: get_runner().run(
            ["git", "ls-remote", "--exit-code", "--", "https://git.example.com/shop.git", "main"],
            timeout=20,
        )
    )

    assert plan.asked_remote is True


def test_a_probe_is_given_a_short_deadline(machine: OnlyLooks) -> None:
    seen: list[int] = []
    original = machine.run

    def run(argv: Any, **kwargs: Any) -> CommandResult:
        seen.append(kwargs["timeout"])
        return original(argv, **kwargs)

    machine.run = run  # type: ignore[method-assign]

    engine.rehearse(
        lambda: get_runner().run(
            ["docker", "ps", "--all", "--no-trunc", "--format", "x"], timeout=600
        )
    )

    assert seen == [PROBE_TIMEOUT_CAP]


def test_a_plan_that_has_used_up_its_time_for_looking_stops_looking_and_says_so(
    machine: OnlyLooks,
) -> None:
    clock = [100.0]
    original = machine.run

    def slow(argv: Any, **kwargs: Any) -> CommandResult:
        clock[0] += 1000.0
        return original(argv, **kwargs)

    machine.run = slow  # type: ignore[method-assign]

    plan = engine.rehearse(
        lambda: [
            get_runner().run(["systemctl", "is-active", name], timeout=5).exit_code
            for name in ("a", "b")
        ],
        now=lambda: clock[0],
    )

    assert plan.skipped_probes == 1
    assert machine.calls == [("systemctl", "is-active", "a")]
    assert plan.result is not None and plan.result[1] == -1
    assert [c.probe for c in plan.commands] == [True, True]


def test_too_many_commands_are_counted_and_not_all_sent(machine: OnlyLooks) -> None:
    from noust.core.plans.shape import MAX_COMMANDS

    def operation() -> None:
        for number in range(MAX_COMMANDS + 7):
            get_runner().run(["chown", "root", f"/srv/{number}"], timeout=5)

    plan = engine.rehearse(operation)

    assert len(plan.commands) == MAX_COMMANDS and plan.omitted == 7


def test_the_scope_is_gone_after_an_error_so_the_next_request_acts_for_real(
    machine: OnlyLooks,
) -> None:
    with pytest.raises(AttributeError):
        engine.rehearse(lambda: (_ for _ in ()).throw(AttributeError("x")))

    assert get_runner() is machine
    assert not is_rehearsal()


def test_nothing_is_left_in_the_directory_the_plan_wrote_to(tmp_path: Path) -> None:
    def operation() -> None:
        get_fs().make_dir(tmp_path / "a" / "b")
        get_fs().write_text(tmp_path / "a" / "b" / "f", "x")
        get_fs().symlink(tmp_path / "a", tmp_path / "link")

    engine.rehearse(operation)

    assert sorted(os.listdir(tmp_path)) == []


def test_a_health_check_is_kept_in_its_place_and_is_not_asked(machine: OnlyLooks) -> None:
    """A plan has no application to ask: the check is said, and the code goes on as if it passed."""
    from noust.deployers.helpers.health import wait_until_command_succeeds, wait_until_healthy

    def operation() -> tuple[bool, bool]:
        get_runner().run(["systemctl", "restart", "shop"], timeout=30)
        answered = wait_until_healthy("http://127.0.0.1:3000/", retries=15, delay=2.0, within=30.0)
        ran = wait_until_command_succeeds(
            lambda seconds: (False, "no"), retries=3, delay=1.0, describe="/srv/shop/bin/health"
        )
        return answered, ran

    plan = engine.rehearse(operation)

    assert plan.result == (True, True)
    assert [(c.text, c.probe, c.check, c.seconds) for c in plan.commands] == [
        ("systemctl restart shop", False, False, None),
        ("GET http://127.0.0.1:3000/", True, True, 30.0),
        ("/srv/shop/bin/health", True, True, 3.0),
    ]


def test_only_so_many_plans_are_made_at_once_and_the_next_one_is_told_to_wait(
    machine: OnlyLooks,
) -> None:
    """A plan looks at the machine for up to a minute: a crowd of them must not use up the threads."""
    import threading

    from noust.core.exceptions import QueueFullError

    inside = threading.Barrier(engine.MAX_CONCURRENT_PLANS + 1)
    release = threading.Event()

    def slow() -> None:
        inside.wait(timeout=10)
        release.wait(timeout=10)

    threads = [
        threading.Thread(target=lambda: engine.rehearse(slow))
        for _ in range(engine.MAX_CONCURRENT_PLANS)
    ]
    for thread in threads:
        thread.start()
    try:
        inside.wait(timeout=10)
        with pytest.raises(QueueFullError):
            engine.rehearse(lambda: None)
    finally:
        release.set()
        for thread in threads:
            thread.join()

    assert engine.rehearse(lambda: "again").result == "again"


def test_the_phases_an_operation_announces_are_the_steps_of_its_commands(
    machine: OnlyLooks,
) -> None:
    phases = engine.Phases()

    def operation() -> None:
        phases.phase(1, 2, "Backing up")
        get_runner().run(["tar", "cf", "/b.tar", "/srv"], timeout=60)
        phases.phase(2, 2, "Restarting")
        get_runner().run(["systemctl", "restart", "shop"], timeout=30)
        get_runner().run(["systemctl", "is-active", "shop"], timeout=30)
        phases.announce("Nothing happens in this one")

    plan = engine.rehearse(operation, phases=phases)

    assert [(step.key, step.title, step.commands) for step in plan.steps] == [
        ("step-1", "Backing up", (0,)),
        ("step-2", "Restarting", (1, 2)),
    ]
    assert [command.step for command in plan.commands] == ["step-1", "step-2", "step-2"]
