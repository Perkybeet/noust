# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Making the image of a container application: a Dockerfile, Railpack, or a registry.

The commands are asserted as argv. The point of most of these tests is what is
not in them: a build secret, a registry password, anything that ``ps`` would
show to every local user.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.container_store import (
    ContainerSettings,
    build_secret_name,
    registry_secret_name,
)
from noust.core.exceptions import DeploymentError, DockerError
from noust.core.fs import RealFileSystem
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.deployers.helpers.container_image import ImageBuilder

APP = "shop-example-com"
RELEASE = "20261006-101500-ab12cd3"
TAG = f"noust-{APP}:{RELEASE}"
PASSWORD = "hunter2-registry-password"
SECRET_VALUE = "//registry.npmjs.org/:_authToken=npm_SECRETVALUE"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    path = tmp_path / "apps" / APP
    path.mkdir(parents=True)
    (path / "Dockerfile").write_text("FROM scratch\n")
    return path


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets", fs=RealFileSystem())


def builder(
    runner: FakeRunner, tree: Path, secrets: SecretStore, tmp_path: Path, **overrides
) -> ImageBuilder:
    return ImageBuilder(
        runner=runner,
        fs=RealFileSystem(),
        logger=Logger(),
        app_name=APP,
        domain="shop.example.com",
        app_path=tree,
        secrets=secrets,
        work_dir=tmp_path / "work",
        **overrides,
    )


def pulling() -> FakeRunner:
    """A runner whose docker describes the image it was asked to pull."""
    return FakeRunner().script(
        ["docker", "inspect"], stdout="sha256:" + "c" * 64 + '|["nginx@sha256:' + "d" * 64 + '"]'
    )


def everything_the_commands_saw(runner: FakeRunner) -> str:
    """Every argv and every environment the runner was given, as one text."""
    parts = [" ".join(call) for call in runner.calls]
    parts += [json.dumps(env) for env in runner.envs if env]
    return "\n".join(parts)


