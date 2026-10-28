# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_files.py`` that other tests build on."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import App, NoustStore

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
