# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker client the central's image carries, and the sums that fix it.

The central deploys containers on its machine's Docker when it is asked to, with the ``docker`` program and
Compose's plugin, so the image has both. They are fetched in a build stage of their own by
``packaging/container/install_docker_client.py`` and the final image copies two files out of it. What is held
here is that a file is written only when its sum is the pinned one (so a release replaced upstream, or a
download altered on the way, fails the build), that both processors Noust publishes for are pinned, and that
the image is still what it was: an unprivileged hub that deploys nothing unless it is asked twice.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

CONTAINER = Path(__file__).resolve().parent.parent / "packaging" / "container"


@pytest.fixture(scope="module")
def pins() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "install_docker_client", CONTAINER / "install_docker_client.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package_with(program: bytes) -> bytes:
    """A static Docker package as download.docker.com ships it: a tarball with ``docker/docker`` in it."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in (("docker/runc", b"runc"), ("docker/docker", program)):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestThePins:
    def test_both_processors_the_image_is_built_for_are_pinned_by_a_full_sum(
        self, pins: ModuleType
    ) -> None:
        assert set(pins.PINS) == {"x86_64", "aarch64"}
        for name, docker_sum, compose_sum in pins.PINS.values():
            assert name in ("x86_64", "aarch64")
            assert re.fullmatch(r"[0-9a-f]{64}", docker_sum)
            assert re.fullmatch(r"[0-9a-f]{64}", compose_sum)

    def test_each_file_has_its_own_sum_and_none_is_shared_by_two(self, pins: ModuleType) -> None:
        sums = [
            value
            for _, docker_sum, compose_sum in pins.PINS.values()
            for value in (docker_sum, compose_sum)
        ]

        assert len(set(sums)) == len(sums)

    def test_the_addresses_are_https_and_name_the_version_that_is_pinned(
        self, pins: ModuleType
    ) -> None:
        docker = pins.DOCKER_URL.format(arch="x86_64", version=pins.DOCKER_VERSION)
        compose = pins.COMPOSE_URL.format(arch="aarch64", version=pins.COMPOSE_VERSION)

        assert docker == "https://download.docker.com/linux/static/stable/x86_64/docker-28.5.2.tgz"
        assert compose == (
            "https://github.com/docker/compose/releases/download/v2.40.3/docker-compose-linux-aarch64"
        )

    def test_a_download_that_is_not_https_is_refused_before_anything_is_asked(
        self, pins: ModuleType
    ) -> None:
        with pytest.raises(pins.PinError, match="https"):
            pins.download("http://download.docker.com/linux/static/stable/x86_64/docker.tgz")


class TestWhatIsWritten:
    @pytest.fixture
    def machine(self, pins: ModuleType, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
        """A processor whose pins are the sums of the files this test serves."""
        program, plugin = b"docker program", b"compose plugin"
        package = package_with(program)
        monkeypatch.setitem(pins.PINS, "x86_64", ("x86_64", sha(package), sha(plugin)))
        return {"program": program, "plugin": plugin, "package": package}

    def serve(self, pins: ModuleType, files: dict[str, bytes]) -> object:
        def fetch(url: str) -> bytes:
            return files["package"] if url.endswith(".tgz") else files["plugin"]

        return fetch

    def test_both_programs_are_written_executable_when_the_files_are_the_pinned_ones(
        self, pins: ModuleType, machine: dict[str, bytes], tmp_path: Path
    ) -> None:
        written = pins.install(tmp_path, "x86_64", self.serve(pins, machine))

        assert [path.name for path in written] == ["docker", "docker-compose"]
        assert (tmp_path / "docker").read_bytes() == machine["program"]
        assert (tmp_path / "docker-compose").read_bytes() == machine["plugin"]
        assert all(path.stat().st_mode & 0o111 == 0o111 for path in written)

    def test_a_file_that_is_not_the_pinned_one_writes_nothing_and_names_both_sums(
        self, pins: ModuleType, machine: dict[str, bytes], tmp_path: Path
    ) -> None:
        replaced = {**machine, "plugin": b"a plugin that was swapped upstream"}

        with pytest.raises(pins.PinError) as caught:
            pins.install(tmp_path, "x86_64", self.serve(pins, replaced))

        message = str(caught.value)
        assert "docker-compose-linux-x86_64" in message
        assert sha(machine["plugin"]) in message and sha(replaced["plugin"]) in message
        assert list(tmp_path.iterdir()) == []

    def test_a_package_that_is_the_pinned_one_but_has_no_program_in_it_is_an_error(
        self, pins: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            entry = tarfile.TarInfo("docker/runc")
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"x"))
        empty, plugin = buffer.getvalue(), b"plugin"
        monkeypatch.setitem(pins.PINS, "x86_64", ("x86_64", sha(empty), sha(plugin)))

        with pytest.raises((pins.PinError, KeyError)):
            pins.install(tmp_path, "x86_64", lambda url: empty if url.endswith(".tgz") else plugin)

    def test_a_processor_that_has_no_pin_is_said_with_the_ones_that_do(
        self, pins: ModuleType, tmp_path: Path
    ) -> None:
        with pytest.raises(pins.PinError, match=r"riscv64.*x86_64"):
            pins.install(tmp_path, "riscv64", lambda url: b"")

    def test_the_command_says_a_wrong_file_on_the_error_stream_and_fails(
        self,
        pins: ModuleType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(pins, "download", lambda url: b"not what was pinned")
        monkeypatch.setattr(pins.platform, "machine", lambda: "x86_64")

        assert pins.main([str(tmp_path / "out")]) == 1
        assert "not the file that was pinned" in capsys.readouterr().err


class TestTheImage:
    @pytest.fixture(scope="class")
    def stages(self) -> dict[str, list[str]]:
        """The Dockerfile's instructions by stage."""
        text = (CONTAINER / "Dockerfile").read_text(encoding="utf-8")
        joined = re.sub(r"\\\n\s*", " ", text)
        stages: dict[str, list[str]] = {}
        current = ""
        for line in joined.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("FROM "):
                current = line.split(" AS ")[-1] if " AS " in line else "final"
                stages[current] = [line]
            else:
                stages[current].append(line)
        return stages

    def test_the_download_is_in_a_stage_of_its_own_with_no_package_manager(
        self, stages: dict[str, list[str]]
    ) -> None:
        builder = " ".join(stages["docker-client"])

        assert "install_docker_client.py /out" in builder
        assert "apt-get" not in builder and "curl" not in builder and "wget" not in builder

    def test_the_final_image_copies_two_files_out_of_it_and_installs_nothing_else(
        self, stages: dict[str, list[str]]
    ) -> None:
        copies = [line for line in stages["final"] if "--from=docker-client" in line]

        assert copies == [
            "COPY --from=docker-client /out/docker /usr/local/bin/docker",
            "COPY --from=docker-client /out/docker-compose "
            "/usr/local/lib/docker/cli-plugins/docker-compose",
        ]
        installs = [line for line in stages["final"] if "apt-get install" in line]
        assert len(installs) == 1 and "docker" not in installs[0]

    def test_it_is_still_an_unprivileged_hub_that_deploys_nothing_until_it_is_asked(
        self, stages: dict[str, list[str]]
    ) -> None:
        final = " ".join(stages["final"])

        assert "USER noust" in stages["final"]
        assert "NOUST_CENTRAL_ROLE=hub" in final
        assert "NOUST_DEPLOY_HERE" not in final
        assert "docker.sock" not in final
        assert "HEALTHCHECK" in final

    def test_the_wheels_version_is_still_the_one_the_release_script_keeps_in_step(
        self, stages: dict[str, list[str]]
    ) -> None:
        assert any(re.fullmatch(r"ARG NOUST_VERSION=\S+", line) for line in stages["final"])