class TestDockerfileBuilds:
    def test_the_simplest_build(self, tree, secrets, tmp_path):
        runner = FakeRunner()

        builder(runner, tree, secrets, tmp_path).build(ContainerSettings(), TAG)

        assert runner.calls == [
            (
                "docker",
                "build",
                "--progress=plain",
                "--file",
                str(tree / "Dockerfile"),
                "--tag",
                TAG,
                str(tree),
            )
        ]
        # BuildKit, which is what secrets and the cache mounts need.
        assert runner.envs[0] == {"DOCKER_BUILDKIT": "1"}

    def test_a_build_without_the_cache_ignores_the_layers_and_pulls_the_base_again(
        self, tree, secrets, tmp_path
    ):
        runner = FakeRunner()

        builder(runner, tree, secrets, tmp_path).build(ContainerSettings(), TAG, no_cache=True)

        (call,) = runner.calls
        assert call[:2] == ("docker", "build")
        assert "--no-cache" in call and "--pull" in call
        assert call[-1] == str(tree)

    def test_a_dockerfile_elsewhere_with_a_context_a_stage_and_arguments(
        self, tree, secrets, tmp_path
    ):
        (tree / "services" / "api").mkdir(parents=True)
        (tree / "docker").mkdir()
        (tree / "docker" / "Dockerfile.prod").write_text("FROM scratch\n")
        runner = FakeRunner()
        settings = ContainerSettings(
            dockerfile="docker/Dockerfile.prod",
            context="services/api",
            target="runtime",
            build_args={"NODE_ENV": "production", "VERSION": "1.2"},
        )

        builder(runner, tree, secrets, tmp_path).build(settings, TAG)

        assert runner.calls == [
            (
                "docker",
                "build",
                "--progress=plain",
                "--file",
                str(tree / "docker" / "Dockerfile.prod"),
                "--tag",
                TAG,
                "--target",
                "runtime",
                "--build-arg",
                "NODE_ENV=production",
                "--build-arg",
                "VERSION=1.2",
                str(tree / "services" / "api"),
            )
        ]

    def test_a_containerfile_is_found_when_there_is_no_dockerfile(self, tree, secrets, tmp_path):
        (tree / "Dockerfile").unlink()
        (tree / "Containerfile").write_text("FROM scratch\n")
        runner = FakeRunner()

        builder(runner, tree, secrets, tmp_path).build(ContainerSettings(), TAG)

        assert runner.calls[0][runner.calls[0].index("--file") + 1] == str(tree / "Containerfile")

    def test_no_dockerfile_says_where_it_looked_and_what_to_do(self, tree, secrets, tmp_path):
        (tree / "Dockerfile").unlink()
        runner = FakeRunner()

        with pytest.raises(DeploymentError) as caught:
            builder(runner, tree, secrets, tmp_path).build(ContainerSettings(), TAG)

        assert "Dockerfile" in caught.value.message
        assert "Containerfile" in caught.value.details
        assert "railpack" in caught.value.details.lower()
        assert runner.calls == []

    def test_a_named_dockerfile_that_is_missing_is_an_error(self, tree, secrets, tmp_path):
        runner = FakeRunner()

        with pytest.raises(DeploymentError) as caught:
            builder(runner, tree, secrets, tmp_path).build(
                ContainerSettings(dockerfile="docker/Dockerfile.prod"), TAG
            )

        assert "docker/Dockerfile.prod" in caught.value.message
        assert runner.calls == []

    def test_a_dockerfile_that_is_a_link_out_of_the_repository_is_refused(
        self, tree, secrets, tmp_path
    ):
        outside = tmp_path / "outside"
        outside.write_text("FROM scratch\n")
        (tree / "Dockerfile").unlink()
        (tree / "Dockerfile").symlink_to(outside)
        runner = FakeRunner()

        with pytest.raises(DeploymentError) as caught:
            builder(runner, tree, secrets, tmp_path).build(ContainerSettings(), TAG)

        assert "outside" in caught.value.message
        assert runner.calls == []

    def test_a_context_that_is_a_link_out_of_the_repository_is_refused(
        self, tree, secrets, tmp_path
    ):
        (tree / "ctx").symlink_to(tmp_path)
        runner = FakeRunner()

        with pytest.raises(DeploymentError):
            builder(runner, tree, secrets, tmp_path).build(
                ContainerSettings(dockerfile="Dockerfile", context="ctx"), TAG
            )

        assert runner.calls == []

    def test_a_failed_build_carries_what_docker_said(self, tree, secrets, tmp_path):
        runner = FakeRunner().script(
            ["docker", "build"], exit_code=1, stdout="#5 ERROR: process did not complete"
        )

        with pytest.raises(DockerError) as caught:
            builder(runner, tree, secrets, tmp_path).build(ContainerSettings(), TAG)

        assert "image" in caught.value.message
        assert "process did not complete" in caught.value.details

    def test_the_builds_output_is_streamed(self, tree, secrets, tmp_path):
        runner = FakeRunner().script(["docker", "build"], stdout="step 1\nstep 2")
        lines: list[str] = []

        class Log(Logger):
            def debug(self, message: str, *args, **kwargs) -> None:
                lines.append(message)

        ImageBuilder(
            runner=runner,
            fs=RealFileSystem(),
            logger=Log(),
            app_name=APP,
            domain="shop.example.com",
            app_path=tree,
            secrets=secrets,
            work_dir=tmp_path / "work",
        ).build(ContainerSettings(), TAG)

        assert lines == ["step 1", "step 2"]


