# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A distribution built from a commit has the same bytes whoever builds it (checklist L-32).

``scripts/reproducible_build.py`` builds the wheel and the source distribution from
``git archive`` of a commit with ``SOURCE_DATE_EPOCH`` set to the commit's time, and
repacks the source distribution, whose tar entries and gzip header otherwise carry the
time of the build, the owner of the checkout and the order of the filesystem.

The repacking is tested here as a function, with archives that differ in exactly those
things. The whole build is tested on a small project of the test's own when the machine
has git, setuptools and wheel; the same script runs on this repository in CI
(``reproducible`` job) and is how a release is built.
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import shutil
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent
EPOCH = 1_760_000_000


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "reproducible_build_script", ROOT / "scripts" / "reproducible_build.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reproducible_build_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return load_script()


def make_sdist(
    path: Path,
    *,
    order: list[str],
    uid: int,
    uname: str,
    mtime: int,
    gzip_time: int,
    mode: int = 0o664,
) -> None:
    """
    Write a source distribution the way a build on some machine would.

    Args:
        path: The ``.tar.gz``.
        order: The order the files are added in.
        uid: Who owns every entry.
        uname: The owner's name.
        mtime: The entries' modification time.
        gzip_time: The time written in the gzip header.
        mode: The mode of the files.
    """
    files = {
        "noust-1.0/pyproject.toml": b"[project]\nname = 'noust'\n",
        "noust-1.0/src/noust/__init__.py": b"__version__ = '1.0'\n",
        "noust-1.0/src/noust/run.sh": b"#!/bin/sh\n",
    }
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        directory = tarfile.TarInfo("noust-1.0")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o775
        directory.mtime = mtime + 99
        directory.uid, directory.uname = uid, uname
        tar.addfile(directory)
        for name in order:
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mtime = mtime
            info.uid = info.gid = uid
            info.uname = info.gname = uname
            info.mode = mode | (0o111 if name.endswith(".sh") else 0)
            tar.addfile(info, io.BytesIO(files[name]))
    with (
        path.open("wb") as handle,
        gzip.GzipFile(
            filename="noust-1.0.tar", mode="wb", fileobj=handle, mtime=gzip_time
        ) as compressed,
    ):
        compressed.write(raw.getvalue())


class TestTheSourceDistributionIsRepacked:
    def test_two_that_differ_only_in_what_a_build_leaves_behind_become_one(
        self, script: ModuleType, tmp_path: Path
    ) -> None:
        names = [
            "noust-1.0/pyproject.toml",
            "noust-1.0/src/noust/__init__.py",
            "noust-1.0/src/noust/run.sh",
        ]
        a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
        make_sdist(a, order=names, uid=1000, uname="yago", mtime=EPOCH + 5000, gzip_time=111)
        make_sdist(
            b,
            order=names[::-1],
            uid=0,
            uname="runner",
            mtime=EPOCH + 9000,
            gzip_time=222,
            mode=0o600,
        )
        assert a.read_bytes() != b.read_bytes()

        script.repack_sdist(a, EPOCH)
        script.repack_sdist(b, EPOCH)

        assert a.read_bytes() == b.read_bytes()

    def test_what_it_holds_is_what_it_held(self, script: ModuleType, tmp_path: Path) -> None:
        path = tmp_path / "x.tar.gz"
        make_sdist(
            path,
            order=["noust-1.0/src/noust/__init__.py", "noust-1.0/pyproject.toml"],
            uid=1000,
            uname="yago",
            mtime=EPOCH + 50,
            gzip_time=5,
        )

        script.repack_sdist(path, EPOCH)

        with tarfile.open(path) as tar:
            members = tar.getmembers()
            assert [m.name for m in members] == sorted(m.name for m in members)
            assert {m.name for m in members} == {
                "noust-1.0",
                "noust-1.0/pyproject.toml",
                "noust-1.0/src/noust/__init__.py",
            }
            for member in members:
                assert member.uid == member.gid == 0
                assert member.uname == member.gname == ""
                assert member.mtime <= EPOCH
            body = tar.extractfile("noust-1.0/src/noust/__init__.py")
            assert body is not None and body.read() == b"__version__ = '1.0'\n"

    def test_a_file_is_executable_or_it_is_not_and_nothing_else_of_its_mode_survives(
        self, script: ModuleType, tmp_path: Path
    ) -> None:
        path = tmp_path / "x.tar.gz"
        make_sdist(
            path,
            order=["noust-1.0/pyproject.toml", "noust-1.0/src/noust/run.sh"],
            uid=1,
            uname="u",
            mtime=1,
            gzip_time=1,
            mode=0o660,
        )

        script.repack_sdist(path, EPOCH)

        with tarfile.open(path) as tar:
            modes = {m.name: m.mode for m in tar.getmembers() if m.isfile()}
        assert modes == {
            "noust-1.0/pyproject.toml": 0o644,
            "noust-1.0/src/noust/run.sh": 0o755,
        }

    def test_an_entry_older_than_the_commit_keeps_its_time(
        self, script: ModuleType, tmp_path: Path
    ) -> None:
        path = tmp_path / "x.tar.gz"
        make_sdist(
            path,
            order=["noust-1.0/pyproject.toml"],
            uid=1,
            uname="u",
            mtime=EPOCH - 77,
            gzip_time=1,
        )

        script.repack_sdist(path, EPOCH)

        with tarfile.open(path) as tar:
            assert tar.getmember("noust-1.0/pyproject.toml").mtime == EPOCH - 77

    def test_the_gzip_header_carries_no_time_and_no_name(
        self, script: ModuleType, tmp_path: Path
    ) -> None:
        path = tmp_path / "x.tar.gz"
        make_sdist(
            path, order=["noust-1.0/pyproject.toml"], uid=1, uname="u", mtime=1, gzip_time=99
        )

        script.repack_sdist(path, EPOCH)

        header = path.read_bytes()[:10]
        assert header[:2] == b"\x1f\x8b"
        assert header[3] == 0  # no flags: no file name
        assert header[4:8] == b"\x00\x00\x00\x00"  # no modification time


