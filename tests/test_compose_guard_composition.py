# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a file brings in is judged as what it says, not as what Compose makes of it.

``docker compose config`` reads the ``env_file`` of every service and puts its lines in ``environment``,
so the model the guard judges has no path left to look at: an ``env_file`` of another application's
``.env`` in a file that an ``include`` or an ``extends`` brought in was never found. The files the
document brings in are read now, each judged from its own directory (or the ``project_directory`` the
include names), and the model is also asked not to resolve them (``config --no-env-resolution``) where
the Compose in use knows how, so that no version leaves a door. What cannot be read is refused, and a
file that leaves the application is not read at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.compose_yaml import load_compose
from noust.core.runner import FakeRunner
from noust.deployers.helpers import compose_guard
from noust.deployers.helpers.compose_guard import (
    ComposeFindings,
    GuardContext,
    inspect_compose,
    with_resolved,
)

OTHER_ENV = "/var/www/apps/shop-example-com/.env"


@pytest.fixture
def app(tmp_path: Path) -> Path:
    """An application directory with a ``deploy`` folder, away from the applications directory."""
    directory = tmp_path / "evil-example-org"
    (directory / "deploy").mkdir(parents=True)
    return directory


def judged(document: Any, app: Path) -> ComposeFindings:
    """What the guard finds in a document for the application at ``app``."""
    context = GuardContext(
        app_path=app, project_dir=app, domain="evil.example.org", project="evil-example-org"
    )
    return inspect_compose(document, app, context=context)


def write(app: Path, name: str, text: str) -> None:
    """Write a file of the application."""
    target = app / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def main(text: str) -> Any:
    """A compose document, read the way Compose reads it."""
    return load_compose(text)


