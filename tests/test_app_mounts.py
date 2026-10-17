# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Files and server paths mounted into an application (checklist B-53).

Pinned here:

- a file Noust keeps lives in ``shared/`` on the release layout and is linked into
  every release like a persistent path, with the mode the operator named and the
  application's account as owner; its content is the file, never a column;
- nothing is written through a link, an environment file is not a file mount, and
  the file is text of a bounded size;
- a path of the server bound into a container is read-only by default, refuses the
  server's own state and the Docker socket, and is root-equivalent;
- the compose file of a container application binds what is mounted;
- the pending changes say a restart is needed when a mounted file changes.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import app_mount_store, audit
from noust.core.container_store import ContainerSettings
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import RealFileSystem
from noust.core.store import App, NoustStore
from noust.deployers import pending_changes
from noust.deployers.helpers.container_compose import ContainerPlan, build_document
from noust.deployers.releases import ReleaseManager
from noust.managers import app_mounts
from noust.managers.app_mounts import MountManager, clean_source, clean_target
from noust.managers.service_manager import ResourceLimits

DOMAIN = "shop.example.com"
RELEASE = "20260925-120000-aaaaaaa"


class OwnerFs(RealFileSystem):
    """The real filesystem, except that handing a file to an account is recorded."""

    def __init__(self) -> None:
        self.owned: list[tuple[Path, str, str, int]] = []

    def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
        self.owned.append((path, user, group, mode))
        path.chmod(mode)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "apps" / "shop-example-com"
    (base / "releases" / RELEASE).mkdir(parents=True)
    (base / "current").symlink_to(f"releases/{RELEASE}")
    (base / "shared").mkdir()
    return base


def add_app(store: NoustStore, root: Path, **fields: Any) -> App:
    values: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "nodejs",
        "app_path": str(root),
        "layout": "releases",
        "identity": "noust-app-shop",
    }
    values.update(fields)
    store.create_app(App(**values))
    row = store.get_app(DOMAIN)
    assert row is not None
    return row


@pytest.fixture
def app(store: NoustStore, root: Path) -> App:
    return add_app(store, root)


@pytest.fixture
def fs() -> OwnerFs:
    return OwnerFs()


@pytest.fixture
def manager(store: NoustStore, fs: OwnerFs) -> MountManager:
    return MountManager(store=store, fs=fs)


# ---------------------------------------------------------------------------
# A file Noust keeps
# ---------------------------------------------------------------------------


def test_a_file_lives_in_shared_and_is_linked_into_the_release_that_serves(
    manager: MountManager, app: App, root: Path
) -> None:
    view = manager.put_file(DOMAIN, "config/production.json", '{"a": 1}\n', actor="ana")

    assert (root / "shared" / "config" / "production.json").read_text() == '{"a": 1}\n'
    link = root / "releases" / RELEASE / "config" / "production.json"
    assert link.is_symlink()
    assert link.read_text() == '{"a": 1}\n'
    assert view.exists and view.size == 9 and view.record.mode == 0o600


def test_the_file_takes_the_mode_asked_and_the_applications_account(
    manager: MountManager, app: App, root: Path, fs: OwnerFs
) -> None:
    manager.put_file(DOMAIN, "config/app.ini", "x=1\n", mode=0o640)

    path = root / "shared" / "config" / "app.ini"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert fs.owned == [(path, "noust-app-shop", "noust-app-shop", 0o640)]


def test_the_default_mode_is_the_applications_alone(
    manager: MountManager, app: App, root: Path
) -> None:
    manager.put_file(DOMAIN, "secret.pem", "-----BEGIN-----\n")

    assert stat.S_IMODE((root / "shared" / "secret.pem").stat().st_mode) == 0o600


def test_changing_the_content_rewrites_the_same_file(
    manager: MountManager, app: App, root: Path, store: NoustStore
) -> None:
    manager.put_file(DOMAIN, "a.conf", "one")
    manager.put_file(DOMAIN, "a.conf", "two")

    assert (root / "shared" / "a.conf").read_text() == "two"
    assert [record.target for record in app_mount_store.list_mounts(DOMAIN, store=store)] == [
        "a.conf"
    ]


def test_a_new_release_is_linked_to_the_files_before_it_starts(
    manager: MountManager, app: App, root: Path
) -> None:
    manager.put_file(DOMAIN, "config/app.ini", "x=1\n")
    releases = ReleaseManager(root)
    new = root / "releases" / "20260926-120000-bbbbbbb"
    new.mkdir()

    releases.link_shared(new, manager.host_targets(DOMAIN))

    assert (new / "config" / "app.ini").is_symlink()
    assert (new / "config" / "app.ini").read_text() == "x=1\n"