class TestBuildSecrets:
    def test_a_secret_is_mounted_from_a_private_file_and_never_named_by_value(
        self, tree, secrets, tmp_path
    ):
        secrets.write(build_secret_name(APP, "npmrc"), SECRET_VALUE)
        runner = FakeRunner()

        builder(runner, tree, secrets, tmp_path).build(
            ContainerSettings(build_secrets=("npmrc",)), TAG
        )

        call = runner.calls[0]
        source = secrets.path(build_secret_name(APP, "npmrc"))
        assert call[call.index("--secret") + 1] == f"id=npmrc,src={source}"
        assert SECRET_VALUE not in everything_the_commands_saw(runner)
        # The file the build reads is 0600 in a directory only root enters.
        assert source.stat().st_mode & 0o777 == 0o600

    def test_a_secret_that_was_never_given_a_value_stops_the_build(self, tree, secrets, tmp_path):
        runner = FakeRunner()

        with pytest.raises(DeploymentError) as caught:
            builder(runner, tree, secrets, tmp_path).build(
                ContainerSettings(build_secrets=("npmrc",)), TAG
            )

        assert "npmrc" in caught.value.message
        assert "--build-secret" in caught.value.details
        assert runner.calls == []

    def test_a_sealed_stores_decrypted_copy_is_discarded_after_the_build(
        self, tree, secrets, tmp_path
    ):
        secrets.write(build_secret_name(APP, "npmrc"), SECRET_VALUE)
        discarded: list[str] = []
        original = secrets.discard_usable_copy
        secrets.discard_usable_copy = lambda name: (discarded.append(name), original(name))[1]  # type: ignore[method-assign]
        runner = FakeRunner()

        builder(runner, tree, secrets, tmp_path).build(
            ContainerSettings(build_secrets=("npmrc",)), TAG
        )

        assert discarded == [build_secret_name(APP, "npmrc")]

    def test_the_copy_is_discarded_when_the_build_fails_too(self, tree, secrets, tmp_path):
        secrets.write(build_secret_name(APP, "npmrc"), SECRET_VALUE)
        discarded: list[str] = []
        secrets.discard_usable_copy = discarded.append  # type: ignore[method-assign]
        runner = FakeRunner().script(["docker", "build"], exit_code=1)

        with pytest.raises(DockerError):
            builder(runner, tree, secrets, tmp_path).build(
                ContainerSettings(build_secrets=("npmrc",)), TAG
            )

        assert discarded == [build_secret_name(APP, "npmrc")]


ACCOUNT = ContainerSettings(
    method="image",
    image="ghcr.io/owner/app:1.4",
    registry_server="ghcr.io",
    registry_username="ci",
    registry_secret=registry_secret_name(APP),
)


