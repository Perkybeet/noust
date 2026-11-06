# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console server's modelled package database says (4.2, item 118).

Server > Software is read and photographed on the sandbox, which has to answer what the software manager asks
the machine in apt's own format and has to change when an install or a removal runs, or the page that offers
them has nothing to show before and nothing different after. What is defended:

- **dpkg-query answers both formats the manager uses**, for what is installed and with which version, and
  says nothing for a package that is not there.
- **A simulated removal lists what the package manager would take along**, and changes nothing; a real one
  changes what dpkg-query says afterwards.
- **An install makes a package installed**, and what it puts on the path is found by the machine's ``exists``.
- **nginx says whether it was built with the stream module** by whether the module's package is installed.
"""

from __future__ import annotations

from pathlib import Path

from scripts.console_server.machine import Unit
from scripts.console_server.server_host import ServerHost

VERSIONS = ("dpkg-query", "-W", "-f", "${Package} ${db:Status-Abbrev} ${Version}\\n")
PRESENT = ("dpkg-query", "-W", "-f", "${Package} ${db:Status-Abbrev}\\n")


def host(tmp_path: Path) -> ServerHost:
    return ServerHost(tmp_path, "web-1", {"nginx": Unit(active="active", managed=False)})


def answer(server: ServerHost, *argv: str):
    result = server.answer(argv)
    assert result is not None, argv
    return result


def test_dpkg_query_says_the_versions_of_what_is_installed_and_nothing_of_the_rest(
    tmp_path: Path,
) -> None:
    result = answer(host(tmp_path), *VERSIONS, "nginx", "chrony", "cloudflared")

    lines = result.stdout.splitlines()
    assert any(
        line.startswith("nginx ii ") and line.endswith("1.24.0-2ubuntu7.1") for line in lines
    )
    assert any(line.startswith("chrony ii ") for line in lines)
    assert not any(line.startswith("cloudflared") for line in lines)
    # dpkg-query exits 1 when it did not find one of them, which the manager reads as "not there".
    assert result.exit_code == 1


def test_the_short_format_is_answered_too(tmp_path: Path) -> None:
    result = answer(host(tmp_path), *PRESENT, "fail2ban")
    assert result.stdout.split()[:2] == ["fail2ban", "ii"]
    assert result.exit_code == 0


def test_a_simulated_removal_lists_what_goes_with_the_package_and_changes_nothing(
    tmp_path: Path,
) -> None:
    server = host(tmp_path)

    simulation = answer(
        server, "apt-get", "-s", "-o", "Debug::NoLocking=1", "remove", "fail2ban", "python3-systemd"
    )

    removed = [
        line.split()[1] for line in simulation.stdout.splitlines() if line.startswith("Remv ")
    ]
    assert removed == ["fail2ban", "python3-systemd", "python3-pyinotify", "whois"]
    assert server.packages.installed("fail2ban")


def test_a_removal_that_runs_makes_the_package_not_installed(tmp_path: Path) -> None:
    server = host(tmp_path)

    answer(server, "apt-get", "remove", "-y", "fail2ban", "python3-systemd")

    assert not answer(server, *PRESENT, "fail2ban").stdout.strip()


def test_an_install_makes_the_package_installed_and_puts_its_program_on_the_path(
    tmp_path: Path,
) -> None:
    server = host(tmp_path)
    assert not server.packages.provides("cloudflared")

    answer(server, "apt-get", "install", "-y", "cloudflared")

    assert "cloudflared ii " in answer(server, *VERSIONS, "cloudflared").stdout
    assert server.packages.provides("cloudflared")


def test_a_program_goes_with_the_package_that_owns_it_and_the_rest_is_the_machines(
    tmp_path: Path,
) -> None:
    server = host(tmp_path)
    assert server.packages.provides("fail2ban-client") is True

    answer(server, "apt-get", "remove", "-y", "fail2ban")

    assert server.packages.provides("fail2ban-client") is False
    # A program no package of the model owns is not the model's to say.
    assert server.packages.provides("nginx") is None


def test_nginx_says_it_was_built_with_the_stream_module_once_its_package_is_there(
    tmp_path: Path,
) -> None:
    server = host(tmp_path)
    assert "--with-stream" not in answer(server, "nginx", "-V").stderr
    assert "load_module" not in answer(server, "nginx", "-T").stdout

    answer(server, "apt-get", "install", "-y", "libnginx-mod-stream")

    assert "--with-stream=dynamic" in answer(server, "nginx", "-V").stderr
    assert "load_module modules/ngx_stream_module.so;" in answer(server, "nginx", "-T").stdout


def test_the_upgrade_simulation_is_still_the_server_areas(tmp_path: Path) -> None:
    result = answer(
        host(tmp_path), "apt-get", "-s", "-o", "Debug::NoLocking=1", "--with-new-pkgs", "upgrade"
    )
    assert "Inst " in result.stdout
