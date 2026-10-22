# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A release as one file (checklist A-49): the format, its checksum and its extraction.

What is pinned: that a bundle is a manifest first and the release after it; that the links a
release has to its application's ``shared/`` are left out and a link that points out of the
release cannot be packed at all; that a bundle is never read without the checksum the operator
brought, and the manifest is judged before the first member is written; and that the bundle is
untrusted input to the end (a path out of the release, a link that points out, a device file,
a setuid bit, a bomb), refused with nothing left behind.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import stat
import tarfile
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import SourceError, ValidationError
from noust.deployers import bundle
from noust.deployers.bundle import (
    MANIFEST_NAME,
    Manifest,
    pack,
    read_manifest,
    sha256_of,
    unpack,
    verify,
)

RELEASE = "20261013-100000-abc1234"


def manifest(**changes: Any) -> Manifest:
    values: dict[str, Any] = {
        "format": bundle.FORMAT,
        "app_type": "nodejs",
        "domain": "shop.example.com",
        "release_id": RELEASE,
        "commit": "abc1234",
        "branch": "main",
        "app_root": "/var/www/apps/shop-example-com",
        "built_at": "2026-10-17T10:00:00+00:00",
        "builder": "builder-1",
        "noust_version": "3.4.0",
        "files": 0,
        "bytes": 0,
    }
    values.update(changes)
    return Manifest(**values)


def tree(root: Path) -> Path:
    """A built release: code, a nested dependency with a relative link, and the links to shared."""
    release = root / "app" / "releases" / RELEASE
    (release / "node_modules" / ".bin").mkdir(parents=True)
    (release / "node_modules" / "tool").mkdir()
    (release / "node_modules" / "tool" / "cli.js").write_text("run\n")
    os.symlink("../tool/cli.js", release / "node_modules" / ".bin" / "tool")
    (release / "server.js").write_text("listen(3000)\n")
    (release / "run.sh").write_text("#!/bin/sh\n")
    (release / "run.sh").chmod(0o755)
    (root / "app" / "shared").mkdir()
    (root / "app" / "shared" / ".env").write_text("SECRET=1\n")
    (root / "app" / "shared" / "storage").mkdir()
    os.symlink("../../shared/.env", release / ".env")
    os.symlink("../../shared/storage", release / "storage")
    return release


def raw_bundle(path: Path, members: list[tuple[str, bytes | str, int]], doc: Any = None) -> Path:
    """
    Write a bundle by hand, to be as hostile as a test needs.

    Members are (name, content, mode); content "-> target" makes a symbolic link.
    """
    document = json.dumps(doc if doc is not None else manifest().to_dict()).encode()
    with tarfile.open(path, "w:gz") as archive:
        head = tarfile.TarInfo(MANIFEST_NAME)
        head.size = len(document)
        archive.addfile(head, io.BytesIO(document))
        for name, content, mode in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if isinstance(content, str) and content.startswith("-> "):
                info.type = tarfile.SYMTYPE
                info.linkname = content[3:]
                archive.addfile(info)
            else:
                data = content if isinstance(content, bytes) else content.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return path