class TestTheRegistryAccount:
    def test_the_password_goes_in_on_stdin_to_a_config_of_its_own(self, tree, secrets, tmp_path):
        secrets.write(registry_secret_name(APP), PASSWORD)
        runner = FakeRunner().script(
            ["docker", "inspect"],
            stdout="sha256:" + "c" * 64 + '|["ghcr.io/owner/app@sha256:' + "d" * 64 + '"]',
        )

        builder(runner, tree, secrets, tmp_path).pull(ACCOUNT, TAG)

        login = runner.calls[0]
        assert login == ("docker", "login", "ghcr.io", "--username", "ci", "--password-stdin")
        assert runner.inputs[0] == PASSWORD
        config = runner.envs[0]["DOCKER_CONFIG"]
        assert Path(config).is_relative_to(secrets.root)
        # The pull is made with that config and no other.
        pull_index = next(
            i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "pull")
        )
        assert runner.envs[pull_index]["DOCKER_CONFIG"] == config

    def test_the_password_is_nowhere_a_process_listing_could_show_it(self, tree, secrets, tmp_path):
        secrets.write(registry_secret_name(APP), PASSWORD)
        runner = pulling()

        builder(runner, tree, secrets, tmp_path).pull(ACCOUNT, TAG)

        assert PASSWORD not in everything_the_commands_saw(runner)

    def test_the_credentials_do_not_outlive_the_pull(self, tree, secrets, tmp_path):
        secrets.write(registry_secret_name(APP), PASSWORD)
        runner = pulling()

        builder(runner, tree, secrets, tmp_path).pull(ACCOUNT, TAG)

        assert not (secrets.root / "container" / APP / "docker").exists()

    def test_nor_when_the_pull_fails(self, tree, secrets, tmp_path):
        secrets.write(registry_secret_name(APP), PASSWORD)
        runner = FakeRunner().script(["docker", "pull"], exit_code=1, stderr="denied")

        with pytest.raises(DockerError):
            builder(runner, tree, secrets, tmp_path).pull(ACCOUNT, TAG)

        assert not (secrets.root / "container" / APP / "docker").exists()

    def test_a_refused_login_stops_before_anything_is_pulled_and_does_not_repeat_the_password(
        self, tree, secrets, tmp_path
    ):
        secrets.write(registry_secret_name(APP), PASSWORD)
        runner = FakeRunner().script(
            ["docker", "login"], exit_code=1, stderr=f"unauthorized: bad {PASSWORD}"
        )

        with pytest.raises(DockerError) as caught:
            builder(runner, tree, secrets, tmp_path).pull(ACCOUNT, TAG)

        assert "ghcr.io" in caught.value.message
        assert PASSWORD not in str(caught.value)
        assert not any(call[:2] == ("docker", "pull") for call in runner.calls)
        assert not (secrets.root / "container" / APP / "docker").exists()

    def test_an_account_whose_password_is_gone_is_said_with_how_to_set_it_again(
        self, tree, secrets, tmp_path
    ):
        runner = FakeRunner()

        with pytest.raises(DeploymentError) as caught:
            builder(runner, tree, secrets, tmp_path).pull(ACCOUNT, TAG)

        assert "password" in caught.value.message
        assert "--registry-password-stdin" in caught.value.details
        assert runner.calls == []

    def test_a_build_with_an_account_pulls_its_base_images_logged_in(self, tree, secrets, tmp_path):
        secrets.write(registry_secret_name(APP), PASSWORD)
        settings = ContainerSettings(
            registry_server="ghcr.io",
            registry_username="ci",
            registry_secret=registry_secret_name(APP),
        )
        runner = FakeRunner()

        builder(runner, tree, secrets, tmp_path).build(settings, TAG)

        assert runner.calls[0][:3] == ("docker", "login", "ghcr.io")
        build = next(i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "build"))
        assert "DOCKER_CONFIG" in runner.envs[build]
        assert runner.envs[build]["DOCKER_BUILDKIT"] == "1"

    def test_an_attached_registry_login_is_used_when_the_application_has_none_of_its_own(
        self, tree, secrets, tmp_path
    ):
        from noust.core.fs import RealFileSystem
        from noust.core.registry_store import RegistryStore
        from noust.core.store import App, get_store
        from noust.managers.registry_logins import RegistryManager

        get_store().create_app(
            App(domain="shop.example.com", app_type="container", source="x", app_path=str(tree))
        )
        runner = pulling()
        registries = RegistryManager(
            store=RegistryStore(), secrets=secrets, runner=runner, fs=RealFileSystem()
        )
        registries.add("ghcr-acme", "ghcr", "bot", PASSWORD)
        registries.attach("shop.example.com", ["ghcr-acme"])
        try:
            builder(runner, tree, secrets, tmp_path).pull(
                ContainerSettings(method="image", image="ghcr.io/acme/app:1"), TAG
            )
        finally:
            # The store outlives the test: leave nobody attached to the other tests' domain.
            registries.remove("ghcr-acme")
            get_store().delete_app("shop.example.com")

        assert runner.calls[0][:3] == ("docker", "login", "ghcr.io")
        pull = next(i for i, call in enumerate(runner.calls) if call[:2] == ("docker", "pull"))
        assert "DOCKER_CONFIG" in runner.envs[pull]
        assert PASSWORD not in " ".join(c for call in runner.calls for c in call)

    def test_without_an_account_nothing_is_logged_into(self, tree, secrets, tmp_path):
        runner = pulling()

        builder(runner, tree, secrets, tmp_path).pull(
            ContainerSettings(method="image", image="nginx:1.27"), TAG
        )

        assert not any(call[:2] == ("docker", "login") for call in runner.calls)
        assert all("DOCKER_CONFIG" not in (env or {}) for env in runner.envs)