def test_the_deployer_links_what_is_mounted_beside_the_persistent_paths(
    manager: MountManager, app: App, store: NoustStore
) -> None:
    manager.put_file(DOMAIN, "config/app.ini", "x=1\n")

    assert manager.host_targets(DOMAIN) == ["config/app.ini"]


def test_in_place_the_file_is_written_where_the_application_reads_it(
    store: NoustStore, tmp_path: Path, fs: OwnerFs
) -> None:
    place = tmp_path / "apps" / "old"
    place.mkdir(parents=True)
    add_app(store, place, layout="inplace")

    MountManager(store=store, fs=fs).put_file(DOMAIN, "config/app.ini", "x=1\n")

    assert (place / "config" / "app.ini").read_text() == "x=1\n"


def test_the_content_is_read_back_for_editing_and_the_read_is_on_record(
    manager: MountManager, app: App
) -> None:
    manager.put_file(DOMAIN, "a.conf", "token=abc\n")

    text = manager.read_file(DOMAIN, "a.conf", actor="ana")

    assert text == "token=abc\n"
    [event] = [
        e["details"]
        for e in audit.get_log().read(action="apps.mounts")
        if e["details"]["action"] == "read"
    ]
    assert event["target"] == "a.conf" and "token" not in str(event)


def test_the_listing_never_carries_the_content(manager: MountManager, app: App) -> None:
    manager.put_file(DOMAIN, "a.conf", "token=abc\n")

    [view] = manager.list_mounts(DOMAIN)

    assert "token" not in str(view.to_dict())
    assert view.to_dict()["mode"] == "600" and view.to_dict()["exists"] is True


def test_removing_deletes_the_file_and_the_link_the_release_has(
    manager: MountManager, app: App, root: Path
) -> None:
    manager.put_file(DOMAIN, "config/a.conf", "x")
    link = root / "releases" / RELEASE / "config" / "a.conf"
    assert link.is_symlink()

    manager.remove(DOMAIN, "config/a.conf", actor="ana")

    assert not (root / "shared" / "config" / "a.conf").exists()
    assert not link.is_symlink()
    assert manager.list_mounts(DOMAIN) == []


def test_removing_what_is_not_mounted_is_refused(manager: MountManager, app: App) -> None:
    with pytest.raises(ValidationError, match="nothing mounted"):
        manager.remove(DOMAIN, "ghost.conf")


def test_a_row_goes_with_the_application_and_survives_a_redeploy(
    manager: MountManager, app: App, store: NoustStore
) -> None:
    manager.put_file(DOMAIN, "a.conf", "x")

    app.status = "running"
    store.update_app(app)
    assert len(manager.list_mounts(DOMAIN)) == 1

    store.delete_app(DOMAIN)
    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_mounts")
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# What is refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["", "/etc/passwd", "../x", "a/../../x", "a\0b", "a\nb"])
def test_a_target_outside_the_application_is_refused(
    manager: MountManager, app: App, target: str
) -> None:
    with pytest.raises(ValidationError):
        manager.put_file(DOMAIN, target, "x")


@pytest.mark.parametrize("target", [".env", "shared/.env", "config/.env.production"])
def test_the_environment_is_not_a_file_mount(
    manager: MountManager, app: App, root: Path, target: str
) -> None:
    with pytest.raises(ValidationError, match="Environment") as caught:
        manager.put_file(DOMAIN, target, "SECRET=1\n")

    assert caught.value.field == "target"
    assert not (root / "shared" / ".env").exists()


def test_a_file_that_is_not_text_or_is_too_large_is_refused(
    manager: MountManager, app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValidationError, match="text"):
        manager.put_file(DOMAIN, "a.bin", "a\0b")
    monkeypatch.setattr(app_mounts, "MAX_FILE_BYTES", 4)
    with pytest.raises(ValidationError, match="up to"):
        manager.put_file(DOMAIN, "a.txt", "12345")


@pytest.mark.parametrize("mode", [0, 0o200, 0o1000, 0o7777])
def test_a_mode_a_file_cannot_have_is_refused(manager: MountManager, app: App, mode: int) -> None:
    with pytest.raises(ValidationError) as caught:
        manager.put_file(DOMAIN, "a.conf", "x", mode=mode)
    assert caught.value.field == "mode"


