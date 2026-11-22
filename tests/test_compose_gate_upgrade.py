# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server that already runs stacks is brought up to date without one of them failing to start.

A stack that existed before the gate has no record of what it was accepted with and a unit that starts it
without asking. The step that takes it over writes the record first (what Compose resolves for it today
and what its containers have: what the version it ran under would have started) and only then writes the
unit with the gate, and runs ``daemon-reload``: nothing is started, stopped or recreated. A stack it cannot
resolve is left as it is and said, and the next pass tries again. The three stacks of the upgrade scenario
(a path by variable outside the tree, ``privileged`` with its exception, one that is stopped) start after
it, and the same stack with its ``.env`` changed to ``/`` afterwards does not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core import compose_acceptance
from noust.core.store import App, AppStatus, NoustStore
from noust.deployers import compose_gate, compose_upgrade
from noust.deployers.helpers import sandbox
from noust.managers.service_manager import ServiceManager
from tests.support.compose_gate_rig import (  # noqa: F401
    UNIT_42,
    Machine,
    lines,
    machine,
    make_stack,
    store,
    unit_of,
    units,
)
from tests.support.docker_grants_samples import container_id, grants_line, ps_row

# ruff: noqa: F811


class TestTheThreeStacksOfTheUpgrade:
    """What the scenario of the harness does on a real server, over the same machinery."""

    def stacks(self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine) -> list[App]:
        outside = make_stack(
            tmp_path, store, units, machine, "outside.example.com", env="DATA=/srv/shared\n"
        )
        privileged = make_stack(
            tmp_path, store, units, machine, "priv.example.com", privileged=True
        )
        sandbox.set_compose_exception(
            "priv.example.com", allowed=True, actor="admin", reason="it needs it", store=store
        )
        stopped = make_stack(
            tmp_path,
            store,
            units,
            machine,
            "stopped.example.com",
            status=AppStatus.STOPPED.value,
        )
        return [outside, privileged, stopped]

    def test_all_three_are_taken_over_and_get_their_unit_with_the_gate(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stacks(tmp_path, store, units, machine)

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert {o.domain: o.state for o in outcomes} == {
            "outside.example.com": "done",
            "priv.example.com": "done",
            "stopped.example.com": "done",
        }
        for domain in ("outside.example.com", "priv.example.com", "stopped.example.com"):
            text = unit_of(units, domain)
            name = domain.replace(".", "-")
            assert len(lines(text, "ExecStartPre")) == 1
            assert f"app compose gate {domain} --project {name}" in lines(text, "ExecStartPre")[0]
            assert len(lines(text, "ExecReload")) == 2
            for directive in (
                "ExecStart=/usr/bin/docker",
                "ExecStop",
                "ExecReload=/usr/bin/docker",
            ):
                found = next(line for line in text.splitlines() if line.startswith(directive))
                assert f"-p {name}" in found

    def test_each_is_recorded_as_taken_over_with_what_it_runs_with(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stacks(tmp_path, store, units, machine)

        compose_upgrade.bring_up_to_date(store=store)

        outside = compose_acceptance.get("outside.example.com", store=store)
        assert outside is not None and outside.inherited and outside.source == "upgrade"
        assert {key[1] for key in outside.keys} == {"outside"}
        priv = compose_acceptance.get("priv.example.com", store=store)
        assert priv is not None and "privileged" in {key[1] for key in priv.keys}
        stopped = compose_acceptance.get("stopped.example.com", store=store)
        assert stopped is not None and stopped.keys == frozenset()
        assert compose_acceptance.inherited_domains(store=store) == [
            "outside.example.com",
            "priv.example.com",
            "stopped.example.com",
        ]

    def test_the_project_each_used_is_kept_and_recorded(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stacks(tmp_path, store, units, machine)

        compose_upgrade.bring_up_to_date(store=store)

        for domain in ("outside.example.com", "priv.example.com", "stopped.example.com"):
            app = store.get_app(domain)
            assert app is not None and app.compose_project == domain.replace(".", "-")

    def test_all_three_start_with_the_gate_afterwards(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        apps = self.stacks(tmp_path, store, units, machine)
        compose_upgrade.bring_up_to_date(store=store)

        for app in apps:
            assert compose_gate.judge_app(app, store=store).allowed, app.domain

    def test_the_same_stack_with_its_env_changed_to_the_root_does_not(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        apps = self.stacks(tmp_path, store, units, machine)
        compose_upgrade.bring_up_to_date(store=store)
        (Path(apps[0].app_path) / ".env").write_text("DATA=/\n")

        outcome = compose_gate.judge_app(apps[0], store=store)

        assert not outcome.allowed
        assert any("service web mounts /" in line for line in outcome.lines)

    def test_nothing_is_started_stopped_or_recreated(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stacks(tmp_path, store, units, machine)

        compose_upgrade.bring_up_to_date(store=store)

        changing = [
            call
            for call in machine.calls
            if call[:2] == ("systemctl", "start")
            or call[:2] == ("systemctl", "restart")
            or call[:2] == ("systemctl", "stop")
            or (call[:2] == ("docker", "compose") and not {"config", "ps"} & set(call))
        ]
        assert changing == []
        assert any(call[:2] == ("systemctl", "daemon-reload") for call in machine.calls)

    def test_a_second_pass_changes_nothing(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stacks(tmp_path, store, units, machine)
        compose_upgrade.bring_up_to_date(store=store)
        before = {d: unit_of(units, d) for d in ("outside.example.com", "priv.example.com")}
        recorded = compose_acceptance.get("outside.example.com", store=store)

        again = compose_upgrade.bring_up_to_date(store=store)

        assert {o.state for o in again} == {"unchanged"}
        assert before == {d: unit_of(units, d) for d in before}
        assert compose_acceptance.get("outside.example.com", store=store) == recorded

    def test_the_operator_s_own_lines_of_the_unit_are_kept(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stacks(tmp_path, store, units, machine)

        compose_upgrade.bring_up_to_date(store=store)

        assert 'Environment="MY_OWN=kept"' in unit_of(units, "outside.example.com")
        assert "TimeoutStartSec=300" in unit_of(units, "outside.example.com")


class TestTheOrderIsWhatKeepsAStackStarting:
    def test_the_record_is_there_before_the_unit_is_written(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        seen: list[bool] = []
        original = ServiceManager.rewrite_unit

        def rewrite(self: ServiceManager, name: str, content: str) -> Path:
            seen.append(compose_acceptance.get("outside.example.com", store=store) is not None)
            return original(self, name, content)

        monkeypatch.setattr(ServiceManager, "rewrite_unit", rewrite)

        compose_upgrade.bring_up_to_date(store=store)

        assert seen == [True]

    def test_a_stack_that_compose_cannot_resolve_is_left_as_it_was_and_said(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        before = unit_of(units, "outside.example.com")
        machine.fail_config = set(machine.stacks)

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["pending"]
        assert unit_of(units, "outside.example.com") == before
        assert compose_acceptance.get("outside.example.com", store=store) is None
        app = store.get_app("outside.example.com")
        assert app is not None and app.compose_project is None
        note = compose_acceptance.note_of("outside.example.com", store=store)
        assert note is not None and note.state == "pending" and "resolve" in note.detail

    def test_the_next_pass_tries_again_and_clears_what_was_said(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        machine.fail_config = set(machine.stacks)
        compose_upgrade.bring_up_to_date(store=store)
        machine.fail_config = set()

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["done"]
        assert compose_acceptance.note_of("outside.example.com", store=store) is None
        assert lines(unit_of(units, "outside.example.com"), "ExecStartPre")

    def test_a_docker_that_does_not_answer_leaves_the_stack_as_it_was(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        before = unit_of(units, "outside.example.com")
        machine.docker_down = True

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["pending"]
        assert unit_of(units, "outside.example.com") == before
        assert compose_acceptance.get("outside.example.com", store=store) is None

    def test_a_unit_without_a_record_never_has_the_gate(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        machine.fail_config = set(machine.stacks)

        compose_upgrade.bring_up_to_date(store=store)

        assert not lines(unit_of(units, "outside.example.com"), "ExecStartPre")

    def test_what_its_containers_have_is_taken_over_too(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # A device its containers were given that neither its file nor its variables say today
        # (the file was changed since): it is what the stack runs with, so it is accepted.
        app = make_stack(tmp_path, store, units, machine, "outside.example.com")
        ident = container_id("web")
        machine.containers = [
            json.loads(
                ps_row(
                    ident,
                    "web-1",
                    project="outside-example-com",
                    service="web",
                    working_dir=app.app_path,
                )
            )
        ]
        machine.script(["docker", "inspect"], stdout=grants_line("web", devices=("/dev/fuse",)))

        compose_upgrade.bring_up_to_date(store=store)

        accepted = compose_acceptance.get("outside.example.com", store=store)
        assert accepted is not None
        assert ("service web", "devices", "/dev/fuse") in accepted.keys

    def test_a_container_of_the_project_from_another_tree_is_not_taken_over(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        machine.containers = [
            json.loads(
                ps_row(
                    container_id("web"),
                    "web-1",
                    project="outside-example-com",
                    service="web",
                    working_dir=str(tmp_path / "somewhere-else"),
                )
            )
        ]
        machine.script(["docker", "inspect"], stdout=grants_line("web", devices=("/dev/fuse",)))

        compose_upgrade.bring_up_to_date(store=store)

        accepted = compose_acceptance.get("outside.example.com", store=store)
        assert accepted is not None
        assert not any(key[1] == "devices" for key in accepted.keys)


class TestAStackThatComposeCannotResolveIsTakenFromItsContainers:
    """
    A stack Compose does not resolve today is not left without a record: what its containers were
    given is what it runs with, and that is what it is accepted with.
    """

    def running(
        self,
        app: App,
        machine: Machine,
        *,
        project: str = "outside-example-com",
        devices: tuple[str, ...] = ("/dev/fuse",),
    ) -> None:
        machine.containers = [
            json.loads(
                ps_row(
                    container_id("web"),
                    "web-1",
                    project=project,
                    service="web",
                    working_dir=app.app_path,
                )
            )
        ]
        machine.script(["docker", "inspect"], stdout=grants_line("web", devices=devices))

    def stack(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine, **extra: str
    ) -> App:
        app = make_stack(tmp_path, store, units, machine, "outside.example.com", **extra)
        machine.fail_config = set(machine.stacks)
        return app

    def test_it_is_taken_with_what_its_containers_have(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        self.running(app, machine)

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [(o.state, o.recorded, o.unit) for o in outcomes] == [("done", True, True)]
        accepted = compose_acceptance.get("outside.example.com", store=store)
        assert accepted is not None and accepted.inherited and accepted.source == "upgrade"
        assert accepted.keys == frozenset({("service web", "devices", "/dev/fuse")})
        assert compose_acceptance.note_of("outside.example.com", store=store) is None

    def test_the_project_is_the_one_on_the_labels_and_the_unit_carries_it(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        self.running(app, machine, project="legacy-shop")

        compose_upgrade.bring_up_to_date(store=store)

        stored = store.get_app("outside.example.com")
        assert stored is not None and stored.compose_project == "legacy-shop"
        text = unit_of(units, "outside.example.com")
        assert "app compose gate outside.example.com --project legacy-shop" in text
        assert "-p legacy-shop up -d --remove-orphans" in text

    def test_the_project_the_store_pins_is_not_renamed_by_the_labels(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        store.set_app_compose_project("outside.example.com", "pinned-name")
        self.running(app, machine, project="pinned-name")

        compose_upgrade.bring_up_to_date(store=store)

        stored = store.get_app("outside.example.com")
        assert stored is not None and stored.compose_project == "pinned-name"
        assert compose_acceptance.get("outside.example.com", store=store) is not None

    def test_a_file_that_is_not_there_is_no_reason_to_leave_a_running_stack_without_a_record(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        (Path(app.app_path) / "docker-compose.yml").unlink()
        self.running(app, machine)

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["done"]
        assert compose_acceptance.get("outside.example.com", store=store) is not None

    def test_what_the_file_asks_for_more_than_its_containers_have_is_refused_at_the_start(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # The record is what the stack runs with, so a file that now names more than that is a start
        # that adds something: held back, which is what the check is for.
        app = self.stack(tmp_path, store, units, machine)
        self.running(app, machine, devices=())
        compose_upgrade.bring_up_to_date(store=store)
        machine.fail_config = set()
        (Path(app.app_path) / ".env").write_text("DATA=/\n")

        outcome = compose_gate.judge_app(app, store=store)

        assert not outcome.allowed
        assert any("service web mounts /" in line for line in outcome.lines)

    def test_what_its_containers_have_goes_on_starting(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        self.running(app, machine, devices=("/dev/fuse",))
        compose_upgrade.bring_up_to_date(store=store)
        machine.fail_config = set()

        outcome = compose_gate.judge_app(app, store=store)

        # Judged against a record, not let through for lack of one.
        assert outcome.allowed and not outcome.unrecorded

    def test_nothing_is_started_stopped_or_recreated_to_take_it(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        self.running(app, machine)

        compose_upgrade.bring_up_to_date(store=store)

        changing = [
            call
            for call in machine.calls
            if call[:2] in (("systemctl", "start"), ("systemctl", "restart"), ("systemctl", "stop"))
            or (call[:2] == ("docker", "compose") and not {"config", "ps"} & set(call))
        ]
        assert changing == []

    def test_with_no_container_it_stays_pending_and_says_both_things(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stack(tmp_path, store, units, machine)
        before = unit_of(units, "outside.example.com")

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["pending"]
        assert "resolve" in outcomes[0].detail and "container" in outcomes[0].detail
        assert unit_of(units, "outside.example.com") == before
        assert compose_acceptance.get("outside.example.com", store=store) is None

    def test_containers_made_from_another_tree_are_not_its_containers(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        self.running(app, machine)
        machine.containers[0]["working_dir"] = str(tmp_path / "somewhere-else")

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["pending"]
        assert compose_acceptance.get("outside.example.com", store=store) is None

    def test_two_projects_in_the_tree_are_not_guessed_between(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = self.stack(tmp_path, store, units, machine)
        self.running(app, machine, project="old-name")
        machine.containers.append(
            json.loads(
                ps_row(
                    container_id("db"),
                    "db-1",
                    project="new-name",
                    service="db",
                    working_dir=app.app_path,
                )
            )
        )

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["pending"]
        assert "new-name" in outcomes[0].detail and "old-name" in outcomes[0].detail
        stored = store.get_app("outside.example.com")
        assert stored is not None and stored.compose_project is None

    def test_a_docker_that_does_not_answer_leaves_it_as_it_was(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        self.stack(tmp_path, store, units, machine)
        before = unit_of(units, "outside.example.com")
        machine.docker_down = True

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["pending"]
        assert unit_of(units, "outside.example.com") == before
        assert compose_acceptance.get("outside.example.com", store=store) is None

    def test_a_stack_that_compose_does_resolve_is_still_taken_with_its_file_too(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # The containers stand in only when the file cannot: where it resolves, what it resolves to
        # is accepted as well, as it always was.
        app = make_stack(
            tmp_path, store, units, machine, "outside.example.com", env="DATA=/srv/shared\n"
        )
        self.running(app, machine, devices=())

        compose_upgrade.bring_up_to_date(store=store)

        accepted = compose_acceptance.get("outside.example.com", store=store)
        assert accepted is not None
        assert {key[1] for key in accepted.keys} == {"outside"}


class TestTwoApplicationsThatShareAProject:
    def test_neither_is_broken_and_both_are_listed(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        first = make_stack(
            tmp_path, store, units, machine, "shop.example.com", compose_dir="docker"
        )
        second = make_stack(
            tmp_path, store, units, machine, "evil.example.org", compose_dir="docker"
        )

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert {o.state for o in outcomes} == {"done"}
        for app in (first, second):
            stored = store.get_app(app.domain)
            assert stored is not None and stored.compose_project == "docker"
        assert compose_upgrade.shared_projects(store=store) == {
            "docker": ("evil.example.org", "shop.example.com")
        }
        # What 4.2 started with a shared name is accepted, so each goes on starting.
        for app in (first, second):
            assert compose_gate.judge_app(app, store=store).allowed, app.domain

    def test_an_application_of_its_own_is_not_listed(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "shop.example.com")
        make_stack(tmp_path, store, units, machine, "blog.example.com")

        compose_upgrade.bring_up_to_date(store=store)

        assert compose_upgrade.shared_projects(store=store) == {}


class TestAMachineWithoutUnits:
    def test_a_stack_kept_alive_by_docker_gets_its_record_and_no_unit(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        (units / "outside-example-com.service").unlink()

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [o.state for o in outcomes] == ["done"]
        assert not (units / "outside-example-com.service").exists()
        assert compose_acceptance.get("outside.example.com", store=store) is not None


class TestGoingBack:
    def test_the_gate_comes_out_of_every_unit_and_the_records_stay(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        compose_upgrade.bring_up_to_date(store=store)

        changed = compose_upgrade.remove_gates(store=store)

        assert changed == ["outside.example.com"]
        text = unit_of(units, "outside.example.com")
        assert not lines(text, "ExecStartPre") and len(lines(text, "ExecReload")) == 1
        assert compose_acceptance.get("outside.example.com", store=store) is not None

    def test_bringing_them_up_again_changes_nothing_but_the_units(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "outside.example.com")
        compose_upgrade.bring_up_to_date(store=store)
        recorded = compose_acceptance.get("outside.example.com", store=store)
        compose_upgrade.remove_gates(store=store)

        outcomes = compose_upgrade.bring_up_to_date(store=store)

        assert [(o.state, o.recorded, o.unit) for o in outcomes] == [("done", False, True)]
        assert compose_acceptance.get("outside.example.com", store=store) == recorded


class TestTheUnitText:
    WRAPPED = (
        "/bin/sh -c '[ ! -x /usr/bin/noust ] || "
        "exec /usr/bin/noust app compose gate a.b --project a-b'"
    )
    # What the lane before this one wrote: the command itself, which fails for ever once Noust is gone.
    FIRST_FORM = "/usr/bin/noust app compose gate a.b --project a-b"

    def gated(self, text: str, *, project: str | None = "a-b") -> str:
        return compose_upgrade.with_gate(
            text, domain="a.b", executable="/usr/bin/noust", project=project
        )

    def unit(self, project: str = "") -> str:
        return UNIT_42.format(name="a-b", domain="a.b", path="/x", project=project)

    def test_the_gate_goes_before_start_and_before_the_rebuild(self) -> None:
        new = self.gated(self.unit())

        assert new.index("ExecStartPre=") < new.index("ExecStart=")
        reloads = lines(new, "ExecReload")
        assert lines(new, "ExecStartPre") == [f"ExecStartPre={self.WRAPPED}"]
        assert reloads[0] == f"ExecReload={self.WRAPPED}"
        assert "--build" in reloads[1]

    def test_the_line_runs_the_command_only_where_there_is_one_to_run(self) -> None:
        # Noust removed (apt, dnf, zypper, pip) leaves the unit as it is: the stack starts as it did.
        line = lines(self.gated(self.unit()), "ExecStartPre")[0]

        assert "[ ! -x /usr/bin/noust ] ||" in line
        assert "exec /usr/bin/noust app compose gate" in line

    def test_a_unit_that_already_has_the_gate_is_returned_as_it_is(self) -> None:
        once = self.gated(self.unit())

        assert self.gated(once) == once

    def test_the_first_form_of_the_line_is_recognised_and_becomes_the_new_one(self) -> None:
        # The unit as the first builds of 4.3 wrote it: the command itself, twice, and the project in
        # every command.
        old = self.unit(" -p a-b")
        old = old.replace(
            "ExecStart=/usr", f"ExecStartPre={self.FIRST_FORM}\nExecStart=/usr", 1
        ).replace("ExecReload=/usr", f"ExecReload={self.FIRST_FORM}\nExecReload=/usr", 1)
        assert lines(old, "ExecStartPre") == [f"ExecStartPre={self.FIRST_FORM}"]

        new = self.gated(old)

        assert lines(new, "ExecStartPre") == [f"ExecStartPre={self.WRAPPED}"]
        assert [line for line in lines(new, "ExecReload") if "gate" in line] == [
            f"ExecReload={self.WRAPPED}"
        ]
        assert len(lines(new, "ExecReload")) == 2
        assert new.index("ExecStartPre=") < new.index("ExecStart=")

    def test_the_new_form_is_left_whatever_path_its_line_names(self) -> None:
        once = compose_upgrade.with_gate(
            self.unit(), domain="a.b", executable="/usr/local/bin/noust", project="a-b"
        )

        assert self.gated(once) == once
        assert "/usr/local/bin/noust" in once

    def test_a_project_the_unit_already_passes_is_the_one_kept(self) -> None:
        new = self.gated(self.unit(" -p legacy"), project="other")

        assert "-p legacy" in lines(new, "ExecStart")[0]
        assert "--project legacy'" in lines(new, "ExecStartPre")[0]
        assert "-p other" not in new

    def test_taking_the_gate_away_leaves_the_unit_as_the_old_template_wrote_it(self) -> None:
        plain = compose_upgrade.without_gate(self.gated(self.unit()))

        assert not lines(plain, "ExecStartPre")
        assert len(lines(plain, "ExecReload")) == 1
        assert "up -d --remove-orphans --build" in lines(plain, "ExecReload")[0]

    def test_taking_it_away_takes_the_first_form_too(self) -> None:
        text = self.unit().replace(
            "ExecStart=/usr", f"ExecStartPre={self.FIRST_FORM}\nExecStart=/usr", 1
        )

        assert not lines(compose_upgrade.without_gate(text), "ExecStartPre")

    def test_a_body_that_is_no_compose_unit_is_not_touched(self) -> None:
        text = "# Generated by Noust\n[Service]\nExecStart=/usr/bin/node server.js\n"

        assert self.gated(text) == text

    def test_nothing_that_reaches_the_line_can_start_another_directive_or_a_command(self) -> None:
        for domain in ("a.b\nUser=nobody", "a.b'; reboot; '", "a b", "a.b$(id)", "a%b"):
            with pytest.raises(ValueError):
                compose_upgrade.with_gate(
                    self.unit(), domain=domain, executable="/usr/bin/noust", project="a-b"
                )

    def test_an_executable_that_cannot_be_written_into_the_line_is_refused_not_swapped(
        self,
    ) -> None:
        # Another path would look for Noust where it is not and let every start through.
        for executable in ("/opt/my noust/bin/noust", "/opt/n'; reboot; '/noust", "/opt/$HOME/n"):
            with pytest.raises(ValueError):
                compose_upgrade.with_gate(
                    self.unit(), domain="a.b", executable=executable, project="a-b"
                )