needs_a_toolchain = pytest.mark.skipif(
    shutil.which("git") is None
    or importlib.util.find_spec("setuptools") is None
    or importlib.util.find_spec("wheel") is None
    or importlib.util.find_spec("build") is None,
    reason="building needs git, setuptools, wheel and build",
)


def tiny_project(root: Path) -> None:
    """
    Args:
        root: An empty directory, which becomes a repository of a one-module project.
    """
    (root / "src" / "tiny").mkdir(parents=True)
    (root / "src" / "tiny" / "__init__.py").write_text("VALUE = 1\n")
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=61", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        '[project]\nname = "tiny"\nversion = "1.0"\n\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
    )


@pytest.mark.allow_subprocess
@needs_a_toolchain
class TestTheWholeBuild:
    @pytest.fixture
    def repository(self, tmp_path: Path, script: ModuleType) -> Path:
        root = tmp_path / "repo"
        root.mkdir()
        tiny_project(root)
        environment = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.test",
            "GIT_AUTHOR_DATE": "2026-10-01T10:00:00+0000",
            "GIT_COMMITTER_DATE": "2026-10-01T10:00:00+0000",
        }
        script.run(["git", "init", "-q", "-b", "main"], cwd=root)
        script.run(["git", "add", "."], cwd=root)
        script.run(["git", "commit", "-q", "-m", "tiny"], cwd=root, env=environment)
        return root

    def test_the_epoch_is_the_commits_time(self, script: ModuleType, repository: Path) -> None:
        assert script.commit_epoch("HEAD", repository) == 1_790_848_800

    def test_only_what_is_committed_is_built(self, script: ModuleType, repository: Path) -> None:
        (repository / "src" / "tiny" / "stray.py").write_text("NOT = 'committed'\n")
        destination = repository.parent / "tree"
        destination.mkdir()

        script.export_tree("HEAD", destination, repository)

        assert (destination / "src" / "tiny" / "__init__.py").exists()
        assert not (destination / "src" / "tiny" / "stray.py").exists()

    def test_two_builds_in_different_places_are_the_same_bytes(
        self, script: ModuleType, repository: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status = script.verify("HEAD", against=None, isolated=False, repository=repository)

        out = capsys.readouterr().out
        assert status == 0, out
        assert "tiny-1.0-py3-none-any.whl" in out and "tiny-1.0.tar.gz" in out
        assert "reproducible" in out

    def test_a_file_that_is_not_the_published_one_is_reported(
        self,
        script: ModuleType,
        repository: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        published = tmp_path / "published"
        published.mkdir()
        (published / "tiny-1.0-py3-none-any.whl").write_bytes(b"not the wheel")

        status = script.verify("HEAD", against=published, isolated=False, repository=repository)

        assert status == 1
        assert "DIFFERS from the published file" in capsys.readouterr().out
