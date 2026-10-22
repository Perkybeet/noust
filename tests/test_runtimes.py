# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The runtime manager: Node from nodejs.org, Python through uv, PHP from the distribution.

Everything the machine does is faked at its seams: the document reader, the
archive downloader (which leaves a tree in the directory it was given), the
runner (which answers version commands and plays ``uv``) and the filesystem
root PHP-FPM is looked for under. What is real is the manager's own logic:
which release a request means, that a checksum travels with the download, that
a broken installation never stays, and that a runtime something runs on is not
removed.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.fs import DryRunFileSystem, RealFileSystem, set_fs
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import App, NoustStore
from noust.core.versions import RuntimeRequest, parse_runtime
from noust.managers.runtimes import (
    RuntimeManager,
    _checksum_for,
)

DIGEST = "ab" * 32


def node_index() -> bytes:
    """A trimmed nodejs.org/dist/index.json."""
    return json.dumps(
        [
            {"version": "v23.1.0", "lts": False, "files": ["linux-x64", "linux-arm64"]},
            {"version": "v22.11.0", "lts": "Jod", "files": ["linux-x64", "linux-arm64"]},
            {"version": "v22.9.0", "lts": False, "files": ["linux-x64"]},
            {"version": "v20.18.0", "lts": "Iron", "files": ["linux-x64"]},
            {"version": "v20.17.0", "lts": "Iron", "files": ["linux-x64"]},
            {"version": "v18.20.4", "lts": "Hydrogen", "files": ["linux-x64"]},
            {"version": "v22.10.0", "lts": False, "files": ["win-x64"]},
        ]
    ).encode()


def shasums(version: str = "22.11.0", platform_name: str = "linux-x64") -> bytes:
    """A SHASUMS256.txt that lists the release's tarball among others."""
    return (
        f"{'cd' * 32}  node-v{version}-darwin-arm64.tar.gz\n"
        f"{DIGEST}  node-v{version}-{platform_name}.tar.xz\n"
        f"{'ef' * 32}  node-v{version}-win-x64.zip\n"
    ).encode()


class FakeSource:
    """Stands in for SourceManager: an archive is a tree it leaves in the destination."""

    def __init__(self, *, binary: bool = True) -> None:
        self.downloads: list[tuple[str, Path]] = []
        self.binary = binary
        self.fail: Exception | None = None
        #: What the unpacked binary says when asked its version; the release's own when None.
        self.says: str | None = None

    def download_archive(self, url: str, destination: Path) -> bool:
        self.downloads.append((url, destination))
        if self.fail is not None:
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "partial").write_text("x")
            raise self.fail
        if self.binary:
            program = destination / "bin" / "node"
            program.parent.mkdir(parents=True, exist_ok=True)
            match = re.search(r"/v(\d+\.\d+\.\d+)/", url)
            assert match is not None
            program.write_text(self.says or f"v{match.group(1)}")
            program.chmod(0o755)
        return True


class PlayingRunner(FakeRunner):
    """A FakeRunner whose freshly unpacked interpreters answer their version command."""

    def run(self, argv: Any, **kwargs: Any) -> Any:
        if (
            len(argv) == 2
            and argv[1] == "--version"
            and str(argv[0]).endswith(("/bin/node", "/bin/python3"))
            and Path(argv[0]).is_file()
        ):
            self.calls.append(tuple(str(a) for a in argv))
            return CommandResult(argv=tuple(argv), exit_code=0, stdout=Path(argv[0]).read_text())
        return super().run(argv, **kwargs)


