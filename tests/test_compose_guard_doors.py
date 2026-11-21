# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Everything a compose file names that is a path of the server is judged like a mount.

The guard looked at bind mounts and at the files of secrets and configs. An ``env_file``, a build
context, an additional context, a build secret or an SSH key, an ``include`` with its own
``env_file`` and ``project_directory``, and a volume whose ``device`` is a directory of the host are
the same thing by other doors: the account that runs Compose reads them as root, and a build copies
what it reads into an image the stack owns. Noust's own state and another application's files are
refused through any of them with no exception that can allow it; the rest outside the application
is refused for a new stack and warned about for one that already had it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.compose_guard import inspect_compose

APP = Path("/var/www/apps/stack-example-com")

#: A door, spelled in the compose file, and the compose key the sentence names.
DOORS: dict[str, tuple[Any, str]] = {
    "env_file as a path": ({"env_file": "{path}"}, "env_file"),
    "env_file as a list": ({"env_file": ["{path}"]}, "env_file"),
    "env_file as a mapping": ({"env_file": [{"path": "{path}", "required": False}]}, "env_file"),
    "the build context": ({"build": {"context": "{path}"}}, "build.context"),
    "the build context as a string": ({"build": "{path}"}, "build.context"),
    "an additional context": (
        {"build": {"context": ".", "additional_contexts": {"shared": "{path}"}}},
        "build.additional_contexts",
    ),
    "an additional context as a list": (
        {"build": {"context": ".", "additional_contexts": ["shared={path}"]}},
        "build.additional_contexts",
    ),
    "an ssh key of the build": (
        {"build": {"context": ".", "ssh": ["default", "key={path}"]}},
        "build.ssh",
    ),
}


def _filled(value: Any, path: str) -> Any:
    """``value`` with ``{path}`` replaced in every string of it."""
    if isinstance(value, str):
        return value.replace("{path}", path)
    if isinstance(value, list):
        return [_filled(entry, path) for entry in value]
    if isinstance(value, dict):
        return {key: _filled(entry, path) for key, entry in value.items()}
    return value


def service_with(door: Any, path: str) -> dict[str, Any]:
    """A document whose service has ``door`` with ``{path}`` filled in."""
    return {"services": {"web": {"image": "nginx", **_filled(door, path)}}}


class TestADoorThatReachesNoustOrAnotherApplication:
    """No exception allows it."""

    @pytest.mark.parametrize("name", sorted(DOORS))
    @pytest.mark.parametrize(
        ("path", "words"),
        [
            ("/etc/noust", "Noust's own configuration and secrets"),
            ("/var/lib/noust/noust.db", "Noust's own configuration and secrets"),
            ("/var/www/apps/other-example-com/shared/.env", "another application's files"),
            ("../other-example-com/.env", "another application's files"),
            ("/var/www/apps", "contains every application's files"),
            ("/", "where Noust keeps its configuration and secrets"),
        ],
    )
    def test_is_refused_and_no_exception_allows_it(self, name: str, path: str, words: str) -> None:
        door, key = DOORS[name]

        findings = inspect_compose(service_with(door, path), APP)

        assert len(findings.refused) == 1, findings
        assert words in findings.refused[0]
        assert f"in {key}" in findings.refused[0]
        assert findings.refused[0] in findings.hard


class TestADoorOutsideTheApplication:
    """Refused for a new stack; an exception can allow it."""

    @pytest.mark.parametrize("name", sorted(DOORS))
    def test_is_refused_but_not_for_good(self, name: str) -> None:
        door, key = DOORS[name]

        findings = inspect_compose(service_with(door, "/srv/shared/secret.env"), APP)

        assert len(findings.refused) == 1, findings
        assert f"in {key}, outside the application" in findings.refused[0]
        assert findings.hard == set()

    @pytest.mark.parametrize("name", sorted(DOORS))
    def test_inside_the_application_is_the_applications_own(self, name: str) -> None:
        door, _key = DOORS[name]

        assert not inspect_compose(service_with(door, "./config/app.env"), APP)

    @pytest.mark.parametrize("name", sorted(DOORS))
    def test_a_path_made_of_a_variable_is_left_to_the_model_compose_resolves(
        self, name: str
    ) -> None:
        door, _key = DOORS[name]

        assert not inspect_compose(service_with(door, "${CONFIG_DIR}/app.env"), APP)

    def test_a_link_inside_the_application_that_leaves_it_is_a_door_out(
        self, tmp_path: Path
    ) -> None:
        app = tmp_path / "evil-example-org"
        app.mkdir()
        os.symlink("/srv", app / "config")

        findings = inspect_compose(
            {"services": {"web": {"image": "x", "env_file": "./config/app.env"}}}, app
        )

        assert len(findings.refused) == 1
        assert "through the link ./config that leads to /srv" in findings.refused[0]

    def test_the_default_env_file_of_the_stack_is_its_own(self) -> None:
        assert not inspect_compose(
            {"services": {"web": {"image": "x", "env_file": [".env", {"path": "./a.env"}]}}}, APP
        )

    def test_a_context_that_is_an_image_or_a_service_is_not_a_path(self) -> None:
        document = {
            "services": {
                "web": {
                    "build": {
                        "context": ".",
                        "additional_contexts": {
                            "base": "docker-image://alpine:3",
                            "lib": "service:lib",
                            "stage": "target:builder",
                        },
                    }
                }
            }
        }

        assert not inspect_compose(document, APP)


