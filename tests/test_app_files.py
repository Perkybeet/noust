# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files of an application, from the console (checklist B-55).

A runner that does what the handful of commands do, over a real temporary tree,
lets these tests read what the manager did to the files and not only what it
asked. Pinned here:

- every command runs as the application's own account, with a clean environment;
- a path is relative to the application and never climbs; a link in the tree is
  shown, can be removed or renamed, and is never walked through, read or written;
- environment files are the environment's: the browser neither reads nor writes them;
- an edit is written beside the file, takes its mode and is renamed over it;
- sizes are bounded, and every change is on the audit trail without its content.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.exceptions import SecurityError, ServiceError, ValidationError
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import App, NoustStore
from noust.managers import app_files
from noust.managers.app_files import AppFiles, clean_path, files_for

DOMAIN = "shop.example.com"
ACCOUNT = "noust-app-shop"


class TreeRunner(FakeRunner):
    """
    The commands the file manager runs, done over a real directory.

    Every call is recorded with the account it was asked to run as and whether it
    asked for a clean environment.
    """

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[tuple[str, ...], str | None, bool]] = []
        self.fail: dict[str, str] = {}

    def run(  # type: ignore[override]
        self, argv: Sequence[str], **kwargs: Any
    ) -> CommandResult:
        args = tuple(str(part) for part in argv)
        self.asked.append((args, kwargs.get("user"), bool(kwargs.get("clean_env"))))
        self.calls.append(args)
        program = args[0]
        if program in self.fail:
            return CommandResult(argv=args, exit_code=1, stderr=self.fail[program])
        handler = getattr(self, f"_{program}", None)
        if handler is None:
            return CommandResult(argv=args, exit_code=127, stderr=f"no {program}")
        try:
            return handler(args, kwargs)
        except OSError as exc:
            return CommandResult(argv=args, exit_code=1, stderr=str(exc))

    def capture_to_file(  # type: ignore[override]
        self, argv: Sequence[str], destination: Path, **kwargs: Any
    ) -> CommandResult:
        args = tuple(str(part) for part in argv)
        self.asked.append((args, kwargs.get("user"), False))
        source = Path(args[-1])
        destination.write_bytes(source.read_bytes())
        return CommandResult(argv=args, exit_code=0)

    # -- the commands -------------------------------------------------------------

    def _find(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        directory = Path(args[2])
        out = []
        for child in sorted(directory.iterdir()):
            info = child.lstat()
            kind = "l" if child.is_symlink() else "d" if child.is_dir() else "f"
            target = os.readlink(child) if child.is_symlink() else ""
            out.append(
                "\0".join(
                    [
                        kind,
                        str(info.st_size),
                        f"{info.st_mtime:.6f}",
                        oct(stat.S_IMODE(info.st_mode))[2:],
                        "app",
                        target,
                        child.name,
                    ]
                )
                + "\0"
            )
        return CommandResult(argv=args, exit_code=0, stdout="".join(out))

    def _stat(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        return CommandResult(argv=args, exit_code=0, stdout=f"{Path(args[-1]).stat().st_size}\n")

    def _head(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        data = Path(args[-1]).read_bytes()[: int(args[2])]
        return CommandResult(argv=args, exit_code=0, stdout=data.decode("utf-8"))

    def _dd(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        target = Path(args[1].removeprefix("of="))
        target.write_bytes(Path(kwargs["stdin_path"]).read_bytes())
        return CommandResult(argv=args, exit_code=0)

    def _chmod(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        reference = Path(args[1].removeprefix("--reference="))
        os.chmod(args[-1], stat.S_IMODE(reference.stat().st_mode))
        return CommandResult(argv=args, exit_code=0)

    def _mv(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        source, destination = Path(args[-2]), Path(args[-1])
        if destination.is_dir() and not destination.is_symlink():
            return CommandResult(argv=args, exit_code=1, stderr="cannot overwrite directory")
        os.replace(source, destination)
        return CommandResult(argv=args, exit_code=0)

    def _mkdir(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        Path(args[-1]).mkdir()
        return CommandResult(argv=args, exit_code=0)

    def _rmdir(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        Path(args[-1]).rmdir()
        return CommandResult(argv=args, exit_code=0)

    def _rm(self, args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        target = Path(args[-1])
        if "-rf" in args:
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        return CommandResult(argv=args, exit_code=0)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An application on the release layout, with a release, shared data and a .env."""
    base = tmp_path / "apps" / "shop-example-com"
    release = base / "releases" / "20260925-120000-aaaaaaa"
    release.mkdir(parents=True)
    (release / "index.js").write_text("console.log('hi')\n")
    (base / "current").symlink_to("releases/20260925-120000-aaaaaaa")
    (base / "shared" / "uploads").mkdir(parents=True)
    (base / "shared" / "uploads" / "logo.png").write_bytes(b"\x89PNG\r\n\x00binary")
    (base / "shared" / "notes.txt").write_text("hello\n")
    os.chmod(base / "shared" / "notes.txt", 0o640)
    (base / "shared" / ".env").write_text("SECRET=1\n")
    return base


@pytest.fixture
def app(store: NoustStore, root: Path) -> App:
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            app_path=str(root),
            layout="releases",
            identity=ACCOUNT,
        )
    )
    row = store.get_app(DOMAIN)
    assert row is not None
    return row


@pytest.fixture
def runner() -> TreeRunner:
    return TreeRunner()


@pytest.fixture
def files(app: App, runner: TreeRunner, tmp_path: Path) -> AppFiles:
    return AppFiles(app, runner=runner, actor="ana", staging=tmp_path / "staging")


def events() -> list[dict[str, Any]]:
    return [event["details"] for event in audit.get_log().read(action="apps.files")]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("", ""),
        ("/", ""),
        ("shared/uploads", "shared/uploads"),
        ("/shared//uploads/", "shared/uploads"),
        ("./shared/./notes.txt", "shared/notes.txt"),
    ],
)
def test_a_path_is_read_relative_to_the_application(raw: str, kept: str) -> None:
    assert str(clean_path(raw)) == (kept or ".")


@pytest.mark.parametrize(
    "raw", ["../etc/passwd", "shared/../../x", "shared/..", "a\0b", "a\nb", "x" * 2000]
)
def test_a_path_that_climbs_or_holds_a_control_character_is_refused(raw: str) -> None:
    with pytest.raises(ValidationError) as caught:
        clean_path(raw)
    assert caught.value.field == "path"


def test_a_file_is_needed_where_a_file_is() -> None:
    with pytest.raises(ValidationError):
        clean_path("", allow_empty=False)


def test_the_environment_files_are_recognised_wherever_they_are() -> None:
    assert app_files.is_env_file(clean_path("shared/.env"))
    assert app_files.is_env_file(clean_path(".env.production"))
    assert not app_files.is_env_file(clean_path("shared/environment.txt"))
    assert not app_files.is_env_file(clean_path("shared/.environment"))


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_the_application_directory_lists_directories_first_and_a_link_as_a_link(
    files: AppFiles,
) -> None:
    listing = files.list_directory("")

    assert [(entry.name, entry.kind) for entry in listing.entries] == [
        ("releases", "dir"),
        ("shared", "dir"),
        ("current", "link"),
    ]
    link = listing.entries[2]
    assert link.target == "releases/20260925-120000-aaaaaaa"


def test_a_listing_says_the_mode_the_owner_the_size_and_the_environment_files(
    files: AppFiles,
) -> None:
    listing = files.list_directory("shared")

    by_name = {entry.name: entry for entry in listing.entries}
    assert (by_name["notes.txt"].mode, by_name["notes.txt"].size) == ("640", 6)
    assert by_name["notes.txt"].owner == "app"
    assert by_name[".env"].sensitive and not by_name["notes.txt"].sensitive
    assert by_name["notes.txt"].modified.endswith("+00:00")


def test_a_link_on_the_way_is_not_walked_through(files: AppFiles) -> None:
    with pytest.raises(SecurityError, match="current is a link"):
        files.list_directory("current")


def test_the_release_it_points_at_is_listed_by_its_own_path(files: AppFiles) -> None:
    listing = files.list_directory("releases/20260925-120000-aaaaaaa")

    assert [entry.name for entry in listing.entries] == ["index.js"]


def test_a_file_is_not_a_directory(files: AppFiles) -> None:
    with pytest.raises(ValidationError, match="not a directory"):
        files.list_directory("shared/notes.txt")


def test_a_listing_is_cut_at_the_limit_and_says_so(
    files: AppFiles, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number in range(5):
        (root / "shared" / f"f{number}.txt").write_text("x")
    monkeypatch.setattr(app_files, "LIST_MAX", 3)

    listing = files.list_directory("shared")

    assert len(listing.entries) == 3 and listing.truncated


def test_a_name_that_is_not_utf8_is_an_error_with_a_way_out_not_a_crash(
    files: AppFiles, runner: TreeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(args: tuple[str, ...], kwargs: dict[str, Any]) -> CommandResult:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(runner, "_find", broken, raising=False)

    with pytest.raises(ServiceError, match="not valid UTF-8"):
        files.list_directory("shared")


def test_everything_runs_as_the_applications_account_with_a_clean_environment(
    files: AppFiles, runner: TreeRunner
) -> None:
    files.list_directory("shared")
    files.read_text("shared/notes.txt")

    assert runner.asked
    assert {user for _, user, _ in runner.asked} == {ACCOUNT}
    assert all(clean for _, _, clean in runner.asked)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_a_text_file_opens_and_the_read_is_on_record_without_its_content(files: AppFiles) -> None:
    opened = files.read_text("shared/notes.txt")

    assert (opened.path, opened.size, opened.content) == ("shared/notes.txt", 6, "hello\n")
    [event] = events()
    assert event["action"] == "read" and event["path"] == "shared/notes.txt"
    assert event["by"] == "ana" and "hello" not in str(event)


def test_a_binary_file_is_not_edited(
    files: AppFiles, runner: TreeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner,
        "_head",
        lambda args, kwargs: CommandResult(argv=args, exit_code=0, stdout="PNG\0data"),
        raising=False,
    )

    with pytest.raises(ValidationError, match="not a text file"):
        files.read_text("shared/uploads/logo.png")


def test_a_file_that_is_not_valid_utf8_is_not_edited_either(files: AppFiles) -> None:
    with pytest.raises(ServiceError, match="not valid UTF-8"):
        files.read_text("shared/uploads/logo.png")


def test_a_file_too_large_to_edit_says_to_download_it(
    files: AppFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_files, "EDIT_MAX_BYTES", 3)

    with pytest.raises(ValidationError, match="Download it"):
        files.read_text("shared/notes.txt")


def test_a_link_cannot_be_read_through(files: AppFiles, root: Path) -> None:
    (root / "shared" / "outside").symlink_to("/etc/hostname")

    with pytest.raises(SecurityError, match="is a link"):
        files.read_text("shared/outside")


def test_a_directory_is_not_a_file_to_open(files: AppFiles) -> None:
    with pytest.raises(ValidationError, match="not a file"):
        files.read_text("shared/uploads")


def test_a_download_is_the_file_byte_for_byte(files: AppFiles, tmp_path: Path) -> None:
    copy = files.reserve()
    try:
        size = files.download("shared/uploads/logo.png", copy)

        assert size == len(b"\x89PNG\r\n\x00binary")
        assert copy.read_bytes() == b"\x89PNG\r\n\x00binary"
    finally:
        copy.unlink()
    [event] = events()
    assert event["action"] == "read" and event["download"] is True


# ---------------------------------------------------------------------------
# The environment is not here
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation",
    ["read", "download", "write", "mkdir", "move_from", "move_to", "remove"],
)
def test_the_browser_leaves_environment_files_to_the_environment(
    files: AppFiles, root: Path, operation: str
) -> None:
    before = (root / "shared" / ".env").read_text()
    copy = files.reserve()
    try:
        with pytest.raises(ValidationError, match="Environment"):
            if operation == "read":
                files.read_text("shared/.env")
            elif operation == "download":
                files.download("shared/.env", copy)
            elif operation == "write":
                files.save_text("shared/.env", "SECRET=2\n")
            elif operation == "mkdir":
                files.make_directory("shared/.env.d")
            elif operation == "move_from":
                files.move("shared/.env", "shared/stolen.txt")
            elif operation == "move_to":
                files.move("shared/notes.txt", "shared/.env")
            else:
                files.remove("shared/.env")
    finally:
        copy.unlink(missing_ok=True)
    assert (root / "shared" / ".env").read_text() == before
    assert not (root / "shared" / "stolen.txt").exists()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_an_edit_is_written_beside_the_file_keeps_its_mode_and_replaces_it(
    files: AppFiles, root: Path, runner: TreeRunner
) -> None:
    written = files.save_text("shared/notes.txt", "hello, world\n")

    path = root / "shared" / "notes.txt"
    assert written == 13 and path.read_text() == "hello, world\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    steps = [args[0] for args, _, _ in runner.asked]
    assert steps == ["dd", "chmod", "mv"]
    assert runner.asked[0][0][1].startswith(f"of={root / 'shared'}/.noust-")
    assert list((root / "shared").glob(".noust-*")) == [], "no partial file is left"


def test_a_new_file_is_created_only_when_allowed(files: AppFiles, root: Path) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        files.save_text("shared/new.txt", "x")

    files.save_text("shared/new.txt", "x", create=True)

    assert (root / "shared" / "new.txt").read_text() == "x"
    assert events()[-1]["created"] is True


def test_a_new_file_asks_no_mode_of_a_file_that_is_not_there(
    files: AppFiles, runner: TreeRunner
) -> None:
    files.save_text("shared/new.txt", "x", create=True)

    assert [args[0] for args, _, _ in runner.asked] == ["dd", "mv"]


def test_a_directory_cannot_be_written_as_a_file(files: AppFiles) -> None:
    with pytest.raises(ValidationError, match="is a directory"):
        files.save_text("shared/uploads", "x")


def test_a_file_cannot_be_written_into_a_directory_that_is_not_there(files: AppFiles) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        files.save_text("shared/missing/new.txt", "x", create=True)


def test_a_link_is_never_written_through(files: AppFiles, root: Path) -> None:
    (root / "shared" / "outside").symlink_to(root / "shared" / "notes.txt")

    with pytest.raises(SecurityError, match="is a link"):
        files.save_text("shared/outside", "overwritten", create=True)

    assert (root / "shared" / "notes.txt").read_text() == "hello\n"


def test_a_write_that_fails_leaves_the_file_as_it_was_and_no_partial(
    files: AppFiles, root: Path, runner: TreeRunner
) -> None:
    runner.fail["mv"] = "mv: cannot move: Permission denied"

    with pytest.raises(ServiceError, match=r"put shared/notes.txt in place") as caught:
        files.save_text("shared/notes.txt", "changed")

    assert "Permission denied" in (caught.value.details or "")
    assert (root / "shared" / "notes.txt").read_text() == "hello\n"
    assert ["rm", "-f"] == list(runner.asked[-1][0][:2]), "the partial file is removed"


def test_what_the_account_may_not_write_is_the_kernels_refusal_with_its_words(
    files: AppFiles, runner: TreeRunner
) -> None:
    runner.fail["dd"] = "dd: failed to open: Permission denied"

    with pytest.raises(ServiceError, match="Could not write") as caught:
        files.save_text("shared/notes.txt", "x")

    assert "Permission denied" in (caught.value.details or "")


def test_an_edit_larger_than_the_limit_is_refused(
    files: AppFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_files, "EDIT_MAX_BYTES", 4)

    with pytest.raises(ValidationError, match="edits files up to"):
        files.save_text("shared/notes.txt", "more than four")


def test_an_upload_larger_than_the_limit_is_refused(
    files: AppFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_files, "UPLOAD_MAX_BYTES", 4)

    with files.staged(b"0123456789") as staged:
        with pytest.raises(ValidationError, match="uploads up to"):
            files.save_bytes("shared/big.bin", staged, create=True, action="upload")


def test_an_upload_is_on_record_as_one(files: AppFiles, root: Path) -> None:
    with files.staged(b"abc") as staged:
        files.save_bytes("shared/up.bin", staged, create=True, action="upload")

    assert (root / "shared" / "up.bin").read_bytes() == b"abc"
    assert events()[-1]["action"] == "upload" and events()[-1]["bytes"] == 3


def test_what_waits_to_be_written_is_only_root_s_and_is_removed(
    files: AppFiles, tmp_path: Path
) -> None:
    with files.staged(b"secret") as staged:
        assert stat.S_IMODE(staged.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(staged.stat().st_mode) == 0o600
        kept = staged

    assert not kept.exists()


# ---------------------------------------------------------------------------
# Directories, moves and deletions
# ---------------------------------------------------------------------------


def test_a_directory_is_created_where_the_parent_is(files: AppFiles, root: Path) -> None:
    files.make_directory("shared/exports")

    assert (root / "shared" / "exports").is_dir()
    with pytest.raises(ValidationError, match="exists already"):
        files.make_directory("shared/exports")
    with pytest.raises(ValidationError, match="does not exist"):
        files.make_directory("shared/a/b")


def test_a_file_is_renamed_and_an_existing_destination_is_refused_unless_asked(
    files: AppFiles, root: Path
) -> None:
    (root / "shared" / "other.txt").write_text("other")

    with pytest.raises(ValidationError, match="exists already"):
        files.move("shared/notes.txt", "shared/other.txt")

    files.move("shared/notes.txt", "shared/other.txt", overwrite=True)
    assert (root / "shared" / "other.txt").read_text() == "hello\n"
    assert not (root / "shared" / "notes.txt").exists()


def test_a_directory_cannot_move_into_itself(files: AppFiles) -> None:
    with pytest.raises(ValidationError, match="into itself"):
        files.move("shared", "shared/uploads/shared")


def test_what_is_not_there_cannot_be_moved(files: AppFiles) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        files.move("shared/ghost", "shared/other")


def test_a_link_can_be_renamed_and_removed_and_what_it_points_at_stays(
    files: AppFiles, root: Path
) -> None:
    target = root / "shared" / "uploads"
    (root / "shared" / "shortcut").symlink_to(target)

    files.move("shared/shortcut", "shared/renamed")
    assert (root / "shared" / "renamed").is_symlink()

    files.remove("shared/renamed")
    assert not (root / "shared" / "renamed").is_symlink()
    assert (target / "logo.png").exists()


def test_a_file_is_deleted(files: AppFiles, root: Path) -> None:
    files.remove("shared/notes.txt")

    assert not (root / "shared" / "notes.txt").exists()
    assert events()[-1] == {
        "action": "remove",
        "path": "shared/notes.txt",
        "by": "ana",
        "recursive": False,
    }


def test_a_directory_with_files_is_deleted_only_when_asked(files: AppFiles, root: Path) -> None:
    with pytest.raises(ServiceError, match="Could not delete"):
        files.remove("shared/uploads")
    assert (root / "shared" / "uploads" / "logo.png").exists()

    files.remove("shared/uploads", recursive=True)
    assert not (root / "shared" / "uploads").exists()


def test_the_recursive_deletion_stays_on_one_filesystem(
    files: AppFiles, runner: TreeRunner
) -> None:
    files.remove("shared/uploads", recursive=True)

    assert "--one-file-system" in runner.asked[-1][0]


def test_the_applications_own_directory_cannot_be_deleted(files: AppFiles) -> None:
    with pytest.raises(ValidationError):
        files.remove("")


def test_what_is_not_there_cannot_be_deleted(files: AppFiles) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        files.remove("shared/ghost")


# ---------------------------------------------------------------------------
# Which applications have files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["docker-compose", "container"])
def test_a_stack_has_no_files_of_this_kind(store: NoustStore, kind: str) -> None:
    store.create_app(App(domain="stack.example.com", app_type=kind, app_path="/srv/stack"))

    with pytest.raises(ValidationError, match="containers"):
        files_for("stack.example.com", store=store)


def test_an_unknown_application_is_refused(store: NoustStore) -> None:
    with pytest.raises(Exception, match="not found"):
        files_for("nobody.example.com", store=store)


def test_an_application_directory_that_is_a_link_is_not_browsed(
    store: NoustStore, tmp_path: Path, runner: TreeRunner
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    store.create_app(
        App(domain="odd.example.com", app_type="nodejs", app_path=str(tmp_path / "linked"))
    )
    odd = files_for("odd.example.com", runner=runner, store=store)

    with pytest.raises(SecurityError, match="not a plain directory"):
        odd.list_directory("")