class Machine:
    """What the manager reads: documents by URL, and a runner that plays the programs."""

    def __init__(self, tmp_path: Path) -> None:
        self.documents: dict[str, bytes] = {}
        self.fetched: list[str] = []
        self.runner = PlayingRunner()
        self.source = FakeSource()
        self.php_root = tmp_path / "php-root"
        self.root = tmp_path / "runtimes"

    def fetch(self, url: str, limit: int) -> bytes:
        self.fetched.append(url)
        if url not in self.documents:
            raise DeploymentError(f"Could not read {url}")
        return self.documents[url]

    def manager(self, *, machine: str = "x86_64") -> RuntimeManager:
        return RuntimeManager(
            runner=self.runner,
            fs=RealFileSystem(),
            root=self.root,
            fetch=self.fetch,
            source_manager=self.source,  # type: ignore[arg-type]
            machine=machine,
            php_root=self.php_root,
        )

    def serve_node(self) -> None:
        self.documents["https://nodejs.org/dist/index.json"] = node_index()
        for version in ("22.11.0", "22.9.0", "20.18.0", "18.20.4", "23.1.0"):
            self.documents[f"https://nodejs.org/dist/v{version}/SHASUMS256.txt"] = shasums(version)

    def install_php_fpm(self, version: str) -> None:
        (self.php_root / "etc" / "php" / version / "fpm" / "pool.d").mkdir(parents=True)
        binary = self.php_root / "usr" / "sbin" / f"php-fpm{version}"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("")
        cli = self.php_root / "usr" / "bin" / f"php{version}"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text("")


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    return Machine(tmp_path)


@pytest.fixture(autouse=True)
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    return recorded


class TestChecksums:
    def test_the_digest_of_a_file_is_found_by_name(self) -> None:
        assert _checksum_for(shasums().decode(), "node-v22.11.0-linux-x64.tar.xz") == DIGEST

    def test_a_file_that_is_not_listed_has_none(self) -> None:
        assert _checksum_for(shasums().decode(), "node-v22.11.0-linux-ppc64le.tar.xz") is None

    def test_a_malformed_line_is_not_a_digest(self) -> None:
        assert (
            _checksum_for("nothex  node-v1-linux-x64.tar.xz\n", "node-v1-linux-x64.tar.xz") is None
        )