class TestTheFilesASecretOrAConfigNames:
    """``file:`` of a secret or a config is a door; so is what a build is given as a secret."""

    def test_a_build_secret_that_is_noust_state_is_refused_for_good(self) -> None:
        document = {
            "secrets": {"s": {"file": "/etc/noust/web-secret"}},
            "services": {"web": {"build": {"context": ".", "secrets": ["s"]}}},
        }

        findings = inspect_compose(document, APP)

        assert len(findings.refused) == 1
        assert findings.refused[0] in findings.hard

    def test_a_secret_file_of_another_application_cannot_be_excused(self) -> None:
        document = {
            "secrets": {"s": {"file": "/var/www/apps/other-example-com/.env"}},
            "services": {"web": {"image": "x", "secrets": ["s"]}},
        }

        findings = inspect_compose(document, APP)

        assert findings.refused[0] in findings.hard

    def test_an_include_that_reaches_noust_state_cannot_be_excused(self) -> None:
        findings = inspect_compose({"include": ["/etc/noust/compose.yaml"], "services": {}}, APP)

        assert findings.refused[0] in findings.hard

    def test_an_include_outside_the_application_can_be(self) -> None:
        findings = inspect_compose({"include": ["/srv/shared/compose.yaml"], "services": {}}, APP)

        assert len(findings.refused) == 1
        assert findings.hard == set()

    @pytest.mark.parametrize(
        ("entry", "key"),
        [
            ({"path": "ok.yaml", "env_file": "/etc/noust/.env"}, "include.env_file"),
            ({"path": "ok.yaml", "env_file": ["/srv/x.env"]}, "include.env_file"),
            ({"path": "ok.yaml", "project_directory": "/srv/other"}, "include.project_directory"),
        ],
    )
    def test_what_an_include_reads_beside_its_file_is_a_door(
        self, entry: Any, key: str, tmp_path: Path
    ) -> None:
        # The file it includes is there, so that what is left to find is the door and nothing else.
        (tmp_path / "ok.yaml").write_text("services: {}\n", encoding="utf-8")
        findings = inspect_compose({"include": [entry], "services": {}}, tmp_path)

        assert len(findings.refused) == 1, findings
        assert f"in {key}" in findings.refused[0]


class TestWhatIsFetchedFromTheNetwork:
    """Docker and Compose would download it; nothing on this server checks where that goes."""

    @pytest.mark.parametrize(
        "context",
        [
            "https://example.invalid/app.tar.gz",
            "http://example.invalid/app.git",
            "git://example.invalid/r.git",
            "git@example.invalid:team/r.git",
            "github.com/team/r",
            "ssh://git@example.invalid/r.git",
            "oci://example.invalid/r:1",
        ],
    )
    def test_a_remote_build_context_is_refused(self, context: str) -> None:
        findings = inspect_compose({"services": {"web": {"build": {"context": context}}}}, APP)

        assert len(findings.refused) == 1, findings
        assert "download from the network" in findings.refused[0]
        assert findings.hard == set()

    def test_a_remote_additional_context_is_refused_too(self) -> None:
        document = {
            "services": {
                "web": {
                    "build": {
                        "context": ".",
                        "additional_contexts": {"lib": "https://example.invalid/lib.git"},
                    }
                }
            }
        }

        assert len(inspect_compose(document, APP).refused) == 1

    @pytest.mark.parametrize(
        "include",
        [
            "git://example.invalid/r.git",
            "oci://example.invalid/r:1",
            "https://example.invalid/compose.yaml",
            {"path": ["https://example.invalid/compose.yaml"]},
        ],
    )
    def test_a_remote_include_is_refused(self, include: Any) -> None:
        findings = inspect_compose({"include": [include], "services": {}}, APP)

        assert len(findings.refused) == 1, findings
        assert "includes" in findings.refused[0]
        assert "download from the network" in findings.refused[0]

    def test_a_pasted_document_has_no_exception_that_can_allow_it(self) -> None:
        from noust.deployers.helpers.compose_guard import GuardContext

        context = GuardContext(APP, pasted=True)

        findings = inspect_compose(
            {"services": {"web": {"build": {"context": "https://example.invalid/a.git"}}}},
            APP,
            context=context,
        )

        assert findings.refused[0] in findings.hard


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def deployer(tmp_path: Path, store: NoustStore, said: list[str] | None = None) -> Any:
    """A deployer whose Compose cannot resolve, with its log going to ``said``."""
    runner = FakeRunner()
    runner.script(["docker", "compose"], stdout="", stderr="invalid compose project", exit_code=1)
    compose = DockerComposeDeployer(runner=runner)
    compose.configure("stack.example.com", "src", app_path=tmp_path / "stack")
    compose.store = store
    if said is not None:
        compose.logger.attach_sink(said.append)
    return compose


