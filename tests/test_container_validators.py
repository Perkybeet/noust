# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a container application's settings may hold, checked before anything is stored.

Every value here ends up in an argv of ``docker`` (run as root), in a compose
file or in a path under the application, so each is held to an allowlist.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import ValidationError
from noust.validators import container as rules

DIGEST = "sha256:" + "a" * 64


class TestWithTag:
    @pytest.mark.parametrize(
        ("reference", "tag", "expected"),
        [
            ("ghcr.io/acme/app", "1.4.2", "ghcr.io/acme/app:1.4.2"),
            ("ghcr.io/acme/app:1.0.0", "1.4.2", "ghcr.io/acme/app:1.4.2"),
            (f"ghcr.io/acme/app:1.0.0@{DIGEST}", "1.4.2", "ghcr.io/acme/app:1.4.2"),
            (f"nginx@{DIGEST}", "stable", "nginx:stable"),
            (
                "registry.example.com:5000/team/app:old",
                "new",
                "registry.example.com:5000/team/app:new",
            ),
        ],
    )
    def test_names_the_same_image_at_another_tag(self, reference, tag, expected):
        assert rules.with_tag(reference, tag) == expected

    @pytest.mark.parametrize("tag", ["", "-x", ".x", "a b", "a/b", "x" * 129, "a:b", "$(id)"])
    def test_refuses_what_is_not_a_tag(self, tag):
        with pytest.raises(ValidationError) as caught:
            rules.with_tag("ghcr.io/acme/app:1", tag)
        assert caught.value.field == "image_tag"


class TestImageReference:
    @pytest.mark.parametrize(
        "reference",
        [
            "nginx",
            "nginx:1.27",
            "library/nginx:1.27-alpine",
            "ghcr.io/owner/app:v1.2.3",
            "registry.example.com:5000/team/app:latest",
            f"nginx@{DIGEST}",
            f"ghcr.io/owner/app:1.0@{DIGEST}",
            "localhost/app:dev",
            "my_org/my.app__x:tag_1",
        ],
    )
    def test_accepts_what_docker_accepts(self, reference):
        assert rules.check_image_reference(reference) == reference

    @pytest.mark.parametrize(
        "reference",
        [
            "",
            " nginx",
            "nginx ",
            "-v /:/host nginx",
            "--privileged",
            "-nginx",
            "NGINX",
            "Owner/App",
            "nginx:",
            "nginx@sha256:abc",
            "nginx@md5:" + "a" * 32,
            "nginx:tag with space",
            "nginx\n",
            "nginx;rm -rf /",
            "nginx$(id)",
            "a" * 300,
            "https://registry.example.com/app",
            "../app",
            "//app",
        ],
    )
    def test_refuses_what_could_become_an_option_or_a_command(self, reference):
        with pytest.raises(ValidationError) as caught:
            rules.check_image_reference(reference)
        assert caught.value.field == "image"
        assert caught.value.details

    def test_names_the_expected_shape(self):
        with pytest.raises(ValidationError) as caught:
            rules.check_image_reference("Not An Image")
        assert "name[:tag][@sha256:digest]" in caught.value.details


class TestRepositoryPaths:
    @pytest.mark.parametrize(
        "path", ["Dockerfile", "docker/Dockerfile.prod", "services/api/Containerfile", "."]
    )
    def test_accepts_paths_inside_the_repository(self, path):
        assert rules.check_repo_path(path, field="context") == path

    @pytest.mark.parametrize(
        "path",
        ["", "/etc/passwd", "../Dockerfile", "a/../../b", "a/./b", "a//b", "a\\b", "a b", "a\x00b"],
    )
    def test_refuses_anything_that_leaves_it_or_is_ambiguous(self, path):
        with pytest.raises(ValidationError) as caught:
            rules.check_repo_path(path, field="dockerfile")
        assert caught.value.field == "dockerfile"

    def test_a_trailing_slash_is_dropped(self):
        assert rules.check_repo_path("docker/", field="context") == "docker"


class TestTarget:
    @pytest.mark.parametrize("target", ["prod", "runtime-slim", "stage_1", "a.b"])
    def test_accepts_stage_names(self, target):
        assert rules.check_target(target) == target

    @pytest.mark.parametrize("target", ["", "-x", "a b", "a;b", "a" * 200])
    def test_refuses_the_rest(self, target):
        with pytest.raises(ValidationError) as caught:
            rules.check_target(target)
        assert caught.value.field == "target"


