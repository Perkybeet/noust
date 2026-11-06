# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The machine behind the Server area (/api/server): an Ubuntu 24.04 VPS with work to do."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .machine import Unit
from .sandbox import Sandbox
from .software_host import Packages

#: The cluster's configuration directory, as it is on the machine.
_PG_CONFIG_DIR = "/etc/postgresql/16/main"

#: The modelled server's memory and processors, which the recommendations are computed
#: from: a 2 GiB VPS, as the Server area describes it, whatever machine runs this script.
_MODELLED_MEMORY_BYTES = 2 * 1024**3
_MODELLED_CPUS = 2


# ---------------------------------------------------------------------------------------
# The machine behind the Server area (/api/server): an Ubuntu 24.04 VPS with work to do.
#
# The server managers read files below a root (HostPaths) and ask the machine through the
# runner; both are answered here, from a small host tree in the sandbox and canned output
# in each tool's own format (apt-get -s, sshd -T, ss, ufw, fail2ban-client, timedatectl,
# hostnamectl, journalctl --output=json...), so every tab renders the way it does on a real
# server: security updates pending, a reboot due, passwords on in SSH, PostgreSQL answering
# the internet through a forgotten rule, two addresses banned. Nothing is executed.

#: Programs the modelled server has for the Server area, beside INSTALLED_PROGRAMS.
SERVER_PROGRAMS = (
    "apt-get",
    "apt-mark",
    "apt-config",
    "dpkg",
    "dpkg-query",
    "needrestart",
    "sshd",
    "ss",
    "ufw",
    "fail2ban-client",
    "hostnamectl",
    "timedatectl",
    "swapon",
    "findmnt",
    "du",
    "ionice",
    "nice",
    "shutdown",
    "systemd-detect-virt",
)

#: What Debian's postgresql-common writes for a cluster: the settings Noust edits that it
#: sets itself, and the include_dir that makes Noust's own conf.d file count.
_SERVER_POSTGRESQL_CONF = """\
# -----------------------------
# PostgreSQL configuration file
# -----------------------------
data_directory = '/var/lib/postgresql/16/main'
hba_file = '/etc/postgresql/16/main/pg_hba.conf'
ident_file = '/etc/postgresql/16/main/pg_ident.conf'
external_pid_file = '/var/run/postgresql/16-main.pid'
port = 5432
max_connections = 100
unix_socket_directories = '/var/run/postgresql'
ssl = on
shared_buffers = 128MB
dynamic_shared_memory_type = posix
max_wal_size = 1GB
min_wal_size = 80MB
log_line_prefix = '%m [%p] %q%u@%d '
cluster_name = '16/main'
datestyle = 'iso, dmy'
timezone = 'Europe/Madrid'
lc_messages = 'en_US.UTF-8'
default_text_search_config = 'pg_catalog.spanish'
include_dir = 'conf.d'
"""

#: Debian's pg_hba.conf for a cluster, the lines that matter: what public access puts its
#: own block above, and the local lines Noust signs in through.
_SERVER_PG_HBA_CONF = """\
# PostgreSQL Client Authentication Configuration File
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             postgres                                peer
local   all             all                                     peer
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
local   replication     all                                     peer
host    replication     all             127.0.0.1/32            scram-sha-256
host    replication     all             ::1/128                 scram-sha-256
"""

#: Ubuntu's mysqld.cnf: the include directory Noust writes 99-noust.cnf into is the one
#: /etc/mysql/my.cnf already reads.
_SERVER_MYSQLD_CNF = """\
[mysqld]
user            = mysql
# bind-address  = 127.0.0.1
mysqlx-bind-address = 127.0.0.1
key_buffer_size         = 16M
myisam-recover-options  = BACKUP
log_error = /var/log/mysql/error.log
max_binlog_size   = 100M
"""

_SERVER_OS_RELEASE = """PRETTY_NAME="Ubuntu 24.04.1 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.1 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
UBUNTU_CODENAME=noble
"""