def test_nothing_is_written_through_a_link(
    manager: MountManager, app: App, root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "shared" / "config").symlink_to(outside)

    with pytest.raises(DeploymentError, match="not a plain directory"):
        manager.put_file(DOMAIN, "config/a.conf", "x")

    assert list(outside.iterdir()) == []


def test_the_file_itself_cannot_be_a_link_or_a_directory(
    manager: MountManager, app: App, root: Path
) -> None:
    (root / "shared" / "dir").mkdir()
    (root / "shared" / "link").symlink_to(root / "shared" / "dir")

    with pytest.raises(DeploymentError, match="link or a directory"):
        manager.put_file(DOMAIN, "dir", "x")
    with pytest.raises(DeploymentError, match="link or a directory"):
        manager.put_file(DOMAIN, "link", "x")


def test_a_failed_hand_over_is_an_error_not_a_file_the_application_cannot_read(
    manager: MountManager, app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_mounts, "hand_over_file", lambda *a, **k: False)

    with pytest.raises(NoustError, match="could not be handed"):
        manager.put_file(DOMAIN, "a.conf", "x")


@pytest.mark.parametrize("kind", ["docker-compose", "static"])
def test_what_takes_no_mounts_says_why(
    store: NoustStore, tmp_path: Path, fs: OwnerFs, kind: str
) -> None:
    add_app(store, tmp_path / "x", app_type=kind, is_static=kind == "static")

    with pytest.raises(ValidationError, match="takes no mounts"):
        MountManager(store=store, fs=fs).put_file(DOMAIN, "a.conf", "x")


def test_an_unknown_application_is_refused(manager: MountManager) -> None:
    with pytest.raises(NoustError, match="not found"):
        manager.put_file("nobody.example.com", "a.conf", "x")


# ---------------------------------------------------------------------------
# A container application
# ---------------------------------------------------------------------------


@pytest.fixture
def container(store: NoustStore, tmp_path: Path, fs: OwnerFs) -> MountManager:
    root = tmp_path / "apps" / "box"
    root.mkdir(parents=True)
    add_app(store, root, app_type="container", layout="releases", identity=None)
    return MountManager(store=store, fs=fs)


def test_a_file_for_a_container_is_kept_beside_its_compose_file_and_bound(
    container: MountManager, store: NoustStore, tmp_path: Path
) -> None:
    container.put_file(DOMAIN, "/etc/app/config.json", "{}", mode=0o644)

    binds = container.bind_mounts(DOMAIN)

    source = tmp_path / "apps" / "box" / "mounts" / "etc__app__config.json"
    assert source.read_text() == "{}"
    assert binds == ((str(source), "/etc/app/config.json", True),)
    assert container.host_targets(DOMAIN) == [], "a container binds it, a release does not link it"


@pytest.mark.parametrize("target", ["config.json", "/", "/a/../b", "/a:b", "/a,b"])
def test_a_target_in_a_container_is_an_absolute_path_that_a_mount_can_name(
    container: MountManager, target: str
) -> None:
    with pytest.raises(ValidationError):
        container.put_file(DOMAIN, target, "x")


def test_the_compose_file_binds_what_is_mounted_read_only_unless_asked(
    container: MountManager, store: NoustStore, tmp_path: Path
) -> None:
    source = tmp_path / "assets"
    source.mkdir()
    container.put_path(DOMAIN, str(source), "/srv/assets")
    container.put_path(DOMAIN, str(source), "/srv/scratch", read_only=False)

    plan = ContainerPlan(
        app_name="shop-example-com",
        domain=DOMAIN,
        release_id=RELEASE,
        host_port=3100,
        settings=ContainerSettings(volumes=("/data",)),
        limits=ResourceLimits(),
        bind_mounts=container.bind_mounts(DOMAIN),
    )
    volumes = build_document(plan)["services"]["app"]["volumes"]

    assert volumes[0] == "data:/data"
    assert f"{source}:/srv/assets:ro" in volumes
    assert f"{source}:/srv/scratch:rw" in volumes


def test_an_application_with_no_mounts_has_the_compose_file_it_had(tmp_path: Path) -> None:
    plan = ContainerPlan(
        app_name="shop",
        domain=DOMAIN,
        release_id=RELEASE,
        host_port=3100,
        settings=ContainerSettings(),
        limits=ResourceLimits(),
    )

    assert "volumes" not in build_document(plan)["services"]["app"]


def test_only_a_container_is_given_a_path_of_the_server(
    manager: MountManager, app: App, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError, match="not a container application"):
        manager.put_path(DOMAIN, str(tmp_path), "/srv/x")


