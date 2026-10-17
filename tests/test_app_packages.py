"""
System packages an application needs (checklist A-44): the declaration, the policy and the install.

What is pinned: that a name is a name and nothing the package manager could read as an option, a
version or a path; that the server's policy governs what may be declared and is applied again at
install time; that Noust installs only what the host lacks, without what a package recommends and
through the one backend that knows each family; that a list the host could not satisfy is not
recorded; that a deployment installs what is missing before it builds and stops if it cannot; and
that a repository has no way to ask for any of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_packages_store, audit
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import App, NoustStore
from noust.managers.app_packages import (
    MAX_PACKAGES,
    AppPackages,
    policy,
    validate_package,
    validate_packages,
)
from noust.managers.server.host import PackageFamily, Platform

DOMAIN = "shop.example.com"
APT = Platform(family=PackageFamily.APT, program="apt-get")
DNF = Platform(family=PackageFamily.DNF, program="dnf")


class HostRunner(FakeRunner):
    """dpkg-query and rpm as a host answers, and a package manager that installs into it."""

    def __init__(self, installed: tuple[str, ...] = (), *, fail_install: bool = False) -> None:
        super().__init__()
        self.installed = set(installed)
        self.fail_install = fail_install

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        kwargs.pop("check", None)
        result = super().run(argv, **kwargs)
        args = [str(part) for part in argv]
        if args[0] == "dpkg-query":
            names = args[4:]
            known = "".join(f"{name} ii \n" for name in names if name in self.installed)
            return replace(result, stdout=known, exit_code=0 if known else 1)
        if args[0] == "rpm":
            names = args[4:]
            known = "".join(f"{name}\n" for name in names if name in self.installed)
            return replace(result, stdout=known, exit_code=0 if known else 1)
        if "install" in args and args[0] in ("apt-get", "dnf", "zypper"):
            if self.fail_install:
                return replace(
                    result,
                    stdout="Reading package lists...",
                    stderr="E: Unable to locate package nonexistent-lib",
                    exit_code=100,
                )
            wanted = [arg for arg in args[args.index("install") + 1 :] if not arg.startswith("-")]
            self.installed.update(wanted)
            return replace(result, stdout=f"Setting up {', '.join(wanted)}", exit_code=0)
        return result

    def installs(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if "install" in call]

    def probes(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] in ("dpkg-query", "rpm")]


class Config:
    """A configuration that answers the policy key and nothing else."""

    def __init__(self, allow: Any = None) -> None:
        self.allow = allow if allow is not None else []

    def get(self, key: str, default: Any = None) -> Any:
        return self.allow if key == "security.app_packages" else default


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(tmp_path), layout="releases")
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    return recorded


def packages(
    store: NoustStore,
    runner: HostRunner | None = None,
    *,
    allow: Any = None,
    platform: Platform = APT,
) -> tuple[AppPackages, HostRunner]:
    runner = runner or HostRunner()
    return AppPackages(store=store, runner=runner, config=Config(allow), platform=platform), runner


class TestAName:
    @pytest.mark.parametrize(
        "name",
        ["libvips-dev", "g++", "libstdc++6", "python3.12-dev", "fonts-noto-core", "0ad", "x"],
    )
    def test_a_package_name(self, name: str) -> None:
        assert validate_package(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            " ",
            "-y",
            "--allow-unauthenticated",
            "foo bar",
            "foo=1.2.3",
            "foo/bar",
            "../etc/passwd",
            "a;b",
            "$(id)",
            "lib*",
            "foo\nbar",
            "a" * 101,
            "-",
            ".hidden",
            "+x",
        ],
    )
    def test_what_is_not_one(self, name: str) -> None:
        with pytest.raises(ValidationError, match="is not a package name") as caught:
            validate_package(name)

        assert caught.value.field == "packages"

    def test_a_list_keeps_the_order_and_drops_repeats(self) -> None:
        assert validate_packages(["b", "a", "b", " a "]) == ("b", "a")

    def test_a_list_has_a_limit(self) -> None:
        with pytest.raises(ValidationError, match="too many"):
            validate_packages([f"pkg{index}" for index in range(MAX_PACKAGES + 1)])

    def test_one_bad_name_refuses_the_whole_list(self) -> None:
        with pytest.raises(ValidationError):
            validate_packages(["libvips-dev", "-y"])


class TestThePolicy:
    def test_nothing_set_allows_any_package(self) -> None:
        assert policy(Config([])) == ()

    def test_names_and_globs_are_read_in_every_spelling_of_the_list(self) -> None:
        assert policy(Config(["libvips*", "ffmpeg"])) == ("libvips*", "ffmpeg")
        assert policy(Config("libvips*, ffmpeg fonts-*")) == ("libvips*", "ffmpeg", "fonts-*")
        assert policy(Config(None)) == ()

    def test_an_entry_that_is_not_a_name_or_a_glob_is_ignored(self) -> None:
        assert policy(Config(["ffmpeg", "-y", "a b", "$(x)", ""])) == ("ffmpeg",)

    def test_a_server_with_a_list_refuses_what_is_outside_it(self, store: NoustStore) -> None:
        manager, runner = packages(store, allow=["libvips*", "ffmpeg"])

        manager.set(DOMAIN, ["libvips-dev", "ffmpeg"], actor="ana", install=False)
        with pytest.raises(ValidationError, match="does not let applications declare: nginx") as c:
            manager.set(DOMAIN, ["ffmpeg", "nginx"], actor="ana", install=False)

        assert "security.app_packages allows: libvips*, ffmpeg" in c.value.details
        assert app_packages_store.get(DOMAIN, store=store).packages == (  # type: ignore[union-attr]
            "libvips-dev",
            "ffmpeg",
        )
        assert runner.installs() == []

    def test_the_policy_is_applied_again_when_installing(self, store: NoustStore) -> None:
        manager, _ = packages(store)
        manager.set(DOMAIN, ["nginx"], actor="ana", install=False)
        tightened, runner = packages(store, allow=["libvips*"])

        with pytest.raises(ValidationError, match="no longer lets applications declare: nginx"):
            tightened.ensure(DOMAIN)

        assert runner.installs() == []
        assert tightened.state(DOMAIN).refused == ("nginx",)


class TestDeclaringAndInstalling:
    def test_it_installs_only_what_the_host_lacks_without_what_they_recommend(
        self, store: NoustStore, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        manager, runner = packages(store, HostRunner(installed=("ffmpeg",)))

        change = manager.set(DOMAIN, ["libvips-dev", "ffmpeg"], actor="ana")

        [install] = runner.installs()
        assert install[:4] == ("apt-get", "-y", "-q", "-o")
        assert install[-3:] == ("install", "--no-install-recommends", "libvips-dev")
        assert "ffmpeg" not in install
        assert change.installed_now == ("libvips-dev",)
        assert change.state.installed == ("libvips-dev", "ffmpeg") and change.state.missing == ()
        assert "Setting up libvips-dev" in change.output
        # No shell and no sudo: an argv, in the package manager's own non-interactive environment.
        assert "sudo" not in install[0] and "sh" not in {install[0], install[1]}
        env = runner.envs[runner.calls.index(install)] or {}
        assert env.get("DEBIAN_FRONTEND") == "noninteractive"
        # The installation comes first: a list the host could not satisfy is not recorded.
        assert [event for event, _ in events] == ["apps.packages_installed", "apps.packages"]
        assert events[0][1]["details"]["packages"] == ["libvips-dev"]
        assert events[1][1]["details"] == {
            "packages": ["libvips-dev", "ffmpeg"],
            "previous": [],
            "by": "ana",
        }

    def test_an_rpm_host_installs_through_its_own_manager(self, store: NoustStore) -> None:
        manager, runner = packages(store, platform=DNF)

        manager.set(DOMAIN, ["vips-devel"], actor="ana")

        assert runner.installs() == [
            ("dnf", "-y", "--setopt=install_weak_deps=False", "install", "vips-devel")
        ]
        assert runner.probes()[0][:4] == ("rpm", "-q", "--qf", "%{NAME}\\n")

    def test_a_list_with_everything_already_there_runs_no_installation(
        self, store: NoustStore
    ) -> None:
        manager, runner = packages(store, HostRunner(installed=("ffmpeg",)))

        change = manager.set(DOMAIN, ["ffmpeg"], actor="ana")

        assert runner.installs() == [] and change.installed_now == ()
        assert change.state.installed == ("ffmpeg",)

    def test_it_can_declare_without_installing_and_the_next_update_does(
        self, store: NoustStore
    ) -> None:
        manager, runner = packages(store)

        change = manager.set(DOMAIN, ["libvips-dev"], actor="ana", install=False)
        assert runner.installs() == [] and change.state.missing == ("libvips-dev",)

        assert manager.ensure(DOMAIN) == ("libvips-dev",)
        assert len(runner.installs()) == 1
        # Done once: the host has it now.
        assert manager.ensure(DOMAIN) == ()
        assert len(runner.installs()) == 1

    def test_replacing_the_list_forgets_the_old_one_and_removes_nothing_from_the_host(
        self, store: NoustStore
    ) -> None:
        manager, runner = packages(store)
        manager.set(DOMAIN, ["libvips-dev", "ffmpeg"], actor="ana")

        change = manager.set(DOMAIN, ["ffmpeg"], actor="ana")
        cleared = manager.set(DOMAIN, [], actor="ana")

        assert change.state.declared == ("ffmpeg",)
        assert cleared.state.declared == () and app_packages_store.get(DOMAIN, store=store) is None
        assert not any(call[0] in ("apt-get",) and "remove" in call for call in runner.calls)
        assert runner.installed == {"libvips-dev", "ffmpeg"}

    def test_the_change_is_audited_once_and_only_when_it_changes(
        self, store: NoustStore, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        manager, _ = packages(store)
        manager.set(DOMAIN, ["ffmpeg"], actor="ana", install=False)
        manager.set(DOMAIN, ["ffmpeg"], actor="ana", install=False)

        assert [event for event, _ in events].count("apps.packages") == 1

    def test_a_package_manager_that_fails_leaves_the_list_as_it_was_and_shows_its_words(
        self, store: NoustStore
    ) -> None:
        manager, _ = packages(store)
        manager.set(DOMAIN, ["ffmpeg"], actor="ana")
        failing, _ = packages(store, HostRunner(installed=("ffmpeg",), fail_install=True))

        with pytest.raises(DeploymentError, match="Could not install") as caught:
            failing.set(DOMAIN, ["ffmpeg", "nonexistent-lib"], actor="ana")

        assert "nonexistent-lib" in caught.value.message
        assert "E: Unable to locate package nonexistent-lib" in (caught.value.output or "")
        assert app_packages_store.get(DOMAIN, store=store).packages == ("ffmpeg",)  # type: ignore[union-attr]

    def test_a_host_whose_packages_noust_does_not_manage(self, store: NoustStore) -> None:
        immutable = Platform(family=PackageFamily.DNF, program="dnf", transactional=True)
        manager, runner = packages(store, platform=immutable)

        with pytest.raises(DeploymentError, match="does not install them") as caught:
            manager.set(DOMAIN, ["ffmpeg"], actor="ana")

        assert "transactions" in caught.value.details and "ffmpeg" in caught.value.details
        assert runner.calls == []
        assert manager.state(DOMAIN).supported is False


class TestWhatCannotDeclarePackages:
    def test_an_unknown_application(self, store: NoustStore) -> None:
        manager, _ = packages(store)

        with pytest.raises(NoustError, match="Application not found"):
            manager.set("nope.example.com", ["ffmpeg"], actor="ana")

    def test_a_preview_uses_its_parents_list(self, store: NoustStore) -> None:
        store.create_app(
            App(
                domain="pr-1.shop.example.com",
                app_type="nodejs",
                source="x",
                app_path="/tmp/p",
                layout="releases",
                preview_parent=DOMAIN,
            )
        )
        manager, runner = packages(store)
        manager.set(DOMAIN, ["libvips-dev"], actor="ana", install=False)

        with pytest.raises(ValidationError, match="is a preview"):
            manager.set("pr-1.shop.example.com", ["x"], actor="ana")
        assert manager.ensure("pr-1.shop.example.com") == ("libvips-dev",)
        assert len(runner.installs()) == 1

    @pytest.mark.parametrize("app_type", ["docker-compose", "container"])
    def test_a_stack_or_a_container_installs_in_its_image(
        self, store: NoustStore, app_type: str
    ) -> None:
        store.create_app(
            App(domain="stack.example.com", app_type=app_type, source="x", app_path="/tmp/s")
        )
        manager, runner = packages(store)

        with pytest.raises(ValidationError, match="in containers of its own") as caught:
            manager.set("stack.example.com", ["ffmpeg"], actor="ana")

        assert "Dockerfile" in caught.value.details and runner.calls == []


class TestWhenAnUpdateBuilds:
    def test_an_application_that_declares_none_costs_no_process(self, store: NoustStore) -> None:
        manager, runner = packages(store)

        assert manager.ensure(DOMAIN) == ()
        assert runner.calls == []

    def test_what_is_there_is_not_installed_again(self, store: NoustStore) -> None:
        manager, runner = packages(store, HostRunner(installed=("ffmpeg",)))
        manager.set(DOMAIN, ["ffmpeg"], actor="ana", install=False)
        runner.calls.clear()

        assert manager.ensure(DOMAIN) == ()
        assert runner.installs() == [] and len(runner.probes()) == 1

    def test_a_failure_stops_the_update_with_the_package_managers_words(
        self, store: NoustStore
    ) -> None:
        manager, _ = packages(store)
        manager.set(DOMAIN, ["nonexistent-lib"], actor="ana", install=False)
        failing, _ = packages(store, HostRunner(fail_install=True))

        with pytest.raises(DeploymentError) as caught:
            failing.ensure(DOMAIN)

        assert "nonexistent-lib" in caught.value.message and caught.value.output

    def test_the_deployer_asks_for_it_before_it_installs_or_builds(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.deployers.nodejs import NodeJSDeployer

        runner = HostRunner()
        deployer = NodeJSDeployer(runner=runner)
        deployer.store = store
        deployer.domain = DOMAIN
        AppPackages(store=store, runner=runner, config=Config(), platform=APT).set(
            DOMAIN, ["libvips-dev"], actor="ana", install=False
        )
        monkeypatch.setattr("noust.managers.app_packages.detect_platform", lambda *_a, **_k: APT)
        steps: list[str] = []

        deployer._ensure_system_packages(steps.append)

        assert steps == ["Installed system packages: libvips-dev"]
        assert runner.installed == {"libvips-dev"}

    def test_the_pipeline_installs_before_the_dependencies(self) -> None:
        import inspect

        from noust.deployers.base import BaseDeployer

        release = inspect.getsource(BaseDeployer._update_release)
        in_place = inspect.getsource(BaseDeployer._update_in_place)

        assert release.index("_ensure_system_packages") < release.index("Installing dependencies")
        assert in_place.index("_ensure_system_packages") < in_place.index("Installing dependencies")


class TestARepositoryCannotAskForPackages:
    def test_noust_yaml_has_no_key_for_them(self, tmp_path: Path) -> None:
        from noust.core.exceptions import ValidationError as Refused
        from noust.deployers.helpers.project_file import load_project_file

        (tmp_path / "noust.yaml").write_text(
            "build:\n  install: npm ci\n  packages: [libvips-dev]\n", encoding="utf-8"
        )

        with pytest.raises(Refused, match="packages"):
            load_project_file(tmp_path)

    def test_the_build_block_has_no_such_key(self) -> None:
        from noust.deployers.helpers.build_config import BUILD_KEYS

        assert "packages" not in BUILD_KEYS


class TestTheBackends:
    def test_apt_reads_installed_as_ii_only(self) -> None:
        from noust.managers.server.pkg import backend_for

        backend = backend_for(APT, runner=HostRunner())
        result = CommandResult(
            argv=("dpkg-query",),
            exit_code=1,
            stdout="libvips-dev ii \nffmpeg rc \nfonts-x un \nother ii \n",
            stderr="dpkg-query: no packages found matching gone",
            duration=0.0,
        )

        assert backend.installed_names(result, ["libvips-dev", "ffmpeg", "fonts-x", "gone"]) == {
            "libvips-dev"
        }

    def test_rpm_reads_the_names_it_prints(self) -> None:
        from noust.managers.server.pkg import backend_for

        backend = backend_for(DNF, runner=HostRunner())
        result = CommandResult(
            argv=("rpm",),
            exit_code=1,
            stdout="vips-devel\npackage ffmpeg is not installed\n",
            stderr="",
            duration=0.0,
        )

        assert backend.installed_names(result, ["vips-devel", "ffmpeg"]) == {"vips-devel"}

    @pytest.mark.parametrize(
        ("family", "program", "flag"),
        [
            (PackageFamily.APT, "apt-get", "--no-install-recommends"),
            (PackageFamily.DNF, "dnf", "--setopt=install_weak_deps=False"),
            (PackageFamily.ZYPPER, "zypper", "--no-recommends"),
        ],
    )
    def test_every_family_leaves_the_recommendations_out(
        self, family: PackageFamily, program: str, flag: str
    ) -> None:
        from noust.managers.server.pkg import backend_for

        backend = backend_for(Platform(family=family, program=program), runner=HostRunner())

        argv = backend.install_lean_argv(["ffmpeg"])

        assert argv[0] == program and flag in argv and argv[-1] == "ffmpeg"


class TestTheStore:
    def test_the_list_goes_with_the_application(self, store: NoustStore) -> None:
        app_packages_store.put(DOMAIN, ("a", "b"), updated_by="ana", store=store)
        assert [record.domain for record in app_packages_store.list_all(store=store)] == [DOMAIN]

        store.delete_app(DOMAIN)

        assert app_packages_store.get(DOMAIN, store=store) is None

    def test_a_damaged_row_counts_as_no_packages(self, store: NoustStore) -> None:
        app_packages_store.put(DOMAIN, ("a",), updated_by="ana", store=store)
        with store._transaction() as cursor:
            cursor.execute("UPDATE app_packages SET packages = 'not json'")

        record = app_packages_store.get(DOMAIN, store=store)

        assert record is not None and record.packages == ()

    def test_an_unknown_application_has_none_to_record(self, store: NoustStore) -> None:
        assert (
            app_packages_store.put("nope.example.com", ("a",), updated_by=None, store=store)
            is False
        )