#: ``apt-get -s upgrade``: two security updates (one a kernel), five others, one kept back.
_SERVER_APT_UPGRADE = """NOTE: This is only a simulation!
      apt-get needs root privileges for real execution.
Reading package lists...
Building dependency tree...
Reading state information...
Calculating upgrade...
The following packages have been kept back:
  linux-generic
The following packages will be upgraded:
  curl libcurl4t64 libssl3t64 linux-image-6.8.0-47-generic nginx nginx-common openssl python3-urllib3
8 upgraded, 1 newly installed, 0 to remove and 1 not upgraded.
Inst libssl3t64 [3.0.13-0ubuntu3.3] (3.0.13-0ubuntu3.4 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst openssl [3.0.13-0ubuntu3.3] (3.0.13-0ubuntu3.4 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst linux-image-6.8.0-47-generic (6.8.0-47.47 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst curl [8.5.0-2ubuntu10.3] (8.5.0-2ubuntu10.4 Ubuntu:24.04/noble-updates [amd64])
Inst libcurl4t64 [8.5.0-2ubuntu10.3] (8.5.0-2ubuntu10.4 Ubuntu:24.04/noble-updates [amd64])
Inst nginx-common [1.24.0-2ubuntu7] (1.24.0-2ubuntu7.1 Ubuntu:24.04/noble-updates [all])
Inst nginx [1.24.0-2ubuntu7] (1.24.0-2ubuntu7.1 Ubuntu:24.04/noble-updates [amd64])
Inst python3-urllib3 [2.0.7-1] (2.0.7-1ubuntu0.1 Ubuntu:24.04/noble-updates [all])
"""

#: ``apt-get -s full-upgrade``: the same, plus what was kept back and the kernel it retires.
_SERVER_APT_FULL = (
    _SERVER_APT_UPGRADE.replace(
        "The following packages have been kept back:\n  linux-generic\n", ""
    )
    + "Remv linux-image-6.8.0-31-generic [6.8.0-31.31]\n"
    + "Inst linux-generic [6.8.0-45.45] (6.8.0-47.47 Ubuntu:24.04/noble-updates [amd64])\n"
)

_SERVER_SSHD_T = """port 22
addressfamily any
listenaddress [::]:22
listenaddress 0.0.0.0:22
permitrootlogin prohibit-password
pubkeyauthentication yes
passwordauthentication yes
kbdinteractiveauthentication no
permitemptypasswords no
maxauthtries 6
maxsessions 10
logingracetime 120
clientaliveinterval 0
x11forwarding yes
allowtcpforwarding yes
allowagentforwarding yes
loglevel INFO
usepam yes
authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2
"""