class TestTheDeployerHoldsTheDoorsInBothDirections:
    """A stack that already had a door goes on with a warning; one that adds it is refused."""

    OUTSIDE = {"services": {"web": {"image": "x", "env_file": "/srv/shared/secret.env"}}}
    FOREIGN = {"services": {"web": {"image": "x", "env_file": "/etc/noust/web-secret"}}}

    def test_a_new_stack_with_an_env_file_of_the_server_is_refused_with_the_way_out(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        with pytest.raises(DeploymentError) as raised:
            deployer(tmp_path, store)._check_host_privileges(self.OUTSIDE, accepted=[])

        assert "asks for root" in raised.value.message
        assert "noust app sandbox compose-exception stack.example.com" in (
            raised.value.details or ""
        )

    def test_a_stack_that_had_it_is_updated_with_a_warning(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        said: list[str] = []
        compose = deployer(tmp_path, store, said)

        compose._check_host_privileges(self.OUTSIDE, accepted=compose._refusals_in(self.OUTSIDE))

        assert any("env_file, outside the application" in line for line in said)

    def test_an_exception_allows_it(self, tmp_path: Path, store: NoustStore) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="shared env", store=store
        )

        deployer(tmp_path, store)._check_host_privileges(self.OUTSIDE, accepted=[])

    def test_noust_state_is_refused_for_a_new_stack_even_with_an_exception(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="trust me", store=store
        )

        with pytest.raises(DeploymentError) as raised:
            deployer(tmp_path, store)._check_host_privileges(self.FOREIGN, accepted=[])

        assert "reaches what no stack may reach" in raised.value.message
        assert "no exception allows it" in (raised.value.details or "")
        assert "compose-exception" not in (raised.value.details or "")

    def test_a_stack_that_had_it_is_not_stopped_for_it(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        said: list[str] = []
        compose = deployer(tmp_path, store, said)

        compose._check_host_privileges(self.FOREIGN, accepted=compose._refusals_in(self.FOREIGN))

        assert any(
            "not allowed to a new stack, with or without an exception" in line for line in said
        )

    def test_a_stack_that_adds_noust_state_is_refused_whatever_it_had(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = deployer(tmp_path, store)

        with pytest.raises(DeploymentError, match="reaches what no stack may reach"):
            compose._check_host_privileges(
                self.FOREIGN, accepted=compose._refusals_in(self.OUTSIDE)
            )

    def test_a_path_made_of_variables_the_model_cannot_resolve_is_refused_once_the_env_is_written(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        document = {"services": {"web": {"image": "x", "volumes": ["${DATA_DIR}:/data"]}}}
        compose = deployer(tmp_path, store)
        compose.compose_path = tmp_path / "stack" / "docker-compose.yml"

        compose._check_host_privileges(document, accepted=[])
        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(document, accepted=[], final=True)


class TestADownloadInBothDirections:
    """A repository's stack that already built from a URL goes on with a warning; one that adds it is refused."""

    REMOTE_BUILD = {
        "services": {"web": {"build": {"context": "https://example.invalid/app.git#main"}}}
    }
    REMOTE_INCLUDE = {
        "include": ["git://example.invalid/shared.git"],
        "services": {"web": {"image": "x"}},
    }

    @pytest.mark.parametrize("document", [REMOTE_BUILD, REMOTE_INCLUDE])
    def test_a_new_stack_is_refused_with_the_way_out(
        self, tmp_path: Path, store: NoustStore, document: dict[str, Any]
    ) -> None:
        with pytest.raises(DeploymentError) as raised:
            deployer(tmp_path, store)._check_host_privileges(document, accepted=[])

        assert "download from the network" in (raised.value.details or "")
        assert "compose-exception stack.example.com" in (raised.value.details or "")

    @pytest.mark.parametrize("document", [REMOTE_BUILD, REMOTE_INCLUDE])
    def test_a_stack_that_had_it_goes_on_with_a_warning(
        self, tmp_path: Path, store: NoustStore, document: dict[str, Any]
    ) -> None:
        said: list[str] = []
        compose = deployer(tmp_path, store, said)

        compose._check_host_privileges(document, accepted=compose._refusals_in(document))

        assert any("download from the network" in line for line in said)

    @pytest.mark.parametrize("document", [REMOTE_BUILD, REMOTE_INCLUDE])
    def test_a_stack_that_adds_it_is_refused(
        self, tmp_path: Path, store: NoustStore, document: dict[str, Any]
    ) -> None:
        compose = deployer(tmp_path, store)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(
                document, accepted=compose._refusals_in({"services": {"web": {"image": "x"}}})
            )

    @pytest.mark.parametrize("document", [REMOTE_BUILD, REMOTE_INCLUDE])
    def test_an_exception_allows_it_for_a_repository(
        self, tmp_path: Path, store: NoustStore, document: dict[str, Any]
    ) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="private registry", store=store
        )

        deployer(tmp_path, store)._check_host_privileges(document, accepted=[])