class TestPulling:
    def test_it_pulls_resolves_what_it_got_and_names_it_for_the_release(
        self, tree, secrets, tmp_path
    ):
        image_id = "sha256:" + "c" * 64
        digest = "sha256:" + "d" * 64
        runner = FakeRunner().script(
            ["docker", "inspect"], stdout=f'{image_id}|["ghcr.io/owner/app@{digest}"]'
        )
        settings = ContainerSettings(method="image", image="ghcr.io/owner/app:1.4")

        pulled = builder(runner, tree, secrets, tmp_path).pull(settings, TAG)

        assert runner.calls[0] == ("docker", "pull", "ghcr.io/owner/app:1.4")
        assert runner.calls[1][:4] == ("docker", "inspect", "--type", "image")
        assert runner.calls[1][-1] == "ghcr.io/owner/app:1.4"
        assert runner.calls[2] == ("docker", "image", "tag", image_id, TAG)
        assert pulled.image_id == image_id
        assert pulled.digest == digest

    def test_an_image_without_a_registry_digest_is_still_deployable(self, tree, secrets, tmp_path):
        runner = FakeRunner().script(["docker", "inspect"], stdout="sha256:" + "c" * 64 + "|[]")

        pulled = builder(runner, tree, secrets, tmp_path).pull(
            ContainerSettings(method="image", image="local/app"), TAG
        )

        assert pulled.digest is None

    def test_the_digest_of_the_images_own_repository_is_the_one_kept(self, tree, secrets, tmp_path):
        mine = "sha256:" + "a" * 64
        other = "sha256:" + "b" * 64
        runner = FakeRunner().script(
            ["docker", "inspect"],
            stdout=f'sha256:{"c" * 64}|["other/app@{other}", "ghcr.io/owner/app@{mine}"]',
        )

        pulled = builder(runner, tree, secrets, tmp_path).pull(
            ContainerSettings(method="image", image="ghcr.io/owner/app:1.4"), TAG
        )

        assert pulled.digest == mine

    def test_a_failed_pull_says_what_the_registry_said_and_how_to_give_an_account(
        self, tree, secrets, tmp_path
    ):
        runner = FakeRunner().script(
            ["docker", "pull"], exit_code=1, stdout="pull access denied for owner/app"
        )

        with pytest.raises(DockerError) as caught:
            builder(runner, tree, secrets, tmp_path).pull(
                ContainerSettings(method="image", image="ghcr.io/owner/app:1.4"), TAG
            )

        assert "ghcr.io/owner/app:1.4" in caught.value.message
        assert "pull access denied" in caught.value.details
        assert "--registry-user" in caught.value.details
        assert not any(call[:3] == ("docker", "image", "tag") for call in runner.calls)

    def test_a_pull_that_cannot_be_inspected_is_an_error(self, tree, secrets, tmp_path):
        runner = FakeRunner().script(["docker", "inspect"], exit_code=1, stderr="No such image")

        with pytest.raises(DockerError):
            builder(runner, tree, secrets, tmp_path).pull(
                ContainerSettings(method="image", image="nginx"), TAG
            )