_SERVER_UFW_STATUS = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
443/tcp                    ALLOW IN    Anywhere
5432/tcp                   ALLOW IN    Anywhere                   # temporary: migration from the old host
22/tcp (v6)                ALLOW IN    Anywhere (v6)
80/tcp (v6)                ALLOW IN    Anywhere (v6)
443/tcp (v6)               ALLOW IN    Anywhere (v6)
"""

_SERVER_UFW_ADDED = """Added user rules (see 'ufw status' for running firewall):
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5432/tcp comment 'temporary: migration from the old host'
"""

#: What the modelled machine spends its disk on, for ``du -sx -B1``, by the end of the path
#: (the sandbox moves Noust's own directories, not their names); the first match wins.
_SERVER_DU = (
    ("/var/cache/apt/archives", 642 * 1024**2),
    ("/var/lib/postgresql", 2_400 * 1024**2),
    ("/var/lib/mysql", 1_100 * 1024**2),
    ("/var/tmp", 12 * 1024**2),  # noqa: S108 - a path of the modelled host, matched, never used
    ("/tmp", 96 * 1024**2),  # noqa: S108 - a path of the modelled host, matched, never used
    ("/log/noust", 180 * 1024**2),
)


def _server_key(comment: str, seed: int) -> tuple[str, str]:
    """
    An ed25519 public key line, in the SSH wire format, and its SHA256 fingerprint.

    Args:
        comment: The key's comment.
        seed: Makes each key distinct.

    Returns:
        The ``authorized_keys`` line and ``SHA256:...``, as ``ssh-keygen -l`` prints it.
    """
    import base64
    import hashlib

    def field(data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + data

    blob = field(b"ssh-ed25519") + field(hashlib.sha256(f"noust-console-{seed}".encode()).digest())
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}", fingerprint


class ServerHost:
    """The modelled VPS: a host tree below ``root`` and the tools' answers."""

    def __init__(self, root: Path, hostname: str, units: dict[str, Unit]) -> None:
        """
        Args:
            root: Where the host tree is written (HostPaths' root).
            hostname: The name the rest of the console reports.
            units: The runner's unit model, for ``systemctl --failed``.
        """
        self.root = root
        self.hostname = hostname
        self.units = units
        self.boot_id = "3f1c2a4b5d6e7f8091a2b3c4d5e6f708"
        self.operator_key, self.operator_fingerprint = _server_key("yago@laptop", 1)
        self.deploy_key, self.deploy_fingerprint = _server_key("github-actions@deploy", 2)
        # What is installed, which Server > Software reads and its installs and removals change.
        self.packages = Packages()

    # -- the host tree ----------------------------------------------------------------

    def write(self, relative: str, content: str = "", *, age: timedelta | None = None) -> Path:
        """Write a file of the host tree, dated ``age`` ago when given."""
        path = self.root / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if age is not None:
            moment = (datetime.now() - age).timestamp()
            os.utime(path, (moment, moment))
        return path

    def build(self) -> None:
        """Write the files the server managers read."""
        self.write("/etc/os-release", _SERVER_OS_RELEASE)
        self.write("/etc/hostname", f"{self.hostname}\n")
        self.write("/etc/fstab", "LABEL=cloudimg-rootfs / ext4 discard,errors=remount-ro 0 1\n")
        self.write("/proc/uptime", "1051234.56 2020000.12\n")
        self.write("/proc/sys/kernel/random/boot_id", f"{self.boot_id}\n")
        self.write("/proc/sys/vm/swappiness", "60\n")
        self.write(
            "/proc/meminfo",
            "MemTotal:        2030612 kB\nMemFree:          210044 kB\nMemAvailable:     918332 kB\n",
        )
        (self.root / "run/systemd/system").mkdir(parents=True, exist_ok=True)
        (self.root / "var/log/journal").mkdir(parents=True, exist_ok=True)
        self.write(
            "/var/run/reboot-required", "*** System restart required ***\n", age=timedelta(days=9)
        )
        self.write(
            "/var/run/reboot-required.pkgs",
            "linux-image-6.8.0-45-generic\nlibc6\n",
            age=timedelta(days=9),
        )
        self.write("/var/lib/apt/periodic/update-success-stamp", age=timedelta(hours=3))
        for directory in (
            "var/lib/apt/lists",
            "var/cache/apt/archives",
            "var/lib/postgresql",
            "var/lib/mysql",
            "tmp",
            "var/tmp",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.write(
            "/var/log/unattended-upgrades/unattended-upgrades.log", "", age=timedelta(hours=20)
        )
        self.write(
            "/etc/apt/apt.conf.d/20auto-upgrades",
            'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n',
        )
        for name in (
            "vmlinuz-6.8.0-45-generic",
            "initrd.img-6.8.0-45-generic",
            "vmlinuz-6.8.0-47-generic",
            "initrd.img-6.8.0-47-generic",
        ):
            self.write(f"/boot/{name}")
        self.write(
            "/etc/ssh/sshd_config",
            "Include /etc/ssh/sshd_config.d/*.conf\nKbdInteractiveAuthentication no\nUsePAM yes\nX11Forwarding yes\n",
        )
        (self.root / "etc/ssh/sshd_config.d").mkdir(parents=True, exist_ok=True)
        self.write(
            "/etc/passwd",
            "root:x:0:0:root:/root:/bin/bash\n"
            "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
            "deploy:x:1000:1000:Deploy,,,:/home/deploy:/bin/bash\n"
            "postgres:x:113:120:PostgreSQL administrator,,,:/var/lib/postgresql:/bin/bash\n",
        )
        self.write("/etc/group", "root:x:0:\nsudo:x:27:deploy\nwww-data:x:33:\ndeploy:x:1000:\n")
        self.write(
            "/etc/shadow",
            "root:!:19990:0:99999:7:::\n"
            "deploy:$y$j9T$Zm9vYmFyYmF6$6Y1o0t9mW2m6c6c0nY2i1m3s8m5o2Q5c4J8m1v0n2s1:19990:0:99999:7:::\n"
            "www-data:*:19990:0:99999:7:::\npostgres:*:19990:0:99999:7:::\n",
        )
        self.write("/etc/shells", "/bin/sh\n/bin/bash\n/usr/bin/bash\n")
        self.write(
            "/etc/sudoers", "Defaults env_reset\nroot ALL=(ALL:ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n"
        )
        self.write(f"{_PG_CONFIG_DIR}/postgresql.conf", _SERVER_POSTGRESQL_CONF)
        self.write(f"{_PG_CONFIG_DIR}/pg_hba.conf", _SERVER_PG_HBA_CONF)
        (self.root / _PG_CONFIG_DIR.lstrip("/") / "conf.d").mkdir(parents=True, exist_ok=True)
        self.write("/etc/mysql/mysql.conf.d/mysqld.cnf", _SERVER_MYSQLD_CNF)
        self.write("/root/.ssh/authorized_keys", f"{self.operator_key}\n")
        self.write("/home/deploy/.ssh/authorized_keys", f"{self.deploy_key}\n")
        for path in ("root/.ssh", "home/deploy/.ssh"):
            (self.root / path).chmod(0o700)
        for path in ("root/.ssh/authorized_keys", "home/deploy/.ssh/authorized_keys"):
            (self.root / path).chmod(0o600)

    # -- the tools' answers ---------------------------------------------------------------

    def answer(self, args: tuple[str, ...]) -> Any:
        """
        Answer a command of the Server area, or None to leave it to the rest of the runner.

        Args:
            args: The argv.

        Returns:
            A CommandResult, or None.
        """
        from noust.core.runner import CommandResult

        def ok(stdout: str = "", code: int = 0) -> CommandResult:
            return CommandResult(argv=args, exit_code=code, stdout=stdout, stderr="")

        packages = self.packages.answer(args)
        if packages is not None:
            return packages
        program, rest = (args[0], args[1:]) if args else ("", ())
        if program == "systemd-detect-virt":
            return ok("none\n", 1)
        if program == "hostnamectl" and rest[:1] == ("--json=short",):
            return ok(
                json.dumps(
                    {
                        "Hostname": self.hostname,
                        "StaticHostname": self.hostname,
                        "PrettyHostname": None,
                        "Chassis": "vm",
                        "MachineID": "5d3e2f1a9b8c7d6e5f4a3b2c1d0e9f8a",
                        "BootID": self.boot_id,
                        "OperatingSystemPrettyName": "Ubuntu 24.04.1 LTS",
                    }
                )
                + "\n"
            )
        if program == "timedatectl" and rest[:1] == ("show",):
            now = datetime.now().astimezone()
            return ok(
                "Timezone=Europe/Madrid\nLocalRTC=no\nCanNTP=yes\nNTP=yes\nNTPSynchronized=yes\n"
                f"TimeUSec={now.strftime('%a %Y-%m-%d %H:%M:%S %Z')}\n"
            )
        if program == "swapon":
            return ok("")
        if program == "systemctl" and rest[:1] == ("is-system-running",):
            failed = any(unit.active == "failed" for unit in self.units.values())
            return ok("degraded\n" if failed else "running\n", 1 if failed else 0)
        if program == "systemctl" and "--failed" in rest:
            return ok(
                "".join(
                    f"{name}.service loaded failed failed {name}\n"
                    for name, unit in sorted(self.units.items())
                    if unit.active == "failed"
                )
            )
        if program == "systemctl" and rest[:1] == ("is-active",) and "fail2ban.service" in rest:
            return ok("active\n")
        if program == "apt-get" and "-s" in rest:
            return ok(
                _SERVER_APT_FULL
                if "full-upgrade" in rest or "dist-upgrade" in rest
                else _SERVER_APT_UPGRADE
            )
        if program == "apt-mark" or (program == "dpkg" and rest[:1] == ("--audit",)):
            return ok("")
        if program == "dpkg-query" and "unattended-upgrades" in rest:
            return ok("ii ")
        if program == "apt-config" and rest[:1] == ("dump",):
            return ok(
                'APT::Periodic::Update-Package-Lists "1";\n'
                'APT::Periodic::Unattended-Upgrade "1";\n'
                'Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}";\n'
                'Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-security";\n'
                'Unattended-Upgrade::Automatic-Reboot "false";\n'
            )
        if program == "needrestart":
            return ok(
                "NEEDRESTART-VER: 3.6\nNEEDRESTART-KCUR: 6.8.0-45-generic\nNEEDRESTART-KEXP: 6.8.0-47-generic\n"
                "NEEDRESTART-KSTA: 3\nNEEDRESTART-SVC: nginx.service\nNEEDRESTART-SVC: cron.service\n"
                "NEEDRESTART-SVC: ssh.service\n"
            )
        if program == "findmnt" and "--verify" in rest:
            return ok("Success, no errors or warnings detected\n")
        if program == "journalctl" and "--disk-usage" in rest:
            return ok("Archived and active journal files take up 1.2G in the file system.\n")
        if program == "journalctl" and "--list-boots" in rest:
            earlier = (datetime.now() - timedelta(days=12, hours=4)).strftime(
                "%a %Y-%m-%d %H:%M:%S UTC"
            )
            before = (datetime.now() - timedelta(days=40)).strftime("%a %Y-%m-%d %H:%M:%S UTC")
            latest = datetime.now().strftime("%a %Y-%m-%d %H:%M:%S UTC")
            return ok(
                f" -1 9a8b7c6d5e4f30211a2b3c4d5e6f7081 {before} {earlier}\n"
                f"  0 {self.boot_id} {earlier} {latest}\n"
            )
        if program == "journalctl" and "--output=json" in rest:
            # The applications' search asks for their units one by one (`-u shop-example-com*`); the
            # server's journal asks for one (`--unit=`) or none.
            if "-u" in rest:
                return ok(self._applications_journal(rest))
            return ok(self._journal_json(rest))
        if program == "journalctl" and any(arg.startswith("_COMM=sshd") for arg in rest):
            return ok(self._ssh_logins())
        if program == "ionice" and "du" in args:
            path = args[-1]
            size = next(
                (size for suffix, size in _SERVER_DU if path.endswith(suffix)), 48 * 1024**2
            )
            return ok(f"{size}\t{path}\n")
        if program == "sshd" and rest[:1] == ("-T",):
            return ok(_SERVER_SSHD_T)
        if program == "sshd" and rest[:1] == ("-t",):
            return ok("")
        if program == "ss" and rest[:1] == ("-Hltnup",):
            return ok(
                'tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=812,fd=3))\n'
                'tcp LISTEN 0 4096 [::]:22 [::]:* users:(("sshd",pid=812,fd=4))\n'
                'tcp LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=1021,fd=6))\n'
                'tcp LISTEN 0 511 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=1021,fd=7))\n'
                'tcp LISTEN 0 244 0.0.0.0:5432 0.0.0.0:* users:(("postgres",pid=990,fd=5))\n'
                'tcp LISTEN 0 151 127.0.0.1:3306 0.0.0.0:* users:(("mysqld",pid=1003,fd=21))\n'
                'tcp LISTEN 0 511 127.0.0.1:6379 0.0.0.0:* users:(("redis-server",pid=977,fd=6))\n'
                'tcp LISTEN 0 2048 127.0.0.1:8080 0.0.0.0:* users:(("python3",pid=1300,fd=9))\n'
                'tcp LISTEN 0 511 127.0.0.1:3004 0.0.0.0:* users:(("node",pid=2204,fd=19))\n'
                'udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=610,fd=13))\n'
            )
        if program == "ss" and "established" in rest:
            return ok('0 0 10.0.0.5:22 203.0.113.7:51234 users:(("sshd",pid=41022,fd=4))\n')
        if program == "ufw" and rest == ("status", "verbose"):
            return ok(_SERVER_UFW_STATUS)
        if program == "ufw" and rest == ("show", "added"):
            return ok(_SERVER_UFW_ADDED)
        if program == "fail2ban-client":
            return ok(self._fail2ban(rest))
        if program == "shutdown":
            return self._shutdown(rest, ok)
        return None

    def _applications_journal(self, rest: tuple[str, ...]) -> str:
        """
        ``journalctl --output=json -u <app>*``: what the applications' units said, newest first.

        An application that runs says it started and answers its requests; one that failed says why,
        in the words its runtime and systemd use, so that the logs of the applications have something
        to search (4.1 task 3: why an application is down).

        Args:
            rest: The arguments after ``journalctl``.

        Returns:
            One JSON entry per line, as journalctl prints them.
        """
        patterns = [
            rest[index + 1].rstrip("*")
            for index, arg in enumerate(rest)
            if arg == "-u" and index + 1 < len(rest)
        ]
        priority = next(
            (int(arg.split("=", 1)[1]) for arg in rest if arg.startswith("--priority=")), 7
        )
        since = next(
            (int(arg[len("--since=@") :]) for arg in rest if arg.startswith("--since=@")), 0
        )
        until = next(
            (int(arg[len("--until=@") :]) for arg in rest if arg.startswith("--until=@")),
            int(time.time()),
        )
        now = datetime.now(timezone.utc)
        entries: list[tuple[datetime, str, int, int, str]] = []
        for name, unit in self.units.items():
            base = name.removesuffix(".service")
            if not unit.managed or not any(
                pattern and base.startswith(pattern) for pattern in patterns
            ):
                continue
            began = now - timedelta(minutes=47)
            entries.append((began, name, 6, unit.pid or 2200, "Server listening on port 3000"))
            entries.append(
                (began + timedelta(minutes=2), name, 6, unit.pid or 2200, "GET /api/health 200 3ms")
            )
            if unit.active == "failed":
                broke = now - timedelta(minutes=12)
                entries.extend(
                    [
                        (broke, name, 3, 2310, "Error: connect ECONNREFUSED 127.0.0.1:5432"),
                        (
                            broke + timedelta(seconds=1),
                            name,
                            3,
                            2310,
                            "    at TCPConnectWrap.afterConnect (node:net:1555:16)",
                        ),
                        (
                            broke + timedelta(seconds=2),
                            name,
                            4,
                            1,
                            f"{name}.service: Main process exited, code=exited, status=1/FAILURE",
                        ),
                        (
                            broke + timedelta(seconds=3),
                            name,
                            3,
                            1,
                            f"{name}.service: Failed with result 'exit-code'.",
                        ),
                    ]
                )
        lines = []
        for moment, name, level, pid, message in sorted(
            entries, key=lambda item: item[0], reverse=True
        ):
            if level > priority or not since <= moment.timestamp() <= until:
                continue
            micros = int(moment.timestamp() * 1_000_000)
            lines.append(
                json.dumps(
                    {
                        "__REALTIME_TIMESTAMP": str(micros),
                        "PRIORITY": str(level),
                        "MESSAGE": message,
                        "_SYSTEMD_UNIT": f"{name}.service"
                        if not name.endswith(".service")
                        else name,
                        "_PID": str(pid),
                    }
                )
            )
        return "\n".join(lines) + ("\n" if lines else "")

    def _journal_json(self, rest: tuple[str, ...]) -> str:
        """``journalctl --output=json``: a morning on the server, filtered as asked."""
        unit = next((arg.split("=", 1)[1] for arg in rest if arg.startswith("--unit=")), None)
        priority = next(
            (int(arg.split("=", 1)[1]) for arg in rest if arg.startswith("--priority=")), 7
        )
        kernel = "--dmesg" in rest
        entries = [
            (
                "kernel",
                6,
                None,
                "Linux version 6.8.0-45-generic (buildd@lcy02-amd64-075) #45-Ubuntu SMP PREEMPT_DYNAMIC",
            ),
            ("systemd-journald.service", 6, 301, "Journal started"),
            (
                "ssh.service",
                6,
                41022,
                "Accepted publickey for root from 203.0.113.7 port 51234 ssh2: ED25519 "
                + self.operator_fingerprint,
            ),
            ("ssh.service", 6, 41110, "Invalid user admin from 185.220.101.4 port 40022"),
            (
                "ssh.service",
                6,
                41111,
                "Failed password for invalid user admin from 185.220.101.4 port 40022 ssh2",
            ),
            ("fail2ban.service", 5, 700, "NOTICE  [sshd] Ban 185.220.101.4"),
            (
                "nginx.service",
                4,
                1021,
                "2026/09/29 09:12:44 [warn] 1021#1021: *88 an upstream response is buffered to a temporary file",
            ),
            (
                "postgresql@16-main.service",
                6,
                990,
                "LOG:  checkpoint complete: wrote 412 buffers (2.5%)",
            ),
            (
                "cron.service",
                6,
                880,
                "(root) CMD (test -x /usr/sbin/anacron || { cd / && run-parts --report /etc/cron.daily; })",
            ),
            (
                "unattended-upgrades.service",
                6,
                1502,
                "Packages that will be upgraded: libssl3t64 openssl",
            ),
            ("noust-web.service", 6, 1300, "Console listening on 127.0.0.1:8080"),
            (
                "mysql.service",
                3,
                1003,
                "[ERROR] [MY-012574] [InnoDB] Unable to lock ./ibdata1 error: 11",
            ),
            (
                "nginx.service",
                3,
                1021,
                '2026/09/29 09:40:02 [error] 1021#1021: *131 connect() failed (111: Connection refused) while connecting to upstream, upstream: "http://127.0.0.1:3007/"',
            ),
            (
                "ssh.service",
                6,
                41200,
                "Received disconnect from 203.0.113.7 port 51234:11: disconnected by user",
            ),
        ]
        # The morning is dated once, when it is first read: an entry keeps its cursor, as it does in
        # a real journal, so "the entries around this one" can find the one that was opened.
        if getattr(self, "_journal_start", None) is None:
            self._journal_start = datetime.now(timezone.utc) - timedelta(minutes=len(entries) * 3)
        start = self._journal_start
        # A cursor reads like journalctl's: --cursor starts at the entry, --after-cursor right after
        # it, --reverse walks back, and --lines counts from there. Without a cursor the whole
        # morning is answered, as before.
        cursor = next((arg.split("=", 1)[1] for arg in rest if arg.startswith("--cursor=")), None)
        after = next(
            (arg.split("=", 1)[1] for arg in rest if arg.startswith("--after-cursor=")), None
        )
        count = next(
            (int(arg.split("=", 1)[1]) for arg in rest if arg.startswith("--lines=")), None
        )
        wanted = cursor if cursor is not None else after
        position = (
            int(wanted.split(";")[1].removeprefix("i="), 16) - 1 if wanted is not None else None
        )
        kept: list[tuple[int, str, int, int | None, str]] = []
        for index, (name, level, pid, message) in enumerate(entries):
            if (
                level > priority
                or (kernel and name != "kernel")
                or (
                    unit is not None
                    and name.removesuffix(".service") != unit.removesuffix(".service")
                )
            ):
                continue
            kept.append((index, name, level, pid, message))
        if position is not None:
            backwards = "--reverse" in rest
            keep_from = position if cursor is not None else position + (-1 if backwards else 1)
            window = [
                item
                for item in kept
                if (item[0] <= keep_from if backwards else item[0] >= keep_from)
            ]
            window = list(reversed(window)) if backwards else window
            kept = window[:count] if count is not None else window
        lines = []
        for index, name, level, pid, message in kept:
            micros = int((start + timedelta(minutes=index * 3)).timestamp() * 1_000_000)
            record = {
                "__CURSOR": f"s=4b1c;i={index + 1:x};b={self.boot_id};m={micros:x}",
                "__REALTIME_TIMESTAMP": str(micros),
                "PRIORITY": str(level),
                "MESSAGE": message,
                **(
                    {"_SYSTEMD_UNIT": name} if name != "kernel" else {"SYSLOG_IDENTIFIER": "kernel"}
                ),
                **({"_PID": str(pid)} if pid is not None else {}),
            }
            lines.append(json.dumps(record))
        return "\n".join(lines) + ("\n" if lines else "")

    def _ssh_logins(self) -> str:
        """``journalctl _COMM=sshd -o short-unix``: the logins sshd recorded."""
        now = time.time()
        return (
            f"{now - 3 * 86400:.6f} {self.hostname} sshd[39001]: Accepted publickey for deploy from 198.51.100.20 port 40210 ssh2: ED25519 {self.deploy_fingerprint}\n"
            f"{now - 2 * 86400:.6f} {self.hostname} sshd[40012]: Accepted publickey for root from 203.0.113.7 port 50122 ssh2: ED25519 {self.operator_fingerprint}\n"
            f"{now - 1800:.6f} {self.hostname} sshd[41022]: Accepted publickey for root from 203.0.113.7 port 51234 ssh2: ED25519 {self.operator_fingerprint}\n"
        )

    @staticmethod
    def _fail2ban(rest: tuple[str, ...]) -> str:
        """``fail2ban-client``: one jail, for sshd, with two addresses banned now."""
        if rest[:1] == ("ping",):
            return "Server replied: pong\n"
        if rest == ("status",):
            return "Status\n|- Number of jail:\t1\n`- Jail list:\tsshd\n"
        if rest == ("status", "sshd"):
            return (
                "Status for the jail: sshd\n"
                "|- Filter\n"
                "|  |- Currently failed:\t3\n"
                "|  |- Total failed:\t1284\n"
                "|  `- Journal matches:\t_SYSTEMD_UNIT=sshd.service + _COMM=sshd\n"
                "`- Actions\n"
                "   |- Currently banned:\t2\n"
                "   |- Total banned:\t97\n"
                "   `- Banned IP list:\t185.220.101.4 45.148.10.182\n"
            )
        return ""

    def _shutdown(self, rest: tuple[str, ...], ok: Any) -> Any:
        """``shutdown -r +N`` leaves the schedule for logind; ``shutdown -c`` removes it."""
        scheduled = self.root / "run/systemd/shutdown/scheduled"
        if rest[:1] == ("-c",):
            scheduled.unlink(missing_ok=True)
            return ok("")
        minutes = next(
            (int(arg[1:]) for arg in rest if arg.startswith("+") and arg[1:].isdigit()), 1
        )
        mode = "reboot" if "-r" in rest else "poweroff"
        due = int((time.time() + minutes * 60) * 1_000_000)
        scheduled.parent.mkdir(parents=True, exist_ok=True)
        scheduled.write_text(f"USEC={due}\nWARN_WALL=1\nMODE={mode}\n", encoding="utf-8")
        return ok("")


#: The modelled server, once serve() has built it; the runner asks it first.
_SERVER_HOST: ServerHost | None = None


def model_server_host(sandbox: Sandbox, hostname: str, units: dict[str, Unit]) -> ServerHost:
    """
    Build the VPS the Server area manages, and point its managers at it.

    Every ``HostPaths()`` in the process resolves below the sandbox's host tree from here on,
    the disks are the modelled VPS's rather than this machine's, and the last scan of what
    takes space is already there, as it is on a server someone measured yesterday.

    Args:
        sandbox: The sandbox.
        hostname: The name the rest of the console reports.
        units: The runner's unit model.

    Returns:
        The model, which the runner consults for every command.
    """
    global _SERVER_HOST
    from noust.managers.server.host import HostPaths
    from noust.managers.server.storage import Mount, StorageManager

    host = ServerHost(sandbox.root / "host", hostname, units)
    host.build()
    HostPaths.__init__.__defaults__ = (host.root,)

    # The database area keeps its own HostPaths, made when the module was imported: without
    # this the engines' settings (and the distribution the install catalog reads) would be
    # this machine's, and a change made in the console would be a write to its /etc.
    import noust.managers.database.settings as database_settings
    from noust.managers.database import flavours

    flavours.HOST = HostPaths(root=host.root)
    # What the recommendations are computed from: the modelled server, not this machine.
    database_settings.server_resources = lambda: database_settings.Resources(
        memory_bytes=_MODELLED_MEMORY_BYTES, cpus=_MODELLED_CPUS
    )

    gib = 1024**3

    def mounts(self: Any) -> list[Any]:
        rows = [
            ("/", "/dev/vda1", "ext4", 78 * gib, 55.6 * gib, 5_111_808, 4_900_112),
            ("/srv", "/dev/vdb", "xfs", 40 * gib, 35.1 * gib, 20_971_520, 20_100_000),
            ("/boot", "/dev/vda16", "ext4", 881 * 1024**2, 118 * 1024**2, 58_496, 58_100),
        ]
        found = []
        for mount_point, device, fstype, total, used, inodes, inodes_free in rows:
            percent = round(used / total * 100, 1)
            inode_percent = round((inodes - inodes_free) / inodes * 100, 1)
            found.append(
                Mount(
                    mount_point=mount_point,
                    device=device,
                    fstype=fstype,
                    total_bytes=int(total),
                    used_bytes=int(used),
                    free_bytes=int(total - used),
                    percent_used=percent,
                    inodes_total=inodes,
                    inodes_free=inodes_free,
                    inodes_percent=inode_percent,
                    readonly=False,
                    status="critical" if percent >= 95 else "warn" if percent >= 85 else "ok",
                )
            )
        return sorted(found, key=lambda mount: mount.percent_used, reverse=True)

    StorageManager.mounts = mounts  # type: ignore[method-assign]

    # The host tree belongs to whoever runs this script; on the modelled VPS the key files
    # are root's, and StrictModes has nothing to say about their owner.
    import noust.managers.server.security_keys as keys_module
    import noust.managers.server.security_probe as probe_module
    import noust.managers.server.security_ssh as ssh_module

    strict = keys_module.strict_mode_problems
    owner = f"belongs to uid {os.getuid()},"

    def owned_by_root(paths: Any, account: Any, path: str) -> list[str]:
        return [problem for problem in strict(paths, account, path) if owner not in problem]

    for module in (keys_module, probe_module, ssh_module):
        module.strict_mode_problems = owned_by_root  # type: ignore[attr-defined]
    _SERVER_HOST = host
    return host


def seed_server_analysis() -> None:
    """Measure the known places and run the hardening checks once, and keep both."""
    from noust.managers.server.security import ServerSecurity
    from noust.web.api.server.common import get_server_context
    from noust.web.api.server.storage import ANALYSIS_KEY

    seed_software_ledger()
    context = get_server_context()
    context.cache.put(ANALYSIS_KEY, context.storage.analyze())
    # The hardening checks ran this morning, as they do on a server someone looks after: the
    # Overview and the Security tab read their last run, and never probe on their own.
    ServerSecurity(actor="console-server").checks(refresh=True)


def seed_software_ledger() -> None:
    """
    Write down what Noust installed on the modelled VPS, so Server > Software tells it from what was there.

    The rest of the packages (systemd, git, curl, chrony, rclone...) came with the image or were put there by
    hand: they are installed and the page says Noust did not install them, which is what a removal asks for
    a stronger yes about.
    """
    from noust.core.software_ledger import SoftwareLedger

    ledger = SoftwareLedger()
    for component, packages, via, actor in (
        ("noust", ("noust",), "package", "cli:root"),
        ("nginx", ("nginx", "nginx-core"), "package", "yago"),
        ("certbot", ("certbot", "python3-certbot-nginx"), "package", "yago"),
        ("nodejs", ("nodejs",), "package", "yago"),
        ("php", ("php8.3-fpm",), "package", "yago"),
        ("fail2ban", ("fail2ban", "python3-systemd"), "fail2ban", "cli:root"),
        ("auto_updates", ("unattended-upgrades",), "package", "cli:root"),
    ):
        ledger.record(component, packages=packages, via=via, actor=actor)
