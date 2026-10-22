# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the package family and the install hints built on it.

Issue #12: on an Ubuntu server a failed PHP deploy told the operator to run
``apt install``, ``dnf install`` and ``zypper install`` in one line. The hint is
now the one command of the machine's package manager, and the full list only
when the machine's family cannot be told.
"""

from __future__ import annotations

import pytest

from noust.core.dependencies import (
    COMPOSER_DEPENDENCY,
    PHP_FPM_DEPENDENCY,
    RCLONE_DEPENDENCY,
    Dependency,
    dependency_install_hint,
)
from noust.core.package_family import (
    PackageFamily,
    detect_family,
    for_this_machine,
    install_apt_packages,
    install_hint,
    package_family,
)
from noust.core.runner import CommandResult, FakeRunner
from noust.managers.server.host import PackageFamily as HostPackageFamily
from noust.managers.server.host import detect_platform, reset_platform_cache

APT, DNF, ZYPPER, NONE = (
    PackageFamily.APT,
    PackageFamily.DNF,
    PackageFamily.ZYPPER,
    PackageFamily.NONE,
)


def machine_with(*programs: str) -> FakeRunner:
    """
    A runner on which only these programs exist.

    Args:
        programs: Executables present on the machine.

    Returns:
        The fake runner.
    """
    return FakeRunner().only_knows(*programs)


class TestDetection:
    @pytest.mark.parametrize(
        ("programs", "family", "program"),
        [
            (("apt-get",), APT, "apt-get"),
            (("dnf",), DNF, "dnf"),
            (("zypper",), ZYPPER, "zypper"),
            # A Debian that carries rpm tools installs through apt.
            (("dnf", "apt-get"), APT, "apt-get"),
            (("zypper", "dnf"), DNF, "dnf"),
            (("pacman",), NONE, ""),
            ((), NONE, ""),
        ],
    )
    def test_the_family_is_the_package_manager_that_is_there(
        self, programs: tuple[str, ...], family: PackageFamily, program: str
    ) -> None:
        assert detect_family(machine_with(*programs)) == (family, program)
        assert package_family(machine_with(*programs)) is family

    def test_the_process_wide_runner_is_the_default(self, runner: FakeRunner) -> None:
        runner.only_knows("zypper")

        assert package_family() is ZYPPER

    def test_a_probe_that_cannot_be_made_is_an_unknown_family(self) -> None:
        class Broken(FakeRunner):
            def exists(self, program: str) -> bool:
                raise PermissionError(13, "PATH is not readable")

        assert package_family(Broken()) is NONE

    def test_the_host_platform_uses_the_same_family(self) -> None:
        """One answer to "which package manager": host.py asks this module."""
        reset_platform_cache()
        try:
            platform = detect_platform(machine_with("zypper", "systemctl"))
        finally:
            reset_platform_cache()

        assert platform.family is ZYPPER
        assert platform.program == "zypper"
        assert HostPackageFamily is PackageFamily


class TestInstallHint:
    def test_an_apt_machine_reads_apt_only(self) -> None:
        hint = install_hint("certbot", runner=machine_with("apt-get"))

        assert hint == "apt install certbot"

    def test_a_dnf_machine_reads_dnf_only(self) -> None:
        assert install_hint("certbot", runner=machine_with("dnf")) == "dnf install certbot"

    def test_a_zypper_machine_reads_zypper_only(self) -> None:
        assert install_hint("certbot", runner=machine_with("zypper")) == "zypper install certbot"

    def test_each_family_can_call_the_package_something_else(self) -> None:
        packages = {APT: "apache2", DNF: "httpd", ZYPPER: "apache2"}

        assert install_hint(packages, runner=machine_with("dnf")) == "dnf install httpd"
        assert (
            install_hint(packages, runner=machine_with())
            == "apt install apache2; dnf install httpd; zypper install apache2"
        )

    def test_an_unknown_family_lists_every_command_in_a_fixed_order(self) -> None:
        hint = install_hint("git", runner=machine_with("pacman"))

        assert hint == "apt install git; dnf install git; zypper install git"

    def test_a_note_is_shown_only_with_the_family_it_is_for(self) -> None:
        notes = {ZYPPER: "On openSUSE, also do the extra step."}

        assert install_hint("x", notes=notes, runner=machine_with("apt-get")) == "apt install x"
        assert install_hint("x", notes=notes, runner=machine_with("zypper")) == (
            "zypper install x. On openSUSE, also do the extra step."
        )
        assert install_hint("x", notes=notes, runner=machine_with()) == (
            "apt install x; dnf install x; zypper install x. On openSUSE, also do the extra step."
        )

    def test_a_family_with_no_package_falls_back_to_the_ones_that_have_one(self) -> None:
        packages = {APT: "python3-pip", DNF: "", ZYPPER: ""}

        assert install_hint(packages, runner=machine_with("dnf")) == "apt install python3-pip"

    def test_a_known_family_overrides_detection(self) -> None:
        """Code that already holds the platform does not ask the runner again."""
        assert install_hint("git", runner=machine_with("apt-get"), family=DNF) == (
            "dnf install git"
        )
        assert install_hint("git", runner=machine_with("apt-get"), family=NONE) == (
            "apt install git; dnf install git; zypper install git"
        )


class TestForThisMachine:
    def test_the_entry_of_this_family_alone(self) -> None:
        options = {APT: "step for apt", DNF: "step for rpm", ZYPPER: "step for rpm"}

        assert for_this_machine(options, runner=machine_with("zypper")) == ["step for rpm"]

    def test_a_step_two_families_share_is_listed_once(self) -> None:
        options = {APT: "step for apt", DNF: "step for rpm", ZYPPER: "step for rpm"}

        assert for_this_machine(options, runner=machine_with()) == ["step for apt", "step for rpm"]

    def test_an_empty_entry_means_nothing_to_offer_and_is_not_replaced(self) -> None:
        options = {APT: "apt install thing", DNF: "dnf install thing", ZYPPER: ""}

        assert for_this_machine(options, runner=machine_with("zypper")) == []
        assert for_this_machine(options, runner=machine_with()) == [
            "apt install thing",
            "dnf install thing",
        ]


class TestDependencyHint:
    def test_the_dependencys_packages_for_this_machine(self) -> None:
        assert (
            dependency_install_hint(RCLONE_DEPENDENCY, runner=machine_with("apt-get"))
            == "apt install rclone"
        )
        assert (
            dependency_install_hint(COMPOSER_DEPENDENCY, runner=machine_with("zypper"))
            == "zypper install php-composer2"
        )

    def test_phpfpm_on_apt_has_no_opensuse_step(self) -> None:
        hint = dependency_install_hint(PHP_FPM_DEPENDENCY, runner=machine_with("apt-get"))

        assert hint.startswith("apt install php-fpm ")
        assert "openSUSE" not in hint
        assert "zypper" not in hint

    def test_phpfpm_on_zypper_has_the_configuration_copy(self) -> None:
        hint = dependency_install_hint(PHP_FPM_DEPENDENCY, runner=machine_with("zypper"))

        assert hint.startswith("zypper install php8-fpm ")
        assert hint.endswith("copy /etc/php8/fpm/php-fpm.conf.default to php-fpm.conf.")

    def test_a_dependency_with_a_script_and_no_packages_names_the_script(self) -> None:
        dep = Dependency(name="thing", command="thing", description="x", install_script="curl | sh")

        assert dependency_install_hint(dep, runner=machine_with("apt-get")) == "curl | sh"

    def test_a_dependency_with_nothing_says_to_use_the_package_manager(self) -> None:
        dep = Dependency(name="thing", command="thing", description="x")

        assert "Install thing" in dependency_install_hint(dep, runner=machine_with("apt-get"))


class TestMissingQuestionaryHint:
    @pytest.fixture(autouse=True)
    def _without_questionary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.cli import prompts

        monkeypatch.setattr(prompts, "AVAILABLE", False)

    def details(self, runner: FakeRunner, *programs: str) -> str:
        from noust.cli.interactive import InteractiveMode
        from noust.core.exceptions import NoustError

        runner.only_knows(*programs)
        with pytest.raises(NoustError, match="questionary") as failure:
            InteractiveMode()
        return failure.value.details

    def test_an_apt_machine_is_not_offered_dnf(self, runner: FakeRunner) -> None:
        details = self.details(runner, "apt-get")

        assert "apt install python3-questionary" in details
        assert "dnf install" not in details
        assert "pip install questionary" in details

    def test_openSUSE_is_offered_pip_alone(self, runner: FakeRunner) -> None:
        details = self.details(runner, "zypper")

        assert "apt install" not in details
        assert "dnf install" not in details
        assert "pip install questionary" in details

    def test_an_unknown_machine_is_offered_both_packages(self, runner: FakeRunner) -> None:
        details = self.details(runner)

        assert "apt install python3-questionary" in details
        assert "dnf install python3-questionary" in details


# -- installing with apt when its lists are empty ---------------------------------------------


class ListsFillAfterAnUpdate(FakeRunner):
    """apt does not know a package until ``apt-get update`` ran, as on a fresh image."""

    def __init__(self, *, update_works: bool = True, message: str | None = None) -> None:
        super().__init__()
        self.updated = False
        self.update_works = update_works
        self.message = message or "E: Unable to locate package php8.3-fpm"

    def run(self, argv, **kwargs):  # type: ignore[no-untyped-def, override]
        if tuple(argv[:2]) == ("apt-get", "update"):
            self.calls.append(tuple(argv))
            self.updated = self.update_works
            return CommandResult(
                argv=tuple(argv), exit_code=0 if self.update_works else 100, stderr="W: no mirror"
            )
        if tuple(argv[:2]) == ("apt-get", "install") and not self.updated:
            self.calls.append(tuple(argv))
            return CommandResult(argv=tuple(argv), exit_code=100, stderr=self.message)
        return super().run(argv, **kwargs)


def test_a_package_the_lists_do_not_know_is_installed_after_updating_them() -> None:
    # A fresh image or a container has no package lists, and `apt-get install` says "Unable to
    # locate package" for everything: `noust runtime install php@8.3` and `noust setup init` failed
    # that way on a server that only needed `apt-get update`. Found installing PHP for real.
    runner = ListsFillAfterAnUpdate()

    result = install_apt_packages(runner, ["php8.3-fpm", "php8.3-cli"], timeout=600)

    assert result.success
    assert runner.calls == [
        ("apt-get", "install", "-y", "php8.3-fpm", "php8.3-cli"),
        ("apt-get", "update"),
        ("apt-get", "install", "-y", "php8.3-fpm", "php8.3-cli"),
    ]


def test_a_package_that_installs_at_once_is_not_preceded_by_an_update() -> None:
    runner = FakeRunner()

    result = install_apt_packages(runner, ["certbot"], timeout=600)

    assert result.success and runner.calls == [("apt-get", "install", "-y", "certbot")]


def test_a_failure_that_is_not_about_the_lists_is_not_retried() -> None:
    runner = FakeRunner()
    runner.script(["apt-get", "install"], exit_code=100, stderr="E: dpkg was interrupted")

    result = install_apt_packages(runner, ["certbot"], timeout=600)

    assert not result.success
    assert runner.calls == [("apt-get", "install", "-y", "certbot")]


def test_an_update_that_fails_leaves_the_first_failure_to_speak() -> None:
    # The operator needs to read that the package is unknown, not that a mirror was unreachable.
    runner = ListsFillAfterAnUpdate(update_works=False)

    result = install_apt_packages(runner, ["php8.3-fpm"], timeout=600)

    assert not result.success
    assert "Unable to locate package" in result.stderr
    assert runner.calls.count(("apt-get", "update")) == 1


def test_the_install_is_tried_twice_at_most() -> None:
    runner = FakeRunner()
    runner.script(["apt-get", "install"], exit_code=100, stderr="E: Unable to locate package nope")

    result = install_apt_packages(runner, ["nope"], timeout=600)

    assert not result.success
    assert runner.calls.count(("apt-get", "install", "-y", "nope")) == 2
    assert runner.calls.count(("apt-get", "update")) == 1


def test_the_environment_and_the_extra_options_travel_with_both_attempts() -> None:
    runner = ListsFillAfterAnUpdate()

    install_apt_packages(
        runner,
        ["fail2ban"],
        timeout=600,
        env={"DEBIAN_FRONTEND": "noninteractive"},
        options=("--no-remove",),
    )

    assert runner.calls[0] == ("apt-get", "install", "-y", "--no-remove", "fail2ban")
    assert runner.calls[-1] == runner.calls[0]
    assert runner.envs[-1] == {"DEBIAN_FRONTEND": "noninteractive"}


def test_a_dependency_of_several_packages_is_installed_as_several_arguments() -> None:
    # PHP-FPM is nine packages in one string; apt was handed the string as a single package name.
    from noust.core.dependencies import DependencyChecker

    runner = FakeRunner()

    ok, _ = DependencyChecker(runner=runner).install_dependency(PHP_FPM_DEPENDENCY)

    assert ok
    assert runner.calls == [("apt-get", "install", "-y", *PHP_FPM_DEPENDENCY.install_apt.split())]
    assert len(runner.calls[0]) > 4


def test_setup_installs_through_the_refreshing_install() -> None:
    from noust.core.dependencies import DependencyChecker

    runner = ListsFillAfterAnUpdate(message="E: Unable to locate package certbot")
    certbot = Dependency(name="certbot", command="certbot", description="x", install_apt="certbot")

    ok, message = DependencyChecker(runner=runner).install_dependency(certbot)

    assert ok, message
    assert ("apt-get", "update") in runner.calls
