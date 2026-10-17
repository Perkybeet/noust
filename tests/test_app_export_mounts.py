# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The mounts and the container settings of an application travel in its export (checklist B-80).

Exporting, cloning and copying an application to another server (``noust app export`` and
``import``, ``noust fleet placement copy``) all go through one document. A mounted file may hold a
token, so its text goes in only with the secrets and is ``null`` otherwise; a path of the server is
a name the other server has to have; a container's registry password and build-secret values are
Noust's own credentials and never go in. What cannot travel is said, never dropped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_mount_store
from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.exceptions import ValidationError
from noust.core.store import App, NoustStore
from noust.deployers import app_export, export_runtime
from noust.deployers.app_export import (
    CreateSpec,
    apply_import,
    dumps,
    export_app,
    plan_import,
    validate_document,
)
from noust.managers.app_mounts import MountManager
from tests.test_app_mounts import OwnerFs

DOMAIN = "shop.example.com"
NEW = "store.example.org"
GIT = "https://github.com/acme/shop.git"
#: Token-shaped, built in pieces so no scanner mistakes the test for a leak.
TOKEN = "ghp_" + "abcdefghijklmnop" + "qrstuvwxyz0123456789"
CONFIG = f'{{"token": "{TOKEN}"}}\n'


class NoCron:
    def list_jobs(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_export, "CronManager", NoCron)


@pytest.fixture
def manager(store: NoustStore) -> MountManager:
    return MountManager(store=store, fs=OwnerFs())


@pytest.fixture(autouse=True)
def mounts_by_the_test_fs(
    monkeypatch: pytest.MonkeyPatch, store: NoustStore, manager: MountManager
) -> None:
    """The import makes mounts through a manager whose hand-over does not need an account."""
    monkeypatch.setattr(app_export, "MountManager", lambda log=None: manager)


def add_app(store: NoustStore, tmp_path: Path, **fields: Any) -> App:
    domain = fields.pop("domain", DOMAIN)
    root = tmp_path / "apps" / domain.replace(".", "-")
    root.mkdir(parents=True, exist_ok=True)
    values: dict[str, Any] = {
        "domain": domain,
        "app_type": "nodejs",
        "source": GIT,
        "branch": "main",
        "port": 3001,
        "app_path": str(root),
        "layout": "releases",
        "identity": "noust-app-shop",
    }
    values.update(fields)
    store.create_app(App(**values))
    row = store.get_app(domain)
    assert row is not None
    return row


@pytest.fixture
def shop(store: NoustStore, tmp_path: Path) -> App:
    return add_app(store, tmp_path)


@pytest.fixture
def stack(store: NoustStore, tmp_path: Path) -> App:
    """A container application running a registry image."""
    row = add_app(
        store,
        tmp_path,
        app_type="container",
        source="registry.example.com/acme/shop:1.27",
        layout="inplace",
        port=8080,
    )
    ContainerStore(store).save(
        DOMAIN,
        ContainerSettings(
            method="image",
            image="registry.example.com/acme/shop:1.27",
            container_port=80,
            volumes=("/var/cache/nginx",),
            command=("nginx", "-g", "daemon off;"),
            registry_server="registry.example.com",
            registry_username="deploy",
            registry_secret="container/shop-example-com/registry",
            digest="sha256:" + "a" * 64,
        ),
        actor="test",
    )
    return row


def export_with(secrets: bool = False) -> dict[str, Any]:
    return export_app(DOMAIN, with_secrets=secrets)


