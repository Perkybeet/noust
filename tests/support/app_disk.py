# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_disk.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore, get_store

DOMAIN = "shop.example.com"
MB = 1024 * 1024


class Disk(FakeRunner):
    """``du`` and ``findmnt`` answering from a real tree."""

    def __init__(self) -> None:
        super().__init__()
        self.mount = "/ ext4 rw,relatime"
        self.slow: set[str] = set()

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv
        if "du" in args:
            target = Path(args[-1])
            if str(target) in self.slow:
                return replace(result, exit_code=1, stderr="timed out")
            total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
            return replace(result, stdout=f"{total}\t{target}\n")
        if args[:1] == ("findmnt",):
            if not self.mount:
                return replace(result, exit_code=1)
            return replace(result, stdout=self.mount + "\n")
        return result

    def measured(self) -> list[str]:
        return [call[-1] for call in self.calls if "du" in call]


def write(path: Path, megabytes: float) -> None:
    """Make a sparse file of that size: the fake ``du`` reads sizes, and no disk is used."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(int(megabytes * MB))


@pytest.fixture
def store() -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


@pytest.fixture
def disk() -> Iterator[Disk]:
    fake = Disk()
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def releases_app(tmp_path: Path, store: NoustStore) -> App:
    """An application on the release layout: three releases, uploads and a repository cache."""
    root = tmp_path / "apps" / "shop-example-com"
    for release, size in (
        ("20260901-100000-aaaaaaa", 60),
        ("20260910-100000-bbbbbbb", 60),
        ("20260920-100000-ccccccc", 100),
    ):
        write(root / "releases" / release / "app.bin", size)
    (root / "current").symlink_to(Path("releases") / "20260920-100000-ccccccc")
    write(root / "shared" / "uploads" / "photo.jpg", 140)
    write(root / "repo" / "pack.bin", 40)
    return store.create_app(
        App(
            domain=DOMAIN, app_type="nodejs", layout="releases", keep_releases=2, app_path=str(root)
        )
    )


@pytest.fixture
def inplace_app(tmp_path: Path, store: NoustStore) -> App:
    root = tmp_path / "apps" / "legacy-example-com"
    write(root / "site.bin", 80)
    write(root / "uploads" / "a.bin", 20)
    return store.create_app(
        App(domain="legacy.example.com", app_type="nodejs", layout="inplace", app_path=str(root))
    )
