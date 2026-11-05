# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The package database of the modelled machine, behind Server > Software (item 118).

The Software page asks the machine three things through the runner: which of the registry's packages are
installed and in which version (``dpkg-query``), what removing some would take with them (``apt-get -s
remove``), and then, as a job, to install or remove them (``apt-get install``, ``apt-get remove``). This
answers each in apt's own format from a small model that the install and the removal change, so a page that
installs ``libnginx-mod-stream`` finds nginx able to publish a port afterwards, and one that removes fail2ban
finds it gone from the list. Nothing is executed.

The model is Ubuntu 24.04 as the rest of the Server area describes it: what Noust set up is there, and the
tools it installs on request (cloudflared, the stream module, ModSecurity, Knot, MongoDB, Apache, Docker) are
not, so the page has something to install and the notices that say what is missing have something to say.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noust.core.runner import CommandResult

#: What is installed, by package: the version dpkg reports.
_INSTALLED: dict[str, str] = {
    "noust": "4.2.0",
    "systemd": "255.4-1ubuntu8.4",
    "openssh-server": "1:9.6p1-3ubuntu13.5",
    "ufw": "0.36.2-6",
    "git": "1:2.43.0-1ubuntu7.1",
    "curl": "8.5.0-2ubuntu10.4",
    "gnupg": "2.4.4-2ubuntu17",
    "nginx": "1.24.0-2ubuntu7.1",
    "nginx-core": "1.24.0-2ubuntu7.1",
    "certbot": "2.9.0-1",
    "python3-certbot-nginx": "2.9.0-1",
    "nodejs": "22.11.0-1nodesource1",
    "php8.3-fpm": "8.3.6-0ubuntu0.24.04.2",
    "fail2ban": "1.0.2-3ubuntu0.1",
    "python3-systemd": "235-1build4",
    "python3-pyinotify": "0.9.6-2ubuntu1",
    "whois": "5.5.22",
    "chrony": "4.5-1ubuntu4.2",
    "unattended-upgrades": "2.9.1+nmu4ubuntu1",
    "rclone": "1.60.1+dfsg-2ubuntu0.24.04.1",
}

#: What else the package manager takes along when one package goes (``Remv`` lines of the simulation).
_TAKES_ALONG: dict[str, tuple[str, ...]] = {"fail2ban": ("python3-pyinotify", "whois")}

#: Packages that are installed beside the ones a component names and go with them.
_COMPANIONS: dict[str, tuple[str, ...]] = {"fail2ban": ("python3-pyinotify", "whois")}

#: The versions an install brings, for what is not installed now.
_AVAILABLE: dict[str, str] = {
    "libnginx-mod-stream": "1.24.0-2ubuntu7.1",
    "libnginx-mod-http-modsecurity": "1.0.3-1build2",
    "modsecurity-crs": "3.3.5-2",
    "knot": "3.3.5-1build2",
    "knot-dnsutils": "3.3.5-1build2",
    "apache2": "2.4.58-1ubuntu8.5",
    "docker.io": "24.0.7-0ubuntu4.1",
    "docker-compose-v2": "2.24.6-0ubuntu1~24.04.1",
    "cloudflared": "2024.10.1",
    "mongodb-org": "7.0.14",
}

#: The programs a package puts on the path, which the machine's ``which`` then finds: present while the package
#: is, gone when it is removed (fail2ban's client would otherwise say fail2ban is still there).
_PROGRAMS: dict[str, tuple[str, ...]] = {
    "cloudflared": ("cloudflared",),
    "docker.io": ("docker",),
    "chrony": ("chronyc",),
    "knot": ("knotd",),
    "apache2": ("apache2",),
    "fail2ban": ("fail2ban-client",),
    "rclone": ("rclone",),
}

#: What nginx prints for ``-V`` without and with the dynamic stream module.
_NGINX_V = "nginx version: nginx/1.24.0 (Ubuntu)\nconfigure arguments: --with-http_ssl_module --with-http_v2_module\n"
_NGINX_V_STREAM = _NGINX_V.replace("--with-http_v2_module", "--with-http_v2_module --with-stream=dynamic")

#: The configuration ``nginx -T`` dumps: Debian's own, which loads whatever ``modules-enabled`` holds.
_NGINX_T = """\
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
# configuration file /etc/nginx/nginx.conf:
user www-data;
worker_processes auto;
pid /run/nginx.pid;
{load}include /etc/nginx/modules-enabled/*.conf;
events {{
    worker_connections 768;
}}
http {{
    include /etc/nginx/mime.types;
}}
"""


