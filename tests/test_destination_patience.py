# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Two ways of talking to a backup destination, said by whoever calls (item 201).

*Asking* (testing a destination, the monitor's probe, listing it for a person at the console) takes
one attempt and short times, so that a destination that is down says so in seconds. *Working*
(sending a copy, fetching one, deleting by retention, and the listings and checks inside those) is
patient: three attempts, a wait that grows between them and times that put up with a bad network,
within a ceiling for the whole operation so that a run is never held all night by one send.

Nothing here waits for real: the clock and the pause are the test's own.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import BackupError
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import NoustStore
from noust.managers import destination_patience as patience
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import BackupManager, BackupMetadata
from tests.support.backup_night import FlakyRclone
from tests.support.patience_clock import PatienceClock

BACKUP_ID = "shop-example-com_20260101_000000"


@pytest.fixture(autouse=True)
def _fresh_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def remote(tmp_path: Path) -> FlakyRclone:
    runner = FlakyRclone(tmp_path / "remote")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    BackupDestinationManager(runner=runner).add(
        "nas", "sftp", {"host": "nas.example.net", "user": "noust", "pass": "s3cr3t-value"}
    )
    return runner


@pytest.fixture
def manager(remote: FlakyRclone) -> BackupDestinationManager:
    return BackupDestinationManager(runner=remote)


@pytest.fixture
def local(tmp_path: Path, remote: FlakyRclone) -> tuple[BackupManager, BackupMetadata]:
    backups = BackupManager(verbose=False, runner=remote)
    backups.backup_dir = tmp_path / "backups"
    folder = backups.backup_dir / "shop-example-com"
    folder.mkdir(parents=True)
    (folder / f"{BACKUP_ID}.tar.gz").write_bytes(b"content")
    metadata = BackupMetadata(
        id=BACKUP_ID,
        domain="shop.example.com",
        app_name="shop-example-com",
        created_at="2026-01-01T00:00:00",
        size_bytes=7,
        app_type="static",
        version="4.3.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
    )
    (folder / f"{BACKUP_ID}.json").write_text(json.dumps(metadata.to_dict()))
    return backups, metadata


class TestWhatIsPermanent:
    @pytest.mark.parametrize(
        "text",
        [
            "Failed to copyto: permission denied",
            "sftp: Access Denied (403)",
            "ssh: handshake failed: ssh: unable to authenticate, attempted methods [none password]",
            "Failed to copyto: AccessDenied: Access Denied",
            "NoSuchBucket: the specified bucket does not exist",
            "Failed to copyto: sftp: no space left on device",
            "Failed to copyto: disk full",
            "Failed to copyto: Quota exceeded",
            "401 Unauthorized",
            "ssh: knownhosts: key mismatch",
        ],
    )
    def test_what_waiting_does_not_mend(self, text: str) -> None:
        assert patience.is_permanent(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Failed to copyto: sftp: write: i/o timeout: connection lost",
            "Could not list the folder: Command timed out after 60s",
            "dial tcp 192.0.2.10:22: connect: connection refused",
            "no route to host",
            "read: connection reset by peer",
            "503 Service Unavailable",
            "",
        ],
    )
    def test_what_a_better_hour_may_mend(self, text: str) -> None:
        assert patience.is_permanent(text) is False


class TestTheNumbers:
    def test_asking_is_what_the_destination_test_has_always_been(self) -> None:
        assert patience.ASK.attempts == 1
        assert patience.env_for(patience.ASK) == {
            "RCLONE_RETRIES": "1",
            "RCLONE_CONTIMEOUT": "15s",
        }

    def test_working_puts_up_with_a_bad_network_but_Noust_keeps_the_count(self) -> None:
        env = patience.env_for(patience.WORK)

        # One rclone retry: the loop is Noust's, or two loops would multiply the attempts.
        assert env["RCLONE_RETRIES"] == "1"
        assert patience.WORK.attempts == 3
        assert patience.WORK.waits == (30.0, 120.0)
        assert env["RCLONE_CONTIMEOUT"] == "60s"
        assert env["RCLONE_TIMEOUT"] == "2m"
        assert patience.WORK.cap_seconds == 2 * 3600

    def test_nothing_says_which_it_is_until_an_operation_does(self) -> None:
        assert patience.current() is patience.ASK
        with patience.using(patience.WORK):
            assert patience.current() is patience.WORK
        assert patience.current() is patience.ASK


