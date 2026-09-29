"""
The packaging of the rename from WASM to Noust (3.0.0).

Every channel keeps its old name as a transitional package that brings noust
in: PyPI wasm-cli, the Debian package wasm and the RPM wasm-cli. Each rule
below is here because breaking it loses something on a server that upgrades,
and packaging/obs/upgrade-test.sh proves the same things by doing the upgrade
in the Release workflow.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - only on 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
TRANSITIONAL = REPO / "packaging/transitional/wasm"


def read(path: str) -> str:
    """
    Read a repository file.

    Args:
        path: Path relative to the repository root.

    Returns:
        Its text.
    """
    return (REPO / path).read_text(encoding="utf-8")


def spec_section(spec: str, name: str) -> str:
    """
    Return the body of one section of an RPM spec.

    Args:
        spec: The spec's text.
        name: The section, e.g. ``%files`` or ``%pre``.

    Returns:
        The lines between the section header and the next section.
    """
    lines = spec.splitlines()
    start = next(i for i, line in enumerate(lines) if line.split() == [name])
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if re.match(r"^%(pre|post|preun|postun|posttrans|files|changelog)\b", lines[i])
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def control_field(control: str, field: str) -> str:
    """
    Return a field of the binary paragraph of a debian/control file.

    Args:
        control: The file's text.
        field: The field name.

    Returns:
        The field's value, continuation lines joined.
    """
    binary = control.split("\nPackage:", 1)[1]
    match = re.search(rf"^{field}:(.*(?:\n[ \t]+.*)*)", binary, re.MULTILINE)
    assert match, f"no {field} in the binary paragraph"
    return " ".join(match.group(1).split())


class TestTheProductPackages:
    """noust.spec and debian.control take over from wasm-cli and wasm."""

    def test_the_rpm_conflicts_with_wasm_cli_2_and_never_obsoletes_it(self):
        """
        Obsoletes made dnf replace wasm-cli 2.x with noust outright, so rpm
        ran the old %preun with $1 = 0 - its final-removal branch - which
        stopped and disabled wasm-web and wasm-monitor on every upgraded
        server. A conflict makes the solver upgrade to the transitional
        wasm-cli instead.
        """
        spec = read("rpm/noust.spec")

        assert re.search(r"^Conflicts:\s+wasm-cli < 3\.0\.0$", spec, re.MULTILINE)
        assert not re.search(r"^Obsoletes:.*wasm-cli", spec, re.MULTILINE)

    def test_the_deb_breaks_and_replaces_wasm_2(self):
        """The Debian way to take over another package's files across a rename."""
        control = read("obs/debian.control")

        assert control_field(control, "Breaks") == "wasm (<< 3.0.0)"
        assert control_field(control, "Replaces") == "wasm (<< 3.0.0)"

    def test_nothing_is_installed_under_etc(self):
        """
        noust moves /etc/wasm to /etc/noust on its first run as root, and the
        rename is atomic only while /etc/noust does not exist: a packaged
        config.yaml there would stop the move or leave two configurations.
        """
        files = spec_section(read("rpm/noust.spec"), "%files")
        assert "%{_sysconfdir}" not in files
        assert "/etc/" not in files
        assert "%config" not in files

        rules = read("obs/debian.rules")
        assert not re.search(r"debian/noust/etc\b", rules)
        assert not any(line.startswith("etc/") for line in read("obs/debian.dirs").splitlines())

    def test_rpm_keeps_the_configuration_rpm_erases_with_wasm_cli_2(self):
        """
        Erasing wasm-cli 2.x renames a modified /etc/wasm/config.yaml to
        .rpmsave before noust runs. %pre keeps a hard link to it and
        %posttrans, after the erase, puts it back.
        """
        spec = read("rpm/noust.spec")
        pre = spec_section(spec, "%pre")
        posttrans = spec_section(spec, "%posttrans")

        assert "ln /etc/wasm/config.yaml /etc/wasm/config.yaml.noust-keep" in pre
        assert "mv /etc/wasm/config.yaml.noust-keep /etc/wasm/config.yaml" in posttrans
        restore = posttrans.index("config.yaml.noust-keep")
        assert restore < posttrans.index("noust config upgrade"), (
            "noust must not run before the configuration is back"
        )

    def test_the_deb_restores_what_the_transitional_preinst_moved_aside(self):
        """noust's postinst may run before wasm's; either one puts it back."""
        postinst = read("obs/debian.postinst")
        configure = postinst[postinst.index("configure)") :]

        restore = configure.index("mv -f /etc/wasm/config.yaml.noust-keep /etc/wasm/config.yaml")
        assert restore < configure.index("noust config upgrade")

    @pytest.mark.parametrize("script", ["obs/debian.postinst", "rpm/noust.spec"])
    def test_the_upgrade_migrates_explicitly_before_touching_units(self, script: str):
        """
        noust's automatic migration stands aside inside a systemd unit, which
        is where unattended-upgrades and dnf-automatic run, so the package
        runs `noust migrate-from-wasm` itself: before the monitor is
        reinstalled (which refuses while wasm-monitor exists) and before the
        console is restarted. A migration that does not finish must not fail
        the install, but must say so.
        """
        text = read(script)
        if script.endswith(".spec"):
            text = spec_section(text, "%posttrans")
        else:
            text = text[text.index("configure)") :]

        migrate = text.index("noust migrate-from-wasm ||")
        assert migrate < text.index("monitor install")
        assert migrate < text.index("restart noust-web.service")
        failure = text[migrate:].splitlines()[0]
        assert "did not finish" in failure and ">&2" in failure, failure
        assert "|| true" not in failure and "|| :" not in failure

    @pytest.mark.parametrize("path", ["rpm/noust.spec", "obs/debian.rules"])
    def test_both_commands_and_both_manuals_are_installed(self, path: str):
        """`wasm` is an alias for the whole 3.x series, `man wasm` included."""
        recipe = read(path)

        for completion in ("noust.bash", "wasm.bash"):
            assert f"src/noust/completions/{completion}" in recipe
        assert re.search(r"ln -sf noust\.1 \S*wasm\.1", recipe)


