"""
Deploying from an archive somebody uploads (checklist A-16): the store, the extraction and the source.

What is pinned: that an archive is sent in chunks that resume at the offset the server holds, is
checked against its digest and its owner, and is extracted once into a directory whose address
(``upload:<id>``) is a source like any other; that an archive is untrusted (a path out of the
project, a link that points out, nothing in it) and leaves nothing behind when it is refused; that
the directory is kept while an application names it and removed when none does; and that the
allowed origins treat an upload as a kind of its own.
"""

from __future__ import annotations

import io
import os
import tarfile
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import paths
from noust.core.ens.origins import OriginNotAllowed, check_origin
from noust.core.exceptions import NoustError, SourceError, ValidationError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.deployers import lifecycle, source_change
from noust.managers.database.uploads import UploadOffsetError, UploadTooLargeError
from noust.managers.source_manager import _GIT_SAFE_CONFIG
from noust.managers.source_uploads import SOURCES, TREE_TTL, SourceUploads
from noust.validators.source import (
    check_upload_id,
    is_upload_tree,
    upload_source,
    upload_source_id,
    upload_tree,
    validate_source,
)

OWNER = "user:ana"
OTHER = "user:bo"
DOMAIN = "shop.example.com"


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setattr(paths, "state_dir", lambda: root)
    return root


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def uploads(state: Path, store: NoustStore) -> SourceUploads:
    return SourceUploads(store=store)