class Packages:
    """The modelled machine's dpkg database, which install and remove change."""

    def __init__(self) -> None:
        """Start from what the machine has set up."""
        self.versions: dict[str, str] = dict(_INSTALLED)

    def installed(self, package: str) -> bool:
        """
        Args:
            package: A package name.

        Returns:
            Whether the machine has it.
        """
        return package in self.versions

    def provides(self, program: str) -> bool | None:
        """
        Args:
            program: An executable name.

        Returns:
            Whether an installed package puts it on the path; None when no package of the model owns
            it, which the machine's own list of programs then answers.
        """
        owners = [package for package, programs in _PROGRAMS.items() if program in programs]
        if not owners:
            return None
        return any(self.installed(package) for package in owners)

    def answer(self, args: tuple[str, ...]) -> CommandResult | None:
        """
        Answer a command about packages, or None to leave it to the rest of the runner.

        Args:
            args: The argv.

        Returns:
            A CommandResult, or None.
        """
        from noust.core.runner import CommandResult

        def done(stdout: str = "", code: int = 0, stderr: str = "") -> CommandResult:
            return CommandResult(argv=args, exit_code=code, stdout=stdout, stderr=stderr)

        program, rest = (args[0], args[1:]) if args else ("", ())
        if program == "dpkg-query" and rest[:1] == ("-W",):
            return self._query(rest, done)
        if program == "apt-get":
            return self._apt(rest, done)
        if program == "nginx" and rest[:1] in (("-V",), ("-T",)):
            streaming = self.installed("libnginx-mod-stream")
            if rest[0] == "-V":
                return done("", 0, _NGINX_V_STREAM if streaming else _NGINX_V)
            load = "load_module modules/ngx_stream_module.so;\n" if streaming else ""
            return done(_NGINX_T.format(load=load))
        return None

    def _query(self, rest: tuple[str, ...], done: Any) -> CommandResult:
        """Answer ``dpkg-query -W -f <format> packages...`` the way dpkg does, in either of the two formats used."""
        names = [item for item in rest[1:] if not item.startswith("-") and not item.startswith("${")]
        # `-f` and its format are two arguments, or one (`-f=${...}`); what is left are the names.
        names = [name for name in names if "${" not in name]
        compact = any("db:Status-Abbrev}" in item and "Version" not in item and "Package" not in item for item in rest)
        lines: list[str] = []
        missing: list[str] = []
        for name in names:
            version = self.versions.get(name)
            if version is None:
                missing.append(name)
            elif compact:
                lines.append("ii ")
            else:
                lines.append(f"{name} ii  {version}")
        stderr = "".join(f"dpkg-query: no packages found matching {name}\n" for name in missing)
        return done("\n".join(lines) + ("\n" if lines else ""), 1 if missing else 0, stderr)

    def _apt(self, rest: tuple[str, ...], done: Any) -> CommandResult | None:
        """Answer the installs and removals of the Software page, simulated or for real; the rest is the Server area's."""
        simulated = "-s" in rest
        verbs = [item for item in rest if item in ("install", "remove")]
        if not verbs:
            return None
        verb = verbs[0]
        packages = [item for item in rest[rest.index(verb) + 1 :] if not item.startswith("-")]
        if verb == "remove":
            taken = [name for name in packages if self.installed(name)]
            along = [extra for name in taken for extra in _TAKES_ALONG.get(name, ()) if self.installed(extra) and extra not in taken]
            if simulated:
                return done("".join(f"Remv {name} [{self.versions[name]}]\n" for name in [*taken, *along]))
            for name in [*taken, *along]:
                self.versions.pop(name, None)
            lines = [f"Removing {name} ({self.versions.get(name, '')}) ..." for name in [*taken, *along]]
            return done("Reading package lists...\n" + "\n".join(lines) + "\n")
        wanted = [name for name in packages if not self.installed(name)]
        if simulated:
            return done("".join(f"Inst {name} ({_AVAILABLE.get(name, '1.0')} Ubuntu:24.04/noble [amd64])\n" for name in wanted))
        for name in wanted:
            self.versions[name] = _AVAILABLE.get(name, "1.0")
            for extra in _COMPANIONS.get(name, ()):
                self.versions.setdefault(extra, "1.0")
        lines = [f"Setting up {name} ({self.versions[name]}) ..." for name in wanted]
        return done("Reading package lists...\nBuilding dependency tree...\n" + "\n".join(lines) + "\n")
