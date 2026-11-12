# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Editing the Compose of a stack with a net: judge it, see what changes, apply it behind the health gate, and go back.

What is held here, and why it is the net: a document is judged by the model Compose resolves it to and not by
the text (an ``include`` cannot hide a privileged service), with Compose's own words when it does not read;
what changes is said before it changes, from Compose's own rehearsal; every saved text is a version with who
saved it and why, so there is a document to go back to; applying runs the application's update and, when the
stack does not come up, the document before is the one that runs again; and a stack whose document is somebody
else's (a repository, a recipe, a container application's generated file, a stack Noust did not make) is not
edited here, and says whose it is.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.docker.errors import DockerBlockedError, DockerNotFoundError, DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import AppRecord, Registry
from noust.core.docker_stack_versions import StackVersionStore
from noust.core.exceptions import DeploymentError
from noust.core.store import App, NoustStore
from noust.core.teams import Scope
from noust.deployers.compose_stack_edit import StackEditor
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.recipes.installed import record_install
from tests.support.compose_document import APP_NAME, DOCUMENT, DOMAIN, NEWER, make_stack
from tests.support.docker_machine import DockerMachine
from tests.support.docker_machine import RemoteTag as Remote

PROJECT = "stack-example-com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


class Updates:
    """Stands in for the update and for putting a stack back, which have tests of their own."""

    def __init__(self) -> None:
        self.updated: list[dict[str, Any]] = []
        self.put_back: list[dict[str, Any]] = []
        self.failure: Exception | None = None
        self.restarted: list[str] = []

    def update_app(self, domain: str, **kwargs: Any) -> Any:
        self.updated.append({"domain": domain, **kwargs})
        if self.failure is not None:
            raise self.failure
        return type("Outcome", (), {"deployment_id": 41, "warnings": None})()

    def put_stack_back(
        self, domain: str, app_name: str, snapshot: Any, failure: Any, **kw: Any
    ) -> None:
        self.put_back.append(
            {"domain": domain, "snapshot": dict(snapshot), "failure": failure, **kw}
        )
        failure.details = (
            f"{failure.details}\n\nThe previous version is running again and answering."
        )

    def health_gate_for(self, app: App, store: NoustStore, log: Any, **kw: Any) -> Any:
        updates = self

        class Gate:
            def restart_and_probe(self) -> tuple[bool, str]:
                updates.restarted.append(app.domain)
                return True, ""

        return Gate()


@pytest.fixture
def updates(monkeypatch: pytest.MonkeyPatch) -> Updates:
    fake = Updates()
    monkeypatch.setattr("noust.deployers.lifecycle.update_app", fake.update_app)
    monkeypatch.setattr("noust.deployers.lifecycle.health_gate_for", fake.health_gate_for)
    monkeypatch.setattr("noust.recipes.updates.put_stack_back", fake.put_stack_back)
    return fake


@pytest.fixture
def stack(store: NoustStore, tmp_path: Path) -> App:
    return make_stack(store, tmp_path)


def machine(app_path: str) -> DockerMachine:
    box = DockerMachine()
    box.box("stack-web-1", image="nginx:1.27", project=PROJECT, service="web", working_dir=app_path)
    box.box("other", image="portainer/portainer-ce:latest", policy="always")
    box.dry_run_output = " ✔ DRY-RUN MODE -  Container stack-web-1  Running   0.0s\n"
    # The registry has the version the tests move to; one that does not exist is a test of its own.
    for version in ("1.27", "1.28"):
        box.remote[f"docker.io/library/nginx:{version}"] = Remote("sha256:" + "b" * 64)
    return box


def editor(box: DockerMachine, store: NoustStore, app_path: str, **options: Any) -> StackEditor:
    gate = DockerGate(
        box,
        store=store,
        registry=lambda: Registry(apps=(AppRecord(DOMAIN, app_path),)),
        disk=lambda path: None,
        **options,
    )
    return StackEditor(gate, store=store, runner=box)


@pytest.fixture
def box(stack: App) -> DockerMachine:
    return machine(stack.app_path)