class TestNode:
    def test_a_major_installs_the_newest_release_of_it(self, machine: Machine) -> None:
        machine.serve_node()

        installed = machine.manager().install(parse_runtime("node@22"))

        # 22.11.0 is newer than 22.9.0, and 23.x is not 22.
        assert installed.version == "22.11.0"
        assert installed.home == machine.root / "node" / "22.11.0"
        assert (installed.home / "bin" / "node").exists()
        assert installed.managed

    def test_the_checksum_travels_with_the_download(self, machine: Machine) -> None:
        machine.serve_node()

        machine.manager().install(parse_runtime("node@22.11.0"))

        [(url, destination)] = machine.source.downloads
        assert url == (
            f"https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-x64.tar.xz#sha256={DIGEST}"
        )
        # Unpacked beside the final directory, never into it.
        assert destination.parent == machine.root / "node"
        assert destination.name.startswith(".staging-")
        assert not destination.exists()
        # A full version needs no index.
        assert "https://nodejs.org/dist/index.json" not in machine.fetched

    def test_the_architecture_names_the_build(self, machine: Machine) -> None:
        machine.documents["https://nodejs.org/dist/v22.11.0/SHASUMS256.txt"] = shasums(
            platform_name="linux-arm64"
        )

        machine.manager(machine="aarch64").install(parse_runtime("node@22.11.0"))

        assert machine.source.downloads[0][0].startswith(
            "https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-arm64.tar.xz"
        )

    def test_a_cpu_without_an_official_build_is_explained(self, machine: Machine) -> None:
        with pytest.raises(DeploymentError) as excinfo:
            machine.manager(machine="riscv64").install(parse_runtime("node@22.11.0"))

        assert "riscv64" in excinfo.value.message
        assert machine.source.downloads == []

    def test_the_unpacked_binary_must_run_and_be_the_version(self, machine: Machine) -> None:
        machine.serve_node()
        machine.source.says = "v18.0.0"

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("node@22.11.0"))

        assert "is not v22.11.0" in excinfo.value.message
        assert not (machine.root / "node" / "22.11.0").exists()
        assert list((machine.root / "node").iterdir()) == [], "staging was not removed"

    def test_an_archive_without_the_binary_installs_nothing(self, machine: Machine) -> None:
        machine.serve_node()
        machine.source.binary = False

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("node@22.11.0"))

        assert "holds no node" in excinfo.value.message
        assert list((machine.root / "node").iterdir()) == []

    def test_a_failed_download_leaves_no_staging_behind(self, machine: Machine) -> None:
        machine.serve_node()
        machine.source.fail = DeploymentError("Download failed")

        with pytest.raises(DeploymentError):
            machine.manager().install(parse_runtime("node@22.11.0"))

        assert list((machine.root / "node").iterdir()) == []

    def test_a_release_the_checksums_do_not_list_is_refused_before_downloading(
        self, machine: Machine
    ) -> None:
        machine.documents["https://nodejs.org/dist/v22.11.0/SHASUMS256.txt"] = (
            f"{DIGEST}  node-v22.11.0-darwin-arm64.tar.gz\n".encode()
        )

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("node@22.11.0"))

        assert "does not list" in excinfo.value.message
        assert machine.source.downloads == []

    def test_a_range_picks_the_newest_long_term_release_it_allows(self, machine: Machine) -> None:
        machine.serve_node()
        request = RuntimeRequest("node", ">=18 <23", explicit=False, source="engines.node")

        installed = machine.manager().install(request)

        # 22.9.0 and 22.11.0 satisfy it, 23.1.0 does not; of the long-term ones
        # 22.11.0 is the newest.
        assert installed.version == "22.11.0"

    def test_lts_means_the_newest_long_term_release(self, machine: Machine) -> None:
        machine.serve_node()

        installed = machine.manager().install(parse_runtime("node@lts"))

        assert installed.version == "22.11.0"

    def test_latest_means_the_newest_release(self, machine: Machine) -> None:
        machine.serve_node()

        installed = machine.manager().install(parse_runtime("node@latest"))

        assert installed.version == "23.1.0"

    def test_nothing_that_satisfies_is_an_error(self, machine: Machine) -> None:
        machine.serve_node()

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("node@99"))

        assert "publishes no release" in excinfo.value.message

    def test_a_version_that_is_installed_is_found_not_downloaded_again(
        self, machine: Machine
    ) -> None:
        machine.serve_node()
        manager = machine.manager()
        first = manager.ensure(parse_runtime("node@22"))

        again = manager.ensure(parse_runtime("node@22"))

        assert again == first
        assert len(machine.source.downloads) == 1

    def test_ensure_for_a_full_version_does_not_accept_another_patch(
        self, machine: Machine
    ) -> None:
        machine.serve_node()
        manager = machine.manager()
        manager.ensure(parse_runtime("node@22.9.0"))

        installed = manager.ensure(parse_runtime("node@22.11.0"))

        assert installed.version == "22.11.0"
        assert len(machine.source.downloads) == 2

    def test_the_installation_is_audited(
        self, machine: Machine, audit_events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        machine.serve_node()

        machine.manager().install(parse_runtime("node@22.11.0"))

        assert audit_events[0][0] == "runtimes.install"
        assert audit_events[0][1]["target"] == "runtime:node@22.11.0"

    def test_a_rehearsal_downloads_nothing(self, machine: Machine) -> None:
        machine.serve_node()
        set_fs(DryRunFileSystem())
        try:
            installed = machine.manager().install(parse_runtime("node@22.11.0"))
        finally:
            set_fs(None)

        assert installed.version == "22.11.0"
        assert machine.source.downloads == []
        assert not (machine.root / "node" / "22.11.0").exists()

    def test_a_stranger_in_the_runtimes_directory_is_not_a_runtime(self, machine: Machine) -> None:
        (machine.root / "node" / "not-a-version").mkdir(parents=True)
        (machine.root / "node" / ".staging-abc").mkdir()
        (machine.root / "node" / "22.1.0").mkdir()  # no bin/node: not complete

        assert machine.manager().list_installed() == []


class TestPython:
    def script_uv(self, machine: Machine, version: str = "3.12.4") -> None:
        """Make ``uv python install`` leave an interpreter in the directory it is given."""
        original = machine.runner.run

        def run(argv: Any, **kwargs: Any) -> Any:
            if list(argv[:3]) == ["uv", "python", "install"]:
                target = Path(argv[argv.index("--install-dir") + 1])
                home = target / f"cpython-{version}-linux-x86_64-gnu"
                (home / "bin").mkdir(parents=True)
                (home / "bin" / "python3").write_text(f"Python {version}")
                # What uv leaves beside it: a lock file it creates world-writable,
                # a scratch directory, and a link from the minor version's name.
                (target / ".lock").write_text("")
                (target / ".lock").chmod(0o666)
                (target / ".temp").mkdir()
                (target / "cpython-3.12-linux-x86_64-gnu").symlink_to(home)
            return original(argv, **kwargs)

        machine.runner.run = run  # type: ignore[method-assign]

    def test_without_uv_the_error_says_how_to_get_it(self, machine: Machine) -> None:
        machine.runner.only_knows("apt-get")

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("python@3.12"))

        assert "needs uv" in excinfo.value.message
        assert "astral.sh/uv" in excinfo.value.details
        assert not (machine.root / "python").exists()

    def test_uv_installs_into_a_directory_named_by_the_version_it_resolved(
        self, machine: Machine
    ) -> None:
        self.script_uv(machine)
        manager = machine.manager()

        installed = manager.install(parse_runtime("python@3.12"))

        assert installed.version == "3.12.4"
        assert installed.home == (
            machine.root / "python" / "3.12.4" / "cpython-3.12.4-linux-x86_64-gnu"
        )
        # Only the interpreter stays: nothing of the installer's, nothing that
        # anybody but root could write to, no link into the staging directory.
        assert [entry.name for entry in (machine.root / "python" / "3.12.4").iterdir()] == [
            "cpython-3.12.4-linux-x86_64-gnu"
        ]
        call = next(c for c in machine.runner.calls if c[:3] == ("uv", "python", "install"))
        assert call[3] == "3.12"
        assert "--no-bin" in call
        assert str(machine.root / "python") in call[call.index("--install-dir") + 1]

    def test_uv_failing_leaves_nothing_and_says_what_it_said(self, machine: Machine) -> None:
        machine.runner.script(["uv"], stderr="error: no download for 3.99", exit_code=2)

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("python@3.99"))

        assert "no download for 3.99" in excinfo.value.details
        assert list((machine.root / "python").iterdir()) == []

    def test_the_interpreter_is_found_afterwards(self, machine: Machine) -> None:
        home = machine.root / "python" / "3.11.9" / "cpython-3.11.9-linux-x86_64-gnu"
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "python3").write_text("")

        found = machine.manager().find(parse_runtime("python@3.11"))

        assert found is not None
        assert found.version == "3.11.9"
        assert found.program("python3") == home / "bin" / "python3"
        assert found.path_environment("/usr/bin") == {"PATH": f"{home / 'bin'}:/usr/bin"}