@pytest.mark.parametrize(
    "source",
    [
        "/",
        "/etc/noust",
        "/etc/noust/config.yaml",
        "/var/lib/noust/noust.db",
        "/var/backups/noust",
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/root",
        "/root/.ssh",
        "/etc/shadow",
        "/proc",
        "relative/path",
        "/srv/a:b",
        "/srv/../etc",
    ],
)
def test_the_servers_own_state_and_the_keys_to_the_machine_are_not_bound(source: str) -> None:
    with pytest.raises(ValidationError) as caught:
        clean_source(source)
    assert caught.value.field == "source"


def test_a_path_that_is_not_there_is_not_bound(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        clean_source(str(tmp_path / "missing"))


def test_a_path_that_exists_is_bound_without_its_trailing_slash(tmp_path: Path) -> None:
    assert clean_source(str(tmp_path) + "/") == str(tmp_path)


def test_a_writable_path_is_refused_where_the_mounts_control_is_on(
    container: MountManager, tmp_path: Path, real_config: Any
) -> None:
    real_config.set("security.frameworks", ["ens-basica"])

    with pytest.raises(ValidationError, match="mounts_read_only"):
        container.put_path(DOMAIN, str(tmp_path), "/srv/x", read_only=False)
    container.put_path(DOMAIN, str(tmp_path), "/srv/x", read_only=True)


def test_a_path_is_removed_without_touching_what_is_on_the_server(
    container: MountManager, tmp_path: Path
) -> None:
    (tmp_path / "keep.txt").write_text("keep")
    container.put_path(DOMAIN, str(tmp_path), "/srv/x")

    container.remove(DOMAIN, "/srv/x")

    assert (tmp_path / "keep.txt").read_text() == "keep"
    assert container.list_mounts(DOMAIN) == []


def test_target_cleaning_is_the_same_everywhere(app: App) -> None:
    assert clean_target(app, " config/a.json ", "file") == "config/a.json"


# ---------------------------------------------------------------------------
# Pending changes
# ---------------------------------------------------------------------------


def test_a_mounted_file_that_changes_is_a_restart_and_shows_no_content(
    manager: MountManager, app: App, store: NoustStore, root: Path
) -> None:
    manager.put_file(DOMAIN, "a.conf", "token=one\n")
    pending_changes.record_applied(DOMAIN, 1, store=store)

    manager.put_file(DOMAIN, "a.conf", "token=two\n")
    changes = pending_changes.pending(DOMAIN, store=store)

    [item] = [i for i in changes.items if i.area == "mounts"]
    assert (item.key, item.change, item.needs) == ("a.conf", "changed", "restart")
    assert "token" not in str(changes.to_dict())


def test_a_restart_applies_the_mounted_files(
    manager: MountManager, app: App, store: NoustStore
) -> None:
    manager.put_file(DOMAIN, "a.conf", "one")
    pending_changes.record_applied(DOMAIN, 1, store=store)
    manager.put_file(DOMAIN, "a.conf", "two")

    pending_changes.mark_restarted(DOMAIN, store=store)

    assert not any(i.area == "mounts" for i in pending_changes.pending(DOMAIN, store=store).items)


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_command_keeps_a_file_from_standard_input_and_lists_it(
    app: App, store: NoustStore, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_mounts, "get_fs", lambda: OwnerFs())

    put = CliRunner().invoke(
        root_cli,
        ["app", "mount", "file", DOMAIN, "config/a.json", "--mode", "640"],
        input='{"a":1}',
    )
    listed = CliRunner().invoke(root_cli, ["--json", "app", "mount", "list", DOMAIN])
    shown = CliRunner().invoke(root_cli, ["app", "mount", "show", DOMAIN, "config/a.json"])

    assert put.exit_code == 0, put.output
    assert (root / "shared" / "config" / "a.json").read_text() == '{"a":1}'
    assert '"target": "config/a.json"' in listed.output
    assert shown.output == '{"a":1}'


def test_the_command_asks_before_removing(
    app: App, manager: MountManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_mounts, "get_fs", lambda: OwnerFs())
    manager.put_file(DOMAIN, "a.conf", "x")

    no = CliRunner().invoke(root_cli, ["app", "mount", "remove", DOMAIN, "a.conf"], input="n\n")
    yes = CliRunner().invoke(root_cli, ["app", "mount", "remove", DOMAIN, "a.conf", "--yes"])

    assert no.exit_code != 0
    assert yes.exit_code == 0, yes.output
    assert os.path.lexists(manager.file_path(app, "a.conf")) is False
