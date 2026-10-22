# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the distribution packages and the container image must hold and must not.

Each rule below is here because breaking it loses something on a server that upgrades.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


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
    """noust.spec and debian.control."""

    def test_every_document_the_packages_list_exists(self):
        """dh_installdocs and rpm's %doc fail the build on a missing file, in OBS, late."""
        listed = [line.strip() for line in read("obs/debian.docs").splitlines() if line.strip()]
        listed += re.findall(r"^%doc (\S+\.md)$", read("rpm/noust.spec"), re.MULTILINE)
        missing = sorted({path for path in listed if not (REPO / path).is_file()})
        assert not missing, f"listed for the packages and not in the tree: {missing}"
        assert "docs/UPGRADING-3.1.md" in listed

    def test_the_rpm_conflicts_with_wasm_cli_2_and_never_obsoletes_it(self):
        """
        Obsoletes made dnf replace the 2.x package with noust outright, so rpm ran the old
        %preun with $1 = 0 - its final-removal branch - which stopped and disabled its units on
        every upgraded server. A conflict refuses the mix instead: 4.0 moves no data, so a
        2.x server goes through 3.3 first.
        """
        spec = read("rpm/noust.spec")

        assert re.search(r"^Conflicts:\s+wasm-cli < 3\.0\.0~$", spec, re.MULTILINE)
        assert not re.search(r"^Obsoletes:.*wasm-cli", spec, re.MULTILINE)

    def test_the_deb_conflicts_with_wasm_2_and_takes_over_nothing(self):
        control = read("obs/debian.control")

        assert control_field(control, "Conflicts") == "wasm (<< 3.0.0~)"
        for field in ("Breaks", "Replaces", "Provides"):
            assert not re.search(rf"^{field}:", control.split("\nPackage:", 1)[1], re.MULTILINE)

    def test_nothing_is_installed_under_etc(self):
        """noust writes /etc/noust itself, 0600 in a 0700 directory."""
        files = spec_section(read("rpm/noust.spec"), "%files")
        assert "%{_sysconfdir}" not in files
        assert "/etc/" not in files
        assert "%config" not in files

        rules = read("obs/debian.rules")
        assert not re.search(r"debian/noust/etc\b", rules)
        assert not any(line.startswith("etc/") for line in read("obs/debian.dirs").splitlines())

    @pytest.mark.parametrize("path", ["rpm/noust.spec", "obs/debian.rules"])
    def test_only_the_noust_command_its_manual_and_its_completions_are_installed(self, path: str):
        # The spec's %changelog is history and names the old command.
        recipe = read(path).split("\n%changelog\n")[0]

        assert "src/noust/completions/noust.bash" in recipe
        assert "man/noust.1" in recipe
        assert not re.search(r"wasm\.(bash|fish|1)|_wasm\b", recipe)

    @pytest.mark.parametrize("script", ["obs/debian.postinst", "rpm/noust.spec"])
    def test_the_upgrade_retires_the_links_before_it_touches_units(self, script: str):
        """
        The package runs `noust setup retire-links` itself, before the monitor is reinstalled
        and the console restarted; it must not fail the install (it keeps whatever is in use).
        """
        text = read(script)
        if script.endswith(".spec"):
            text = spec_section(text, "%posttrans")
        else:
            text = text[text.index("configure)") :]

        retire = text.index('noust setup retire-links --reason "package upgrade"')
        assert retire < text.index("monitor install")
        assert retire < text.index("restart noust-web.service")
        line = text[retire:].splitlines()[0]
        assert "|| true" in line or "|| :" in line, line

    @pytest.mark.parametrize(
        "script", ["obs/debian.postinst", "obs/debian.prerm", "obs/debian.postrm"]
    )
    def test_no_maintainer_script_still_looks_after_the_old_units_or_the_old_command(
        self, script: str
    ):
        assert not re.search(
            r"wasm-(web|monitor)|migrate-from|config\.yaml\.noust-keep", read(script)
        )

    def test_the_rpm_scriptlets_do_not_either(self):
        text = read("rpm/noust.spec")
        scriptlets = "\n".join(
            spec_section(text, name) for name in ("%pre", "%post", "%preun", "%posttrans")
        )

        assert not re.search(
            r"wasm-(web|monitor)|migrate-from|config\.yaml\.noust-keep", scriptlets
        )


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
