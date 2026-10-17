# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The hooks of a preview's life: seeding it once it serves, cleaning up before it goes
(checklist B-40).

``noust.yaml`` declares ``preview_post_create`` and ``preview_pre_destroy`` beside the
deployment's hooks. They run in the preview, as its own account, the way ``noust app exec``
runs a command; a failure is a warning and never keeps a preview from being built or removed.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.store import NoustStore
from noust.deployers.helpers.hooks import ALL_PHASES, Hook, HookSet
from noust.deployers.helpers.project_file import parse_hooks_document
from noust.managers import previews
from noust.terminal import execution
from tests.test_previews import (
    PARENT,
    FakeContext,
    child_domain,
    clock,
    deployed_child,
    enable,
    jobs,
    posted,
    store,
    stored_preview,
    timer,
)

FIXTURES = (clock, jobs, posted, store, timer)

DOCUMENT = """
hooks:
  pre_deploy:
    - run: ./migrate.sh
  preview_post_create:
    - run: ./scripts/seed.sh --small
      timeout: 120
    - run: ./scripts/webhook-register.sh
  preview_pre_destroy:
    - run: ./scripts/cleanup.sh
"""


class TestTheDocument:
    def test_the_phases_of_a_previews_life_are_read(self) -> None:
        hooks = parse_hooks_document(DOCUMENT, source="repo")

        assert [h.command for h in hooks.preview_post_create] == [
            "./scripts/seed.sh --small",
            "./scripts/webhook-register.sh",
        ]
        assert hooks.preview_post_create[0].timeout == 120
        assert [h.command for h in hooks.preview_pre_destroy] == ["./scripts/cleanup.sh"]
        assert [h.command for h in hooks.pre_deploy] == ["./migrate.sh"]
        assert hooks.declared

    def test_a_document_with_only_those_is_a_document(self) -> None:
        hooks = parse_hooks_document(
            "hooks:\n  preview_pre_destroy:\n    - run: ./bye.sh\n", source="repo"
        )

        assert hooks.declared and hooks.source == "repo"

    def test_they_are_in_the_description_the_api_and_the_cli_print(self) -> None:
        described = parse_hooks_document(DOCUMENT, source="operator").to_dict()

        assert set(ALL_PHASES) <= set(described)
        assert described["preview_pre_destroy"][0]["run"] == ["./scripts/cleanup.sh"]

    @pytest.mark.parametrize("key", ["workdir", "migrates"])
    def test_a_preview_hook_has_no_workdir_and_no_schema_change(self, key: str) -> None:
        value = "scripts" if key == "workdir" else "true"
        text = f"hooks:\n  preview_post_create:\n    - run: ./seed.sh\n      {key}: {value}\n"

        with pytest.raises(ValidationError) as caught:
            parse_hooks_document(text, source="repo")

        assert caught.value.field == f"hooks.preview_post_create[0].{key}"

    def test_an_unknown_phase_is_still_refused_and_the_message_lists_the_new_ones(self) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_hooks_document("hooks:\n  preview_midway:\n    - run: x\n", source="repo")

        assert "preview_post_create" in (caught.value.details or "")

    def test_the_deploy_hooks_are_untouched(self) -> None:
        hooks = HookSet(pre_deploy=(Hook(run=("a",)),))

        assert hooks.phase("pre_deploy") == hooks.pre_deploy
        assert hooks.phase("preview_post_create") == ()