class TestRailpack:
    def railpack_runner(self) -> FakeRunner:
        return FakeRunner().script(["railpack", "--version"], stdout="railpack version 0.40.1")

    def test_the_plan_is_prepared_then_built_by_docker_through_its_frontend(
        self, tree, secrets, tmp_path
    ):
        runner = self.railpack_runner()

        builder(runner, tree, secrets, tmp_path).build(ContainerSettings(method="railpack"), TAG)

        plan = tmp_path / "work" / "railpack-plan.json"
        prepare = next(call for call in runner.calls if call[:2] == ("railpack", "prepare"))
        assert prepare == (
            "railpack",
            "prepare",
            str(tree),
            "--plan-out",
            str(plan),
            "--info-out",
            str(tmp_path / "work" / "railpack-info.json"),
        )
        build = next(call for call in runner.calls if call[:2] == ("docker", "build"))
        assert build == (
            "docker",
            "build",
            "--progress=plain",
            "--build-arg",
            "BUILDKIT_SYNTAX=ghcr.io/railwayapp/railpack-frontend:v0.40.1",
            "--file",
            str(plan),
            "--tag",
            TAG,
            str(tree),
        )

    def test_a_secret_travels_in_the_environment_and_a_variable_in_the_plans_arguments(
        self, tree, secrets, tmp_path
    ):
        secrets.write(build_secret_name(APP, "NPM_RC"), SECRET_VALUE)
        runner = self.railpack_runner()
        settings = ContainerSettings(
            method="railpack",
            build_args={"RAILPACK_NODE_VERSION": "22"},
            build_secrets=("NPM_RC",),
        )

        builder(runner, tree, secrets, tmp_path).build(settings, TAG)

        prepare_index = next(
            i for i, c in enumerate(runner.calls) if c[:2] == ("railpack", "prepare")
        )
        prepare = runner.calls[prepare_index]
        # A variable is not secret and is given with its value; a secret by
        # name only, and Railpack reads its value from its own environment.
        assert prepare.count("--env") == 2
        assert "RAILPACK_NODE_VERSION=22" in prepare
        assert "NPM_RC" in prepare
        assert runner.envs[prepare_index] == {"NPM_RC": SECRET_VALUE}
        # It reads a repository nobody here wrote, without Noust's environment.
        assert runner.clean_envs[prepare_index] is True

    def test_docker_is_given_every_value_under_a_name_the_build_cannot_choose(
        self, tree, secrets, tmp_path
    ):
        secrets.write(build_secret_name(APP, "NPM_RC"), SECRET_VALUE)
        runner = self.railpack_runner()
        settings = ContainerSettings(
            method="railpack",
            build_args={"RAILPACK_NODE_VERSION": "22"},
            build_secrets=("NPM_RC",),
        )

        builder(runner, tree, secrets, tmp_path).build(settings, TAG)

        index = next(i for i, c in enumerate(runner.calls) if c[:2] == ("docker", "build"))
        build = runner.calls[index]
        assert "id=RAILPACK_NODE_VERSION,env=NOUST_BUILD_SECRET_0" in build
        assert "id=NPM_RC,env=NOUST_BUILD_SECRET_1" in build
        assert runner.envs[index]["NOUST_BUILD_SECRET_0"] == "22"
        assert runner.envs[index]["NOUST_BUILD_SECRET_1"] == SECRET_VALUE
        assert runner.envs[index]["DOCKER_BUILDKIT"] == "1"
        assert SECRET_VALUE not in " ".join(" ".join(call) for call in runner.calls)

    def test_the_cache_is_told_when_a_secret_changed(self, tree, secrets, tmp_path):
        secrets.write(build_secret_name(APP, "NPM_RC"), "one")
        first = self.railpack_runner()
        builder(first, tree, secrets, tmp_path).build(
            ContainerSettings(method="railpack", build_secrets=("NPM_RC",)), TAG
        )
        secrets.write(build_secret_name(APP, "NPM_RC"), "two")
        second = self.railpack_runner()
        builder(second, tree, secrets, tmp_path).build(
            ContainerSettings(method="railpack", build_secrets=("NPM_RC",)), TAG
        )

        def hash_of(runner: FakeRunner) -> str:
            build = next(call for call in runner.calls if call[:2] == ("docker", "build"))
            argument = next(a for a in build if a.startswith("secrets-hash="))
            return argument.split("=", 1)[1]

        assert hash_of(first) != hash_of(second)
        assert "one" not in hash_of(first)

    def test_a_context_inside_the_repository_is_what_railpack_reads(self, tree, secrets, tmp_path):
        (tree / "apps" / "api").mkdir(parents=True)
        runner = self.railpack_runner()

        builder(runner, tree, secrets, tmp_path).build(
            ContainerSettings(method="railpack", context="apps/api"), TAG
        )

        prepare = next(call for call in runner.calls if call[:2] == ("railpack", "prepare"))
        assert prepare[2] == str(tree / "apps" / "api")

    def test_without_railpack_it_says_how_to_install_it(self, tree, secrets, tmp_path):
        runner = FakeRunner().only_knows("docker")

        with pytest.raises(DeploymentError) as caught:
            builder(runner, tree, secrets, tmp_path).build(
                ContainerSettings(method="railpack"), TAG
            )

        assert "Railpack" in caught.value.message
        assert "noust setup railpack" in caught.value.details
        assert runner.calls == []

    def test_a_plan_that_cannot_be_made_is_the_railpack_error_verbatim(
        self, tree, secrets, tmp_path
    ):
        runner = self.railpack_runner().script(
            ["railpack", "prepare"], exit_code=1, stderr="no start command could be found"
        )

        with pytest.raises(DockerError) as caught:
            builder(runner, tree, secrets, tmp_path).build(
                ContainerSettings(method="railpack"), TAG
            )

        assert "no start command could be found" in caught.value.details
        assert not any(call[:2] == ("docker", "build") for call in runner.calls)

    def test_a_daemon_without_the_containerd_image_store_is_told_how_to_get_it(
        self, tree, secrets, tmp_path
    ):
        """
        Railpack's BuildKit frontend merges layers, which Docker allows with the containerd
        image store only. The real-machine harness (Docker 29 with overlay2) printed BuildKit's
        sentence and nothing about what to change, and a server that upgraded Docker keeps the
        classic store it already had.
        """
        runner = self.railpack_runner().script(
            ["docker", "build"],
            exit_code=1,
            stderr="ERROR: failed to solve: requested experimental feature mergeop has been "
            "disabled on the build server: only enabled with containerd image store backend",
        )

        with pytest.raises(DockerError) as caught:
            builder(runner, tree, secrets, tmp_path).build(
                ContainerSettings(method="railpack"), TAG
            )

        assert "only enabled with containerd image store backend" in caught.value.details
        assert "containerd-snapshotter" in caught.value.details
        assert "/etc/docker/daemon.json" in caught.value.details

    def test_the_plan_is_removed_afterwards(self, tree, secrets, tmp_path):
        runner = self.railpack_runner()

        builder(runner, tree, secrets, tmp_path).build(ContainerSettings(method="railpack"), TAG)

        assert not (tmp_path / "work" / "railpack-plan.json").exists()