class TestWhatAnIncludedFileSays:
    """The env_file Compose folds into the model is found where it is written."""

    def test_the_env_file_of_another_application_in_an_included_file_is_refused_for_good(
        self, app: Path
    ) -> None:
        write(
            app, "deploy/part.yaml", f"services:\n  w:\n    image: x\n    env_file: {OTHER_ENV}\n"
        )
        document = main("include:\n  - deploy/part.yaml\nservices:\n  other:\n    image: x\n")

        found = judged(document, app)

        hard = [s for s in found.refused if s in found.hard]
        assert len(hard) == 1, found.refused
        assert "service w names" in hard[0] and "another application's files" in hard[0]

    def test_a_file_included_by_an_included_file_is_judged_too(self, app: Path) -> None:
        write(app, "deploy/part.yaml", "include:\n  - deeper/more.yaml\nservices: {}\n")
        write(
            app, "deploy/deeper/more.yaml", "services:\n  w:\n    image: x\n    privileged: true\n"
        )

        found = judged(main("include: [deploy/part.yaml]\nservices: {}\n"), app)

        assert any("service w is privileged" in s for s in found.refused), found.refused

    def test_what_an_included_file_gives_the_machine_is_found_in_the_file_as_written(
        self, app: Path
    ) -> None:
        write(app, "deploy/part.yaml", "services:\n  w:\n    image: x\n    use_api_socket: true\n")

        found = judged(main("include: [deploy/part.yaml]\nservices: {}\n"), app)

        assert any("Docker's API socket" in s for s in found.refused), found.refused

    def test_a_key_the_guard_does_not_know_in_an_included_file_is_refused(self, app: Path) -> None:
        write(app, "deploy/part.yaml", "services:\n  w:\n    image: x\n    brand_new_key: 1\n")

        found = judged(main("include: [deploy/part.yaml]\nservices: {}\n"), app)

        assert any("service w uses brand_new_key" in s for s in found.refused), found.refused

    def test_the_relative_paths_of_an_included_file_are_read_from_its_own_directory(
        self, app: Path
    ) -> None:
        # From deploy/ these are the application's own .env and a file above the application.
        write(
            app,
            "deploy/part.yaml",
            "services:\n  inside:\n    image: x\n    env_file: ../.env\n"
            "  outside:\n    image: x\n    env_file: ../../outside.env\n",
        )

        found = judged(main("include: [deploy/part.yaml]\nservices: {}\n"), app)

        assert len(found.refused) == 1, found.refused
        assert "service outside names ../../outside.env in env_file, outside" in found.refused[0]

    def test_the_project_directory_of_an_include_is_the_base_of_what_it_names(
        self, app: Path
    ) -> None:
        write(app, "deploy/part.yaml", "services:\n  w:\n    image: x\n    env_file: ../x.env\n")

        beside = judged(main("include: [deploy/part.yaml]\nservices: {}\n"), app)
        from_the_root = judged(
            main("include:\n  - path: deploy/part.yaml\n    project_directory: .\nservices: {}\n"),
            app,
        )

        # ../x.env from deploy/ is the application's own; from its root it leaves.
        assert not beside
        assert len(from_the_root.refused) == 1
        assert "env_file, outside the application" in from_the_root.refused[0]

    def test_a_project_directory_that_leaves_the_application_is_not_followed_and_is_refused(
        self, app: Path
    ) -> None:
        write(app, "deploy/part.yaml", "services:\n  w:\n    image: x\n")

        found = judged(
            main(
                "include:\n  - path: deploy/part.yaml\n    project_directory: /srv/other\n"
                "services: {}\n"
            ),
            app,
        )

        assert len(found.refused) == 1
        assert "include.project_directory" in found.refused[0]

    def test_a_file_that_includes_itself_is_read_once(self, app: Path) -> None:
        write(app, "a.yaml", "include: [b.yaml]\nservices: {}\n")
        write(
            app,
            "b.yaml",
            "include: [a.yaml]\nservices:\n  w:\n    image: x\n    privileged: true\n",
        )

        found = judged(main("include: [a.yaml]\nservices: {}\n"), app)

        assert [s for s in found.refused if "is privileged" in s] == [
            "service w is privileged: it has every capability and every device of this server"
        ]
        assert not [s for s in found.refused if "which Noust could not read" in s]

    def test_a_path_made_of_variables_is_left_to_the_model(self, app: Path) -> None:
        assert not judged(main("include: ['${DIR}/part.yaml']\nservices: {}\n"), app)


