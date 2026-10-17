#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Build the wheel and the source distribution from a commit so that anyone can build the same bytes (L-32).

"The package is the tag": a tag names a commit, and a distribution built from that commit with the
same tools has the same SHA-256 whoever builds it, wherever, whenever. Three things made two builds of
one commit differ, and this script removes each:

- **Time.** A wheel is a zip whose entries carry a modification time; ``SOURCE_DATE_EPOCH`` (the
  commit's own time) fixes it. The source distribution is a tar.gz whose entries carry the time of the
  build, the owner of the checkout and the order of the filesystem, and whose gzip header carries the
  time it was compressed; it is repacked here with the commit's time, no owner and sorted entries.
- **The tree.** What is built is ``git archive`` of the commit, never the working directory: no ignored
  file, no leftover ``build/`` and no editor's backup can get in. It is also how the OBS tarball is made.
- **The tools.** The wheel's bytes depend on the setuptools and wheel that built it, so the versions are
  printed and recorded; ``--no-isolation`` builds with the ones installed, which a verifier pins.

Usage:
    scripts/reproducible_build.py build  [--ref REF] [--output DIR]    Build both into DIR.
    scripts/reproducible_build.py verify [--ref REF] [--against DIR]   Build twice, in two different
                                                                         directories, and compare; with
                                                                         --against, compare with the
                                                                         files of a release too.

Exit status: 0 when the builds agree, 1 when they differ, 2 when a build could not be made.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata as metadata
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: A tar entry that is a file is stored with one of these modes, whatever the checkout had.
FILE_MODE = 0o644
EXECUTABLE_MODE = 0o755

#: The distributions whose versions decide the bytes of what is built.
TOOLCHAIN = ("setuptools", "wheel", "build")


class BuildError(Exception):
    """A build could not be made."""


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    """
    Args:
        argv: The command, as an argument vector (never a shell).
        cwd: Where to run it.
        env: Extra environment variables.

    Returns:
        Its standard output.

    Raises:
        BuildError: The command failed; the message carries its error output.
    """
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(
        argv, cwd=cwd, env=merged, capture_output=True, text=True, timeout=1800, check=False
    )
    if result.returncode != 0:
        raise BuildError(f"{' '.join(argv)} failed:\n{result.stderr or result.stdout}")
    return result.stdout


def commit_epoch(ref: str, repository: Path = ROOT) -> int:
    """
    Args:
        ref: A commit, tag or branch.
        repository: The repository.

    Returns:
        The commit's time as seconds since the epoch: what ``SOURCE_DATE_EPOCH`` is for this build.
    """
    return int(run(["git", "log", "-1", "--format=%ct", ref], cwd=repository).strip())


def export_tree(ref: str, destination: Path, repository: Path = ROOT) -> None:
    """
    Write the files of a commit, and only those, into a directory.

    Args:
        ref: A commit, tag or branch.
        destination: An empty directory.
        repository: The repository.

    Raises:
        BuildError: ``git archive`` failed.
    """
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if archive.returncode != 0:
        raise BuildError(f"git archive {ref} failed:\n{archive.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(destination, filter="data")


def repack_sdist(path: Path, epoch: int) -> None:
    """
    Rewrite a source distribution so that its bytes depend on its content alone.

    The entries are sorted, belong to nobody, carry a mode that depends only on whether the file is
    executable, and are never newer than ``epoch``; the gzip header carries no name and no time.

    Args:
        path: A ``.tar.gz``, rewritten in place.
        epoch: ``SOURCE_DATE_EPOCH``: no entry is given a later time.
    """
    with tarfile.open(path, "r:gz") as source:
        members = sorted(source.getmembers(), key=lambda member: member.name)
        payloads = {
            member.name: source.extractfile(member).read()  # type: ignore[union-attr]
            for member in members
            if member.isfile()
        }
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as target:
        for member in members:
            clean = tarfile.TarInfo(member.name)
            clean.type = member.type
            clean.mtime = min(int(member.mtime), epoch)
            clean.uid = clean.gid = 0
            clean.uname = clean.gname = ""
            if member.isdir():
                clean.mode = EXECUTABLE_MODE
                target.addfile(clean)
            elif member.isfile():
                clean.mode = EXECUTABLE_MODE if member.mode & 0o111 else FILE_MODE
                clean.size = len(payloads[member.name])
                target.addfile(clean, io.BytesIO(payloads[member.name]))
            else:
                clean.linkname = member.linkname
                clean.mode = member.mode
                target.addfile(clean)
    with (
        path.open("wb") as handle,
        gzip.GzipFile(
            filename="", mode="wb", fileobj=handle, mtime=0, compresslevel=9
        ) as compressed,
    ):
        compressed.write(raw.getvalue())


def sha256(path: Path) -> str:
    """
    Args:
        path: A file.

    Returns:
        Its SHA-256, in hexadecimal.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def toolchain() -> dict[str, str]:
    """
    Returns:
        The version of each tool whose version decides the bytes, and of Python.
    """
    versions = {"python": ".".join(map(str, sys.version_info[:3]))}
    for name in TOOLCHAIN:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def build(
    ref: str,
    output: Path,
    *,
    isolated: bool = False,
    repository: Path = ROOT,
    workdir: Path | None = None,
) -> dict[str, Any]:
    """
    Build the wheel and the source distribution of a commit.

    Args:
        ref: A commit, tag or branch.
        output: Where the two files go; created.
        isolated: Build in PEP 517 isolation, with whatever setuptools is newest, instead of the
            tools installed here (``--no-isolation``). Convenient; the result then depends on the day.
        repository: The repository.
        workdir: A directory to work in; a temporary one by default.

    Returns:
        ``{"ref", "commit", "source_date_epoch", "toolchain", "files": {name: sha256}}``.

    Raises:
        BuildError: The tree could not be exported or a build failed.
    """
    epoch = commit_epoch(ref, repository)
    commit = run(["git", "rev-parse", ref], cwd=repository).strip()
    output.mkdir(parents=True, exist_ok=True)
    own = workdir is None
    work = workdir or Path(tempfile.mkdtemp(prefix="noust-repro-"))
    try:
        tree = work / "tree"
        tree.mkdir(parents=True, exist_ok=True)
        export_tree(ref, tree, repository)
        command = [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(output)]
        if not isolated:
            command.append("--no-isolation")
        run(
            [*command, str(tree)],
            env={"SOURCE_DATE_EPOCH": str(epoch), "PYTHONHASHSEED": "0"},
        )
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)
    for sdist in sorted(output.glob("*.tar.gz")):
        repack_sdist(sdist, epoch)
    files = {path.name: sha256(path) for path in sorted(output.iterdir()) if path.is_file()}
    return {
        "ref": ref,
        "commit": commit,
        "source_date_epoch": epoch,
        "toolchain": toolchain(),
        "files": files,
    }


def verify(ref: str, *, against: Path | None, isolated: bool, repository: Path = ROOT) -> int:
    """
    Build a commit twice, in different directories, and compare.

    Args:
        ref: A commit, tag or branch.
        against: A directory with distributions someone published, to compare with as well.
        isolated: See :func:`build`.
        repository: The repository.

    Returns:
        0 when every file is the same in every build, 1 when one differs.
    """
    with tempfile.TemporaryDirectory(prefix="noust-repro-") as scratch:
        base = Path(scratch)
        first = build(
            ref,
            base / "first" / "out",
            isolated=isolated,
            repository=repository,
            workdir=base / "a",
        )
        second = build(
            ref,
            base / "second" / "out",
            isolated=isolated,
            repository=repository,
            workdir=base / "deeper" / "b",
        )
    print(f"commit {first['commit']}  SOURCE_DATE_EPOCH={first['source_date_epoch']}")
    print("tools " + ", ".join(f"{k} {v}" for k, v in first["toolchain"].items()))
    published: dict[str, str] = {}
    if against is not None:
        published = {
            path.name: sha256(path) for path in sorted(against.iterdir()) if path.is_file()
        }
    different = 0
    for name, digest in first["files"].items():
        again = second["files"].get(name)
        line = f"{digest}  {name}"
        if again != digest:
            line += f"   DIFFERS between two builds ({again})"
            different += 1
        if name in published and published[name] != digest:
            line += f"   DIFFERS from the published file ({published[name]})"
            different += 1
        print(line)
    missing = sorted(set(second["files"]) ^ set(first["files"]))
    for name in missing:
        print(f"only one build made {name}")
        different += 1
    for name in sorted(set(published) - set(first["files"])):
        print(f"not built here: {name}")
    print("reproducible" if different == 0 else f"{different} difference(s)")
    return 0 if different == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: Arguments; the process's by default.

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify"):
        sub = commands.add_parser(name)
        sub.add_argument("--ref", default="HEAD", help="The commit, tag or branch (default HEAD).")
        sub.add_argument(
            "--isolated",
            action="store_true",
            help="Build in an isolated environment (newest setuptools) instead of the installed one.",
        )
    commands.choices["build"].add_argument(
        "--output", type=Path, default=ROOT / "dist", help="Where the files go (default dist/)."
    )
    commands.choices["build"].add_argument("--json", action="store_true", help="Print the record.")
    commands.choices["verify"].add_argument(
        "--against", type=Path, default=None, help="A directory of published files to compare with."
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            record = build(args.ref, args.output, isolated=args.isolated)
            if args.json:
                print(json.dumps(record, indent=2))
            else:
                for name, digest in record["files"].items():
                    print(f"{digest}  {name}")
            return 0
        return verify(args.ref, against=args.against, isolated=args.isolated)
    except BuildError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