class TestPhp:
    def test_an_installed_version_is_found_and_its_cli_linked(self, machine: Machine) -> None:
        machine.install_php_fpm("8.2")
        machine.install_php_fpm("8.3")

        installed = machine.manager().ensure(parse_runtime("php@8.2"))

        assert installed.version == "8.2"
        assert not installed.managed
        link = machine.root / "php" / "8.2" / "bin" / "php"
        assert os.readlink(link) == "/usr/bin/php8.2"

    def test_a_version_that_is_not_installed_is_installed_with_apt(self, machine: Machine) -> None:
        machine.runner.only_knows("apt-get")
        original = machine.runner.run

        def run(argv: Any, **kwargs: Any) -> Any:
            if list(argv[:3]) == ["apt-get", "install", "-y"]:
                machine.install_php_fpm("8.4")
            return original(argv, **kwargs)

        machine.runner.run = run  # type: ignore[method-assign]

        installed = machine.manager().install(parse_runtime("php@8.4"))

        assert installed.version == "8.4"
        call = next(c for c in machine.runner.calls if c[0] == "apt-get")
        assert "php8.4-fpm" in call
        assert "php8.4-mysql" in call
        assert not any(argument.startswith("php-") for argument in call)

    def test_apt_that_cannot_find_it_says_where_the_versions_come_from(
        self, machine: Machine
    ) -> None:
        machine.runner.only_knows("apt-get")
        machine.runner.script(
            ["apt-get"], stderr="E: Unable to locate package php8.9-fpm", exit_code=100
        )

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("php@8.9"))

        assert "Unable to locate" in excinfo.value.details
        assert "ppa:ondrej/php" in excinfo.value.details
        assert "packages.sury.org" in excinfo.value.details

    def test_a_server_without_apt_is_told_the_way_for_its_own(self, machine: Machine) -> None:
        machine.runner.only_knows("dnf")

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("php@8.3"))

        assert "remirepo" in excinfo.value.details
        assert "apt-get" not in [c[0] for c in machine.runner.calls]

    def test_a_major_alone_cannot_be_installed(self, machine: Machine) -> None:
        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().install(parse_runtime("php@8"))

        assert "php@8.3" in excinfo.value.details

    def test_php_is_not_removed_by_noust(self, machine: Machine) -> None:
        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().remove("php", "8.3")

        assert "apt remove php8.3-fpm" in excinfo.value.details


