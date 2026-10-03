# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the ownership hand-over helpers.

:func:`~noust.deployers.helpers.permissions.hand_over_tree` covers a deployed
application tree and only warns on failure, because the build is already good
and an app that never writes to its own directory runs fine regardless.
:func:`~noust.deployers.helpers.permissions.hand_over_file` exists for the
single files a restore or a database engine hands to another account - a
Redis snapshot, a staged database dump - where the caller cannot shrug off a
failure: reporting "restored" over a file still owned by root is exactly the
silent failure CLAUDE.md rule 2 exists to remove.
"""

from __future__ import annotations

import errno
import grp
import io
import os
import pwd
from pathlib import Path

import pytest

from noust.core.fs import RealFileSystem, _set_owner_entry
from noust.core.logger import Logger
from noust.deployers.helpers.permissions import hand_over_file
from tests.conftest import OwnershipChange


def quiet(stream: io.StringIO | None = None) -> Logger:
    return Logger(no_color=True, stream=stream or io.StringIO())


def test_hand_over_file_gives_the_entry_its_owner_and_mode(
    tmp_path: Path, ownership_changes: list[OwnershipChange]
) -> None:
    target = tmp_path / "dump.rdb"
    target.write_text("x")
    target.chmod(0o600)

    ok = hand_over_file(target, user="redis", group="redis", mode=0o640, logger=quiet())

    assert ok is True
    assert ownership_changes == [OwnershipChange(target, "redis", "redis", 0o640)]
    assert target.stat().st_mode & 0o7777 == 0o640


def test_hand_over_file_reports_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed hand-over must not be reported as a completed one."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(RealFileSystem, "set_owner", refuse)
    out = io.StringIO()
    target = tmp_path / "dump.rdb"
    target.write_text("x")

    ok = hand_over_file(target, user="redis", group="redis", mode=0o640, logger=quiet(out))

    assert ok is False
    assert "Operation not permitted" in out.getvalue()


@pytest.mark.real_ownership
def test_hand_over_file_reports_an_account_that_does_not_exist(tmp_path: Path) -> None:
    out = io.StringIO()
    target = tmp_path / "dump.rdb"
    target.write_text("x")
    target.chmod(0o600)

    ok = hand_over_file(
        target, user="no-such-account-x9", group="no-such-group-x9", mode=0o644, logger=quiet(out)
    )

    assert ok is False
    assert "no such account" in out.getvalue()
    assert target.stat().st_mode & 0o7777 == 0o600


@pytest.mark.real_ownership
def test_hand_over_file_changes_the_inode_and_never_a_link_s_target(tmp_path: Path) -> None:
    """
    The hand-over is made on the pinned entry, never on the path by name.

    It used to check for a link and then run ``chown user:group path``: in a
    directory the engine's account owns (/etc/redis), that account swaps a
    link to /etc/shadow in between and root hands the target over.
    """
    victim = tmp_path / "shadow"
    victim.write_text("root:x:0\n")
    victim.chmod(0o640)
    link = tmp_path / "redis.conf"
    link.symlink_to(victim)
    me = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name

    ok = hand_over_file(link, user=me, group=group, mode=0o666, logger=quiet())

    assert ok is False
    assert victim.stat().st_mode & 0o7777 == 0o640
    with pytest.raises(OSError) as raised:
        _set_owner_entry(link, os.getuid(), os.getgid(), 0o666)
    assert raised.value.errno == errno.ELOOP


@pytest.mark.real_ownership
def test_hand_over_file_never_carries_setuid_setgid_or_sticky(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("x")
    me = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name

    ok = hand_over_file(target, user=me, group=group, mode=0o7755, logger=quiet())

    assert ok is True
    assert target.stat().st_mode & 0o7777 == 0o755


@pytest.mark.real_ownership
def test_write_text_gives_the_owner_to_the_temporary_file_before_the_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner is applied through the descriptor, so nothing follows the name after."""
    seen: list[int] = []
    real_fchown = os.fchown

    def fchown(fd: int, uid: int, gid: int) -> None:
        seen.append(fd)
        real_fchown(fd, uid, gid)

    monkeypatch.setattr(os, "fchown", fchown)
    monkeypatch.setattr(os, "chown", lambda *a, **k: pytest.fail("chown by name"))
    target = tmp_path / "redis.conf"
    target.write_text("old\n")

    RealFileSystem().write_text(target, "new\n", mode=0o4640, owner=(os.getuid(), os.getgid()))

    assert seen, "the owner went to the descriptor"
    assert target.read_text() == "new\n"
    assert target.stat().st_mode & 0o7777 == 0o640