@pytest.fixture
def edit(store: NoustStore, stack: App, box: DockerMachine) -> StackEditor:
    return editor(box, store, stack.app_path)


def kept(store: NoustStore) -> str:
    return (recipe_source_dir(APP_NAME, store) / COMPOSE_FILE).read_text()


class TestWhichStacksAreEditedHere:
    def test_a_stack_deployed_from_a_document_is_edited_as_that_document(
        self, edit: StackEditor
    ) -> None:
        shown = edit.view(PROJECT)

        assert (shown.editable, shown.kind, shown.domain) == (True, "document", DOMAIN)
        assert shown.text == DOCUMENT
        assert shown.why == ""

    def test_reading_it_is_on_record(
        self, edit: StackEditor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.deployers.compose_stack_edit.audit.record",
            lambda event, **kw: seen.append({"event": event, **kw}),
        )

        edit.view(PROJECT, actor="ana")

        assert seen[0]["event"] == "apps.compose.document"
        assert seen[0]["details"]["action"] == "read"

    def test_a_stack_brought_to_noust_that_is_not_a_repository_is_edited_in_place(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        root = tmp_path / "opt" / "stacks" / "nextcloud"
        root.mkdir(parents=True)
        (root / "compose.yaml").write_text(DOCUMENT)
        store.create_app(
            App(
                domain="cloud",
                app_type="docker-compose",
                source="",
                app_path=str(root),
                layout="inplace",
            )
        )
        box = DockerMachine()
        box.box(
            "cloud-web-1", image="nginx:1.27", project="cloud", service="web", working_dir=str(root)
        )
        gate = DockerGate(
            box,
            store=store,
            registry=lambda: Registry(apps=(AppRecord("cloud", str(root)),)),
            disk=lambda path: None,
        )

        shown = StackEditor(gate, store=store, runner=box).view("cloud")

        assert (shown.editable, shown.kind) == (True, "file")
        assert shown.text == DOCUMENT
        assert shown.path == str(root / "compose.yaml")

    @pytest.mark.parametrize(
        ("source", "code"),
        [("https://example.org/shop.git", "repository")],
    )
    def test_a_stack_from_a_repository_has_its_document_there(
        self, store: NoustStore, tmp_path: Path, source: str, code: str
    ) -> None:
        root = tmp_path / "apps" / "shop"
        root.mkdir(parents=True)
        (root / "docker-compose.yml").write_text(DOCUMENT)
        store.create_app(
            App(
                domain="shop.example.com",
                app_type="docker-compose",
                source=source,
                app_path=str(root),
                layout="inplace",
            )
        )
        box = DockerMachine()
        box.box("shop-web-1", project="shop", service="web", working_dir=str(root))
        gate = DockerGate(
            box,
            store=store,
            registry=lambda: Registry(apps=(AppRecord("shop.example.com", str(root)),)),
            disk=lambda path: None,
        )
        found = StackEditor(gate, store=store, runner=box)

        shown = found.view("shop")

        assert (shown.editable, shown.why_code) == (False, code)
        assert "repository" in shown.why
        with pytest.raises(DockerBlockedError) as caught:
            found.check("shop", NEWER)
        assert caught.value.reason == code

    def test_a_stack_installed_from_a_recipe_is_the_recipes(
        self, store: NoustStore, stack: App, box: DockerMachine
    ) -> None:
        from noust.recipes import get_recipe

        record_install(DOMAIN, get_recipe("vaultwarden"), store=store)

        shown = editor(box, store, stack.app_path).view(PROJECT)

        assert (shown.editable, shown.why_code) == (False, "recipe")

    def test_a_container_applications_file_is_generated_and_says_where_it_is_changed(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        root = tmp_path / "apps" / "api"
        root.mkdir(parents=True)
        store.create_app(
            App(
                domain="api.example.com",
                app_type="container",
                app_path=str(root),
                layout="releases",
            )
        )
        box = DockerMachine()
        box.box("api-1", project="api", working_dir=str(root))
        gate = DockerGate(
            box,
            store=store,
            registry=lambda: Registry(apps=(AppRecord("api.example.com", str(root)),)),
            disk=lambda path: None,
        )

        shown = StackEditor(gate, store=store, runner=box).view("api")

        assert (shown.editable, shown.why_code) == (False, "generated")
        assert "noust app container set api.example.com" in shown.why

    def test_what_nobody_of_noust_made_is_brought_first(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        box.box("nextcloud-app-1", project="nextcloud", working_dir="/opt/stacks/nextcloud")

        shown = edit.view("nextcloud")

        assert (shown.editable, shown.why_code) == (False, "bring_first")
        with pytest.raises(DockerBlockedError) as caught:
            edit.check("nextcloud", NEWER)
        assert caught.value.reason == "bring_first"
        assert "noust docker stack bring nextcloud" in caught.value.via

    def test_a_database_engines_stack_is_its_own_door(self, store: NoustStore, stack: App) -> None:
        from noust.core.docker import names

        box = machine(stack.app_path)
        box.box(
            "noust-db-pg16",
            image="postgres:16",
            project=names.instance_project("pg16"),
            instance="pg16",
        )
        gate = DockerGate(
            box,
            store=store,
            registry=lambda: Registry(apps=(AppRecord(DOMAIN, stack.app_path),), engines=("pg16",)),
            disk=lambda path: None,
        )

        with pytest.raises(DockerOwnedError):
            StackEditor(gate, store=store, runner=box).view(names.instance_project("pg16"))

    def test_a_stack_a_person_may_not_see_is_not_there(
        self, store: NoustStore, stack: App, box: DockerMachine
    ) -> None:
        mine = editor(box, store, stack.app_path, scope=Scope.nothing(), sees_app=lambda d: False)

        with pytest.raises(DockerNotFoundError):
            mine.view(PROJECT)


class TestJudgingADocument:
    def test_a_good_change_says_what_was_checked_and_how_it_differs(
        self, edit: StackEditor
    ) -> None:
        found = edit.check(PROJECT, NEWER)

        assert found.valid is True
        assert found.changed is True
        assert (found.added, found.removed) == (1, 1)
        assert "-    image: nginx:1.27" in found.diff
        assert "+    image: nginx:1.28" in found.diff
        assert [(c.name, c.ok) for c in found.checks] == [
            ("compose", True),
            ("privileges", True),
            ("images", True),
            ("variables", True),
        ]
        assert [(s.name, s.image) for s in found.services] == [("web", "nginx:1.28")]

    def test_the_same_text_is_not_a_change(self, edit: StackEditor) -> None:
        found = edit.check(PROJECT, DOCUMENT)

        assert found.changed is False
        assert found.diff == ""
        assert found.valid is True

    def test_what_compose_cannot_read_is_said_in_composes_words_with_the_line(
        self, edit: StackEditor
    ) -> None:
        broken = DOCUMENT.replace('- "127.0.0.1:8080:80"', '- ["127.0.0.1:8080:80"')

        found = edit.check(PROJECT, broken)

        assert found.valid is False
        problem = found.problems[0]
        assert problem.code == "compose"
        assert problem.line is not None
        assert "did not find expected" in problem.output
        assert not any(c.ok for c in found.checks if c.name == "compose")

    def test_nothing_is_run_against_what_does_not_read_beyond_the_reading(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        edit.check(PROJECT, "services: [")

        assert not [c for c in box.calls if "--dry-run" in c]

    def test_a_privileged_container_is_refused_by_what_the_resolved_model_says(
        self, edit: StackEditor
    ) -> None:
        hidden = DOCUMENT.replace("nginx:1.27", "nginx:1.27\n    privileged: true")

        found = edit.check(PROJECT, hidden)

        assert found.valid is False
        assert found.problems[0].code == "privileges"
        assert "privileged" in found.problems[0].message
        assert found.problems[0].service == "web"

    def test_an_include_cannot_hide_it(self, edit: StackEditor, box: DockerMachine) -> None:
        # The file says nothing of it: what Compose resolves the file to does.
        box.remote["docker.io/library/x:1"] = Remote("sha256:" + "c" * 64)
        box.resolved_for = NEWER
        box.resolved = {
            "services": {
                "web": {"image": "nginx:1.28"},
                "agent": {"image": "x:1", "privileged": True},
            }
        }

        found = edit.check(PROJECT, NEWER)

        assert found.valid is False
        assert [p.code for p in found.problems] == ["privileges"]
        assert found.problems[0].service == "agent"

    def test_the_docker_socket_is_refused_the_same_way(self, edit: StackEditor) -> None:
        socket = DOCUMENT.replace(
            "    ports:",
            "    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n    ports:",
        )

        found = edit.check(PROJECT, socket)

        assert found.valid is False
        assert "docker" in found.problems[0].message.lower()

    def test_what_the_stack_already_runs_with_is_not_new(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        # The stack ran privileged before this change, and an edit that does not add to it is not the
        # edit that turns it on.
        old = DOCUMENT.replace("nginx:1.27", "nginx:1.27\n    privileged: true")
        stack = make_stack(store, tmp_path)
        (recipe_source_dir(APP_NAME, store) / COMPOSE_FILE).write_text(old)
        box = machine(stack.app_path)
        found = editor(box, store, stack.app_path).check(
            PROJECT, old.replace("nginx:1.27", "nginx:1.28")
        )

        assert found.valid is True
        assert any("privileged" in warning for warning in found.warnings)

    def test_an_exception_recorded_for_the_stack_lets_it_through_with_a_warning(
        self, store: NoustStore, stack: App, box: DockerMachine
    ) -> None:
        from noust.deployers.helpers import sandbox

        sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="ana", reason="needs it", store=store
        )
        hidden = DOCUMENT.replace("nginx:1.27", "nginx:1.27\n    privileged: true")

        found = editor(box, store, stack.app_path).check(PROJECT, hidden)

        assert found.valid is True
        assert any("needs it" in warning for warning in found.warnings)

    def test_a_variable_the_document_requires_and_the_stack_lacks_is_a_problem(
        self, edit: StackEditor, stack: App
    ) -> None:
        (Path(stack.app_path) / ".env").write_text("")

        found = edit.check(PROJECT, NEWER)

        assert found.valid is False
        assert found.problems[0].code == "variables"
        assert "DB_PASSWORD" in found.problems[0].message

    def test_a_new_image_the_registry_does_not_have_is_a_problem_and_one_it_has_is_not(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        box.remote["docker.io/library/nginx:1.28"] = Remote("sha256:" + "b" * 64)

        good = edit.check(PROJECT, NEWER)
        bad = edit.check(PROJECT, DOCUMENT.replace("nginx:1.27", "nginx:9.99"))

        assert good.valid is True
        assert [(i.service, i.state) for i in good.images] == [("web", "found")]
        assert bad.valid is False
        assert bad.problems[0].code == "image_missing"
        assert "nginx:9.99" in bad.problems[0].message

    def test_an_image_that_did_not_change_is_not_asked_about(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        edit.check(PROJECT, DOCUMENT + "# a comment\n")

        assert not [c for c in box.calls if c[:3] == ("docker", "buildx", "imagetools")]

    def test_a_registry_that_cannot_be_asked_does_not_stop_a_change_and_says_so(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        box.buildx = False

        found = edit.check(PROJECT, NEWER)

        assert found.valid is True
        assert [(i.service, i.state) for i in found.images] == [("web", "unknown")]
        assert any("nginx:1.28" in warning for warning in found.warnings)

    def test_what_the_change_would_do_is_composes_rehearsal_and_changes_nothing(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        box.dry_run_output = (
            " ✔ DRY-RUN MODE -  Container stack-web-1  Recreated   0.0s\n"
            " ✔ DRY-RUN MODE -  Container stack-db-1   Running     0.0s\n"
        )
        box.box(
            "stack-db-1",
            project=PROJECT,
            service="db",
            working_dir=box.boxes["stack-web-1"].working_dir,
        )

        found = edit.check(PROJECT, NEWER)

        assert found.effects is not None
        assert [(e.service, e.action) for e in found.effects.changes] == [("web", "recreate")]
        assert found.effects.removes_volumes is False
        assert box.changes() == []

    def test_a_volume_that_would_go_is_said(self, edit: StackEditor, box: DockerMachine) -> None:
        box.dry_run_output = " ✔ DRY-RUN MODE -  Volume stack-example-com_data  Removed   0.0s\n"

        found = edit.check(PROJECT, NEWER)

        assert found.effects is not None
        assert found.effects.removes_volumes is True

    def test_a_rehearsal_that_fails_is_a_warning_with_composes_words_and_not_a_veto(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        box.dry_run_failure = "service web: pull access denied for private/thing"

        found = edit.check(PROJECT, NEWER)

        assert found.valid is True
        assert found.effects is None
        assert any("pull access denied" in warning for warning in found.warnings)

    def test_the_text_goes_to_compose_on_its_standard_input_and_to_nothing_else(
        self, edit: StackEditor, box: DockerMachine
    ) -> None:
        edit.check(PROJECT, NEWER)

        runs = [
            (call, text)
            for call, text in zip(box.calls, box.inputs, strict=True)
            if call[:2] == ("docker", "compose")
        ]
        assert runs
        assert all(text == NEWER for _call, text in runs)
        assert all(NEWER.strip() not in " ".join(call) for call, _text in runs)

    def test_a_document_that_is_not_a_compose_document_is_refused_plainly(
        self, edit: StackEditor
    ) -> None:
        found = edit.check(PROJECT, "just: [a, list")
        again = edit.check(PROJECT, "- 1\n- 2\n")

        assert found.valid is False and again.valid is False
        assert again.problems[0].code == "compose"


class TestTheHistory:
    def test_the_first_save_keeps_what_the_stack_had_so_there_is_something_to_go_back_to(
        self, store: NoustStore, edit: StackEditor
    ) -> None:
        staged = edit.stage(PROJECT, NEWER, reason="Use nginx 1.28", actor="ana")

        versions = StackVersionStore(store).versions(DOMAIN)
        assert [(v.version, v.source) for v in versions] == [(2, "edit"), (1, "found")]
        assert (versions[0].actor, versions[0].reason) == ("ana", "Use nginx 1.28")
        assert staged.version == 2
        assert StackVersionStore(store).get(DOMAIN, 1).text == DOCUMENT  # type: ignore[union-attr]

    def test_staging_writes_nothing_where_the_stack_runs_from(
        self, store: NoustStore, edit: StackEditor
    ) -> None:
        edit.stage(PROJECT, NEWER, reason="x", actor="ana")

        assert kept(store) == DOCUMENT

    def test_a_document_that_does_not_pass_is_not_kept(
        self, store: NoustStore, edit: StackEditor
    ) -> None:
        with pytest.raises(DockerBlockedError) as caught:
            edit.stage(
                PROJECT,
                DOCUMENT.replace("nginx:1.27", "nginx:1.27\n    privileged: true"),
                reason="x",
                actor="ana",
            )

        assert caught.value.reason == "invalid"
        assert "privileged" in str(caught.value)
        assert StackVersionStore(store).versions(DOMAIN) == []

    def test_the_same_text_is_not_a_change_to_keep(self, edit: StackEditor) -> None:
        with pytest.raises(DockerBlockedError) as caught:
            edit.stage(PROJECT, DOCUMENT, reason="x", actor="ana")

        assert caught.value.reason == "unchanged"

    def test_the_list_is_newest_first_and_a_version_comes_with_what_differs_from_now(
        self, edit: StackEditor, updates: Updates
    ) -> None:
        staged = edit.stage(PROJECT, NEWER, reason="one", actor="ana")
        edit.apply(PROJECT, staged.version, actor="ana")

        listed = edit.versions(PROJECT)
        old = edit.version(PROJECT, 1)

        assert [v.version for v in listed] == [2, 1]
        assert old.text == DOCUMENT
        assert (
            "-    image: nginx:1.28" in old.diff
        )  # what restoring it would take out of what runs now
        assert "+    image: nginx:1.27" in old.diff
        assert old.diff_against == "current"

    def test_a_version_that_is_not_there_is_not_there(self, edit: StackEditor) -> None:
        with pytest.raises(DockerNotFoundError):
            edit.version(PROJECT, 9)


class TestApplying:
    def test_it_writes_the_version_runs_the_update_and_says_how_it_went(
        self, store: NoustStore, edit: StackEditor, updates: Updates
    ) -> None:
        staged = edit.stage(PROJECT, NEWER, reason="Use nginx 1.28", actor="ana")

        done = edit.apply(PROJECT, staged.version, actor="ana", trigger="panel", job_id="job-1")

        assert kept(store) == NEWER
        assert [(u["domain"], u["trigger"], u["job_id"]) for u in updates.updated] == [
            (DOMAIN, "panel", "job-1")
        ]
        assert (done.applied, done.deployment_id, done.version) == (True, 41, staged.version)
        assert updates.put_back == []

    def test_it_is_on_record(
        self, edit: StackEditor, updates: Updates, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.deployers.compose_stack_edit.audit.record",
            lambda event, **kw: seen.append({"event": event, **kw}),
        )
        staged = edit.stage(PROJECT, NEWER, reason="Use nginx 1.28", actor="ana")

        edit.apply(PROJECT, staged.version, actor="ana")

        events = [item for item in seen if item["event"] == "docker.compose"]
        assert [item["details"]["action"] for item in events] == ["stage", "apply"]
        assert events[-1]["details"]["version"] == staged.version
        assert events[-1]["target"] == f"app:{DOMAIN}"

    def test_when_the_stack_does_not_come_up_the_document_before_is_the_one_that_runs(
        self, store: NoustStore, edit: StackEditor, updates: Updates
    ) -> None:
        staged = edit.stage(PROJECT, NEWER, reason="Use nginx 1.28", actor="ana")
        updates.failure = DeploymentError("web did not answer", details="The health gate failed.")

        with pytest.raises(DeploymentError) as caught:
            edit.apply(PROJECT, staged.version, actor="ana")

        assert updates.put_back and updates.put_back[0]["snapshot"] == {COMPOSE_FILE: DOCUMENT}
        assert "running again" in caught.value.details
        # And the history says what happened: the document before is a version again.
        versions = StackVersionStore(store).versions(DOMAIN)
        assert [(v.version, v.source) for v in versions[:2]] == [(3, "revert"), (2, "edit")]
        assert "web did not answer" in versions[0].reason

    def test_a_version_that_was_pruned_cannot_be_applied(
        self, edit: StackEditor, updates: Updates
    ) -> None:
        with pytest.raises(DockerNotFoundError):
            edit.apply(PROJECT, 7, actor="ana")

        assert updates.updated == []

    def test_what_was_not_judged_again_would_still_be_refused_when_it_runs(
        self, store: NoustStore, edit: StackEditor, updates: Updates
    ) -> None:
        # Between staging and applying the stack lost the value the document requires: the apply judges
        # it again, since the check is of its moment.
        staged = edit.stage(PROJECT, NEWER, reason="x", actor="ana")
        (Path(make_root(store)) / ".env").write_text("")

        with pytest.raises(DockerBlockedError) as caught:
            edit.apply(PROJECT, staged.version, actor="ana")

        assert caught.value.reason == "invalid"
        assert updates.updated == []
        assert kept(store) == DOCUMENT


def make_root(store: NoustStore) -> str:
    app = store.get_app(DOMAIN)
    assert app is not None
    return app.app_path


class TestGoingBack:
    def test_a_version_is_put_back_by_the_same_road_as_an_edit(
        self, store: NoustStore, edit: StackEditor, updates: Updates
    ) -> None:
        staged = edit.stage(PROJECT, NEWER, reason="Use nginx 1.28", actor="ana")
        edit.apply(PROJECT, staged.version, actor="ana")

        back = edit.stage_restore(PROJECT, 1, reason="Back to 1.27", actor="ana")
        edit.apply(PROJECT, back.version, actor="ana")

        assert kept(store) == DOCUMENT
        versions = StackVersionStore(store).versions(DOMAIN)
        assert (versions[0].source, versions[0].reason) == ("restore", "Back to 1.27")
        assert len(updates.updated) == 2

    def test_a_version_that_is_what_runs_now_is_not_a_change(
        self, edit: StackEditor, updates: Updates
    ) -> None:
        staged = edit.stage(PROJECT, NEWER, reason="x", actor="ana")
        edit.apply(PROJECT, staged.version, actor="ana")

        with pytest.raises(DockerBlockedError) as caught:
            edit.stage_restore(PROJECT, staged.version, reason="x", actor="ana")

        assert caught.value.reason == "unchanged"


class TestAFileInItsOwnFolder:
    @pytest.fixture
    def folder(self, store: NoustStore, tmp_path: Path) -> Path:
        root = tmp_path / "opt" / "stacks" / "nextcloud"
        root.mkdir(parents=True)
        path = root / "compose.yaml"
        path.write_text(DOCUMENT)
        path.chmod(0o640)
        (root / ".env").write_text("DB_PASSWORD=hunter2\n")
        store.create_app(
            App(
                domain="cloud",
                app_type="docker-compose",
                source="",
                app_path=str(root),
                layout="inplace",
            )
        )
        return root

    def found(self, store: NoustStore, folder: Path) -> StackEditor:
        box = DockerMachine()
        box.box(
            "cloud-web-1",
            image="nginx:1.27",
            project="cloud",
            service="web",
            working_dir=str(folder),
        )
        box.dry_run_output = " ✔ DRY-RUN MODE -  Container cloud-web-1  Running   0.0s\n"
        box.remote["docker.io/library/nginx:1.28"] = Remote("sha256:" + "b" * 64)
        gate = DockerGate(
            box,
            store=store,
            registry=lambda: Registry(apps=(AppRecord("cloud", str(folder)),)),
            disk=lambda path: None,
        )
        return StackEditor(gate, store=store, runner=box)

    def test_it_is_written_where_it_is_with_the_mode_it_had(
        self, store: NoustStore, folder: Path, updates: Updates
    ) -> None:
        edit = self.found(store, folder)
        staged = edit.stage("cloud", NEWER, reason="Use nginx 1.28", actor="ana")

        edit.apply("cloud", staged.version, actor="ana")

        path = folder / "compose.yaml"
        assert path.read_text() == NEWER
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
        assert updates.updated[0]["domain"] == "cloud"

    def test_when_it_does_not_come_up_the_file_goes_back_and_the_stack_is_started_from_it(
        self, store: NoustStore, folder: Path, updates: Updates
    ) -> None:
        edit = self.found(store, folder)
        staged = edit.stage("cloud", NEWER, reason="x", actor="ana")
        updates.failure = DeploymentError("web did not answer", details="The health gate failed.")

        with pytest.raises(DeploymentError):
            edit.apply("cloud", staged.version, actor="ana")

        assert (folder / "compose.yaml").read_text() == DOCUMENT
        assert updates.put_back[0]["snapshot"] == {"compose.yaml": DOCUMENT}
        assert updates.put_back[0].get("write_source") is False

    def test_a_file_the_stack_does_not_have_is_not_made_up(
        self, store: NoustStore, folder: Path
    ) -> None:
        (folder / "compose.yaml").unlink()
        edit = self.found(store, folder)

        shown = edit.view("cloud")

        assert shown.editable is False
        assert shown.why_code == "no_file"

    def test_a_link_is_never_written_through(
        self, store: NoustStore, folder: Path, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "elsewhere.yaml"
        elsewhere.write_text(DOCUMENT)
        (folder / "compose.yaml").unlink()
        (folder / "compose.yaml").symlink_to(elsewhere)
        edit = self.found(store, folder)

        shown = edit.view("cloud")

        assert shown.editable is False
        assert elsewhere.read_text() == DOCUMENT


def test_the_commands_the_editor_only_looks_with_are_declared_read_only() -> None:
    from noust.core.runner import is_read_only

    assert is_read_only(
        ("docker", "compose", "-p", "shop", "-f", "-", "config", "--format", "json")
    )
    assert is_read_only(
        ("docker", "compose", "-p", "shop", "-f", "-", "up", "-d", "--dry-run", "--no-build")
    )
    assert not is_read_only(("docker", "compose", "-p", "shop", "-f", "-", "up", "-d"))