class TestChoosing:
    def host_node(self, machine: Machine, version: str = "22.9.0") -> None:
        machine.runner.script(["node", "--version"], stdout=f"v{version}\n")

    def test_what_the_build_block_pins_is_an_order(self, machine: Machine) -> None:
        machine.serve_node()
        self.host_node(machine, "22.9.0")  # the server's own would do, and is not used

        choice = machine.manager().choose(parse_runtime("node@22"))

        assert choice.runtime is not None
        assert choice.runtime.managed
        assert choice.warning is None

    def test_an_explicit_request_that_cannot_be_met_fails(self, machine: Machine) -> None:
        # nodejs.org cannot be read.
        with pytest.raises(DeploymentError):
            machine.manager().choose(parse_runtime("node@22"))

    def test_a_version_file_is_met_by_the_servers_own_runtime_when_it_satisfies(
        self, machine: Machine
    ) -> None:
        self.host_node(machine, "22.9.0")
        request = RuntimeRequest("node", "22", explicit=False, source=".nvmrc")

        choice = machine.manager().choose(request)

        assert choice.runtime is None
        assert "the server's 22.9.0 satisfies it" in choice.reason
        assert machine.source.downloads == []

    def test_a_version_file_the_server_does_not_satisfy_is_met_by_a_managed_runtime(
        self, machine: Machine
    ) -> None:
        machine.serve_node()
        self.host_node(machine, "22.9.0")
        request = RuntimeRequest("node", "20", explicit=False, source=".nvmrc")

        choice = machine.manager().choose(request)

        assert choice.runtime is not None
        assert choice.runtime.version == "20.18.0"
        assert ".nvmrc asks for node 20" in choice.reason

    def test_a_version_file_never_breaks_a_deployment_that_worked(self, machine: Machine) -> None:
        self.host_node(machine, "22.9.0")
        request = RuntimeRequest("node", "20", explicit=False, source=".nvmrc")

        # nodejs.org cannot be read: the deployment goes on with the server's own.
        choice = machine.manager().choose(request)

        assert choice.runtime is None
        assert choice.warning is not None
        assert "could not be installed" in choice.warning
        assert "node@<version>" in choice.warning

    def test_a_range_that_cannot_be_read_is_a_warning_not_an_error(self, machine: Machine) -> None:
        self.host_node(machine, "22.9.0")
        request = RuntimeRequest("node", "banana", explicit=False, source="engines.node")

        choice = machine.manager().choose(request)

        assert choice.runtime is None
        assert choice.warning is not None and "engines.node" in choice.warning

    def test_an_alias_is_satisfied_by_the_server(self, machine: Machine) -> None:
        self.host_node(machine, "16.20.0")
        request = RuntimeRequest("node", "lts/*", explicit=False, source=".nvmrc")

        assert machine.manager().choose(request).runtime is None

    def test_php_in_composer_json_chooses_the_newest_installed_that_satisfies(
        self, machine: Machine
    ) -> None:
        machine.install_php_fpm("8.1")
        machine.install_php_fpm("8.3")
        request = RuntimeRequest("php", "^8.1", explicit=False, source="composer.json")

        choice = machine.manager().choose(request)

        assert choice.runtime is not None and choice.runtime.version == "8.3"

    def test_php_that_nothing_installed_satisfies_falls_back_with_a_warning(
        self, machine: Machine
    ) -> None:
        machine.install_php_fpm("8.1")
        request = RuntimeRequest("php", "^8.4", explicit=False, source="composer.json")

        choice = machine.manager().choose(request)

        assert choice.runtime is not None and choice.runtime.version == "8.1"
        assert choice.warning is not None and "no PHP-FPM here is" in choice.warning