class TestWhatCannotBeReadIsRefused:
    """A file that is not a compose file is not guessed at, and its words are never repeated."""

    @pytest.mark.parametrize(
        ("name", "content", "reason"),
        [
            ("missing.yaml", None, "not there or is not a file"),
            ("folder", "", "not there or is not a file"),
            (
                "broken.yaml",
                "services: [unclosed\n  secret: hunter2hunter2\n",
                "could not read as a compose document",
            ),
            ("env.txt", "DB_PASSWORD=hunter2hunter2\n", "could not read as a compose document"),
            ("list.yaml", "- hunter2hunter2\n- b\n", "could not read as a compose document"),
        ],
    )
    def test_what_is_not_a_compose_file(
        self, app: Path, name: str, content: str | None, reason: str
    ) -> None:
        if name == "folder":
            (app / "folder").mkdir()
        elif content is not None:
            write(app, name, content)

        found = judged(main(f"include: [{name}]\nservices: {{}}\n"), app)

        assert len(found.refused) == 1, found.refused
        assert reason in found.refused[0]
        assert "hunter2" not in found.refused[0]
        assert found.refused[0] not in found.hard

    def test_a_file_that_is_not_text_is_refused(self, app: Path) -> None:
        (app / "binary.yaml").write_bytes(b"\xff\xfe\x00\x01 services")

        found = judged(main("include: [binary.yaml]\nservices: {}\n"), app)

        assert len(found.refused) == 1 and "could not read" in found.refused[0]

    def test_a_file_that_is_too_large_is_refused_unread(
        self, app: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write(app, "big.yaml", "services: {}\n" + "# padding\n" * 20)
        monkeypatch.setattr(compose_guard, "MAX_COMPOSED_BYTES", 40)

        found = judged(main("include: [big.yaml]\nservices: {}\n"), app)

        assert len(found.refused) == 1 and "larger than 1 MiB" in found.refused[0]

    def test_a_chain_of_files_longer_than_the_budget_is_refused(self, app: Path) -> None:
        for number in range(compose_guard.MAX_COMPOSED_DEPTH + 3):
            write(app, f"f{number}.yaml", f"include: [f{number + 1}.yaml]\nservices: {{}}\n")
        write(app, f"f{compose_guard.MAX_COMPOSED_DEPTH + 3}.yaml", "services: {}\n")

        found = judged(main("include: [f0.yaml]\nservices: {}\n"), app)

        assert len(found.refused) == 1
        assert "nested too deep" in found.refused[0]

    def test_more_files_than_the_budget_are_refused(
        self, app: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(compose_guard, "MAX_COMPOSED_FILES", 3)
        names = [f"p{number}.yaml" for number in range(6)]
        for name in names:
            write(app, name, "services: {}\n")

        found = judged(main("include: [" + ", ".join(names) + "]\nservices: {}\n"), app)

        assert [s for s in found.refused if "nested too deep, or among too many" in s]

    def test_a_file_outside_the_application_is_refused_by_its_line_and_never_read(
        self, app: Path
    ) -> None:
        outside = app.parent / "outside.yaml"
        outside.write_text("services: [unclosed\n  secret: hunter2hunter2\n", encoding="utf-8")

        found = judged(main("include: [../outside.yaml]\nservices: {}\n"), app)

        assert len(found.refused) == 1
        assert "outside the application" in found.refused[0]
        assert "could not read" not in found.refused[0] and "not there" not in found.refused[0]

    def test_a_link_that_leads_out_is_not_followed_to_read(self, app: Path) -> None:
        outside = app.parent / "elsewhere.yaml"
        outside.write_text(
            "services:\n  w:\n    image: x\n    privileged: true\n", encoding="utf-8"
        )
        (app / "inc.yaml").symlink_to(outside)

        found = judged(main("include: [inc.yaml]\nservices: {}\n"), app)

        assert len(found.refused) == 1
        assert "outside the application" in found.refused[0]
        assert not [s for s in found.refused if "is privileged" in s]

    def test_a_download_is_refused_as_one_and_not_read(self, app: Path) -> None:
        found = judged(main("include: ['git://example.invalid/r.git']\nservices: {}\n"), app)

        assert len(found.refused) == 1
        assert "download" in found.refused[0]


class TestWhatAnExtendedFileSays:
    """The service a service extends is judged as the service it is made of."""

    def test_the_env_file_of_another_application_in_an_extended_service_is_refused_for_good(
        self, app: Path
    ) -> None:
        write(
            app,
            "deploy/part.yaml",
            f"services:\n  base:\n    image: x\n    env_file: {OTHER_ENV}\n",
        )
        document = main(
            "services:\n  web:\n    extends:\n      file: deploy/part.yaml\n      service: base\n"
        )

        found = judged(document, app)

        hard = [s for s in found.refused if s in found.hard]
        assert len(hard) == 1, found.refused
        assert hard[0].startswith("service web names") and "another application's files" in hard[0]

    def test_only_the_service_that_is_extended_is_judged(self, app: Path) -> None:
        write(
            app,
            "deploy/part.yaml",
            "services:\n  base:\n    image: x\n  evil:\n    image: x\n    privileged: true\n",
        )
        document = main(
            "services:\n  web:\n    extends:\n      file: deploy/part.yaml\n      service: base\n"
        )

        assert not judged(document, app)

    def test_the_paths_of_an_extended_service_are_read_from_its_files_directory(
        self, app: Path
    ) -> None:
        write(
            app,
            "deploy/part.yaml",
            "services:\n  base:\n    image: x\n    env_file: ../../out.env\n",
        )
        document = main(
            "services:\n  web:\n    extends:\n      file: deploy/part.yaml\n      service: base\n"
        )

        found = judged(document, app)

        assert len(found.refused) == 1
        assert "service web names ../../out.env in env_file, outside" in found.refused[0]

    def test_an_extended_file_that_cannot_be_read_is_refused(self, app: Path) -> None:
        document = main(
            "services:\n  web:\n    extends:\n      file: deploy/missing.yaml\n      service: base\n"
        )

        found = judged(document, app)

        assert len(found.refused) == 1
        assert "not there or is not a file" in found.refused[0] and "(extends)" in found.refused[0]

    def test_an_extended_service_that_extends_another_file_is_followed(self, app: Path) -> None:
        write(
            app,
            "deploy/part.yaml",
            "services:\n  base:\n    extends:\n      file: ../common.yaml\n      service: root\n",
        )
        write(app, "common.yaml", "services:\n  root:\n    image: x\n    privileged: true\n")
        document = main(
            "services:\n  web:\n    extends:\n      file: deploy/part.yaml\n      service: base\n"
        )

        found = judged(document, app)

        assert any("service web is privileged" in s for s in found.refused), found.refused

    def test_extending_a_service_of_the_same_file_reads_nothing(self, app: Path) -> None:
        document = main("services:\n  base:\n    image: x\n  web:\n    extends: base\n")

        assert not judged(document, app)


class TestTheModelAskedNotToResolveTheEnvFiles:
    """Where the Compose in use knows how, the model keeps the paths, whatever the files say."""

    COMMANDS = [
        ["docker", "compose", "-p", "stack", "config", "--profile", "x", "--format", "json"]
    ]

    def resolved(self, runner: FakeRunner, app: Path) -> ComposeFindings:
        context = GuardContext(
            app_path=app, project_dir=app, domain="evil.example.org", project="evil-example-org"
        )
        findings, _said = with_resolved(
            ComposeFindings(), {"services": {}}, runner, self.COMMANDS, app, context=context
        )
        return findings

    def test_the_commands_are_asked_with_the_flag_after_config(self) -> None:
        asked = compose_guard._without_env_resolution(self.COMMANDS)

        assert asked == [
            [
                "docker",
                "compose",
                "-p",
                "stack",
                "config",
                "--no-env-resolution",
                "--profile",
                "x",
                "--format",
                "json",
            ]
        ]
        assert compose_guard._without_env_resolution([["docker", "ps"]]) == []

    def test_an_env_file_that_only_this_model_has_is_refused_for_good(self, app: Path) -> None:
        resolved = json.dumps({"services": {"w": {"image": "x", "environment": {"A": "b"}}}})
        kept = json.dumps(
            {"services": {"w": {"image": "x", "env_file": [{"path": OTHER_ENV, "required": True}]}}}
        )
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout=resolved)
        runner.script(
            ["docker", "compose", "-p", "stack", "config", "--no-env-resolution"], stdout=kept
        )

        findings = self.resolved(runner, app)

        hard = [s for s in findings.refused if s in findings.hard]
        assert len(hard) == 1, findings.refused
        assert "another application's files" in hard[0]

    def test_a_compose_that_does_not_know_the_flag_is_judged_by_the_model_and_the_files(
        self, app: Path
    ) -> None:
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout='{"services": {"w": {"image": "x"}}}')
        runner.script(
            ["docker", "compose", "-p", "stack", "config", "--no-env-resolution"],
            stderr="unknown flag: --no-env-resolution",
            exit_code=1,
        )

        findings = self.resolved(runner, app)

        assert not findings
        assert len(runner.calls) == 2

    def judged_with(self, document: Any, app: Path, runner: FakeRunner) -> ComposeFindings:
        context = GuardContext(
            app_path=app, project_dir=app, domain="evil.example.org", project="evil-example-org"
        )
        written = inspect_compose(document, app, context=context)
        findings, _said = with_resolved(
            written, document, runner, self.COMMANDS, app, context=context
        )
        return findings

    def test_an_env_file_made_of_a_variable_is_judged_where_this_compose_kept_the_path(
        self, app: Path
    ) -> None:
        document = main("services:\n  w:\n    image: x\n    env_file: ${ENV_FILE:-.env}\n")
        kept = json.dumps(
            {"services": {"w": {"image": "x", "env_file": [{"path": OTHER_ENV, "required": True}]}}}
        )
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout='{"services": {"w": {"image": "x"}}}')
        runner.script(
            ["docker", "compose", "-p", "stack", "config", "--no-env-resolution"], stdout=kept
        )

        findings = self.judged_with(document, app, runner)

        hard = [s for s in findings.refused if s in findings.hard]
        assert len(hard) == 1 and "another application's files" in hard[0], findings.refused
        assert not [s for s in findings.refused if "where that leads" in s]

    def test_one_that_stays_in_the_application_is_nothing(self, app: Path) -> None:
        document = main("services:\n  w:\n    image: x\n    env_file: ${ENV_FILE:-.env}\n")
        kept = json.dumps(
            {"services": {"w": {"image": "x", "env_file": [{"path": f"{app}/.env"}]}}}
        )
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout='{"services": {"w": {"image": "x"}}}')
        runner.script(
            ["docker", "compose", "-p", "stack", "config", "--no-env-resolution"], stdout=kept
        )

        assert not self.judged_with(document, app, runner)

    def test_a_compose_that_keeps_no_path_leaves_the_door_unseen_and_it_is_refused(
        self, app: Path
    ) -> None:
        document = main("services:\n  w:\n    image: x\n    env_file: ${ENV_FILE:-.env}\n")
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout='{"services": {"w": {"image": "x"}}}')
        runner.script(
            ["docker", "compose", "-p", "stack", "config", "--no-env-resolution"],
            stderr="unknown flag: --no-env-resolution",
            exit_code=1,
        )

        findings = self.judged_with(document, app, runner)

        assert len(findings.refused) == 1, findings.refused
        assert "service w names its env_file through a variable" in findings.refused[0]
        assert findings.refused[0] not in findings.hard

    def test_a_service_of_an_included_file_that_this_compose_does_not_keep_is_unseen(
        self, app: Path
    ) -> None:
        write(app, "deploy/part.yaml", "services:\n  db:\n    image: x\n    env_file: ${DB_ENV}\n")
        document = main("include: [deploy/part.yaml]\nservices: {}\n")
        folded = json.dumps({"services": {"db": {"image": "x", "environment": {"A": "b"}}}})
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout=folded)

        findings = self.judged_with(document, app, runner)

        assert [s for s in findings.refused if "service db names its env_file" in s]

    def test_a_path_that_is_written_out_needs_no_model_to_be_seen(self, app: Path) -> None:
        document = main("services:\n  w:\n    image: x\n    env_file: ./.env\n")
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout='{"services": {"w": {"image": "x"}}}')

        assert not self.judged_with(document, app, runner)

    def test_a_label_file_made_of_a_variable_is_always_unseen(self, app: Path) -> None:
        document = main("services:\n  w:\n    image: x\n    label_file: ${LABELS}\n")
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout='{"services": {"w": {"image": "x"}}}')

        findings = self.judged_with(document, app, runner)

        assert [s for s in findings.refused if "names its label_file through a variable" in s]

    def test_an_env_file_inside_the_application_is_nothing(self, app: Path) -> None:
        kept = json.dumps(
            {"services": {"w": {"image": "x", "env_file": [{"path": f"{app}/.env"}]}}}
        )
        runner = FakeRunner()
        runner.script(["docker", "compose"], stdout='{"services": {}}')
        runner.script(
            ["docker", "compose", "-p", "stack", "config", "--no-env-resolution"], stdout=kept
        )

        assert not self.resolved(runner, app)