class TestTheImagesOnTheHost:
    def test_the_releases_that_have_an_image_are_read_from_docker(self, tree, secrets, tmp_path):
        runner = FakeRunner().script(
            ["docker", "images"],
            stdout=f"noust-{APP}:{RELEASE}\nnoust-{APP}:20261005-090000-1111111\n"
            f"noust-{APP}:latest\nnoust-other:{RELEASE}\n<none>:<none>\n",
        )

        present = builder(runner, tree, secrets, tmp_path).present_releases()

        assert present == {RELEASE, "20261005-090000-1111111"}
        assert runner.calls[0][:2] == ("docker", "images")
        assert f"reference=noust-{APP}:*" in runner.calls[0]

    def test_docker_that_cannot_list_is_an_error_not_an_empty_answer(self, tree, secrets, tmp_path):
        runner = FakeRunner().script(["docker", "images"], exit_code=1, stderr="daemon down")

        with pytest.raises(DockerError):
            builder(runner, tree, secrets, tmp_path).present_releases()

    def test_old_images_are_removed_by_tag_and_a_failure_is_reported_not_raised(
        self, tree, secrets, tmp_path
    ):
        runner = FakeRunner().script(
            ["docker", "image", "rm", f"noust-{APP}:20261005-090000-1111111"],
            exit_code=1,
            stderr="conflict: image is being used",
        )

        failures = builder(runner, tree, secrets, tmp_path).remove_images(
            [RELEASE, "20261005-090000-1111111"]
        )

        assert runner.calls == [
            ("docker", "image", "rm", f"noust-{APP}:{RELEASE}"),
            ("docker", "image", "rm", f"noust-{APP}:20261005-090000-1111111"),
        ]
        assert len(failures) == 1
        assert "being used" in failures[0]