class TestPacking:
    def test_it_writes_the_manifest_first_and_the_release_after_it(self, tmp_path: Path) -> None:
        release = tree(tmp_path)
        out = tmp_path / "out" / f"shop{bundle.BUNDLE_SUFFIX}"
        out.parent.mkdir()

        packed = pack(release, manifest(), out, shared=tmp_path / "app" / "shared")

        with tarfile.open(out, "r:gz") as archive:
            names = archive.getnames()
        assert names[0] == MANIFEST_NAME and names[1] == "release"
        assert "release/server.js" in names and "release/node_modules/tool/cli.js" in names
        assert packed.manifest.files == 3 and packed.manifest.bytes == len("run\n") + len(
            "listen(3000)\n"
        ) + len("#!/bin/sh\n")
        assert packed.sha256 == sha256_of(out) and packed.size == out.stat().st_size
        assert read_manifest(out) == packed.manifest

    def test_the_checksum_is_written_beside_it_the_way_sha256sum_does(self, tmp_path: Path) -> None:
        out = tmp_path / f"shop{bundle.BUNDLE_SUFFIX}"
        packed = pack(tree(tmp_path), manifest(), out, shared=tmp_path / "app" / "shared")

        sidecar = tmp_path / f"shop{bundle.BUNDLE_SUFFIX}.sha256"
        assert sidecar.read_text() == f"{packed.sha256}  {out.name}\n"

    def test_the_links_to_the_applications_shared_directory_are_left_out(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / f"shop{bundle.BUNDLE_SUFFIX}"
        pack(tree(tmp_path), manifest(), out, shared=tmp_path / "app" / "shared")

        with tarfile.open(out, "r:gz") as archive:
            names = archive.getnames()
        assert "release/.env" not in names and "release/storage" not in names
        assert "release/node_modules/.bin/tool" in names

    def test_a_link_inside_the_release_stays_a_link(self, tmp_path: Path) -> None:
        out = tmp_path / f"shop{bundle.BUNDLE_SUFFIX}"
        pack(tree(tmp_path), manifest(), out, shared=tmp_path / "app" / "shared")

        with tarfile.open(out, "r:gz") as archive:
            link = archive.getmember("release/node_modules/.bin/tool")
        assert link.issym() and link.linkname == "../tool/cli.js"

    def test_owners_and_unsafe_modes_are_not_carried(self, tmp_path: Path) -> None:
        release = tree(tmp_path)
        (release / "danger").write_text("x")
        (release / "danger").chmod(0o4777)
        out = tmp_path / f"shop{bundle.BUNDLE_SUFFIX}"
        pack(release, manifest(), out, shared=tmp_path / "app" / "shared")

        with tarfile.open(out, "r:gz") as archive:
            members = {m.name: m for m in archive.getmembers()}
        assert members["release/danger"].mode == 0o755 & 0o4777
        assert members["release/run.sh"].mode == 0o755
        assert all(m.uid == 0 and m.gid == 0 and m.uname == "" for m in members.values())

    @pytest.mark.parametrize(
        "target", ["/usr/bin/python3", "../../../etc/passwd", "../../../../outside"]
    )
    def test_a_link_that_points_out_of_the_release_cannot_be_packed(
        self, tmp_path: Path, target: str
    ) -> None:
        release = tree(tmp_path)
        (release / ".venv" / "bin").mkdir(parents=True)
        os.symlink(target, release / ".venv" / "bin" / "python")
        out = tmp_path / f"shop{bundle.BUNDLE_SUFFIX}"

        with pytest.raises(ValidationError, match="points outside the release") as caught:
            pack(release, manifest(), out, shared=tmp_path / "app" / "shared")

        assert "virtual environment" in caught.value.details and not out.exists()

    def test_a_pipe_cannot_be_carried(self, tmp_path: Path) -> None:
        release = tree(tmp_path)
        os.mkfifo(release / "pipe")

        with pytest.raises(ValidationError, match="not a regular file"):
            pack(release, manifest(), tmp_path / "x.tar.gz", shared=tmp_path / "app" / "shared")

    def test_a_bundle_is_never_written_over_another(self, tmp_path: Path) -> None:
        out = tmp_path / f"shop{bundle.BUNDLE_SUFFIX}"
        out.write_bytes(b"precious")

        with pytest.raises(ValidationError, match="exists already"):
            pack(tree(tmp_path), manifest(), out, shared=tmp_path / "app" / "shared")

        assert out.read_bytes() == b"precious"


class TestTheChecksum:
    def make(self, tmp_path: Path) -> bundle.Bundle:
        return pack(
            tree(tmp_path),
            manifest(),
            tmp_path / f"shop{bundle.BUNDLE_SUFFIX}",
            shared=tmp_path / "app" / "shared",
        )

    def test_the_right_digest_in_any_case_passes(self, tmp_path: Path) -> None:
        packed = self.make(tmp_path)

        assert verify(packed.path, packed.sha256) == packed.sha256
        assert verify(packed.path, packed.sha256.upper()) == packed.sha256
        assert verify(packed.path, f"  {packed.sha256}\n") == packed.sha256

    def test_another_file_or_a_cut_one_is_the_same_refusal(self, tmp_path: Path) -> None:
        packed = self.make(tmp_path)
        cut = tmp_path / "cut.tar.gz"
        cut.write_bytes(packed.path.read_bytes()[:-20])

        with pytest.raises(ValidationError, match="not the file the checksum names") as caught:
            verify(cut, packed.sha256)

        assert "nothing was extracted" in caught.value.details

    @pytest.mark.parametrize(
        "text", ["", "abc", "z" * 64, "a" * 63, "a" * 65, "sha256:" + "a" * 64]
    )
    def test_what_is_not_a_digest_is_not_compared(self, tmp_path: Path, text: str) -> None:
        packed = self.make(tmp_path)

        with pytest.raises(ValidationError, match="not a SHA-256") as caught:
            verify(packed.path, text)

        assert caught.value.field == "sha256"


class TestTheManifest:
    @pytest.mark.parametrize(
        "doc",
        [
            [],
            "text",
            {},
            {**manifest().to_dict(), "extra": 1},
            {k: v for k, v in manifest().to_dict().items() if k != "commit"},
            {**manifest().to_dict(), "files": "3"},
            {**manifest().to_dict(), "files": True},
            {**manifest().to_dict(), "app_type": None},
            {**manifest().to_dict(), "commit": 7},
        ],
    )
    def test_anything_but_exactly_a_manifest_is_refused(self, tmp_path: Path, doc: Any) -> None:
        path = raw_bundle(tmp_path / "b.tar.gz", [], doc=doc)

        with pytest.raises(ValidationError, match="not a Noust release bundle"):
            read_manifest(path)

    def test_another_format_says_which(self, tmp_path: Path) -> None:
        path = raw_bundle(tmp_path / "b.tar.gz", [], doc={**manifest().to_dict(), "format": 2})

        with pytest.raises(ValidationError, match="is format 2"):
            read_manifest(path)

    def test_a_commit_may_be_absent_for_a_source_that_is_not_git(self, tmp_path: Path) -> None:
        path = raw_bundle(
            tmp_path / "b.tar.gz", [], doc={**manifest().to_dict(), "commit": None, "branch": None}
        )

        assert read_manifest(path).commit is None

    def test_a_file_that_is_not_a_gzip_tar(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.write_text("hello")
        zipped = tmp_path / "z.gz"
        zipped.write_bytes(gzip.compress(b"not a tar"))

        for path in (plain, zipped):
            with pytest.raises(ValidationError, match="not a Noust release bundle"):
                read_manifest(path)

    def test_the_manifest_has_to_be_the_first_member_and_small(self, tmp_path: Path) -> None:
        late = tmp_path / "late.tar.gz"
        with tarfile.open(late, "w:gz") as archive:
            info = tarfile.TarInfo("release/server.js")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        big = tmp_path / "big.tar.gz"
        with tarfile.open(big, "w:gz") as archive:
            head = tarfile.TarInfo(MANIFEST_NAME)
            head.size = bundle.MAX_MANIFEST_BYTES + 1
            archive.addfile(head, io.BytesIO(b" " * head.size))

        for path in (late, big):
            with pytest.raises(ValidationError, match="not a Noust release bundle"):
                read_manifest(path)


class TestUnpacking:
    def packed(self, tmp_path: Path) -> bundle.Bundle:
        return pack(
            tree(tmp_path),
            manifest(),
            tmp_path / f"shop{bundle.BUNDLE_SUFFIX}",
            shared=tmp_path / "app" / "shared",
        )

    def test_the_release_arrives_under_its_id_and_nothing_else_is_left(
        self, tmp_path: Path
    ) -> None:
        packed = self.packed(tmp_path)
        releases = tmp_path / "other" / "releases"

        got, destination = unpack(packed.path, releases, sha256=packed.sha256)

        assert got == packed.manifest and destination == releases / RELEASE
        assert (destination / "server.js").read_text() == "listen(3000)\n"
        assert os.readlink(destination / "node_modules" / ".bin" / "tool") == "../tool/cli.js"
        assert (destination / "run.sh").stat().st_mode & 0o111
        assert not (destination / ".env").exists() and not (destination / "storage").exists()
        assert [entry.name for entry in releases.iterdir()] == [RELEASE]

    def test_the_checksum_comes_before_everything(self, tmp_path: Path) -> None:
        packed = self.packed(tmp_path)
        releases = tmp_path / "other" / "releases"

        with pytest.raises(ValidationError, match="not the file the checksum names"):
            unpack(packed.path, releases, sha256="0" * 64)

        assert not releases.exists() or not list(releases.iterdir())

    def test_the_manifest_is_judged_before_a_member_is_written(self, tmp_path: Path) -> None:
        packed = self.packed(tmp_path)
        releases = tmp_path / "other" / "releases"

        def refuse(found: Manifest) -> None:
            raise ValidationError(f"no {found.app_type} here")

        with pytest.raises(ValidationError, match="no nodejs here"):
            unpack(packed.path, releases, sha256=packed.sha256, check=refuse)

        assert not releases.exists() or not list(releases.iterdir())

    @pytest.mark.parametrize("release_id", ["../escape", "/etc", "a b", "20261013-100000", ""])
    def test_a_release_id_is_an_id_and_never_a_path(self, tmp_path: Path, release_id: str) -> None:
        path = raw_bundle(
            tmp_path / "b.tar.gz",
            [("release/a", "x", 0o644)],
            doc={**manifest().to_dict(), "release_id": release_id},
        )

        with pytest.raises(ValidationError, match="not a release id"):
            unpack(path, tmp_path / "releases", sha256=sha256_of(path))

        assert not (tmp_path / "releases").exists() or not list((tmp_path / "releases").iterdir())

    def test_a_release_that_is_there_already_is_never_replaced(self, tmp_path: Path) -> None:
        packed = self.packed(tmp_path)
        releases = tmp_path / "other" / "releases"
        (releases / RELEASE).mkdir(parents=True)
        (releases / RELEASE / "mine").write_text("kept")

        with pytest.raises(ValidationError, match="exists already"):
            unpack(packed.path, releases, sha256=packed.sha256)

        assert (releases / RELEASE / "mine").read_text() == "kept"

    def attack(self, tmp_path: Path, members: list[tuple[str, bytes | str, int]]) -> Path:
        return raw_bundle(tmp_path / "evil.tar.gz", members)

    @pytest.mark.parametrize(
        "members",
        [
            [("release/../../escaped", "x", 0o644)],
            [("/etc/escaped", "x", 0o644)],
            [("release/a", "-> /etc", 0o777), ("release/a/passwd", "x", 0o644)],
            [("release/link", "-> ../../outside", 0o777)],
            [("release/link", "-> /etc/passwd", 0o777)],
        ],
        ids=["dotdot", "absolute", "through a link", "link out", "absolute link"],
    )
    def test_a_member_that_would_leave_the_release_leaves_nothing(
        self, tmp_path: Path, members: list[tuple[str, bytes | str, int]]
    ) -> None:
        path = self.attack(tmp_path, members)
        releases = tmp_path / "other" / "releases"

        with pytest.raises(SourceError):
            unpack(path, releases, sha256=sha256_of(path))

        assert not (releases / RELEASE).exists()
        assert not any(entry.name.startswith(".bundle-") for entry in releases.iterdir())
        assert not (tmp_path / "escaped").exists() and not (tmp_path / "other" / "escaped").exists()

    def test_a_device_file_is_refused(self, tmp_path: Path) -> None:
        document = json.dumps(manifest().to_dict()).encode()
        path = tmp_path / "dev.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            head = tarfile.TarInfo(MANIFEST_NAME)
            head.size = len(document)
            archive.addfile(head, io.BytesIO(document))
            device = tarfile.TarInfo("release/null")
            device.type = tarfile.CHRTYPE
            archive.addfile(device)

        with pytest.raises(SourceError):
            unpack(path, tmp_path / "releases", sha256=sha256_of(path))

    def test_setuid_and_group_write_are_dropped_on_the_way_in(self, tmp_path: Path) -> None:
        path = self.attack(tmp_path, [("release/su", "x", 0o4777), ("release/g", "x", 0o664)])

        _manifest, destination = unpack(path, tmp_path / "releases", sha256=sha256_of(path))

        assert stat.S_IMODE((destination / "su").stat().st_mode) == 0o755
        assert stat.S_IMODE((destination / "g").stat().st_mode) == 0o644

    def test_the_caps_stop_a_bomb(self, tmp_path: Path) -> None:
        path = self.attack(
            tmp_path, [(f"release/f{index}", "x" * 10, 0o644) for index in range(10)]
        )

        with pytest.raises(SourceError):
            unpack(path, tmp_path / "releases", sha256=sha256_of(path), max_entries=5)
        with pytest.raises(SourceError):
            unpack(path, tmp_path / "releases", sha256=sha256_of(path), max_bytes=50)

        assert not (tmp_path / "releases" / RELEASE).exists()

    def test_a_bundle_with_no_release_in_it(self, tmp_path: Path) -> None:
        path = raw_bundle(tmp_path / "empty.tar.gz", [("elsewhere/a", "x", 0o644)])

        with pytest.raises(ValidationError, match="holds no release"):
            unpack(path, tmp_path / "releases", sha256=sha256_of(path))

        assert not (tmp_path / "releases" / RELEASE).exists()
