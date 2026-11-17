# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The package of the UGREEN App Center, ``packaging/ugreen/`` (items 198 and 199, decisions D7 and D8).

Noust is offered to the store as an unprivileged Docker app, and what was said to UGREEN about it is
what the file says: no Docker socket, no ``privileged``, no capabilities, a read-only root. The two
ports hold together (the one the icon opens only redirects, the console's is a parameter the person
types), the version is the release's, and what makes the package a signed and reviewed thing is
never in the repository: no ``.upk``, no developer signature.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.cli.commands.central import DEFAULT_PORT
from noust.core import install_source
from noust.core.compose_yaml import load_compose

REPOSITORY = Path(__file__).resolve().parent.parent
PACKAGE = REPOSITORY / "packaging" / "ugreen"
CONTAINER_COMPOSE = REPOSITORY / "packaging" / "container" / "compose.yaml"


def _pyproject() -> dict[str, Any]:
    """Read pyproject.toml: tomllib joined the standard library in 3.11, and Noust runs on 3.10."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - only on 3.10
        import tomli as tomllib

    return tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))


#: What the folder holds. A file that is not here is not part of the package, and one of these that
#: goes missing is a package that cannot be built.
EXPECTED_FILES = {
    "build.sh",
    "project.yaml",
    "rootfs_common/docker-compose.yaml",
    "rootfs_common/icon.png",
}

#: What makes a package a signed one, or a built one, and has no place in a repository.
NEVER_TRACKED = (".upk", ".sig", ".tar", ".pem", ".key", ".p12", ".product.json")

#: Variables the store gives a compose file that no parameter declares.
GIVEN_BY_THE_STORE = {"TZ"}


@pytest.fixture(scope="module")
def project() -> dict[str, Any]:
    return yaml.safe_load((PACKAGE / "project.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return load_compose((PACKAGE / "rootfs_common" / "docker-compose.yaml").read_text("utf-8"))


@pytest.fixture(scope="module")
def service(compose: dict[str, Any]) -> dict[str, Any]:
    return dict(compose["services"]["noust"])


def parameters(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["key"]: entry for entry in project["parameters"]}


class TestWhatIsInTheFolder:
    def test_it_holds_the_package_and_nothing_else(self) -> None:
        found = {
            path.relative_to(PACKAGE).as_posix() for path in PACKAGE.rglob("*") if path.is_file()
        }

        assert found == EXPECTED_FILES

    def test_nothing_built_or_signed_is_there(self) -> None:
        for path in PACKAGE.rglob("*"):
            assert not path.name.endswith(NEVER_TRACKED), path
            assert path.name != "build_dir", path

    def test_the_builder_says_where_the_signature_goes_and_that_it_is_not_here(self) -> None:
        text = (PACKAGE / "build.sh").read_text(encoding="utf-8")

        assert "never comes near this folder" in text

    def test_the_icon_is_a_256_pixel_png(self) -> None:
        header = (PACKAGE / "rootfs_common" / "icon.png").read_bytes()[:24]

        assert header[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", header[16:24]) == (256, 256)


class TestWhatItIsNotGiven:
    def test_no_socket_of_docker_and_no_part_of_the_machine(self, service: dict[str, Any]) -> None:
        for volume in service.get("volumes", []):
            text = str(volume)
            assert "docker.sock" not in text and "/var/run" not in text and "/run/" not in text
        assert [str(volume).split(":")[0] for volume in service["volumes"]] == ["noust-data"]

    @pytest.mark.parametrize(
        "key",
        [
            "privileged",
            "cap_add",
            "devices",
            "pid",
            "ipc",
            "uts",
            "userns_mode",
            "group_add",
            "sysctls",
            "extra_hosts",
            "volumes_from",
            "env_file",
        ],
    )
    def test_none_of_what_widens_a_container_is_asked_for(
        self, service: dict[str, Any], key: str
    ) -> None:
        assert key not in service

    def test_the_network_is_the_bridge_and_never_the_machines(
        self, service: dict[str, Any]
    ) -> None:
        assert service["network_mode"] == "bridge"

    def test_it_is_confined_exactly_like_the_central_of_the_container_compose(
        self, service: dict[str, Any]
    ) -> None:
        reference = load_compose(CONTAINER_COMPOSE.read_text("utf-8"))["services"]["noust"]

        for key in ("user", "cap_drop", "security_opt", "read_only", "tmpfs", "stop_grace_period"):
            assert service[key] == reference[key], key
        assert service["user"] == "10001:10001"
        assert service["cap_drop"] == ["ALL"]
        assert service["read_only"] is True
        assert "no-new-privileges:true" in service["security_opt"]

    def test_the_store_app_does_not_deploy_on_the_nas(self, service: dict[str, Any]) -> None:
        environment = service["environment"]

        assert "NOUST_DEPLOY_HERE" not in environment
        assert "NOUST_CENTRAL_ROLE" not in environment
        assert "central-deploy-here.md" in (
            PACKAGE / "rootfs_common" / "docker-compose.yaml"
        ).read_text(encoding="utf-8")

    def test_the_only_data_is_the_volume_the_image_owns(
        self, compose: dict[str, Any], service: dict[str, Any]
    ) -> None:
        assert service["volumes"] == ["noust-data:/data"]
        assert set(compose["volumes"]) == {"noust-data"}


class TestThePorts:
    def test_the_icon_opens_the_port_that_only_redirects_and_it_is_the_same_inside_and_out(
        self, project: dict[str, Any], service: dict[str, Any]
    ) -> None:
        declared = project["port"]

        assert f"{declared}:{declared}" in service["ports"]
        assert service["environment"]["NOUST_REDIRECT_PORT"] == str(declared)

    def test_it_is_a_number_few_apps_use_and_not_the_consoles_own(
        self, project: dict[str, Any]
    ) -> None:
        assert project["port"] != DEFAULT_PORT
        # The ports an app store's own apps and a home network's usually hold.
        assert project["port"] not in {
            80,
            443,
            3000,
            5000,
            8000,
            8080,
            8081,
            8096,
            8123,
            8443,
            9000,
        }
        assert 1024 < project["port"] < 49152

    def test_the_console_is_the_parameter_the_person_types_and_the_redirect_follows_it(
        self, project: dict[str, Any], service: dict[str, Any]
    ) -> None:
        parameter = parameters(project)["NOUST_CONSOLE_PORT"]

        assert (parameter["type"], parameter["changeable"], parameter["required"]) == (
            "number",
            True,
            True,
        )
        assert f"${{NOUST_CONSOLE_PORT}}:{DEFAULT_PORT}" in service["ports"]
        assert service["environment"]["NOUST_PUBLIC_HTTPS_PORT"] == "${NOUST_CONSOLE_PORT}"

    def test_those_are_the_only_ports_published(
        self, project: dict[str, Any], service: dict[str, Any]
    ) -> None:
        assert sorted(service["ports"]) == sorted(
            [f"${{NOUST_CONSOLE_PORT}}:{DEFAULT_PORT}", f"{project['port']}:{project['port']}"]
        )

    def test_the_icon_is_not_set_to_open_over_https_the_store_cannot_do_it(
        self, project: dict[str, Any]
    ) -> None:
        assert project["open_type"] == "tab"
        assert "httpsEnable" not in project and "https_port" not in project


class TestTheParameters:
    def test_every_variable_the_compose_file_uses_is_declared_or_given_by_the_store(
        self, project: dict[str, Any]
    ) -> None:
        text = (PACKAGE / "rootfs_common" / "docker-compose.yaml").read_text(encoding="utf-8")
        used = set(re.findall(r"\$\{([A-Z0-9_]+)\}", text))

        assert used - GIVEN_BY_THE_STORE <= set(parameters(project))

    def test_every_parameter_is_used_and_says_what_it_is_in_both_languages(
        self, project: dict[str, Any]
    ) -> None:
        text = (PACKAGE / "rootfs_common" / "docker-compose.yaml").read_text(encoding="utf-8")

        for key, parameter in parameters(project).items():
            assert f"${{{key}}}" in text, key
            assert set(parameter["i18n"]) == {"en-US", "es-ES"}, key
            for words in parameter["i18n"].values():
                assert words["name"] and words["description"], key

    def test_the_optional_ones_may_be_left_empty_and_the_port_may_not(
        self, project: dict[str, Any]
    ) -> None:
        found = parameters(project)

        assert found["NOUST_ALLOW_IP"]["required"] is False
        assert found["NOUST_TLS_NAMES"]["required"] is False
        assert found["NOUST_CONSOLE_PORT"]["required"] is True

    def test_the_examples_are_the_documentation_ranges(self, project: dict[str, Any]) -> None:
        text = (PACKAGE / "project.yaml").read_text(encoding="utf-8")
        addresses = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))

        assert addresses and all(address.startswith("192.0.2.") for address in addresses)

    def test_it_says_where_new_versions_come_from(self, service: dict[str, Any]) -> None:
        assert service["environment"]["NOUST_INSTALLED_FROM"] == install_source.UGREEN_APP_CENTER


class TestTheVersion:
    def test_the_image_the_store_runs_is_the_release_by_its_version(
        self, project: dict[str, Any], service: dict[str, Any]
    ) -> None:
        released = _pyproject()["project"]["version"]

        assert project["version"] == released
        assert service["image"] == f"ghcr.io/perkybeet/noust:{released}"

    def test_the_store_refuses_a_moving_tag_and_so_do_we(self, service: dict[str, Any]) -> None:
        assert not service["image"].endswith((":latest", ":stable"))
        assert ":" in service["image"] and "@" not in service["image"].split("/")[-1]

    def test_the_app_id_is_the_one_the_store_never_lets_change(
        self, project: dict[str, Any]
    ) -> None:
        assert project["app_id"] == "dev.noust.central"
        assert project["is_docker_app"] is True
        assert project["only_admin"] is True

    def test_what_it_asks_of_the_nas_is_the_network_and_nothing_else(
        self, project: dict[str, Any]
    ) -> None:
        assert project["permissions"] == ["NETWORK.ACCESS_INTERNET"]

    def test_it_is_listed_for_both_architectures_a_nas_has(self, project: dict[str, Any]) -> None:
        assert set(project["support_arch"]) == {"amd64", "arm64"}


class TestTheListing:
    def test_the_card_says_what_the_app_does_not_do_and_what_to_do_if_it_is_disabled(
        self, project: dict[str, Any]
    ) -> None:
        english = " ".join(project["i18n"]["en-US"]["description"].split())
        spanish = " ".join(project["i18n"]["es-ES"]["description"].split())

        assert "does not run applications on the NAS" in english
        assert "no access to the NAS's Docker" in english
        assert "Disabled" in english and "App Center" in english
        assert "no ejecuta aplicaciones en el propio NAS" in spanish
        assert "Desactivado" in spanish and "Centro de Apps" in spanish

    def test_the_card_points_at_the_guide_that_exists(self, project: dict[str, Any]) -> None:
        for language in ("en-US", "es-ES"):
            link = project["i18n"][language]["help"]
            assert link.endswith("/docs/ugreen.md")
            assert (REPOSITORY / "docs" / "ugreen.md").is_file()


#: A Docker that only writes down what it was asked, and makes the files the real one would: what the
#: builder does with Docker is what is under test, not Docker.
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case "$1" in
save)
    while [ "$#" -gt 0 ]; do
        if [ "$1" = "-o" ]; then
            shift
            : > "$1"
            break
        fi
        shift
    done
    ;;
run)
    for argument in "$@"; do
        case "$argument" in
        *:/work)
            mkdir -p "${argument%:/work}/build_dir/pkgs/upk"
            : > "${argument%:/work}/build_dir/pkgs/upk/amd64_nasync_dev.noust.central_x.upk"
            ;;
        esac
    done
    ;;
esac
"""