class Exec:
    """Stands in for ``noust app exec``: records what ran, and how each ended."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.exit_codes: dict[str, int] = {}
        self.raises: DeploymentError | None = None

    def __call__(self, domain: str, argv: list[str], **kw: Any) -> execution.ExecResult:
        self.calls.append(
            {"domain": domain, "argv": argv, **{k: v for k, v in kw.items() if k != "on_line"}}
        )
        if self.raises is not None:
            raise self.raises
        kw["on_line"](f"ran {argv[0]}")
        code = self.exit_codes.get(argv[0], 0)
        return execution.ExecResult(code, False, 0.1, 10, False, domain)


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> Exec:
    fake = Exec()
    monkeypatch.setattr(execution, "run_exec", fake)
    return fake


@pytest.fixture
def declared(monkeypatch: pytest.MonkeyPatch) -> None:
    hooks = parse_hooks_document(DOCUMENT, source="repo")
    monkeypatch.setattr(
        "noust.deployers.helpers.hooks.resolve_hooks", lambda app, root, store: hooks
    )


class TestRunningThem:
    def test_each_runs_in_the_preview_in_order(
        self, store: NoustStore, ran: Exec, declared: None
    ) -> None:
        deployed_child(store)
        lines: list[str] = []

        warnings = previews.run_preview_hooks(child_domain(), "preview_post_create", lines.append)

        assert warnings == []
        assert [c["argv"] for c in ran.calls] == [
            ["./scripts/seed.sh", "--small"],
            ["./scripts/webhook-register.sh"],
        ]
        assert all(c["domain"] == child_domain() for c in ran.calls)
        assert ran.calls[0]["timeout"] == 120
        assert "ran ./scripts/seed.sh" in lines

    def test_the_first_failure_is_a_warning_and_stops_the_rest(
        self, store: NoustStore, ran: Exec, declared: None
    ) -> None:
        deployed_child(store)
        ran.exit_codes["./scripts/seed.sh"] = 3

        warnings = previews.run_preview_hooks(
            child_domain(), "preview_post_create", lambda _l: None
        )

        assert len(warnings) == 1 and "exited with 3" in warnings[0]
        assert len(ran.calls) == 1

    def test_a_command_that_could_not_start_is_a_warning(
        self, store: NoustStore, ran: Exec, declared: None
    ) -> None:
        deployed_child(store)
        ran.raises = DeploymentError("Nothing to run it in")

        warnings = previews.run_preview_hooks(
            child_domain(), "preview_pre_destroy", lambda _l: None
        )

        assert "could not run" in warnings[0] and "Nothing to run it in" in warnings[0]

    def test_a_preview_that_declares_none_runs_nothing(
        self, store: NoustStore, ran: Exec, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deployed_child(store)
        monkeypatch.setattr(
            "noust.deployers.helpers.hooks.resolve_hooks", lambda app, root, store: HookSet()
        )

        assert previews.run_preview_hooks(child_domain(), "preview_post_create", print) == []
        assert ran.calls == []

    def test_a_file_that_is_not_valid_is_logged_and_runs_nothing(
        self, store: NoustStore, ran: Exec, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deployed_child(store)

        def broken(app: Any, root: Any, store: Any) -> HookSet:
            raise ValidationError("hooks.preview_post_create must be a list")

        monkeypatch.setattr("noust.deployers.helpers.hooks.resolve_hooks", broken)

        assert previews.run_preview_hooks(child_domain(), "preview_post_create", print) == []


class TestWhereTheyHappen:
    def build(self, monkeypatch: pytest.MonkeyPatch, store: NoustStore, *, existed: bool) -> None:
        def deploy(parent: Any, record: Any, context: Any, settings: Any) -> dict[str, Any]:
            deployed_child(store)
            return {"status": "deployed"}

        monkeypatch.setattr(previews, "_deploy", deploy)
        monkeypatch.setattr(previews, "_update", lambda domain, commit, context: {"status": "ok"})
        monkeypatch.setattr(previews, "_sync_from_parent", lambda *a, **k: None)
        if existed:
            deployed_child(store)

    def test_seeding_runs_once_after_the_first_build_only(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(store)
        seen: list[tuple[str, str]] = []
        monkeypatch.setattr(
            previews,
            "run_preview_hooks",
            lambda domain, phase, report: seen.append((domain, phase)) or [],
        )
        from tests.test_previews import event

        previews.handle_pull_request(event())
        self.build(monkeypatch, store, existed=False)

        previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())

        assert seen == [(child_domain(), "preview_post_create")]
        # A later push builds again and seeds nothing.
        seen.clear()
        previews.handle_pull_request(
            event(action=previews.PullRequestAction.UPDATED, head_sha="b" * 40)
        )
        previews.preview_deploy_job(PARENT, 7, job_context=FakeContext())
        assert seen == []

    def test_a_failing_seed_does_not_fail_the_preview(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(store)
        monkeypatch.setattr(
            previews, "run_preview_hooks", lambda domain, phase, report: ["the seed exited with 3"]
        )
        from tests.test_previews import event

        previews.handle_pull_request(event())
        self.build(monkeypatch, store, existed=False)
        context = FakeContext()

        previews.preview_deploy_job(PARENT, 7, job_context=context)

        record = store.get_preview(PARENT, 7)
        assert record is not None and record.status == previews.READY
        assert "the seed exited with 3" in context.logs

    def test_clean_up_runs_before_the_preview_is_taken_down(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch, posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        deployed_child(store)
        stored_preview(store)
        order: list[str] = []
        monkeypatch.setattr(
            previews,
            "run_preview_hooks",
            lambda domain, phase, report: order.append(phase) or ["clean-up failed"],
        )
        monkeypatch.setattr(
            previews, "_delete", lambda domain, on_phase, logger: order.append("delete") or ()
        )

        warnings = previews.remove_preview(child_domain(), reason="closed")

        assert order == ["preview_pre_destroy", "delete"]
        assert warnings == ("clean-up failed",)
        assert store.get_preview(PARENT, 7) is None

    def test_a_preview_whose_application_is_gone_runs_no_clean_up(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch, posted: list[dict[str, Any]]
    ) -> None:
        enable(store)
        stored_preview(store)
        monkeypatch.setattr(
            previews, "run_preview_hooks", lambda *a, **k: pytest.fail("nothing to run it in")
        )

        previews.remove_preview(child_domain(), reason="expired")

        assert store.get_preview(PARENT, 7) is None