class TestTheTransitionalPackages:
    """wasm (deb), wasm-cli (rpm) and wasm-cli (PyPI) only bring noust in."""

    def test_the_deb_is_empty_in_oldlibs_and_depends_on_noust(self):
        """
        Section oldlibs is what makes apt move the manual-install mark from
        wasm to noust (APT::Move-Autobit-Sections); anywhere else noust stays
        "automatically installed" and `apt autoremove` removes it the day the
        operator removes wasm.
        """
        control = (TRANSITIONAL / "debian.control").read_text(encoding="utf-8")

        assert re.search(r"^Section: oldlibs$", control, re.MULTILINE)
        assert control_field(control, "Depends").startswith("noust (>=")
        assert control_field(control, "Architecture") == "all"

    def test_the_deb_makes_dpkg_forget_the_old_conffile(self):
        """
        dpkg keeps an obsolete conffile registered and deletes it on purge -
        through the /etc/wasm -> /etc/noust link, the operator's
        /etc/noust/config.yaml. It forgets one that is absent at unpack, so
        preinst moves it aside, only on an upgrade from 2.x.
        """
        preinst = (TRANSITIONAL / "debian.preinst").read_text(encoding="utf-8")
        postinst = (TRANSITIONAL / "debian.postinst").read_text(encoding="utf-8")
        postrm = (TRANSITIONAL / "debian.postrm").read_text(encoding="utf-8")

        assert 'dpkg --compare-versions "$2" lt 3.0.0~' in preinst
        assert "mv -f /etc/wasm/config.yaml /etc/wasm/config.yaml.noust-keep" in preinst
        assert "mv -f /etc/wasm/config.yaml.noust-keep /etc/wasm/config.yaml" in postinst
        abort = postrm[postrm.index("abort-install|abort-upgrade)") :]
        assert "/etc/wasm/config.yaml.noust-keep /etc/wasm/config.yaml" in abort.split(";;")[0]

    def test_the_rpm_is_empty_and_requires_noust(self):
        """Nothing but its README and licence, and noust at least as new."""
        spec = (TRANSITIONAL / "wasm.spec").read_text(encoding="utf-8")

        assert re.search(r"^Name:\s+wasm-cli$", spec, re.MULTILINE)
        assert re.search(r"^Requires:\s+noust >= %\{version\}$", spec, re.MULTILINE)
        files = [line for line in spec_section(spec, "%files").splitlines() if line.strip()]
        assert all(line.startswith(("%doc", "%license")) for line in files), files

    def test_the_pypi_package_pins_noust_and_keeps_the_alias(self):
        """
        wasm-cli pins noust to its own version, so `pip install -U wasm-cli`
        moves noust along. It declares `wasm` because pip installs noust
        first and then removes wasm-cli 2.x, whose file list includes
        bin/wasm: without it the upgrade deleted the alias noust had just
        installed.
        """
        project = tomllib.loads(read("packaging/transitional/wasm-cli/pyproject.toml"))["project"]

        assert project["name"] == "wasm-cli"
        assert project["dependencies"] == [f"noust=={project['version']}"]
        assert project["scripts"] == {"wasm": "noust.cli.app:entrypoint"}
        noust = tomllib.loads(read("pyproject.toml"))["project"]
        assert project["scripts"]["wasm"] == noust["scripts"]["noust"]


class TestTheContainerImage:
    """packaging/container: the central for a NAS."""

    def test_it_runs_as_an_unprivileged_user_under_tini(self):
        """The central only listens and dials out; nothing in it needs root."""
        dockerfile = read("packaging/container/Dockerfile")

        assert re.search(r"^USER noust$", dockerfile, re.MULTILINE)
        assert 'ENTRYPOINT ["/usr/bin/tini", "--",' in dockerfile
        assert re.search(r"^HEALTHCHECK ", dockerfile, re.MULTILINE)
        assert 'VOLUME ["/data"]' in dockerfile
        assert re.search(r"^EXPOSE 8443$", dockerfile, re.MULTILINE)

    def test_it_installs_the_wheel_of_its_own_version(self):
        """The image is exactly the release, not whatever PyPI serves."""
        dockerfile = read("packaging/container/Dockerfile")

        assert "COPY dist/noust-${NOUST_VERSION}-py3-none-any.whl" in dockerfile
        installs = [
            line
            for line in dockerfile.splitlines()
            if "pip install" in line and not line.lstrip().startswith("#")
        ]
        assert installs, "the Dockerfile installs nothing"
        assert all("/tmp/noust-${NOUST_VERSION}-py3-none-any.whl" in line for line in installs)