class TestTheLoop:
    def run(
        self,
        runner: FakeRunner,
        clock: PatienceClock,
        timeout: int = 60,
        mode: patience.Patience = patience.WORK,
    ) -> CommandResult:
        source = Path(getattr(runner, "root", Path("."))).parent / "a"
        source.write_bytes(b"content")
        with patience.using(mode):
            return patience.run(
                runner,
                ["rclone", "copyto", str(source), "nas:b"],
                env={},
                timeout=timeout,
                secrets=[],
            )

    def test_it_fails_twice_and_goes_the_third_time(
        self, remote: FlakyRclone, patience_clock: PatienceClock
    ) -> None:
        answers = iter(["i/o timeout", "connection lost", None])
        remote.failing = lambda verb, args: next(answers)

        result = self.run(remote, patience_clock)

        assert result.success
        assert len(remote.uploads()) == 3
        assert patience_clock.slept == [30.0, 120.0]

    def test_every_attempt_is_said(
        self,
        remote: FlakyRclone,
        patience_clock: PatienceClock,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        answers = iter(["i/o timeout", "connection lost", None])
        remote.failing = lambda verb, args: next(answers)

        self.run(remote, patience_clock)

        said = capsys.readouterr().out + caplog.text
        assert "attempt 1 of 3 failed" in said
        assert "attempt 2 of 3 failed" in said
        assert "i/o timeout" in said
        assert "30s" in said and "2m" in said

    def test_the_third_failure_ends_it_and_says_how_many_tries_there_were(
        self, remote: FlakyRclone, patience_clock: PatienceClock
    ) -> None:
        remote.down = True

        result = self.run(remote, patience_clock)

        assert not result.success
        assert len(remote.uploads()) == 3
        assert "i/o timeout" in result.stderr
        assert "tried 3 times" in result.stderr

    def test_what_waiting_does_not_mend_is_tried_once(
        self, remote: FlakyRclone, patience_clock: PatienceClock
    ) -> None:
        remote.failing = lambda verb, args: "Failed to copyto: permission denied"

        result = self.run(remote, patience_clock)

        assert not result.success
        assert len(remote.uploads()) == 1
        assert patience_clock.slept == []
        assert "tried" not in result.stderr

    def test_a_thing_that_is_not_there_is_an_answer_and_not_a_failure(
        self, patience_clock: PatienceClock
    ) -> None:
        runner = FakeRunner()
        runner.script(("rclone", "lsjson"), exit_code=3, stderr="directory not found")

        with patience.using(patience.WORK):
            result = patience.run(
                runner, ["rclone", "lsjson", "nas:x"], env={}, timeout=60, secrets=[]
            )

        assert result.exit_code == 3
        assert len(runner.calls) == 1

    def test_asking_is_one_attempt_whatever_goes_wrong(
        self, remote: FlakyRclone, patience_clock: PatienceClock
    ) -> None:
        remote.down = True

        result = self.run(remote, patience_clock, mode=patience.ASK)

        assert not result.success
        assert len(remote.uploads()) == 1
        assert patience_clock.slept == []

    def test_an_operation_is_never_held_all_night(self, patience_clock: PatienceClock) -> None:
        """When every attempt uses its whole deadline, the ceiling cuts the third."""
        runner = FakeRunner()

        def burn(argv, **kwargs):  # type: ignore[no-untyped-def]
            patience_clock.advance(kwargs["timeout"])
            return CommandResult(
                argv=tuple(argv), exit_code=1, stderr="i/o timeout", timed_out=True
            )

        runner.run = burn  # type: ignore[method-assign]

        with patience.using(patience.WORK):
            result = patience.run(
                runner, ["rclone", "copyto", "a", "nas:b"], env={}, timeout=3600, secrets=[]
            )

        assert not result.success
        assert patience_clock.elapsed() <= patience.WORK.cap_seconds + 120
        # Each of the attempts was given what was left of the ceiling, never more than its own deadline.
        assert patience_clock.timeouts[0] == 3600
        assert all(t <= 3600 for t in patience_clock.timeouts)
        assert len(patience_clock.timeouts) == 2

    def test_a_retried_call_sends_its_input_again(self, patience_clock: PatienceClock) -> None:
        runner = FakeRunner()
        seen: list[str | None] = []

        def sink(argv, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(kwargs.get("input"))
            ok = len(seen) == 2
            return CommandResult(argv=tuple(argv), exit_code=0 if ok else 1, stderr="i/o timeout")

        runner.run = sink  # type: ignore[method-assign]

        with patience.using(patience.WORK):
            result = patience.run(
                runner, ["rclone", "rcat", "nas:x"], env={}, timeout=30, secrets=[], input="{}"
            )

        assert result.success
        assert seen == ["{}", "{}"]


class TestTheOperations:
    def test_sending_a_copy_that_times_out_twice_is_a_copy_that_arrived(
        self,
        manager: BackupDestinationManager,
        remote: FlakyRclone,
        local: tuple[BackupManager, BackupMetadata],
        patience_clock: PatienceClock,
    ) -> None:
        backups, metadata = local
        answers = iter(["sftp: write: i/o timeout", "connection lost"])
        remote.failing = lambda verb, args: next(answers, None) if verb == "copyto" else None

        summary = manager.push(metadata, "nas", backup_manager=backups)

        assert summary["uploaded"] == [f"{BACKUP_ID}.tar.gz", f"{BACKUP_ID}.json"]
        assert len(remote.uploads()) == 4  # the archive three times and the sidecar once
        assert patience_clock.slept == [30.0, 120.0]

    def test_a_send_that_never_gets_through_ends_with_the_whole_story(
        self,
        manager: BackupDestinationManager,
        remote: FlakyRclone,
        local: tuple[BackupManager, BackupMetadata],
        patience_clock: PatienceClock,
    ) -> None:
        backups, metadata = local
        remote.down = True

        with pytest.raises(BackupError) as failed:
            manager.push(metadata, "nas", backup_manager=backups)

        assert "i/o timeout" in str(failed.value)
        assert "tried 3 times" in str(failed.value)

    def test_what_a_send_takes_on_the_wire_is_given_the_patient_times(
        self,
        manager: BackupDestinationManager,
        remote: FlakyRclone,
        local: tuple[BackupManager, BackupMetadata],
    ) -> None:
        backups, metadata = local

        manager.push(metadata, "nas", backup_manager=backups)

        envs = [
            env
            for call, env in zip(remote.calls, remote.envs, strict=True)
            if call[:2] == ("rclone", "copyto")
        ]
        assert envs and all(env and env["RCLONE_CONTIMEOUT"] == "60s" for env in envs)

    def test_testing_a_destination_is_still_one_quick_attempt(
        self, manager: BackupDestinationManager, remote: FlakyRclone, patience_clock: PatienceClock
    ) -> None:
        remote.down = True

        with pytest.raises(BackupError):
            manager.test("nas")

        mkdirs = [c for c in remote.calls if c[:2] == ("rclone", "mkdir")]
        assert len(mkdirs) <= 2  # the folder that settles, then the test's own: one attempt each
        assert patience_clock.slept == []
        envs = [env for env in remote.envs if env is not None]
        assert envs and all(env["RCLONE_CONTIMEOUT"] == "15s" for env in envs)

    def test_listing_a_destination_for_a_person_is_one_quick_attempt(
        self, manager: BackupDestinationManager, remote: FlakyRclone, patience_clock: PatienceClock
    ) -> None:
        remote.down = True

        with pytest.raises(BackupError):
            manager.remote_list("nas", "shop-example-com")

        assert len([c for c in remote.calls if c[:2] == ("rclone", "lsjson")]) == 1
        assert patience_clock.slept == []

    def test_a_restore_that_is_fetching_a_copy_is_patient(
        self,
        manager: BackupDestinationManager,
        remote: FlakyRclone,
        local: tuple[BackupManager, BackupMetadata],
        tmp_path: Path,
        patience_clock: PatienceClock,
    ) -> None:
        backups, metadata = local
        manager.push(metadata, "nas", backup_manager=backups)
        answers = iter(["i/o timeout"])
        remote.failing = lambda verb, args: next(answers, None) if verb == "copyto" else None

        archive, sidecar = manager.download("nas", BACKUP_ID, "shop-example-com", tmp_path / "in")

        assert archive.is_file() and sidecar.is_file()
        assert patience_clock.slept == [30.0]