class TestRemoving:
    def install_node(self, machine: Machine, version: str = "22.11.0") -> Path:
        home = machine.root / "node" / version
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "node").write_text("")
        return home

    @pytest.fixture
    def store(self, tmp_path: Path):
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        instance.create_app(
            App(
                domain="app.example.com",
                app_type="nodejs",
                source="x",
                app_path=str(tmp_path / "a"),
            )
        )
        yield instance
        NoustStore.reset_instance()

    def test_an_unused_runtime_is_removed_and_audited(
        self,
        machine: Machine,
        store: NoustStore,
        audit_events: list[tuple[str, dict[str, Any]]],
    ) -> None:
        home = self.install_node(machine)

        machine.manager().remove("node", "22.11.0")

        assert not home.exists()
        assert audit_events[-1][0] == "runtimes.remove"

    def test_a_runtime_an_application_runs_on_is_not_removed(
        self, machine: Machine, store: NoustStore
    ) -> None:
        from noust.core import app_build_store

        home = self.install_node(machine)
        app_build_store.set_applied(
            "app.example.com",
            {"runtime": {"language": "node", "version": "22.11.0"}},
            store=store,
        )

        with pytest.raises(DeploymentError) as excinfo:
            machine.manager().remove("node", "22.11.0")

        assert "app.example.com" in excinfo.value.message
        assert home.exists()

    def test_what_is_not_installed_is_an_error(self, machine: Machine, store: NoustStore) -> None:
        with pytest.raises(DeploymentError):
            machine.manager().remove("node", "22.11.0")

    @pytest.mark.parametrize(("language", "version"), [("ruby", "3.2.0"), ("node", "22")])
    def test_a_name_that_is_not_a_runtime_is_refused(
        self, machine: Machine, store: NoustStore, language: str, version: str
    ) -> None:
        with pytest.raises(ValidationError):
            machine.manager().remove(language, version)

    def test_a_link_is_not_followed_out_of_the_runtimes_directory(
        self, machine: Machine, store: NoustStore, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (machine.root / "node").mkdir(parents=True)
        (machine.root / "node" / "22.11.0").symlink_to(outside)

        with pytest.raises(DeploymentError):
            machine.manager().remove("node", "22.11.0")

        assert outside.exists()


class TestListing:
    def test_what_is_installed_is_listed_by_language_newest_first(self, machine: Machine) -> None:
        for version in ("20.18.0", "22.11.0"):
            home = machine.root / "node" / version
            (home / "bin").mkdir(parents=True)
            (home / "bin" / "node").write_text("")
        machine.install_php_fpm("8.3")

        listed = [(item.language, item.version) for item in machine.manager().list_installed()]

        assert listed == [("node", "22.11.0"), ("node", "20.18.0"), ("php", "8.3")]

    def test_the_servers_own_runtime_is_read_from_its_own_program(self, machine: Machine) -> None:
        machine.runner.script(["node", "--version"], stdout="v20.19.0\n")
        machine.runner.script(["python3", "--version"], stdout="Python 3.12.3\n")
        manager = machine.manager()

        node = manager.host("node")
        python = manager.host("python")

        assert node is not None and node.version == "20.19.0" and not node.managed
        assert python is not None and python.version == "3.12.3"
        # A system runtime is already on the path.
        assert node.path_environment() == {}

    def test_a_server_without_it_has_none(self, machine: Machine) -> None:
        machine.runner.only_knows("apt-get")

        assert machine.manager().host("node") is None

    def test_a_staging_directory_is_executable_by_nobody_else_until_it_is_placed(
        self, machine: Machine
    ) -> None:
        # The staging directory is 0755 like what it becomes: world-readable
        # (a build reads it) and writable by root alone.
        machine.serve_node()
        machine.manager().install(parse_runtime("node@22.11.0"))

        final = machine.root / "node" / "22.11.0"
        assert stat.S_IMODE(final.stat().st_mode) & 0o022 == 0


class FakeResponse:
    """What ``_open_url`` returns: a context manager that reads in chunks."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.position = 0

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        chunk = self.body[self.position : self.position + size]
        self.position += len(chunk)
        return chunk


class TestReadingADocument:
    def test_a_document_is_read_whole(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.managers import runtimes

        monkeypatch.setattr(
            runtimes, "_open_url", lambda url, timeout: FakeResponse(b"x" * 200_000)
        )

        assert runtimes._http_get("https://nodejs.org/dist/index.json") == b"x" * 200_000

    def test_one_that_is_too_large_is_refused_not_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import runtimes

        monkeypatch.setattr(runtimes, "_open_url", lambda url, timeout: FakeResponse(b"x" * 5000))

        with pytest.raises(DeploymentError) as excinfo:
            runtimes._http_get("https://nodejs.org/dist/index.json", limit=1000)

        assert "larger than 1000 bytes" in excinfo.value.message

    def test_a_transfer_that_trickles_is_stopped_by_the_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import runtimes

        ticks = iter([0.0, 1.0, 10_000.0, 10_001.0])
        monkeypatch.setattr(runtimes.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(
            runtimes, "_open_url", lambda url, timeout: FakeResponse(b"x" * 500_000)
        )

        with pytest.raises(DeploymentError) as excinfo:
            runtimes._http_get("https://nodejs.org/dist/index.json")

        assert "was not complete within" in excinfo.value.details

    def test_a_network_failure_says_what_to_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from urllib.error import URLError

        from noust.managers import runtimes

        def refuse(url: str, timeout: int) -> Any:
            raise URLError("Temporary failure in name resolution")

        monkeypatch.setattr(runtimes, "_open_url", refuse)

        with pytest.raises(DeploymentError) as excinfo:
            runtimes._http_get("https://nodejs.org/dist/index.json")

        assert "name resolution" in excinfo.value.details
        assert "nodejs.org" in excinfo.value.details

    def test_only_http_and_https_are_ever_asked_for(self) -> None:
        from noust.core.exceptions import SourceError
        from noust.managers import runtimes

        with pytest.raises(SourceError):
            runtimes._http_get("file:///etc/passwd")