@pytest.mark.allow_subprocess
class TestTheBuilder:
    """``packaging/ugreen/build.sh`` with a Docker that cannot do anything."""

    @pytest.fixture
    def bench(self, tmp_path: Path) -> dict[str, Path]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        ugcli = tmp_path / "ugcli"
        ugcli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        ugcli.chmod(0o755)
        return {
            "bin": bin_dir,
            "ugcli": ugcli,
            "log": tmp_path / "docker.log",
            "out": tmp_path / "work",
        }

    def build(
        self, bench: dict[str, Path], *arguments: str, out: Path | None = None, **env: str
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": f"{bench['bin']}:/usr/bin:/bin",
            "DOCKER_LOG": str(bench["log"]),
            "UGCLI": str(bench["ugcli"]),
            "OUT": str(out or bench["out"]),
            **env,
        }
        return subprocess.run(
            ["sh", str(PACKAGE / "build.sh"), *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )

    def test_it_stages_the_package_outside_the_repository_and_packs_it(
        self, bench: dict[str, Path]
    ) -> None:
        done = self.build(bench, "3")

        assert done.returncode == 0, done.stderr
        project = bench["out"] / "dev.noust.central"
        staged = {
            path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()
        }
        version = yaml.safe_load((PACKAGE / "project.yaml").read_text("utf-8"))["version"]
        assert staged >= {
            "project.yaml",
            "rootfs_common/docker-compose.yaml",
            "rootfs_common/icon.png",
            f"rootfs_amd64/images/noust-{version}-amd64.tar",
            f"rootfs_arm64/images/noust-{version}-arm64.tar",
        }
        assert "amd64_nasync_dev.noust.central_x.upk" in done.stdout

    def test_it_asks_docker_for_the_published_image_of_each_architecture_and_packs_with_the_build_number(
        self, bench: dict[str, Path]
    ) -> None:
        self.build(bench, "7")
        version = yaml.safe_load((PACKAGE / "project.yaml").read_text("utf-8"))["version"]
        image = f"ghcr.io/perkybeet/noust:{version}"

        calls = bench["log"].read_text(encoding="utf-8").splitlines()

        assert f"pull --quiet --platform linux/amd64 {image}" in calls
        assert f"pull --quiet --platform linux/arm64 {image}" in calls
        run = next(call for call in calls if call.startswith("run "))
        assert "ugcli check && ugcli pack --arch all --build 7" in run
        assert f"--user {os.getuid()}:{os.getgid()}" in run
        assert "debian:12" in run

    def test_the_container_that_packs_gets_the_project_and_the_tool_and_nothing_of_the_machine(
        self, bench: dict[str, Path]
    ) -> None:
        self.build(bench)

        run = next(
            call
            for call in bench["log"].read_text(encoding="utf-8").splitlines()
            if call.startswith("run ")
        )

        assert (
            "--privileged" not in run and "docker.sock" not in run and "--network host" not in run
        )
        assert f"{bench['out']}/dev.noust.central:/work" in run
        assert f"{bench['ugcli']}:/usr/local/bin/ugcli:ro" in run

    @pytest.mark.parametrize(
        "inside",
        [
            "work-should-not-exist",
            "packaging/ugreen/work-should-not-exist",
            "packaging/../work-should-not-exist",
            "packaging/ugreen/../ugreen/a/b/c",
        ],
    )
    def test_it_refuses_to_work_inside_the_repository_and_creates_nothing_there(
        self, bench: dict[str, Path], inside: str
    ) -> None:
        done = self.build(bench, out=REPOSITORY / inside)

        assert done.returncode == 2, done.stderr
        assert "inside the repository" in done.stderr
        assert not bench["log"].exists()
        assert not (REPOSITORY / "work-should-not-exist").exists()
        assert not (REPOSITORY / "packaging" / "ugreen" / "work-should-not-exist").exists()
        assert not (REPOSITORY / "packaging" / "ugreen" / "a").exists()

    def test_a_link_into_the_repository_is_the_repository(
        self, bench: dict[str, Path], tmp_path: Path
    ) -> None:
        link = tmp_path / "elsewhere"
        link.symlink_to(REPOSITORY / "packaging")

        done = self.build(bench, out=link / "ugreen" / "work")

        assert done.returncode == 2 and "inside the repository" in done.stderr
        assert not (REPOSITORY / "packaging" / "ugreen" / "work").exists()

    def test_a_relative_directory_is_taken_from_where_it_is_run(
        self, bench: dict[str, Path], tmp_path: Path
    ) -> None:
        done = subprocess.run(
            ["sh", str(PACKAGE / "build.sh")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=tmp_path,
            env={
                "PATH": f"{bench['bin']}:/usr/bin:/bin",
                "DOCKER_LOG": str(bench["log"]),
                "UGCLI": str(bench["ugcli"]),
                "OUT": "relative-work",
            },
        )

        assert done.returncode == 0, done.stderr
        assert (tmp_path / "relative-work" / "dev.noust.central" / "project.yaml").is_file()

    @pytest.mark.parametrize("build", ["x", "1.5", "-1", "1 2", "$(id)", "1;id"])
    def test_a_build_number_is_a_number(self, bench: dict[str, Path], build: str) -> None:
        done = self.build(bench, build)

        assert done.returncode == 2
        assert "the build number is a number" in done.stderr
        assert not bench["log"].exists()

    def test_without_the_tool_it_says_what_to_set(self, bench: dict[str, Path]) -> None:
        done = self.build(bench, UGCLI="")

        assert done.returncode != 0
        assert "UGCLI" in done.stderr
        assert not bench["log"].exists()

    def test_it_stops_when_the_compose_file_runs_another_image_than_the_version(
        self, bench: dict[str, Path], tmp_path: Path
    ) -> None:
        copy = tmp_path / "copy"
        shutil.copytree(PACKAGE, copy)
        compose = copy / "rootfs_common" / "docker-compose.yaml"
        compose.write_text(
            re.sub(r"(ghcr\.io/perkybeet/noust:)\S+", r"\g<1>0.0.1", compose.read_text("utf-8")),
            "utf-8",
        )

        done = subprocess.run(
            ["sh", str(copy / "build.sh")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={
                "PATH": f"{bench['bin']}:/usr/bin:/bin",
                "UGCLI": str(bench["ugcli"]),
                "DOCKER_LOG": str(bench["log"]),
                "OUT": str(bench["out"]),
            },
        )

        assert done.returncode == 1
        assert "does not use" in done.stderr
        assert not bench["log"].exists()


@pytest.mark.allow_subprocess
@pytest.mark.parametrize("shell", ["sh", "dash"])
def test_the_builder_parses_in_posix_sh(shell: str) -> None:
    program = shutil.which(shell)
    if program is None:
        pytest.skip(f"{shell} is not installed")

    done = subprocess.run(
        [program, "-n", str(PACKAGE / "build.sh")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert done.returncode == 0, done.stderr


@pytest.mark.allow_subprocess
def test_the_builder_is_shellcheck_clean() -> None:
    """CI runs the same check; here it runs when shellcheck is on the machine."""
    program = shutil.which("shellcheck")
    if program is None:
        pytest.skip("shellcheck is not installed (CI runs it)")

    done = subprocess.run(
        [program, "-s", "sh", str(PACKAGE / "build.sh")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