def deployed(store: NoustStore, tmp_path: Path, specs: list[CreateSpec]):
    """A deploy that creates the row the real one would, and puts the seeded files in place."""

    def deploy(spec: CreateSpec) -> None:
        specs.append(spec)
        row = add_app(
            store,
            tmp_path,
            domain=spec.domain,
            app_type=spec.app_type,
            source=spec.source,
            layout=spec.layout or "releases",
            port=spec.port or 3002,
        )
        for target, text in spec.seed_files.items():
            path = Path(row.app_path) / "shared" / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    return deploy


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExporting:
    def test_a_file_is_named_and_its_text_is_withheld(
        self, shop: App, manager: MountManager
    ) -> None:
        manager.put_file(DOMAIN, "config/production.json", CONFIG, mode=0o640)

        document = export_with()

        assert document["mounts"] == [
            {
                "kind": "file",
                "target": "config/production.json",
                "mode": "640",
                "secret": True,
                "content": None,
            }
        ]
        assert TOKEN not in dumps(document)

    def test_with_the_secrets_the_text_goes_in(self, shop: App, manager: MountManager) -> None:
        manager.put_file(DOMAIN, "config/production.json", CONFIG, mode=0o640)

        (entry,) = export_with(secrets=True)["mounts"]

        assert entry["content"] == CONFIG and entry["secret"] is True and "note" not in entry

    def test_reading_a_file_for_the_document_is_audited_with_who_asked(
        self, shop: App, manager: MountManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.put_file(DOMAIN, "config/production.json", CONFIG)
        events: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.managers.app_mounts.audit.record", lambda name, **kw: events.append(kw)
        )

        export_app(DOMAIN, with_secrets=True, actor="ana")

        assert [event["details"]["by"] for event in events] == ["ana"]
        assert TOKEN not in json.dumps(events)

    def test_a_path_of_the_server_is_a_name(
        self, store: NoustStore, stack: App, tmp_path: Path, manager: MountManager
    ) -> None:
        data = tmp_path / "shared-assets"
        data.mkdir()
        manager.put_path(DOMAIN, str(data), "/assets", read_only=False)

        document = export_with()

        assert document["mounts"] == [
            {"kind": "path", "target": "/assets", "source": str(data), "read_only": False}
        ]

    def test_a_file_that_is_gone_from_the_disk_travels_as_a_name_and_says_why(
        self, shop: App, manager: MountManager
    ) -> None:
        view = manager.put_file(DOMAIN, "config/production.json", CONFIG)
        Path(view.path).unlink()

        (entry,) = export_with(secrets=True)["mounts"]

        assert entry["content"] is None and "could not be read" in entry["note"]

    def test_a_file_that_does_not_fit_is_named_and_never_cut(
        self, shop: App, manager: MountManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.put_file(DOMAIN, "a.conf", "a" * 400)
        manager.put_file(DOMAIN, "b.conf", "b" * 400)
        monkeypatch.setattr(export_runtime, "CONTENT_BUDGET", 600)

        first, second = export_with(secrets=True)["mounts"]

        assert first["content"] == "a" * 400
        assert second["content"] is None
        assert "do not fit" in second["note"]
        assert f"noust app mount file {DOMAIN} b.conf --from FILE" in second["note"]

    def test_an_application_with_none_exports_an_empty_list_and_no_container(
        self, shop: App
    ) -> None:
        document = export_with()

        assert document["mounts"] == [] and document["container"] is None

    def test_a_stack_takes_no_mounts_so_it_exports_none(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        add_app(store, tmp_path, app_type="docker-compose", layout="inplace")

        assert export_with()["mounts"] == []

    def test_a_container_exports_how_it_runs_without_its_credentials(self, stack: App) -> None:
        document = export_with(secrets=True)

        assert document["container"] == {
            "method": "image",
            "image": "registry.example.com/acme/shop:1.27",
            "dockerfile": None,
            "context": ".",
            "target": None,
            "build_args": {},
            "build_secrets": [],
            "container_port": 80,
            "volumes": ["/var/cache/nginx"],
            "restart_policy": "unless-stopped",
            "registry_server": "registry.example.com",
            "registry_username": "deploy",
            "entrypoint": None,
            "command": ["nginx", "-g", "daemon off;"],
        }
        assert "container/shop-example-com" not in dumps(document)
        assert "sha256:" not in dumps(document)

    def test_the_ids_of_the_build_secrets_go_and_their_values_and_names_do_not(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        add_app(store, tmp_path, app_type="container", layout="inplace", port=8080)
        ContainerStore(store).save(
            DOMAIN,
            ContainerSettings(
                method="dockerfile",
                build_args={"NODE_ENV": "production"},
                build_secrets=("npmrc",),
            ),
            actor="test",
        )

        container = export_with(secrets=True)["container"]

        assert container["build_secrets"] == ["npmrc"]
        assert container["build_args"] == {"NODE_ENV": "production"}
        assert container["method"] == "dockerfile" and container["image"] is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def document_with(**sections: Any) -> dict[str, Any]:
    base = {
        "format": app_export.FORMAT,
        "version": app_export.VERSION,
        "app": {"domain": DOMAIN, "app_type": "nodejs", "source": GIT},
    }
    base.update(sections)
    return base


class TestValidating:
    def test_a_document_from_before_they_were_exported_has_none(self) -> None:
        checked = validate_document(document_with())

        assert checked["mounts"] == [] and checked["container"] is None

    def test_a_file_is_given_its_defaults(self) -> None:
        checked = validate_document(document_with(mounts=[{"kind": "file", "target": "a.conf"}]))

        assert checked["mounts"] == [
            {"kind": "file", "target": "a.conf", "mode": "600", "content": None}
        ]

    @pytest.mark.parametrize(
        ("mount", "field"),
        [
            ({"kind": "socket", "target": "a"}, "mounts[0].kind"),
            ({"kind": "file"}, "mounts[0].target"),
            ({"kind": "file", "target": 5}, "mounts[0].target"),
            ({"kind": "file", "target": "a", "mode": "999"}, "mounts[0].mode"),
            ({"kind": "file", "target": "a", "mode": "0o640"}, "mounts[0].mode"),
            ({"kind": "file", "target": "a", "mode": "4755"}, "mounts[0].mode"),
            ({"kind": "file", "target": "a", "mode": "640\n"}, "mounts[0].mode"),
            ({"kind": "file", "target": "a", "mode": "200"}, "mounts[0].mode"),
            ({"kind": "file", "target": "a", "content": 5}, "mounts[0].content"),
            ({"kind": "file", "target": "a", "content": "x\x00y"}, "mounts[0].content"),
            ({"kind": "path", "target": "/a"}, "mounts[0].source"),
            (
                {"kind": "path", "target": "/a", "source": "/b", "read_only": "yes"},
                "mounts[0].read_only",
            ),
        ],
    )
    def test_a_mount_that_is_not_one_names_the_field(
        self, mount: dict[str, Any], field: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_document(document_with(mounts=[mount]))

        assert caught.value.field == field

    def test_the_same_target_twice_is_refused(self) -> None:
        mount = {"kind": "file", "target": "a.conf"}

        with pytest.raises(ValidationError, match="mounted twice"):
            validate_document(document_with(mounts=[mount, dict(mount)]))

    def test_a_list_is_required_and_it_is_bounded(self) -> None:
        with pytest.raises(ValidationError, match="mounts must be a list"):
            validate_document(document_with(mounts={"a": 1}))
        many = [{"kind": "file", "target": f"f{i}"} for i in range(export_runtime.MAX_MOUNTS + 1)]
        with pytest.raises(ValidationError, match="more than"):
            validate_document(document_with(mounts=many))

    def test_the_container_is_checked_by_the_rules_of_a_container(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_document(document_with(container={"method": "magic"}))

        assert caught.value.field == "container.method"

    def test_a_container_keeps_only_what_a_container_has(self) -> None:
        checked = validate_document(
            document_with(
                container={"method": "image", "image": "nginx:1.27", "surprise": 1, "digest": "x"}
            )
        )

        assert checked["container"] == {"method": "image", "image": "nginx:1.27"}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def file_mount(**fields: Any) -> dict[str, Any]:
    return {
        "kind": "file",
        "target": "config/production.json",
        "mode": "640",
        "secret": True,
        "content": CONFIG,
        **fields,
    }


def plan_of(app_type: str = "nodejs", **sections: Any) -> Any:
    doc = document_with(**sections)
    doc["app"]["app_type"] = app_type
    return plan_import(doc, domain=NEW, source=GIT if app_type != "container" else "nginx:1.27")


class TestPlanning:
    def test_a_file_is_put_in_place_before_the_first_start(self, store: NoustStore) -> None:
        plan = plan_of(mounts=[file_mount()])

        assert any(
            line == f"file mount config/production.json ({len(CONFIG)} bytes, mode 640)"
            for line in plan.steps
        )
        assert plan.create.seed_files == {"config/production.json": CONFIG}
        assert plan.create.seed_modes == {"config/production.json": 0o640}
        assert TOKEN not in repr(plan.create)
        assert TOKEN not in json.dumps(app_export.plan_summary(plan))

    def test_without_its_text_a_file_is_skipped_and_the_command_to_give_it_is_said(
        self, store: NoustStore
    ) -> None:
        plan = plan_of(mounts=[file_mount(content=None)])

        (step,) = [s for s in plan.skipped if s.part.startswith("file mount")]
        assert "not in the export" in step.detail
        assert f"noust app mount file {NEW} config/production.json --from FILE --mode 640" in (
            step.detail
        )
        assert plan.create.seed_files == {} and plan.mounts == []

    def test_a_path_is_for_a_container_only(self, store: NoustStore, tmp_path: Path) -> None:
        mount = {"kind": "path", "target": "/assets", "source": str(tmp_path), "read_only": True}

        plan = plan_of(mounts=[mount])

        (step,) = [s for s in plan.skipped if s.part.startswith("path mount")]
        assert "container application" in step.detail

    def test_a_path_the_server_has_is_bound_and_the_operator_is_asked(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        mount = {"kind": "path", "target": "/assets", "source": str(tmp_path), "read_only": True}

        plan = plan_of("container", mounts=[mount], container={"method": "image", "image": "nginx"})

        assert f"bind {tmp_path} at /assets read-only" in plan.steps
        assert any("binds 1 path(s) of this server" in reason for reason in plan.confirm)

    def test_a_path_this_server_does_not_have_is_skipped_with_the_reason(
        self, store: NoustStore
    ) -> None:
        mount = {"kind": "path", "target": "/assets", "source": "/srv/not-here", "read_only": True}

        plan = plan_of("container", mounts=[mount], container={"method": "image", "image": "nginx"})

        (step,) = [s for s in plan.skipped if s.part.startswith("path mount")]
        assert "does not exist on this server" in step.detail
        assert not plan.confirm

    def test_the_servers_own_state_is_never_bound(self, store: NoustStore) -> None:
        mount = {"kind": "path", "target": "/keys", "source": "/etc/ssh", "read_only": True}

        plan = plan_of("container", mounts=[mount], container={"method": "image", "image": "nginx"})

        (step,) = [s for s in plan.skipped if s.part.startswith("path mount")]
        assert "cannot be bound" in step.detail and plan.mounts == []

    def test_a_site_that_serves_from_disk_takes_no_mounts(self, store: NoustStore) -> None:
        plan = plan_of("static", mounts=[file_mount()])

        (step,) = [s for s in plan.skipped if s.part.startswith("file mount")]
        assert "take no mounts" in step.detail

    def test_an_environment_file_is_not_a_mount(self, store: NoustStore) -> None:
        plan = plan_of(mounts=[file_mount(target=".env.production")])

        (step,) = [s for s in plan.skipped if s.part.startswith("file mount")]
        assert "environment file" in step.detail

    @pytest.mark.parametrize(
        "target",
        [
            "../../etc/cron.d/x",
            "/etc/passwd",
            "a/../../b",
            "a\nb",
            "",
            ".env",
            "shared/.env.production",
            "a/./../../b",
        ],
    )
    def test_a_document_cannot_put_a_file_outside_the_application(
        self, store: NoustStore, target: str
    ) -> None:
        document = document_with(mounts=[file_mount(target=target)])

        try:
            plan = plan_import(document, domain=NEW, source=GIT)
        except ValidationError:
            return  # refused outright is as good as skipped

        assert plan.create.seed_files == {} and plan.mounts == []
        assert [s.part for s in plan.skipped if s.part.startswith("file mount")]

    @pytest.mark.parametrize(
        "source",
        [
            "/",
            "/etc/noust",
            "/etc/noust/../noust",
            "/var/lib/noust/noust.db",
            "/var/run/docker.sock",
            "/root",
            "relative/path",
            "/srv/a:b",
            "/srv/a,b",
            "/srv/../etc/shadow",
            "/proc/1/root",
        ],
    )
    def test_a_document_cannot_bind_what_a_container_must_never_see(
        self, store: NoustStore, source: str
    ) -> None:
        mount = {"kind": "path", "target": "/x", "source": source, "read_only": True}

        plan = plan_of("container", mounts=[mount], container={"method": "image", "image": "nginx"})

        assert plan.mounts == [] and not plan.confirm
        assert [s.part for s in plan.skipped if s.part.startswith("path mount")]

    def test_a_container_is_created_with_the_settings_of_the_export(
        self, store: NoustStore
    ) -> None:
        plan = plan_of(
            "container",
            container={
                "method": "image",
                "image": "nginx:1.27",
                "container_port": 80,
                "command": ["nginx"],
                "registry_server": "registry.example.com",
                "registry_username": "deploy",
                "build_secrets": ["npmrc"],
            },
        )

        assert plan.create.container is not None
        assert plan.create.container["image"] == "nginx:1.27"
        assert plan.create.container["command"] == ["nginx"]
        assert "registry_username" not in plan.create.container
        assert "build_secrets" not in plan.create.container
        assert any(line.startswith("container settings: image nginx:1.27") for line in plan.steps)

    def test_what_a_container_needs_and_cannot_bring_is_listed_with_the_command(
        self, store: NoustStore
    ) -> None:
        plan = plan_of(
            "container",
            container={
                "method": "image",
                "image": "nginx:1.27",
                "registry_server": "registry.example.com",
                "registry_username": "deploy",
                "build_secrets": ["npmrc", "token"],
            },
        )

        parts = {step.part: step.detail for step in plan.skipped}
        account = parts["registry account deploy at registry.example.com"]
        assert f"noust app container set {NEW} --registry-user deploy" in account
        assert "--registry-password-stdin" in account
        assert (
            f"noust app container set {NEW} --build-secret ID=FILE"
            in parts["build secrets npmrc, token"]
        )

    def test_a_container_that_is_not_valid_stops_the_plan(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError):
            plan_of("container", container={"method": "image"})

    def test_a_container_file_waits_for_the_container_to_exist(self, store: NoustStore) -> None:
        plan = plan_of(
            "container",
            mounts=[file_mount(target="/etc/app/config.json")],
            container={"method": "image", "image": "nginx"},
        )

        assert plan.create.seed_files == {}
        assert any("after the first start" in line for line in plan.steps)
        assert [m["target"] for m in plan.mounts] == ["/etc/app/config.json"]


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class TestApplying:
    def test_the_deploy_is_given_the_files_and_then_the_mounts_are_recorded(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        specs: list[CreateSpec] = []
        plan = plan_of(mounts=[file_mount()])

        report = apply_import(plan, deploy=deployed(store, tmp_path, specs), cron=NoCron())

        assert specs[0].seed_files == {"config/production.json": CONFIG}
        (record,) = app_mount_store.list_mounts(NEW)
        assert (record.kind, record.target, record.mode) == (
            "file",
            "config/production.json",
            0o640,
        )
        row = store.get_app(NEW)
        assert row is not None
        assert (Path(row.app_path) / "shared/config/production.json").read_text() == CONFIG
        assert not [step for step in report.not_applied if "mount" in step.part]

    def test_a_mount_the_manager_refuses_is_reported_and_the_rest_goes_on(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "vanishes"
        elsewhere.mkdir()
        mounts = [
            {"kind": "path", "target": "/a", "source": str(elsewhere), "read_only": True},
            file_mount(target="/etc/app/config.json"),
        ]
        plan = plan_of("container", mounts=mounts, container={"method": "image", "image": "nginx"})
        elsewhere.rmdir()

        report = apply_import(plan, deploy=deployed(store, tmp_path, []), cron=NoCron())

        refused = {step.part: step.detail for step in report.not_applied}
        assert "does not exist on this server" in refused["path mount /a"]
        assert [m.target for m in app_mount_store.list_mounts(NEW)] == ["/etc/app/config.json"]

    def test_a_container_is_told_to_update_to_be_bound_to_its_mounts(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        plan = plan_of(
            "container",
            mounts=[file_mount(target="/etc/app/config.json")],
            container={"method": "image", "image": "nginx"},
        )

        report = apply_import(plan, deploy=deployed(store, tmp_path, []), cron=NoCron())

        (step,) = [
            s for s in report.not_applied if s.part == "mounts reach the running application"
        ]
        assert f"noust update {NEW}" in step.detail

    def test_an_application_that_started_with_its_files_is_told_nothing(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        plan = plan_of(mounts=[file_mount()])

        report = apply_import(plan, deploy=deployed(store, tmp_path, []), cron=NoCron())

        assert not any("reach the running" in step.part for step in report.steps)

    def test_a_round_trip_under_another_domain_carries_the_mounts(
        self, shop: App, manager: MountManager, store: NoustStore, tmp_path: Path
    ) -> None:
        manager.put_file(DOMAIN, "config/production.json", CONFIG, mode=0o640)
        manager.put_file(DOMAIN, "certs/ca.pem", "-----BEGIN-----\n", mode=0o600)
        before = export_app(DOMAIN, with_secrets=True)

        plan = plan_import(before, domain=NEW)
        report = apply_import(plan, deploy=deployed(store, tmp_path, []), cron=NoCron())

        after = export_app(NEW, with_secrets=True)
        assert after["mounts"] == before["mounts"]
        assert not [step for step in report.not_applied if "mount" in step.part]

    def test_the_default_import_without_secrets_leaves_the_files_to_be_given(
        self, shop: App, manager: MountManager, store: NoustStore, tmp_path: Path
    ) -> None:
        manager.put_file(DOMAIN, "config/production.json", CONFIG)

        plan = plan_import(export_app(DOMAIN), domain=NEW)
        report = apply_import(plan, deploy=deployed(store, tmp_path, []), cron=NoCron())

        assert app_mount_store.list_mounts(NEW) == []
        assert any(step.part == "file mount config/production.json" for step in report.not_applied)

    def test_a_container_round_trips_how_it_runs(
        self, stack: App, store: NoustStore, tmp_path: Path
    ) -> None:
        before = export_app(DOMAIN, with_secrets=True)
        specs: list[CreateSpec] = []

        plan = plan_import(before, domain=NEW)
        apply_import(plan, deploy=deployed(store, tmp_path, specs), cron=NoCron())

        container = specs[0].container
        assert container is not None
        assert container["command"] == ["nginx", "-g", "daemon off;"]
        assert container["volumes"] == ["/var/cache/nginx"]
        assert container["container_port"] == 80


def test_every_client_of_a_spec_asks_the_deploy_job_the_same_way() -> None:
    spec = CreateSpec(
        domain=NEW,
        source=GIT,
        container={"method": "image", "image": "nginx"},
        seed_files={"a": "x"},
        seed_modes={"a": 0o600},
    )

    arguments = spec.job_arguments()

    assert arguments["container"] == {"method": "image", "image": "nginx"}
    assert arguments["seed_files"] == {"a": "x"} and arguments["seed_modes"] == {"a": 0o600}
    assert arguments["database"] is None and arguments["env_audience"] is None


def test_the_console_job_hands_the_files_and_the_container_to_the_deployer(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one job every import, clone and copy ends in gives the deployer what the spec holds."""
    from noust.web.jobs import Job, JobContext, JobType, deploy_app_job

    configured: dict[str, Any] = {}

    class FakeDeployer:
        last_deployment_id = 3

        def configure(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def deploy(self) -> bool:
            store.create_app(App(domain=NEW, app_type="nodejs"))
            return True

    monkeypatch.setattr("noust.deployers.get_deployer", lambda *a, **k: FakeDeployer())
    spec = CreateSpec(
        domain=NEW,
        source=GIT,
        app_type="nodejs",
        port=3000,
        ssl=False,
        container={"method": "image", "image": "nginx"},
        seed_files={"config/a.json": "{}"},
        seed_modes={"config/a.json": 0o640},
    )
    job = Job(id="job-m", type=JobType.DEPLOY, name="deploy", description="")

    deploy_app_job(**spec.job_arguments(), job_context=JobContext(job, lambda _job: None))

    assert configured["seed_files"] == {"config/a.json": "{}"}
    assert configured["seed_modes"] == {"config/a.json": 0o640}
    assert configured["container"] == {"method": "image", "image": "nginx"}


def test_a_job_without_files_configures_the_deployer_as_it_always_did(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.web.jobs import Job, JobContext, JobType, deploy_app_job

    configured: dict[str, Any] = {}

    class FakeDeployer:
        last_deployment_id = 3

        def configure(self, **kwargs: Any) -> None:
            configured.update(kwargs)

        def deploy(self) -> bool:
            store.create_app(App(domain=NEW, app_type="nodejs"))
            return True

    monkeypatch.setattr("noust.deployers.get_deployer", lambda *a, **k: FakeDeployer())
    job = Job(id="job-n", type=JobType.DEPLOY, name="deploy", description="")

    deploy_app_job(
        **CreateSpec(
            domain=NEW, source=GIT, app_type="nodejs", port=3000, ssl=False
        ).job_arguments(),
        job_context=JobContext(job, lambda _job: None),
    )

    assert "seed_files" not in configured and "container" not in configured
