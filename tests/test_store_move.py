# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A store that never left the user's directory, moved to the state directory (item 194).

The store of a server was in ``~/.local/share/noust`` and ``/var/lib/noust`` held an empty
``noust.db``. Moving a store is the whole folder, and its protected secrets are sealed under a key
that lives beside it in the user's directory and in ``/etc/noust`` for the system's, so copying
the file loses the destination's password and mints new signing keys. What is pinned:

- the doctor names the store that is outside the state directory, and the empty file;
- every check that can refuse runs before a service is stopped or a file written;
- the move copies the folder, snapshots every SQLite file, seals each protected secret again
  under the key of the place it goes to (and does not carry the old key there), switches the store
  in one rename, retires the old file, and says where the copy of both folders is;
- anything that fails undoes what was written, starts the services again and leaves the old store
  where it was;
- ``--dry-run`` changes nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core import paths
from noust.core import store as store_module
from noust.core.exceptions import NoustError, SetupError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.logger import Logger
from noust.core.runner import FakeRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore, get_store
from noust.managers import setup_doctor
from noust.managers.store_location import StoreMover, assess

# Seconds of one second apart would make two safety copies of one run collide: the clock is the
# one thing every test of a move has to read, and it is read at most twice.


class Machine:
    """A server whose store is in the user's directory, as it was found."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.config = base / "etc-noust"
        self.system = base / "var-lib-noust"
        self.home = base / "root" / ".local" / "share" / "noust"
        self.backups = base / "backups"
        for directory in (self.config, self.system, self.home):
            directory.mkdir(parents=True)

    def listing(self, root: Path) -> list[str]:
        return sorted(
            f"{path.relative_to(root)}:{path.stat().st_size if path.is_file() else 'dir'}"
            for path in root.rglob("*")
        )


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Machine]:
    made = Machine(tmp_path)
    monkeypatch.setattr(paths, "CONFIG_DIR", made.config)
    monkeypatch.setattr(paths, "STATE_DIR", made.system)
    monkeypatch.setattr(paths, "BACKUP_DIR", made.backups)
    monkeypatch.setattr(store_module, "DEFAULT_DB_PATH", made.system / "noust.db")
    monkeypatch.setattr(store_module, "USER_DB_PATH", made.home / "noust.db")
    NoustStore.reset_instance()
    store = NoustStore(made.home / "noust.db")
    store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))
    store.create_app(App(domain="blog.example.com", app_type="static", app_path="/srv/blog"))
    store.close()
    NoustStore.reset_instance()
    # What the user's directory holds besides the store.
    secrets = SecretStore(root=made.home / "secrets")
    secrets.write_protected_json("backups/destinations/nas", {"password": "nas-password"})
    secrets.write_protected_json("github/leases/shop", {"token": "lease-token"})
    secrets.write("fleet/token", "plain-fleet-token")
    (made.home / "deploy-logs").mkdir()
    (made.home / "deploy-logs" / "shop.log").write_text("deployed shop")
    (made.home / "locks").mkdir()
    (made.home / "totp.key").write_bytes(b"k" * 32)
    threats = sqlite3.connect(made.home / "threats.db")
    threats.execute("CREATE TABLE seen (ip TEXT)")
    threats.execute("INSERT INTO seen VALUES ('203.0.113.7')")
    threats.commit()
    threats.close()
    # What was left in the system's directory: an empty store file and state that is not the store's.
    (made.system / "noust.db").write_bytes(b"")
    (made.system / "uploads").mkdir()
    (made.system / "uploads" / "abc.txt").write_text("an upload")
    yield made
    NoustStore.reset_instance()
    set_fs(None)
    set_runner(None)


@pytest.fixture
def runner() -> Iterator[FakeRunner]:
    fake = FakeRunner()
    fake.script(("systemctl", "is-active", "--quiet", "noust-web.service"), exit_code=0)
    fake.script(("systemctl", "is-active", "--quiet", "noust-monitor.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-previews.service"), exit_code=3)
    set_runner(fake)
    yield fake
    set_runner(None)


def mover(runner: FakeRunner) -> StoreMover:
    return StoreMover(runner=runner, logger=Logger(verbose=False))


class TestWhereTheStoreIs:
    def test_it_is_in_the_users_directory_and_the_state_directory_holds_an_empty_file(
        self, machine: Machine
    ) -> None:
        found = assess()

        assert found.store == machine.home / "noust.db"
        assert found.state == machine.system
        assert found.outside is True
        assert found.orphan == machine.system / "noust.db"

    def test_a_store_in_the_state_directory_is_where_it_should_be(self, machine: Machine) -> None:
        found = assess(machine.system / "noust.db", machine.system)

        assert found.outside is False and found.orphan is None

    def test_an_empty_file_is_not_named_when_the_store_is_beside_it(self, machine: Machine) -> None:
        (machine.system / "noust.db").write_bytes(b"not empty")

        assert assess(machine.system / "noust.db", machine.system).orphan is None

    def test_the_state_directory_without_any_file_has_no_orphan(self, machine: Machine) -> None:
        (machine.system / "noust.db").unlink()

        found = assess()

        assert found.outside is True and found.orphan is None


class TestTheDoctor:
    def test_it_names_the_store_outside_the_state_directory_and_the_command_that_moves_it(
        self, machine: Machine
    ) -> None:
        section = setup_doctor.store_section(assess())

        assert section is not None and section.key == "store"
        outside, orphan = section.items
        assert outside.status == "warn"
        assert str(machine.home / "noust.db") in outside.text
        assert str(machine.system) in outside.text
        assert outside.fix == "noust setup move-store"
        assert outside.coded is not None and outside.coded.code == "doctor.store.outside"
        assert str(machine.system / "noust.db") in orphan.text
        assert orphan.coded is not None and orphan.coded.code == "doctor.store.orphan"

    def test_it_says_nothing_when_the_store_is_in_the_state_directory(
        self, machine: Machine
    ) -> None:
        assert (
            setup_doctor.store_section(assess(machine.system / "noust.db", machine.system)) is None
        )

    def test_the_command_it_suggests_is_one_that_exists(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        section = setup_doctor.store_section(assess())
        assert section is not None
        words = section.items[0].fix.split()[1:]  # type: ignore[union-attr]

        shown = CliRunner().invoke(cli, [*words, "--help"])

        assert shown.exit_code == 0, shown.output
        assert "--yes" in shown.output


class TestWhatRefusesBeforeAnythingChanges:
    def test_a_store_already_in_the_state_directory_has_nothing_to_move(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        location = assess(machine.system / "noust.db", machine.system)

        with pytest.raises(SetupError) as refused:
            StoreMover(runner=runner, location=location).plan()

        assert "already in" in str(refused.value)

    def test_a_state_directory_that_holds_a_store_of_its_own_is_never_overwritten(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        # Noust would pick that store if it could write there; when it cannot, it picks the other.
        elsewhere = sqlite3.connect(machine.system / "noust.db")
        elsewhere.execute("CREATE TABLE apps (x)")
        elsewhere.commit()
        elsewhere.close()
        location = assess(machine.home / "noust.db", machine.system)

        with pytest.raises(SetupError) as refused:
            StoreMover(runner=runner, location=location).plan()

        assert "already holds a store" in str(refused.value)

    def test_a_file_that_would_be_overwritten_stops_it(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        (machine.system / "deploy-logs").mkdir()
        (machine.system / "deploy-logs" / "shop.log").write_text("another server's")

        with pytest.raises(SetupError) as refused:
            mover(runner).plan()

        assert "deploy-logs/shop.log" in str(refused.value)
        assert (machine.system / "deploy-logs" / "shop.log").read_text() == "another server's"

    def test_a_protected_secret_that_does_not_open_stops_it(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        (machine.home / "secrets.key").unlink()

        with pytest.raises(SetupError) as refused:
            mover(runner).plan()

        assert "backups/destinations/nas" in str(refused.value) or "github" in str(refused.value)

    def test_sealed_secrets_are_not_moved(self, machine: Machine, runner: FakeRunner) -> None:
        (machine.home / "secrets" / ".seal").write_text("{}")

        with pytest.raises(SetupError) as refused:
            mover(runner).plan()

        assert "sealed" in str(refused.value)

    def test_nothing_was_stopped_nor_written_by_a_refusal(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        (machine.home / "secrets" / ".seal").write_text("{}")
        before = machine.listing(machine.system)

        with pytest.raises(SetupError):
            mover(runner).move()

        assert machine.listing(machine.system) == before
        assert not [call for call in runner.calls if call[:2] == ("systemctl", "stop")]
        assert not machine.backups.exists()

    def test_a_state_directory_this_account_cannot_write_says_to_run_as_root(
        self, machine: Machine, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.access", lambda path, mode: False)

        with pytest.raises(SetupError) as refused:
            mover(runner).plan()

        assert "as root" in str(refused.value)

    def test_a_disk_without_room_is_refused(
        self, machine: Machine, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from collections import namedtuple

        usage = namedtuple("usage", "total used free")
        monkeypatch.setattr("shutil.disk_usage", lambda path: usage(10, 10, 0))

        with pytest.raises(SetupError) as refused:
            mover(runner).plan()

        assert "room" in str(refused.value)

    def test_the_plan_says_what_would_be_moved(self, machine: Machine, runner: FakeRunner) -> None:
        plan = mover(runner).plan()

        assert plan.source == machine.home / "noust.db"
        assert plan.destination == machine.system / "noust.db"
        assert plan.secrets == 2
        assert plan.orphan == machine.system / "noust.db"
        assert plan.files >= 6 and plan.size > 0


class TestTheMove:
    def test_the_store_is_where_noust_looks_for_it_and_holds_what_it_held(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        result = mover(runner).move()

        assert result.moved and not result.rehearsed
        found = get_store()
        assert found.db_path == machine.system / "noust.db"
        assert sorted(app.domain for app in found.list_apps()) == [
            "blog.example.com",
            "shop.example.com",
        ]
        assert assess().outside is False

    def test_the_folder_goes_with_it_and_every_sqlite_file_is_a_whole_database(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        mover(runner).move()

        assert (machine.system / "deploy-logs" / "shop.log").read_text() == "deployed shop"
        assert (machine.system / "totp.key").read_bytes() == b"k" * 32
        assert (machine.system / "uploads" / "abc.txt").read_text() == "an upload"
        threats = sqlite3.connect(machine.system / "threats.db")
        try:
            assert threats.execute("SELECT ip FROM seen").fetchall() == [("203.0.113.7",)]
        finally:
            threats.close()

    def test_the_protected_secrets_open_under_the_key_of_the_state_directory_and_give_the_same(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        mover(runner).move()

        here = SecretStore(root=machine.system / "secrets")
        assert here.read_protected_json("backups/destinations/nas") == {"password": "nas-password"}
        assert here.read_protected_json("github/leases/shop") == {"token": "lease-token"}
        assert here.read("fleet/token") == "plain-fleet-token"
        assert (machine.config / "secrets.key").is_file()

    def test_the_old_key_is_not_carried_into_the_state_directory(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        old_key = (machine.home / "secrets.key").read_bytes()

        mover(runner).move()

        assert not (machine.system / "secrets.key").exists()
        assert (machine.config / "secrets.key").read_bytes() != old_key

    def test_a_key_the_server_already_had_is_the_one_used(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        SecretStore(root=machine.system / "secrets").write_protected_json("probe", {"x": "y"})
        key = (machine.config / "secrets.key").read_bytes()
        (machine.system / "secrets" / "probe").unlink()
        (machine.system / "secrets").rmdir()

        mover(runner).move()

        assert (machine.config / "secrets.key").read_bytes() == key
        assert SecretStore(root=machine.system / "secrets").read_protected_json(
            "backups/destinations/nas"
        ) == {"password": "nas-password"}

    def test_the_modes_are_those_of_a_secret(self, machine: Machine, runner: FakeRunner) -> None:
        mover(runner).move()

        for path in (
            machine.system / "noust.db",
            machine.system / "secrets" / "backups" / "destinations" / "nas",
            machine.system / "threats.db",
        ):
            assert path.stat().st_mode & 0o777 == 0o600, path

    def test_the_old_store_is_renamed_not_deleted_and_nothing_opens_it_again(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        result = mover(runner).move()

        assert not (machine.home / "noust.db").exists()
        assert result.retired is not None and result.retired.is_file()
        assert result.retired.name.startswith("noust.db.moved-")
        assert (machine.home / "secrets.key").is_file()

    def test_a_copy_of_both_folders_and_both_keys_is_kept_first(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        old_key = (machine.home / "secrets.key").read_bytes()
        result = mover(runner).move()

        kept = result.safety_copy
        assert kept is not None and kept.parent == machine.backups / "store-move"
        assert kept.stat().st_mode & 0o777 == 0o700
        assert (kept / "source" / "noust.db").is_file()
        assert (kept / "source" / "secrets.key").read_bytes() == old_key
        assert (kept / "replaced-noust.db").stat().st_size == 0

    def test_what_writes_to_the_store_is_stopped_for_the_move_and_started_again(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        mover(runner).move()

        verbs = [call[1:3] for call in runner.calls if call[0] == "systemctl"]
        assert ("stop", "noust-web.service") in verbs
        assert verbs.index(("stop", "noust-web.service")) < verbs.index(
            ("start", "noust-web.service")
        )
        assert ("stop", "noust-monitor.service") not in verbs

    def test_it_can_be_run_again_and_has_nothing_to_do(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        mover(runner).move()
        NoustStore.reset_instance()

        with pytest.raises(SetupError) as again:
            StoreMover(runner=runner).move()

        assert "already in" in str(again.value)

    def test_a_dry_run_writes_nothing(self, machine: Machine, runner: FakeRunner) -> None:
        # Opening the store is what makes SQLite's own -wal and -shm: not the move's doing.
        get_store()
        before = (machine.listing(machine.system), machine.listing(machine.home))
        set_fs(DryRunFileSystem())

        result = mover(runner).move()

        assert result.rehearsed and not result.moved
        assert (machine.listing(machine.system), machine.listing(machine.home)) == before
        assert not [call for call in runner.calls if call[:2] == ("systemctl", "stop")]


class TestAMoveThatFails:
    def break_the_verification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def broken(self: StoreMover, plan: object) -> None:
            raise SetupError("The copy of the store does not hold what the store holds")

        monkeypatch.setattr(StoreMover, "_verify", broken)

    def test_what_was_written_is_undone_and_the_old_store_is_where_it_was(
        self, machine: Machine, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = machine.listing(machine.system)
        old = (machine.home / "noust.db").read_bytes()
        self.break_the_verification(monkeypatch)

        with pytest.raises(SetupError) as failed:
            mover(runner).move()

        assert machine.listing(machine.system) == before
        assert (machine.system / "noust.db").stat().st_size == 0
        assert (machine.home / "noust.db").read_bytes() == old
        assert not (machine.config / "secrets.key").exists()
        assert "undone" in str(failed.value) and "store-move" in str(failed.value)

    def test_the_services_that_were_stopped_are_started_again(
        self, machine: Machine, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.break_the_verification(monkeypatch)

        with pytest.raises(SetupError):
            mover(runner).move()

        verbs = [call[1:3] for call in runner.calls if call[0] == "systemctl"]
        assert ("start", "noust-web.service") in verbs

    def test_the_store_that_resolves_is_still_the_old_one(
        self, machine: Machine, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.break_the_verification(monkeypatch)

        with pytest.raises(SetupError):
            mover(runner).move()

        NoustStore.reset_instance()
        assert get_store().db_path == machine.home / "noust.db"
        assert len(get_store().list_apps()) == 2

    def test_a_secret_that_stops_opening_under_the_new_key_fails_the_move(
        self, machine: Machine, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = SecretStore.read_protected_json
        calls = {"n": 0}

        def different(self: SecretStore, name: str) -> dict[str, object]:
            value = original(self, name)
            if self.root.parent == machine.system:
                calls["n"] += 1
                return {**value, "tampered": True}
            return value

        monkeypatch.setattr(SecretStore, "read_protected_json", different)

        with pytest.raises(SetupError) as failed:
            mover(runner).move()

        assert "does not open to what it held" in str(failed.value)
        assert (machine.system / "noust.db").stat().st_size == 0


class TestTheCommand:
    def test_a_dry_run_prints_what_it_would_do_and_changes_nothing(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        before = machine.listing(machine.system)

        out = CliRunner().invoke(cli, ["--dry-run", "setup", "move-store"])

        assert out.exit_code == 0, out.output
        assert str(machine.home / "noust.db") in out.output
        assert "would" in out.output.lower()
        assert machine.listing(machine.system) == before

    def test_it_asks_before_it_stops_anything_and_needs_yes_where_nobody_can_answer(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        out = CliRunner().invoke(cli, ["setup", "move-store"])

        # The error boundary (cli.app.main) says it; here it is the exception it would say.
        assert out.exit_code != 0 and isinstance(out.exception, NoustError)
        assert "--yes" in str(out.exception)
        assert (machine.home / "noust.db").exists()
        assert not [call for call in runner.calls if call[:2] == ("systemctl", "stop")]

    def test_with_yes_it_moves_and_says_where_the_copy_is(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        out = CliRunner().invoke(cli, ["setup", "move-store", "--yes"])

        assert out.exit_code == 0, out.output
        assert not (machine.home / "noust.db").exists()
        assert (machine.system / "noust.db").stat().st_size > 0
        assert "store-move" in out.output
        assert "noust setup doctor" in out.output

    def test_a_refusal_is_said_with_how_to_go_on_and_exits_non_zero(
        self, machine: Machine, runner: FakeRunner
    ) -> None:
        (machine.home / "secrets" / ".seal").write_text("{}")

        out = CliRunner().invoke(cli, ["setup", "move-store", "--yes"])

        assert out.exit_code != 0 and isinstance(out.exception, NoustError)
        assert "sealed" in str(out.exception)