class TestBuildArgs:
    def test_accepts_plain_arguments(self):
        args = {"NODE_ENV": "production", "VERSION": "1.2.3", "EMPTY": ""}
        assert rules.check_build_args(args) == args

    @pytest.mark.parametrize("name", ["", "1ABC", "A-B", "A B", "A=B", "a" * 200])
    def test_refuses_names_an_argument_cannot_have(self, name):
        with pytest.raises(ValidationError) as caught:
            rules.check_build_args({name: "x"})
        assert caught.value.field == "build_args"

    @pytest.mark.parametrize(
        "name", ["DATABASE_PASSWORD", "API_TOKEN", "STRIPE_SECRET_KEY", "NPM_TOKEN", "AWS_API_KEY"]
    )
    def test_refuses_names_that_look_like_secrets_and_points_at_build_secrets(self, name):
        with pytest.raises(ValidationError) as caught:
            rules.check_build_args({name: "x"})
        assert name in caught.value.message
        assert "build secret" in caught.value.details

    def test_refuses_a_value_with_credentials_in_a_url(self):
        with pytest.raises(ValidationError) as caught:
            rules.check_build_args({"REGISTRY": "https://user:hunter2@example.com/simple"})
        assert "hunter2" not in str(caught.value)

    @pytest.mark.parametrize("value", ["a\nb", "a\x00b", "x" * 5000])
    def test_refuses_values_that_break_a_command_line(self, value):
        with pytest.raises(ValidationError):
            rules.check_build_args({"A": value})

    def test_a_secret_is_not_echoed_in_the_refusal(self):
        with pytest.raises(ValidationError) as caught:
            rules.check_build_args({"A": "a\nb-hunter2"})
        assert "hunter2" not in str(caught.value)


class TestSecretIds:
    def test_accepts_ids(self):
        assert rules.check_secret_ids(["npmrc", "pip_conf", "a.b-c"]) == (
            "npmrc",
            "pip_conf",
            "a.b-c",
        )

    def test_refuses_duplicates_bad_names_and_too_many(self):
        for bad in (["a", "a"], ["-x"], ["a b"], [""], [f"s{i}" for i in range(40)]):
            with pytest.raises(ValidationError) as caught:
                rules.check_secret_ids(bad)
            assert caught.value.field == "build_secrets"


class TestContainerPort:
    @pytest.mark.parametrize("port", [1, 80, 8080, 65535])
    def test_accepts_ports(self, port):
        assert rules.check_container_port(port) == port

    @pytest.mark.parametrize("port", [0, -1, 65536, "80", True, None])
    def test_refuses_the_rest(self, port):
        with pytest.raises(ValidationError) as caught:
            rules.check_container_port(port)
        assert caught.value.field == "container_port"


class TestVolumes:
    def test_accepts_absolute_container_paths(self):
        assert rules.check_volumes(["/data", "/var/lib/app/"]) == (
            "/data",
            "/var/lib/app",
        )

    @pytest.mark.parametrize(
        "path",
        ["data", "/", "/a/../b", "/a:/b", "/a b", "", "/proc", "/sys/x", "/dev", "/a\x00b"],
    )
    def test_refuses_paths_that_are_not_a_safe_mount_point(self, path):
        with pytest.raises(ValidationError) as caught:
            rules.check_volumes([path])
        assert caught.value.field == "volumes"

    def test_refuses_two_volumes_at_one_path(self):
        with pytest.raises(ValidationError):
            rules.check_volumes(["/data", "/data/"])


class TestRestartPolicy:
    @pytest.mark.parametrize("policy", ["no", "always", "unless-stopped", "on-failure"])
    def test_accepts_docker_policies(self, policy):
        assert rules.check_restart_policy(policy) == policy

    @pytest.mark.parametrize("policy", ["", "sometimes", "on-failure:3 --x"])
    def test_refuses_the_rest(self, policy):
        with pytest.raises(ValidationError) as caught:
            rules.check_restart_policy(policy)
        assert caught.value.field == "restart_policy"


class TestRegistry:
    @pytest.mark.parametrize(
        "server", ["ghcr.io", "registry.example.com", "registry.example.com:5000", "localhost:5000"]
    )
    def test_accepts_a_registry_host(self, server):
        assert rules.check_registry_server(server) == server

    @pytest.mark.parametrize(
        "server", ["", "https://ghcr.io", "ghcr.io/owner", "-x", "a b", "ghcr.io\n"]
    )
    def test_refuses_a_url_or_a_path(self, server):
        with pytest.raises(ValidationError) as caught:
            rules.check_registry_server(server)
        assert caught.value.field == "registry_server"

    def test_the_server_must_be_the_one_the_image_is_pulled_from(self):
        assert rules.registry_of("ghcr.io/owner/app:1") == "ghcr.io"
        assert rules.registry_of("registry.example.com:5000/app") == "registry.example.com:5000"
        assert rules.registry_of("localhost/app") == "localhost"
        # Docker Hub: no host in the name.
        assert rules.registry_of("nginx") == "docker.io"
        assert rules.registry_of("owner/app:1") == "docker.io"

    @pytest.mark.parametrize("user", ["ci", "owner@example.com", "robot$builder", "a.b-c_d"])
    def test_accepts_usernames(self, user):
        assert rules.check_registry_username(user) == user

    @pytest.mark.parametrize("user", ["", "a b", "a\nb", "-x", "a" * 300])
    def test_refuses_usernames_that_break_a_command_or_a_file(self, user):
        with pytest.raises(ValidationError) as caught:
            rules.check_registry_username(user)
        assert caught.value.field == "registry_username"


class TestMethod:
    @pytest.mark.parametrize("method", ["dockerfile", "railpack", "image"])
    def test_accepts_the_three_ways(self, method):
        assert rules.check_method(method) == method

    def test_refuses_the_rest(self):
        with pytest.raises(ValidationError) as caught:
            rules.check_method("buildpacks")
        assert caught.value.field == "method"
        assert "dockerfile" in caught.value.details