class Limits:
    """A configuration that answers one key and leaves the others to their defaults."""

    def __init__(self, **values: Any) -> None:
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def make_zip(files: dict[str, bytes], *, top: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(f"{top}/{name}" if top else name, content)
    return buffer.getvalue()


def make_tgz(members: list[tuple[str, bytes | str]]) -> bytes:
    """Members are (name, content), or (name, "-> target") for a symbolic link."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if isinstance(content, str) and content.startswith("-> "):
                info.type = tarfile.SYMTYPE
                info.linkname = content[3:]
                archive.addfile(info)
            else:
                data = content.encode() if isinstance(content, str) else content
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def send(manager: SourceUploads, name: str, data: bytes, *, owner: str = OWNER, chunk: int = 4096):
    """Start an upload and send it all; returns the session."""
    session = manager.start(name, len(data), owner=owner)
    for offset in range(0, len(data), chunk):
        manager.sessions.append(session.id, offset, data[offset : offset + chunk], owner=owner)
    return session


PROJECT = {"package.json": b'{"name": "shop"}', "src/index.js": b"console.log(1)"}


class TestSendingAnArchive:
    def test_it_is_sent_in_chunks_and_extracted_once(self, uploads: SourceUploads) -> None:
        data = make_zip(PROJECT)
        session = send(uploads, "shop.zip", data, chunk=64)

        uploaded = uploads.finish(session.id, owner=OWNER)

        tree = upload_tree(session.id)
        assert uploaded.source == f"upload:{session.id}"
        assert (tree / "package.json").read_bytes() == PROJECT["package.json"]
        assert (tree / "src" / "index.js").read_bytes() == PROJECT["src/index.js"]
        assert (uploaded.files, uploaded.bytes) == (2, sum(len(v) for v in PROJECT.values()))
        assert (tree.stat().st_mode & 0o777) == 0o700
        # The archive is thrown away once extracted: the project is what is kept.
        assert not (paths.uploads_dir() / ".pending" / session.id).exists()

    def test_one_directory_around_everything_is_taken_off(self, uploads: SourceUploads) -> None:
        session = send(uploads, "shop-1.2.zip", make_zip(PROJECT, top="shop-1.2"))

        uploads.finish(session.id, owner=OWNER)

        assert (upload_tree(session.id) / "package.json").is_file()

    @pytest.mark.parametrize("name", ["shop.tar.gz", "shop.tgz"])
    def test_a_tarball_is_read_like_a_zip(self, uploads: SourceUploads, name: str) -> None:
        session = send(uploads, name, make_tgz([("package.json", b"{}"), ("a/b.txt", b"x")]))

        uploaded = uploads.finish(session.id, owner=OWNER)

        assert uploaded.files == 2 and (upload_tree(session.id) / "a" / "b.txt").is_file()

    def test_the_source_it_prints_is_a_local_directory_everything_already_reads(
        self, uploads: SourceUploads
    ) -> None:
        session = send(uploads, "shop.zip", make_zip(PROJECT))
        uploaded = uploads.finish(session.id, owner=OWNER)

        kind, normalized = validate_source(uploaded.source)

        assert kind == "local" and normalized == str(upload_tree(session.id))
        assert is_upload_tree(normalized)
        # The record is outside the tree, so it is never copied into a release.
        assert not (upload_tree(session.id) / f"{session.id}.json").exists()
        assert uploads.get(session.id) is not None

    def test_a_chunk_at_the_wrong_offset_says_where_to_resume(self, uploads: SourceUploads) -> None:
        data = make_zip(PROJECT)
        session = uploads.start("shop.zip", len(data), owner=OWNER)
        uploads.sessions.append(session.id, 0, data[:50], owner=OWNER)

        with pytest.raises(UploadOffsetError) as caught:
            uploads.sessions.append(session.id, 10, data[10:60], owner=OWNER)

        assert caught.value.received == 50
        uploads.sessions.append(session.id, 50, data[50:], owner=OWNER)
        assert uploads.finish(session.id, owner=OWNER).files == 2

    def test_an_upload_with_bytes_missing_is_not_finished(self, uploads: SourceUploads) -> None:
        data = make_zip(PROJECT)
        session = uploads.start("shop.zip", len(data), owner=OWNER)
        uploads.sessions.append(session.id, 0, data[:50], owner=OWNER)

        with pytest.raises(UploadOffsetError):
            uploads.finish(session.id, owner=OWNER)

        assert not upload_tree(session.id).exists()

    def test_a_digest_that_does_not_match_discards_the_data(self, uploads: SourceUploads) -> None:
        data = make_zip(PROJECT)
        session = uploads.start("shop.zip", len(data), owner=OWNER, sha256="0" * 64)
        uploads.sessions.append(session.id, 0, data, owner=OWNER)

        with pytest.raises(ValidationError, match="does not match the SHA-256"):
            uploads.finish(session.id, owner=OWNER)

        assert not upload_tree(session.id).exists()
        assert not (paths.uploads_dir() / ".pending" / session.id).exists()

    def test_only_the_one_who_started_it_can_continue_it(self, uploads: SourceUploads) -> None:
        data = make_zip(PROJECT)
        session = uploads.start("shop.zip", len(data), owner=OWNER)

        with pytest.raises(ValidationError, match="no such upload"):
            uploads.sessions.append(session.id, 0, data, owner=OTHER)
        with pytest.raises(ValidationError, match="no such upload"):
            uploads.finish(session.id, owner=OTHER)


class TestWhatIsRefusedBeforeABytePasses:
    @pytest.mark.parametrize("name", ["shop.txt", "shop", "notes.md"])
    def test_a_name_that_is_not_an_archive(self, uploads: SourceUploads, name: str) -> None:
        with pytest.raises(ValidationError, match="does not look like a code archive") as caught:
            uploads.start(name, 100, owner=OWNER)

        assert ".zip" in str(caught.value.details) and "An archive's name" in str(
            caught.value.details
        )

    def test_a_compressed_file_that_is_not_a_tarball(self, uploads: SourceUploads) -> None:
        with pytest.raises(SourceError, match="Unsupported archive format"):
            uploads.start("database.gz", 100, owner=OWNER)

    @pytest.mark.parametrize("name", ["../shop.zip", "a/b.zip", "", "x\n.zip"])
    def test_a_name_that_is_a_path(self, uploads: SourceUploads, name: str) -> None:
        with pytest.raises((ValidationError, SourceError)):
            uploads.start(name, 100, owner=OWNER)

    def test_a_size_past_the_limit_the_server_is_configured_with(
        self, state: Path, store: NoustStore
    ) -> None:
        from noust.managers.database.uploads import UploadStore

        sessions = UploadStore(
            state / "uploads" / ".pending",
            config=Limits(**{SOURCES.limit_key: 1000}),  # type: ignore[arg-type]
            profile=SOURCES,
        )
        manager = SourceUploads(store=store, sessions=sessions)

        with pytest.raises(UploadTooLargeError, match="limit is 1000") as caught:
            manager.start("shop.zip", 1001, owner=OWNER)

        assert "uploads.source_max_bytes" in str(caught.value.details)

    def test_an_empty_upload(self, uploads: SourceUploads) -> None:
        with pytest.raises(ValidationError, match="empty"):
            uploads.start("shop.zip", 0, owner=OWNER)


class TestAnArchiveIsUntrustedInput:
    def test_a_member_that_climbs_out_of_the_project_leaves_nothing(
        self, uploads: SourceUploads
    ) -> None:
        data = make_zip({"../escaped.txt": b"x", "package.json": b"{}"})
        session = send(uploads, "shop.zip", data)

        with pytest.raises(SourceError):
            uploads.finish(session.id, owner=OWNER)

        assert not upload_tree(session.id).exists()
        assert not (paths.uploads_dir() / "escaped.txt").exists()
        assert not (paths.uploads_dir().parent / "escaped.txt").exists()
        assert not (paths.uploads_dir() / ".pending" / session.id).exists()
        assert uploads.items() == []

    def test_a_link_that_points_out_of_the_project(self, uploads: SourceUploads) -> None:
        data = make_tgz([("package.json", b"{}"), ("etc", "-> /etc"), ("etc/passwd", b"root::0")])
        session = send(uploads, "shop.tgz", data)

        with pytest.raises(SourceError):
            uploads.finish(session.id, owner=OWNER)

        assert not upload_tree(session.id).exists()

    def test_an_archive_with_no_files_in_it(self, uploads: SourceUploads) -> None:
        session = send(uploads, "empty.zip", make_zip({"empty/": b""}))

        with pytest.raises(SourceError, match="holds no files"):
            uploads.finish(session.id, owner=OWNER)

        assert not upload_tree(session.id).exists()

    def test_something_that_is_not_an_archive_at_all(self, uploads: SourceUploads) -> None:
        session = send(uploads, "shop.zip", b"this is not a zip file" * 10)

        with pytest.raises(SourceError, match="Cannot read zip archive"):
            uploads.finish(session.id, owner=OWNER)

        assert not upload_tree(session.id).exists()


class TestAddingAFileOnTheServer:
    def test_it_goes_through_the_same_session_and_extraction(
        self, uploads: SourceUploads, tmp_path: Path
    ) -> None:
        archive = tmp_path / "shop.zip"
        archive.write_bytes(make_zip(PROJECT))

        uploaded = uploads.add_file(archive, owner=OWNER)

        assert uploaded.file_name == "shop.zip" and uploaded.files == 2
        assert (upload_tree(uploaded.id) / "package.json").is_file()
        assert uploads.sessions.root.exists() and not list(uploads.sessions.root.iterdir())

    def test_a_link_or_a_missing_file_is_not_an_archive(
        self, uploads: SourceUploads, tmp_path: Path
    ) -> None:
        real = tmp_path / "shop.zip"
        real.write_bytes(make_zip(PROJECT))
        link = tmp_path / "link.zip"
        link.symlink_to(real)

        with pytest.raises(SourceError, match="is not a file"):
            uploads.add_file(link, owner=OWNER)
        with pytest.raises(SourceError, match="is not a file"):
            uploads.add_file(tmp_path / "nope.zip", owner=OWNER)

    def test_an_unsafe_archive_is_refused_the_same_way(
        self, uploads: SourceUploads, tmp_path: Path
    ) -> None:
        archive = tmp_path / "evil.zip"
        archive.write_bytes(make_zip({"../escaped.txt": b"x"}))

        with pytest.raises(SourceError):
            uploads.add_file(archive, owner=OWNER)

        assert uploads.items() == [] and not (tmp_path / "escaped.txt").exists()


class TestTheIdentifierNamesNoPath:
    @pytest.mark.parametrize(
        "text",
        ["upload:../../etc", "upload:", "upload:ABC", "upload:" + "a" * 31, "upload:" + "g" * 32],
    )
    def test_only_what_noust_issued_is_an_upload(self, text: str) -> None:
        with pytest.raises(SourceError, match="Not an upload"):
            upload_source_id(text)

    def test_an_identifier_alone(self) -> None:
        assert check_upload_id("a" * 32) == "a" * 32
        with pytest.raises(SourceError):
            check_upload_id("..")

    def test_a_source_for_an_upload_that_is_not_there(self, state: Path) -> None:
        with pytest.raises(SourceError, match="There is no upload"):
            validate_source(upload_source("a" * 32))

    def test_the_manager_refuses_an_identifier_that_is_a_path(self, uploads: SourceUploads) -> None:
        with pytest.raises(SourceError):
            uploads.get("..")
        with pytest.raises(SourceError):
            uploads.forget("../..")

    def test_a_path_next_to_the_uploads_is_not_an_upload_tree(self, state: Path) -> None:
        assert not is_upload_tree(paths.uploads_dir() / ("a" * 32) / "src")
        assert not is_upload_tree(state)
        assert not is_upload_tree(paths.uploads_dir() / "short")


class TestWhoUsesIt:
    def deploy(self, store: NoustStore, source: str, domain: str = DOMAIN) -> App:
        return store.create_app(
            App(domain=domain, app_type="nodejs", source=source, app_path=f"/tmp/{domain}")
        )

    def test_a_tree_an_application_names_is_in_use_whichever_way_it_wrote_it(
        self, uploads: SourceUploads, store: NoustStore
    ) -> None:
        first = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        second = uploads.finish(send(uploads, "b.zip", make_zip(PROJECT)).id, owner=OWNER)
        self.deploy(store, first.source)
        self.deploy(store, str(upload_tree(second.id)), "other.example.com")

        in_use = {item.id: item.in_use for item in uploads.items()}

        assert in_use == {first.id: True, second.id: True}

    def test_it_is_removed_when_nothing_names_it_and_kept_while_something_does(
        self, uploads: SourceUploads, store: NoustStore
    ) -> None:
        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        self.deploy(store, uploaded.source)

        assert uploads.release(uploaded.source) is False
        assert upload_tree(uploaded.id).is_dir()

        store.delete_app(DOMAIN)
        assert uploads.release(uploaded.source) is True
        assert not upload_tree(uploaded.id).exists()
        assert not (paths.uploads_dir() / f"{uploaded.id}.json").exists()

    def test_a_source_that_is_not_an_upload_is_left_alone(self, uploads: SourceUploads) -> None:
        assert uploads.release("https://github.com/acme/shop.git") is False
        assert uploads.release(None) is False
        assert uploads.release("/srv/code") is False

    def test_deleting_the_application_takes_its_archive_with_it(
        self, uploads: SourceUploads, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        monkeypatch.setattr("noust.managers.source_uploads.get_store", lambda: store)

        lifecycle._forget_upload(uploaded.source, lambda message, exc: pytest.fail(message))

        assert not upload_tree(uploaded.id).exists()

    def test_pruning_removes_what_was_never_used_after_a_week(
        self, uploads: SourceUploads, store: NoustStore
    ) -> None:
        stale = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        fresh = uploads.finish(send(uploads, "b.zip", make_zip(PROJECT)).id, owner=OWNER)
        used = uploads.finish(send(uploads, "c.zip", make_zip(PROJECT)).id, owner=OWNER)
        self.deploy(store, used.source)
        old = time.time() - TREE_TTL - 60
        for upload in (stale, used):
            os.utime(paths.uploads_dir() / f"{upload.id}.json", (old, old))

        removed = uploads.prune()

        assert removed == [stale.id]
        assert upload_tree(fresh.id).is_dir() and upload_tree(used.id).is_dir()

    def test_pruning_sweeps_sessions_nobody_finished(self, uploads: SourceUploads) -> None:
        session = uploads.start("a.zip", 100, owner=OWNER)
        directory = paths.uploads_dir() / ".pending" / session.id
        old = time.time() - 3 * 24 * 3600
        for entry in directory.iterdir():
            os.utime(entry, (old, old))

        uploads.prune()

        assert not directory.exists()


class TestAsASource:
    def test_the_allowed_origins_treat_an_upload_as_a_kind_of_its_own(
        self, uploads: SourceUploads
    ) -> None:
        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        tree = str(upload_tree(uploaded.id))

        with pytest.raises(OriginNotAllowed):
            check_origin(tree, ["github.com/acme"], kind="upload")
        with pytest.raises(OriginNotAllowed):
            check_origin(tree, ["local"], kind="upload")
        check_origin(tree, ["upload"], kind="upload")
        check_origin(tree, [], kind="upload")

    def test_the_fetch_classifies_a_tree_inside_the_uploads_as_an_upload(
        self, uploads: SourceUploads, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import source_manager

        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        monkeypatch.setattr(
            "noust.core.ens.origins.allowed_sources", lambda *_a, **_k: ["github.com/acme"]
        )

        with pytest.raises(OriginNotAllowed):
            source_manager._validate_source_keeping_credentials(uploaded.source)

        monkeypatch.setattr(
            "noust.core.ens.origins.allowed_sources",
            lambda *_a, **_k: ["github.com/acme", "upload"],
        )
        assert source_manager._validate_source_keeping_credentials(uploaded.source)[0] == "local"

    def test_a_release_is_staged_from_the_tree(
        self, uploads: SourceUploads, tmp_path: Path
    ) -> None:
        from noust.core.fs import RealFileSystem
        from noust.core.logger import Logger
        from noust.deployers.helpers.release_build import stage_release
        from noust.deployers.releases import ReleaseManager
        from noust.managers.source_manager import SourceManager

        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        app_root = tmp_path / "app"
        app_root.mkdir()

        staged = stage_release(
            uploaded.source,
            None,
            releases=ReleaseManager(app_root, fs=RealFileSystem()),
            source_manager=SourceManager(),
            logger=Logger(),
        )

        assert (staged.path / "package.json").read_bytes() == PROJECT["package.json"]
        assert staged.commit is None


DOCKERFILE = 'FROM node:22-alpine\nRUN apk add --no-cache git\nCMD ["node", "-v"]\n'


class TestAPastedDockerfile:
    def test_it_becomes_a_project_of_one_file(self, uploads: SourceUploads) -> None:
        uploaded = uploads.add_dockerfile(DOCKERFILE, owner=OWNER)

        tree = upload_tree(uploaded.id)
        assert sorted(entry.name for entry in tree.iterdir()) == ["Dockerfile"]
        assert (tree / "Dockerfile").read_text() == DOCKERFILE
        assert (uploaded.file_name, uploaded.files, uploaded.bytes) == (
            "Dockerfile",
            1,
            len(DOCKERFILE.encode()),
        )
        assert uploaded.source == f"upload:{uploaded.id}" and uploaded.owner == OWNER
        assert validate_source(uploaded.source) == ("local", str(tree))
        assert (tree.stat().st_mode & 0o777) == 0o700
        assert uploads.get(uploaded.id) is not None

    def test_the_container_deployer_finds_it_in_the_directory_its_source_names(
        self, uploads: SourceUploads
    ) -> None:
        from noust.deployers.inspect import inspect_source

        uploaded = uploads.add_dockerfile(DOCKERFILE, owner=OWNER, name="Containerfile")

        inspection = inspect_source(uploaded.source)

        assert inspection.app_type == "container"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "is empty"),
            ("   \n\n", "is empty"),
            ("RUN echo hi\n", "No FROM instruction"),
            ("# FROM scratch is commented out\n", "No FROM instruction"),
            ("FROM x\x00y\n", "holds a NUL byte"),
            ("FROM scratch\n" + "#" * (256 * 1024), "too large"),
        ],
    )
    def test_text_that_is_not_a_dockerfile(
        self, uploads: SourceUploads, text: str, message: str
    ) -> None:
        with pytest.raises(ValidationError, match=message) as caught:
            uploads.add_dockerfile(text, owner=OWNER)

        assert caught.value.field == "content"
        assert uploads.items() == []

    def test_stages_and_arguments_before_the_first_from_are_fine(
        self, uploads: SourceUploads
    ) -> None:
        text = "# syntax=docker/dockerfile:1\nARG V=22\nFROM node:${V}\n"

        assert uploads.add_dockerfile(text, owner=OWNER).files == 1

    def test_only_the_names_of_a_dockerfile(self, uploads: SourceUploads) -> None:
        with pytest.raises(ValidationError, match="not the name of a Dockerfile"):
            uploads.add_dockerfile(DOCKERFILE, owner=OWNER, name="../escape")

        assert uploads.items() == []

    def test_a_file_named_dockerfile_is_taken_as_one_and_not_extracted(
        self, uploads: SourceUploads, tmp_path: Path
    ) -> None:
        path = tmp_path / "Dockerfile"
        path.write_text(DOCKERFILE)

        uploaded = uploads.add_file(path, owner=OWNER)

        assert (uploaded.file_name, uploaded.files) == ("Dockerfile", 1)

    def test_it_is_audited_like_any_other_upload(
        self, uploads: SourceUploads, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import audit

        events: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(audit, "record", lambda event, **kw: events.append((event, kw)))

        uploaded = uploads.add_dockerfile(DOCKERFILE, owner=OWNER)

        [(event, details)] = events
        assert event == "source.upload" and details["target"] == f"source:upload/{uploaded.id}"
        assert details["details"]["file"] == "Dockerfile"
        assert len(details["details"]["sha256"]) == 64

    def test_deleting_the_application_takes_it_with_it(
        self, uploads: SourceUploads, store: NoustStore
    ) -> None:
        uploaded = uploads.add_dockerfile(DOCKERFILE, owner=OWNER)
        store.create_app(
            App(domain=DOMAIN, app_type="container", source=uploaded.source, app_path="/tmp/x")
        )

        assert uploads.release(uploaded.source) is False
        store.delete_app(DOMAIN)
        assert uploads.release(uploaded.source) is True
        assert not upload_tree(uploaded.id).exists()


class TestInspectingAnUpload:
    def test_the_wizard_can_say_what_an_uploaded_project_is(self, uploads: SourceUploads) -> None:
        from noust.deployers.inspect import inspect_source

        files = {
            "package.json": b'{"name": "shop", "scripts": {"build": "vite build"}, '
            b'"dependencies": {"express": "4"}}',
            "package-lock.json": b"{}",
            "index.js": b"require('express')",
        }
        uploaded = uploads.finish(send(uploads, "shop.zip", make_zip(files)).id, owner=OWNER)

        inspection = inspect_source(uploaded.source)

        assert inspection.app_type == "nodejs"
        assert inspection.package_manager == "npm"


class TestChangingAnApplicationsSourceToAnUpload:
    NEW = "https://git.example.org/team/shop.git"
    HEAD = "4f1c2b9d8e7a6f5c4b3a29181716151413121110"

    def app(self, store: NoustStore, source: str) -> None:
        store.create_app(
            App(
                domain=DOMAIN,
                app_type="nodejs",
                source=source,
                branch=None,
                app_path="/tmp/x",
                layout="releases",
            )
        )

    def test_the_plan_calls_it_an_upload_and_records_the_source_by_its_identifier(
        self, uploads: SourceUploads, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(source_change, "get_store", lambda: store)
        self.app(store, "https://github.com/acme/shop.git")
        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)

        plan = source_change.plan_change(DOMAIN, str(upload_tree(uploaded.id)), check=False)

        assert (plan.target, plan.target_kind, plan.target_branch) == (
            uploaded.source,
            "upload",
            None,
        )

    def test_the_allowed_origins_apply_to_the_change_too(
        self, uploads: SourceUploads, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(source_change, "get_store", lambda: store)
        self.app(store, "https://github.com/acme/shop.git")
        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        monkeypatch.setattr(
            "noust.core.ens.origins.allowed_sources", lambda *_a, **_k: ["github.com/acme"]
        )

        with pytest.raises(NoustError, match="not allowed"):
            source_change.plan_change(DOMAIN, uploaded.source, check=False)

    def test_leaving_an_upload_for_a_repository_removes_the_archive(
        self,
        uploads: SourceUploads,
        store: NoustStore,
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(source_change, "get_store", lambda: store)
        monkeypatch.setattr("noust.managers.source_uploads.get_store", lambda: store)
        uploaded = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        self.app(store, uploaded.source)
        runner.script(
            [
                "git",
                *_GIT_SAFE_CONFIG,
                "ls-remote",
                "--exit-code",
                "--",
                self.NEW,
                "refs/heads/main",
            ],
            stdout=f"{self.HEAD}\trefs/heads/main\n",
        )
        set_runner(runner)

        plan = source_change.change_source(DOMAIN, self.NEW, "main")

        assert plan.current_kind == "upload" and plan.target_kind == "git"
        row = store.get_app(DOMAIN)
        assert row is not None and row.source == self.NEW
        assert not upload_tree(uploaded.id).exists()
        set_runner(None)

    def test_moving_to_another_upload_removes_the_first_and_keeps_the_second(
        self, uploads: SourceUploads, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(source_change, "get_store", lambda: store)
        monkeypatch.setattr("noust.managers.source_uploads.get_store", lambda: store)
        first = uploads.finish(send(uploads, "a.zip", make_zip(PROJECT)).id, owner=OWNER)
        second = uploads.finish(send(uploads, "b.zip", make_zip(PROJECT)).id, owner=OWNER)
        self.app(store, first.source)

        source_change.change_source(DOMAIN, second.source, check=False)

        row = store.get_app(DOMAIN)
        assert row is not None and row.source == second.source
        assert not upload_tree(first.id).exists() and upload_tree(second.id).is_dir()
