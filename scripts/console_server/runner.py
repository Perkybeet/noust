# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The runner that answers systemctl, journalctl, nginx, certbot and the rest from the model of the
machine, so nothing is ever spawned."""

from __future__ import annotations

import os
import re

# Only for the fleet's --seal bootstrap (see seal_sandbox_before_serving): a real,
# throwaway process that seals a sandbox's secrets before this one serves it, so this
# process never learns the passphrase itself and starts up genuinely locked. Nothing
# this script serves ever runs through it - noust.core.runner.CommandRunner still is,
# via the fake installed below - and CONTRIBUTING.md's rule 1 is about src/noust, not this
# development and test-only script.
import subprocess
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from . import server_host
from .constants import CALL_HISTORY
from .demo_database import (
    _PG_AVAILABLE_EXTENSIONS,
    _PG_DUMP_BYTES,
    _PG_RESTORE_LIST,
    _DemoDatabase,
    _psql_database,
)
from .engine_settings import (
    _PG_CLUSTER_LINE,
    _PG_CLUSTER_UNIT,
    _DatabaseContainer,
    _DatabaseContainers,
    _host_tree,
    _mysql_variables,
    _postgres_settings,
)
from .fleet import _enroll_keyscan, _enroll_ssh
from .machine import (
    CLIENT_VERSIONS,
    FAILED_JOURNAL_LINES,
    INSTALLED_PROGRAMS,
    JOURNAL_LINES,
    Unit,
    _foreign_unit_lines,
    _git_commit_subject_line,
)
from .server_host import _PG_CONFIG_DIR, SERVER_PROGRAMS
from .sources import forget_known_host, known_hosts_listing
from .sql_models import (
    _MYSQL_DATABASES,
    _MYSQL_DEMO_ROWS,
    _MYSQL_DEMO_ROWS_HEADERS,
    _MYSQL_USERS,
    _PG_DATABASES,
    _PG_DEMO_ROWS,
    _PG_DEMO_ROWS_CSV,
    _PG_USERS,
    _psql_console_sql,
    _sql_identifier,
    _sql_literal,
)


def make_runner(
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    certs: list[str],
    systemd_dir: Path,
) -> Any:
    """
    Build the runner that answers for the modelled machine.

    Args:
        units: Unit name (without ``.service``) to its state; mutated by
            ``systemctl start``, ``stop`` and ``restart``.
        ports: Unit name to the port its application listens on.
        domains: Unit name to its application's domain.
        certs: Domains holding a certificate, for ``certbot certificates``.
        systemd_dir: The sandboxed unit directory, whose cron timers
            ``systemctl list-unit-files`` reports.

    Returns:
        A :class:`noust.core.runner.FakeRunner` answering from the model.
    """
    from noust.core.runner import CommandResult, FakeRunner, runuser_prefix

    lock = threading.Lock()

    def unit_of(argument: str) -> str:
        return argument.removesuffix(".service")

    def base_name(name: str) -> str:
        """The unit's name stripped of `.service` or `.timer`, for tracking that applies to both."""
        return name.removesuffix(".service").removesuffix(".timer")

    def template_of(name: str) -> Path | None:
        """
        The template file an instance (``shop@green``) is loaded from, when it is installed.

        systemd loads an instance from ``<prefix>@.service``; with no template on disk the
        instance does not exist, whatever it was before.
        """
        prefix, at, instance = name.partition("@")
        if not at or not prefix or not instance:
            return None
        template = systemd_dir / f"{prefix}@.service"
        return template if template.is_file() else None

    def instance_port(template: Path, instance: str) -> int | None:
        """
        The port an instance listens on: PORT in ``<colors dir>/<instance>.env``.

        The template names that file (``EnvironmentFile=<colors>/%i.env``); systemd expands
        ``%i`` to the instance, and the application reads PORT from it.
        """
        for line in template.read_text(encoding="utf-8").splitlines():
            if line.startswith("EnvironmentFile=") and line.endswith("/%i.env"):
                env_file = Path(line.split("=", 1)[1].lstrip("-").replace("%i", instance))
                try:
                    match = re.search(r"^PORT=(\d+)", env_file.read_text(encoding="utf-8"), re.M)
                except OSError:
                    return None
                return int(match.group(1)) if match else None
        return None

    # Enable/disable of a unit `_systemctl` does not otherwise model (a cron timer, or a
    # service created through the API this run rather than seeded into `units`): systemctl
    # enable/disable always succeeds against a real unit file, and `list-unit-files` and
    # `is-enabled` must be able to answer it afterwards. Keyed by base_name() so a `.timer`
    # and its paired `.service` - and a plain service - all agree. Absent means "enabled": a
    # freshly written unit is enabled by default, matching what `noust service create` and
    # `noust cron create` do on a real machine before this ever runs.
    enabled_overrides: dict[str, bool] = {}

    def ok(argv: tuple[str, ...], stdout: str = "", exit_code: int = 0) -> CommandResult:
        return CommandResult(argv=argv, exit_code=exit_code, stdout=stdout, stderr="")

    class ConsoleRunner(FakeRunner):
        """A FakeRunner that behaves like the seeded machine."""

        def __init__(self) -> None:
            """Start with a bounded call history."""
            super().__init__()
            # The model itself, for the fakes installed beside the runner (a health probe,
            # a port check) that must agree with it about what runs and where.
            self.model_units = units
            self.model_ports = ports
            self.model_domains = domains
            self.calls = deque(maxlen=CALL_HISTORY)  # type: ignore[assignment]
            self.inputs = deque(maxlen=CALL_HISTORY)  # type: ignore[assignment]
            self._stdin = threading.local()
            # What the modelled machine has installed. MongoDB and Docker are
            # deliberately absent, so the console shows an engine to install.
            self.only_knows(*INSTALLED_PROGRAMS, *SERVER_PROGRAMS)
            # A terminal opened in the console talks to a shell that echoes what is typed and
            # ends on `exit`, not to a real one: see FakeRunner.pty.
            self.pty_echo = True
            # Per-instance copies: the Databases page can create and drop
            # databases, and a stateless dict would have `create_database`
            # succeed and then have its own follow-up `get_database_info`
            # report "does not exist" (PostgresManager re-checks existence).
            self._pg_databases: dict[str, tuple[str, int]] = dict(_PG_DATABASES)
            self._mysql_databases: dict[str, tuple[int, int]] = dict(_MYSQL_DATABASES)
            # The extensions loaded in the modelled PostgreSQL's databases (the same for all).
            self._pg_extensions: dict[str, str] = {"pg_trgm": "1.6"}
            # What a database's own page reads: its tables, rows, metrics, access.
            self._demo_database = _DemoDatabase()
            # Docker's database containers, and the one a command is being run inside.
            self._containers = _DatabaseContainers()
            self._inside = threading.local()

        def exists(self, program: str) -> bool:
            # What a package installed through the Software page puts on the path is there from then on, and
            # what one removed took with it.
            host = server_host._SERVER_HOST
            owned = host.packages.provides(program) if host is not None else None
            return super().exists(program) if owned is None else owned

        def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
            # Noust's own SQL reaches the database clients on stdin; the psql
            # console statement arrives in -c instead (see _psql_console_sql).
            self._stdin.value = kwargs.get("input") or ""
            return super().run(argv, **kwargs)

        def _lookup(
            self,
            argv: Sequence[str],
            user: str | None = None,
            env: Mapping[str, str] | None = None,
            *,
            record: bool = True,
        ) -> CommandResult:
            args = tuple(str(a) for a in argv)
            recorded = (*runuser_prefix(user), *args) if user is not None else args
            if record:
                self.calls.append(recorded)
                self.envs.append(dict(env) if env is not None else None)
            # Answered as the command it runs: switching the account (runuser -u
            # postgres -- psql) asks the same question of the machine.
            program = args[0] if args else ""
            # The Server area's tools first: see model_server_host.
            server = (
                server_host._SERVER_HOST.answer(args)
                if server_host._SERVER_HOST is not None
                else None
            )
            if server is not None:
                return server
            # A one-off command runs in the application's sandbox, which wraps it in a
            # systemd-run call: the model answers the program inside, for the two it knows.
            inner = getattr(self, "_inner_argv", None)
            if program == "systemd-run" and inner and inner[0] in ("echo", "false"):
                return self._lookup(inner, record=False)
            with lock:
                if program == "systemctl":
                    return self._systemctl(args)
                if program == "journalctl":
                    return self._journal(args)
            if program == "docker":
                inside = self._containers.exec_target(args)
                if inside is not None:
                    return self._exec_in_container(args, *inside)
                answered = self._containers.answer(args)
                if answered is not None:
                    return CommandResult(args, *answered)
            if program == "pg_lsclusters":
                return ok(args, _PG_CLUSTER_LINE)
            if program.endswith("/bin/postgres") and "-C" in args:
                # postgres -C NAME prints the value its configuration files give NAME.
                asked = _postgres_settings(_host_tree()).get(args[args.index("-C") + 1])
                return ok(args, f"{asked[0]}\n" if asked else "")
            if program == "nginx":
                if "-v" in args:
                    return CommandResult(args, 0, "", "nginx version: nginx/1.24.0 (Ubuntu)\n")
                return CommandResult(
                    args,
                    0,
                    "",
                    "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok\n"
                    "nginx: configuration file /etc/nginx/nginx.conf test is successful\n",
                )
            if "--version" in args and program in CLIENT_VERSIONS:
                return ok(args, CLIENT_VERSIONS[program])
            if program == "echo":
                # What a one-off command printed (POST /api/apps/{domain}/exec): the console's
                # E2E runs `echo` and `false`, the two programs the model needs to answer.
                return ok(args, " ".join(args[1:]) + "\n")
            if program == "false":
                return CommandResult(args, 1, "", "")
            if program == "pg_dump":
                return ok(args, _PG_DUMP_BYTES)
            if program == "pg_restore" and "--list" in args:
                return ok(args, _PG_RESTORE_LIST)
            if program == "psql":
                demo = self._demo_database.answer(
                    _psql_database(args),
                    getattr(self._stdin, "value", "") or _psql_console_sql(args),
                    csv="--csv" in args,
                    container=getattr(self._inside, "container", None),
                )
                if demo is not None:
                    return ok(args, demo)
            if program in ("psql", "mysql", "redis-cli"):
                # The structured SQL console asks for a header row: psql with
                # `--csv`, mysql with `-B` alone (no `-N`). Every other caller
                # of these clients keeps the old headerless shape.
                headers = "--csv" in args or (
                    program == "mysql" and "-B" in args and "-N" not in args
                )
                statement = getattr(self._stdin, "value", "") or _psql_console_sql(args)
                return ok(args, self._sql(program, statement, headers=headers))
            if program == "certbot" and args[1:2] == ("certificates",):
                return ok(args, self._certificates())
            if program == "certbot" and "--version" in args:
                return ok(args, "certbot 2.9.0\n")
            if program == "openssl" and args[1:2] == ("enc",):
                # Noust's own state is sealed and opened through `openssl enc` (the secrets of
                # a central, a bundle of Noust's own backup): the files it writes are real
                # ones in the sandbox, so a model that wrote nothing would fail them. The
                # cipher is the machine's own; everything else of openssl stays a model.
                return self._real_openssl(args, env, getattr(self._stdin, "value", ""))
            if program == "openssl" and "-issuer" in args:
                # CertManager reads the issuer straight from the certificate
                # file through openssl; the model answers the same line real
                # Let's Encrypt leaf certificates carry, regardless of which
                # file it was pointed at.
                return ok(args, "issuer=C = US, O = Let's Encrypt, CN = R11\n")
            if program == "systemd-analyze" and args[1:2] == ("calendar",):
                return ok(args, self._systemd_analyze_calendar(args))
            if program == "systemd-analyze" and args[1:2] == ("verify",):
                return self._systemd_analyze_verify(args)
            if program == "systemd-analyze" and "--version" in args:
                return ok(args, "systemd 255 (255.4-1ubuntu8)\n")
            if program == "git":
                subject = _git_commit_subject_line(args)
                if subject is not None:
                    return ok(args, subject + "\n")
            if program == "ssh-keygen":
                return self._ssh_keygen(args)
            if program == "ssh-keyscan":
                return _enroll_keyscan(args)
            if program == "ssh" and "--" in args:
                return _enroll_ssh(args, env)
            return ok(args)

        def _exec_in_container(
            self, args: tuple[str, ...], container: _DatabaseContainer, inner: tuple[str, ...]
        ) -> CommandResult:
            """
            Run a client inside a database container: the same answers as the host's engine,
            over the container's own databases.

            Args:
                args: The ``docker exec`` as Noust built it.
                container: The container it names.
                inner: The command that runs in it, program first.

            Returns:
                What the client prints, or Docker's refusal for a container that is stopped.
            """
            if container.state != "running":
                return CommandResult(
                    args,
                    1,
                    "",
                    f"Error response from daemon: container {container.container_id} "
                    "is not running\n",
                )
            self._inside.container = container
            try:
                return replace(self._lookup(inner, record=False), argv=args)
            finally:
                self._inside.container = None

        @staticmethod
        def _real_openssl(
            args: tuple[str, ...], env: Mapping[str, str] | None, data: str
        ) -> CommandResult:
            """
            Run ``openssl enc`` for real, with the key in the environment as the caller put it.

            Args:
                args: The argv, ``openssl enc ...``.
                env: The caller's extra environment (the key is in it, never in argv).
                data: What the caller wrote to standard input.

            Returns:
                What openssl did, or a failed result naming why it could not run.
            """
            try:
                done = subprocess.run(
                    list(args),
                    input=data,
                    capture_output=True,
                    text=True,
                    env={**os.environ, **dict(env or {})},
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return CommandResult(args, 127, "", f"{exc}\n")
            return CommandResult(args, done.returncode, done.stdout, done.stderr)

        @staticmethod
        def _ssh_keygen(args: tuple[str, ...]) -> CommandResult:
            """
            Fake the fleet's own key pair (``NodeKeys.ensure_keypair``).

            Writes a structurally valid ed25519 pair, exactly as
            ``tests.support.fleet_support.KeygenRunner`` already does for pytest (rule 3: one
            implementation, reused here rather than copied). No real key material is
            needed: this console never opens a real SSH connection with it - see
            ``make_loopback_tunnels`` - so nothing ever reads these files for their
            cryptographic content, only for their shape and for the fingerprint the
            fleet's join code carries.
            """
            from tests.support.fleet_support import ed25519_line

            # The host keys Noust pinned (Settings, Integrations): listed and forgotten
            # from the file, as ssh-keygen does, never written as a key pair.
            if "-l" in args and "-f" in args:
                return ok(args, known_hosts_listing(Path(args[args.index("-f") + 1])))
            if "-R" in args and "-f" in args:
                forget_known_host(Path(args[args.index("-f") + 1]), args[args.index("-R") + 1])
                return ok(args)
            if "-f" not in args:
                return ok(args)
            path = Path(args[args.index("-f") + 1])
            comment = args[args.index("-C") + 1] if "-C" in args else ""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
            )
            path.chmod(0o600)
            # Deterministic within this process, and different per node name (the
            # comment noust always passes), which is all a fingerprint needs here.
            seed = hash(str(path)) & 0xFFFF
            Path(f"{path}.pub").write_text(ed25519_line(seed, comment) + "\n")
            return ok(args)

        def _systemctl(self, args: tuple[str, ...]) -> CommandResult:
            verb = next((a for a in args[1:] if not a.startswith("-")), "")
            targets = [a for a in args[2:] if not a.startswith("-") and a != verb]
            if verb == "--version" or "--version" in args:
                return ok(args, "systemd 255 (255.4-1ubuntu8)\n")
            if verb == "list-unit-files":
                return ok(args, self._unit_files(targets))
            if verb == "list-units":
                return ok(args, self._list_units(targets))
            name = unit_of(targets[-1]) if targets else ""
            if name == _PG_CLUSTER_UNIT:
                # The cluster's own unit is the machine's `postgresql`: one state for both.
                name = "postgresql"
            if verb == "restart" and name == "systemd-journald":
                # The journal's daemon is always there; the page that sets how long it keeps the
                # logs restarts it to read the drop-in it wrote.
                return ok(args)
            unit = units.get(name)
            template = template_of(name)
            if unit is None and template is not None and verb in ("start", "restart"):
                # A blue/green instance comes to exist when it is first started: loaded from
                # its template, listening on the port its own environment file gives it.
                instance = name.partition("@")[2]
                unit = units[name] = Unit(active="inactive", enabled=False)
                base = name.partition("@")[0]
                domains[name] = domains.get(base, base)
                port = instance_port(template, instance)
                if port is not None:
                    ports[name] = port
            if unit is not None and verb in ("start", "restart") and "@" in name:
                # Its port may have moved since it last ran (a switch rewrites the file).
                if template is not None:
                    port = instance_port(template, name.partition("@")[2])
                    if port is not None:
                        ports[name] = port
            if verb == "is-active":
                state = unit.active if unit else "inactive"
                return ok(args, f"{state}\n", 0 if state == "active" else 3)
            if verb == "is-enabled":
                enabled = (
                    unit.enabled
                    if unit is not None
                    else enabled_overrides.get(base_name(name), True)
                )
                return ok(args, "enabled\n" if enabled else "disabled\n", 0 if enabled else 1)
            if verb == "show":
                if "-p" in args:
                    return ok(args, "FragmentPath=\n")
                if any(a.startswith("--property=") for a in args):
                    return ok(args, self._cron_properties(targets[-1] if targets else ""))
                return ok(args, self._show(name, unit))
            if unit is not None and verb in ("start", "restart"):
                unit.active, unit.since, unit.pid = "active", datetime.now(), 40000 + len(name)
                return ok(args)
            if unit is not None and verb == "stop":
                unit.active, unit.since, unit.pid = "inactive", datetime.now(), 0
                return ok(args)
            if verb in ("enable", "disable"):
                if unit is not None:
                    unit.enabled = verb == "enable"
                # Recorded regardless of whether `unit` models this name, so a cron timer or
                # an ad-hoc created service (see the comment on enabled_overrides) is tracked
                # exactly like one `units` already knows.
                enabled_overrides[base_name(name)] = verb == "enable"
                return ok(args)
            if verb in ("start", "stop", "restart") and unit is None:
                # Cron job units (noust-cron-{name}.service) are written to the sandboxed
                # systemd directory but never registered in the live `units` model: they have
                # no persistent active/inactive state worth modelling, only a one-shot run.
                # "noust cron run" starts one by name, so a unit that genuinely exists on disk
                # succeeds here; only a name with no unit file at all is "not found".
                if (systemd_dir / f"{name}.service").exists():
                    return ok(args)
                return CommandResult(
                    args, 5, "", f"Failed to {verb} {targets[-1]}: Unit not found.\n"
                )
            return ok(args)

        @staticmethod
        def _show(name: str, unit: Unit | None) -> str:
            if unit is None and template_of(name) is not None:
                # An instance of an installed template nothing has started yet.
                return "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nMemoryCurrent=0\nResult=success\n"
            if unit is None:
                # A unit written to disk but never given live state here (created through the
                # API this run, or a cron timer) is loaded and inactive, the way systemd
                # reports one nothing has started - never plain "not-found". Either way,
                # MainPID always appears as a number: ServiceInfo.pid is `int | None`, and
                # `details.get("MainPID", "")` used to default to "", which pydantic refused
                # to parse as an int and turned GET /api/services into a 500 the moment a
                # service was created without a modelled Unit.
                if (systemd_dir / f"{name}.service").exists():
                    return "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nMemoryCurrent=0\nResult=success\n"
                return "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\n"
            # `since` is local wall-clock time; systemd's answer here is labelled UTC, so it is
            # converted first rather than relabelled (off by the machine's offset otherwise).
            since = unit.since.astimezone(timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")
            return (
                f"Id={name}.service\n"
                "LoadState=loaded\n"
                f"ActiveState={unit.active}\n"
                f"SubState={unit.sub}\n"
                f"MainPID={unit.pid}\n"
                f"MemoryCurrent={96 * 1024 * 1024 if unit.active == 'active' else 0}\n"
                f"ActiveEnterTimestamp={since if unit.active == 'active' else ''}\n"
                f"NRestarts={unit.restarts}\n"
                f"Result={'exit-code' if unit.active == 'failed' else 'success'}\n"
            )

        def _sql(self, program: str, statement: str, *, headers: bool = False) -> str:
            # An instance method, not a staticmethod: `self._pg_databases` and
            # `self._mysql_databases` start as copies of the module's seed data
            # and are mutated by CREATE/DROP DATABASE below, so a database the
            # Databases page just created answers to every query about it
            # afterwards - including PostgresManager.create_database's own
            # follow-up call to get_database_info, which re-checks existence.
            #
            # A client run inside a database container (see _exec_in_container) is the
            # same PostgreSQL over the container's own databases and roles.
            container = getattr(self._inside, "container", None)
            pg_databases = container.databases if container is not None else self._pg_databases
            pg_users = container.users if container is not None else _PG_USERS
            if program == "psql" and statement.strip() == "SHOW ssl;":
                # Debian and Ubuntu ship PostgreSQL with TLS on (a self-signed certificate),
                # which is what lets public access ask for TLS without being told otherwise.
                return "on\n"
            if program == "psql" and statement.strip() == "SHOW server_version_num;":
                return "160004\n"
            if program == "psql" and "FROM pg_available_extensions e" in statement:
                # The server's own list: what its packages installed, with what is loaded.
                return "".join(
                    "\x1f".join(
                        [
                            name,
                            version,
                            self._pg_extensions.get(name, ""),
                            comment,
                            "true" if trusted else "false",
                            requires,
                        ]
                    )
                    + "\n"
                    for name, (
                        version,
                        comment,
                        trusted,
                        requires,
                    ) in _PG_AVAILABLE_EXTENSIONS.items()
                )
            if program == "psql" and "FROM pg_extension WHERE extname" in statement:
                return "".join(f"{name}\n" for name in sorted(self._pg_extensions))
            if (
                program == "psql"
                and statement.strip() == "SELECT name FROM pg_available_extensions;"
            ):
                return "".join(f"{name}\n" for name in _PG_AVAILABLE_EXTENSIONS)
            loaded = re.fullmatch(
                r'\s*CREATE EXTENSION IF NOT EXISTS "([^"]+)"( CASCADE)?;\s*', statement
            )
            if program == "psql" and loaded and loaded.group(1) in _PG_AVAILABLE_EXTENSIONS:
                self._pg_extensions[loaded.group(1)] = _PG_AVAILABLE_EXTENSIONS[loaded.group(1)][0]
                return ""
            unloaded = re.fullmatch(r'\s*DROP EXTENSION "([^"]+)"( CASCADE)?;\s*', statement)
            if program == "psql" and unloaded:
                self._pg_extensions.pop(unloaded.group(1), None)
                return ""
            if program == "psql" and statement.strip() == "SHOW hba_file;":
                # The file public access edits to make an account refuse a connection
                # without TLS: the modelled host's own, never this machine's.
                host = _host_tree()
                return f"{host / _PG_CONFIG_DIR.lstrip('/') / 'pg_hba.conf'}\n" if host else ""
            if program == "psql" and "FROM pg_hba_file_rules" in statement:
                return "0\n"
            if program == "psql" and statement.strip() == "SELECT pg_reload_conf();":
                return "t\n"
            # The settings page: pg_settings and SHOW GLOBAL VARIABLES, as the engine
            # would answer them over the files Noust has written (see _postgres_settings).
            if program == "psql" and "FROM pg_settings" in statement:
                return "".join(
                    f"{key}|{value}|{context}|{source}\n"
                    for key, (value, context, source) in _postgres_settings(_host_tree()).items()
                    if f"'{key}'" in statement
                )
            if program == "mysql" and "SHOW GLOBAL VARIABLES" in statement:
                return "".join(
                    f"{name}\t{value}\n"
                    for name, value in _mysql_variables(_host_tree()).items()
                    if f"'{name}'" in statement
                )
            #
            # More specific matches first: PostgresManager.get_database_info's owner
            # query and database_exists' existence check both contain "pg_database"
            # as a substring (inside "pg_database_size" and "FROM pg_database"), so
            # they would otherwise fall through to the generic listing below.
            #
            # list_databases()'s own query joins pg_roles the same way
            # get_database_info()'s does (for the owner column), so both contain
            # "d.datdba = r.oid"; list_databases() is the one with no per-database
            # WHERE literal ("datistemplate = false" instead of "datname = '...'")
            # and must be checked first, or its listing query would be mistaken for
            # a lookup of one database and answer as though `name` were None.
            #
            # The dump loader (pg_loader.owner_query and role_query) asks who owns the
            # database it loads into and whether that role could reach the machine: one
            # row, "owner|f" for an ordinary role. It joins pg_roles the other way round
            # ("r.oid = d.datdba"), so the owner lookups below do not match it, and the
            # generic listing at the end would answer a question it was not asked.
            if program == "psql" and "r.oid = d.datdba" in statement and "pg_has_role" in statement:
                info = pg_databases.get(_sql_literal(statement) or "")
                return f"{info[0]}|f\n" if info else ""
            if (
                program == "psql"
                and "FROM pg_catalog.pg_roles r WHERE r.rolname" in statement
                and "pg_has_role" in statement
            ):
                role = _sql_literal(statement)
                return f"{role}|f\n" if role else ""
            if (
                program == "psql"
                and "d.datdba = r.oid" in statement
                and "datistemplate" in statement
            ):
                return "".join(
                    f"{name}|UTF8|{size}|{owner}\n" for name, (owner, size) in pg_databases.items()
                )
            if program == "psql" and "d.datdba = r.oid" in statement:
                name = _sql_literal(statement)
                info = pg_databases.get(name or "")
                return f"{name}|UTF8|{info[1]}|{info[0]}\n" if info else ""
            if program == "psql" and statement.lstrip().startswith("SELECT 1 FROM pg_database"):
                return "1\n" if _sql_literal(statement) in pg_databases else ""
            if program == "psql" and statement.lstrip().startswith("CREATE DATABASE"):
                name = _sql_identifier(statement, '"')
                if name is not None:
                    owner = _sql_identifier(statement, '"', after="OWNER") or "postgres"
                    pg_databases[name] = (owner, 8192)
                return ""
            if program == "psql" and statement.lstrip().startswith("DROP DATABASE"):
                name = _sql_identifier(statement, '"')
                if name is not None:
                    pg_databases.pop(name, None)
                return ""
            # list_users()'s combined query aliases every column ("r.rolsuper"
            # rather than the old bare "rolsuper"), so it no longer contains
            # that literal; matched instead on `has_database_privilege`, the
            # function unique to this query's databases-per-role column - see
            # _PG_USERS for its (now 5-column) shape.
            if program == "psql" and "has_database_privilege" in statement:
                return pg_users
            # PostgresManager.server_port() asks the superuser session which
            # port the cluster listens on, for connection strings and the
            # read-only console's TCP login.
            if program == "psql" and statement.strip() == "SHOW port;":
                return "5432\n"
            if program == "psql" and statement.strip() == "SHOW listen_addresses;":
                return "localhost\n"
            if program == "psql" and "FROM demo_orders" in statement:
                return _PG_DEMO_ROWS_CSV if headers else _PG_DEMO_ROWS
            if program == "psql" and "pg_database" in statement:
                return "".join(
                    f"{name}|UTF8|{size}\n" for name, (_owner, size) in pg_databases.items()
                )
            # Same reasoning for MySQL: get_database_info's size query sums
            # bare "DATA_LENGTH + INDEX_LENGTH" columns (single table, no
            # alias needed); list_databases()'s own query joins
            # INFORMATION_SCHEMA.TABLES aliased "t" for the same sum, so it
            # reads "t.DATA_LENGTH + t.INDEX_LENGTH" instead and is matched
            # separately, checked first since the alias makes it more specific.
            if program == "mysql" and "t.DATA_LENGTH + t.INDEX_LENGTH" in statement:
                seeded = "".join(
                    f"{name}\tutf8mb4\t{size}\n"
                    for name, (size, _tables) in self._mysql_databases.items()
                )
                return "information_schema\tutf8mb3\t0\nmysql\tutf8mb4\t0\n" + seeded
            if program == "mysql" and "DATA_LENGTH + INDEX_LENGTH" in statement:
                info = self._mysql_databases.get(_sql_literal(statement) or "")
                return f"{info[0]}\t{info[1]}\n" if info else "0\t0\n"
            if program == "mysql" and statement.lstrip().startswith("SELECT SCHEMA_NAME FROM"):
                name = _sql_literal(statement)
                return f"{name}\n" if name in self._mysql_databases else ""
            if program == "mysql" and statement.lstrip().startswith(
                "SELECT DEFAULT_CHARACTER_SET_NAME"
            ):
                return "utf8mb4\n"
            if program == "mysql" and statement.lstrip().startswith("CREATE DATABASE"):
                name = _sql_identifier(statement, "`")
                if name is not None:
                    self._mysql_databases[name] = (0, 0)
                return ""
            if program == "mysql" and statement.lstrip().startswith("DROP DATABASE"):
                name = _sql_identifier(statement, "`")
                if name is not None:
                    self._mysql_databases.pop(name, None)
                return ""
            # list_users()'s combined query still contains this fragment (the
            # join is "FROM mysql.user u"), with a 3rd column of comma
            # separated databases appended - see _MYSQL_USERS.
            if program == "mysql" and "mysql.user" in statement:
                return _MYSQL_USERS
            if program == "mysql" and "FROM demo_orders" in statement:
                return _MYSQL_DEMO_ROWS_HEADERS if headers else _MYSQL_DEMO_ROWS
            if program == "mysql" and "SCHEMATA" in statement:
                return "information_schema\tutf8mb3\nmysql\tutf8mb4\n" + "".join(
                    f"{name}\tutf8mb4\n" for name in self._mysql_databases
                )
            if program == "redis-cli" and "keyspace" in statement:
                return "# Keyspace\ndb0:keys=1284,expires=210,avg_ttl=0\ndb2:keys=37,expires=0\n"
            if program == "redis-cli" and "databases" in statement:
                return "databases\n16\n"
            return ""

        @staticmethod
        def _systemd_analyze_calendar(args: tuple[str, ...]) -> str:
            """
            Fake `systemd-analyze calendar --iterations=N <expr>` for the cron
            job dialog's "next N runs" preview.

            Not real calendar arithmetic - the console only needs believable,
            parseable, always-in-the-future dates to prove the round trip
            works, not the exact semantics of an arbitrary OnCalendar
            expression. Every run is a day apart starting tomorrow at 02:00,
            regardless of what the expression actually says.
            """
            expr = args[-1] if len(args) > 1 else "*-*-* 02:00:00"
            iterations = 5
            for arg in args:
                if arg.startswith("--iterations="):
                    iterations = int(arg.split("=", 1)[1])
            start = datetime.now().replace(hour=2, minute=0, second=0, microsecond=0) + timedelta(
                days=1
            )
            lines = [f"  Original form: {expr}", f"Normalized form: {expr}"]
            for index in range(iterations):
                stamp = (start + timedelta(days=index)).strftime("%a %Y-%m-%d %H:%M:%S UTC")
                label = "Next elapse" if index == 0 else f"Iter. #{index + 1}"
                lines.append(f"      {label}: {stamp}")
            return "\n".join(lines) + "\n"

        @staticmethod
        def _systemd_analyze_verify(args: tuple[str, ...]) -> CommandResult:
            """
            Fake `systemd-analyze verify <path>` for the unit editor's "test
            configuration" button.

            Reads the candidate unit the caller staged (through the real,
            sandboxed filesystem - `ServiceManager.verify_unit` writes it
            before running this) and reports the one failure a hand-edited
            unit realistically hits: no `ExecStart=`. Anything else is
            reported as a clean pass, matching systemd-analyze's own silence
            on success.
            """
            path = Path(args[-1]) if len(args) > 1 else None
            content = path.read_text() if path and path.exists() else ""
            if not re.search(r"^ExecStart=\S", content, re.MULTILINE):
                name = path.name if path else "unit"
                return CommandResult(
                    args,
                    1,
                    "",
                    f"{name}: Service has no ExecStart= setting. Refusing.\n",
                )
            return ok(args)

        @staticmethod
        def _unit_files(patterns: list[str]) -> str:
            lines = []
            for path in sorted(systemd_dir.glob("*")):
                if patterns and not any(fnmatch(path.name, p) for p in patterns):
                    continue
                state = (
                    "enabled" if enabled_overrides.get(base_name(path.name), True) else "disabled"
                )
                lines.append(f"{path.name} {state} {state}")
            return "\n".join(lines) + "\n"

        @staticmethod
        def _cron_properties(unit: str) -> str:
            now = datetime.now()
            stamp = "%a %Y-%m-%d %H:%M:%S UTC"
            if unit.endswith(".timer"):
                calendar = "*-*-* 02:00:00"
                try:
                    text = (systemd_dir / unit).read_text(encoding="utf-8")
                    calendar = next(
                        line.split("=", 1)[1]
                        for line in text.splitlines()
                        if line.startswith("OnCalendar=")
                    )
                except (OSError, StopIteration):
                    pass
                return (
                    f"TimersCalendar={{ OnCalendar={calendar} ; next_elapse=n/a }}\n"
                    f"LastTriggerUSec={(now - timedelta(hours=9)).strftime(stamp)}\n"
                    f"NextElapseUSecRealtime={(now + timedelta(hours=15)).strftime(stamp)}\n"
                )
            failed = "cleanup" in unit
            return (
                f"ExecMainStatus={1 if failed else 0}\n"
                f"ExecMainExitTimestamp={(now - timedelta(hours=9)).strftime(stamp)}\n"
                f"Result={'exit-code' if failed else 'success'}\n"
            )

        @staticmethod
        def _list_units(patterns: list[str]) -> str:
            lines = ["UNIT LOAD ACTIVE SUB DESCRIPTION"]
            for name, unit in sorted(units.items()):
                if not unit.managed:
                    continue
                if template_of(name) is None and not (systemd_dir / f"{name}.service").exists():
                    # A unit whose file was removed (an instance: its template) is no longer
                    # loaded: blue/green retires an application's own unit, and removes the
                    # instances' template when it is turned off.
                    continue
                if patterns and not any(
                    fnmatch(f"{name}.service", p) or fnmatch(name, p) for p in patterns
                ):
                    continue
                description = domains.get(name, name)
                lines.append(f"{name}.service loaded {unit.active} {unit.sub} {description}")
            lines.extend(_foreign_unit_lines(units, patterns))
            return "\n".join(lines) + "\n"

        @staticmethod
        def _journal(args: tuple[str, ...]) -> CommandResult:
            name = ""
            if "-u" in args:
                index = args.index("-u")
                name = unit_of(args[index + 1]) if index + 1 < len(args) else ""
            unit = units.get(name)
            template = FAILED_JOURNAL_LINES if unit and unit.active == "failed" else JOURNAL_LINES
            start = datetime.now() - timedelta(minutes=len(template))
            lines = [
                f"{(start + timedelta(minutes=i)).strftime('%b %d %H:%M:%S')} acme "
                f"{name or 'systemd'}[{unit.pid if unit and unit.pid else 1}]: "
                + line.format(unit=name, domain=domains.get(name, name), port=ports.get(name, 3000))
                for i, line in enumerate(template)
            ]
            return CommandResult(args, 0, "\n".join(lines) + "\n", "")

        @staticmethod
        def _certificates() -> str:
            blocks = ["Saving debug log to /var/log/letsencrypt/letsencrypt.log", "", "-" * 79]
            blocks.append("Found the following certs:")
            for offset, domain in enumerate(certs):
                expiry = datetime.now() + timedelta(days=12 + offset * 17)
                days = (expiry - datetime.now()).days
                blocks += [
                    f"  Certificate Name: {domain}",
                    "    Serial Number: 4a3f9c2e1b7d",
                    "    Key Type: ECDSA",
                    f"    Domains: {domain} www.{domain}",
                    f"    Expiry Date: {expiry.strftime('%Y-%m-%d %H:%M:%S')}+00:00 "
                    f"(VALID: {days} days)",
                    f"    Certificate Path: /etc/letsencrypt/live/{domain}/fullchain.pem",
                    f"    Private Key Path: /etc/letsencrypt/live/{domain}/privkey.pem",
                ]
            blocks.append("-" * 79)
            return "\n".join(blocks) + "\n"

    return ConsoleRunner()
