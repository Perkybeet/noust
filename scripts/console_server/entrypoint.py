# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Serving: the uvicorn server and the command's main."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .arguments import parse_args
from .backup_destinations import seed_showcase_backup_destination, use_showcase_diagnose_paths
from .code_hosts import model_code_host
from .constants import (
    CATALOG_ENV,
    CODE_HOST_ENV,
    FLEET_NODE_APP_ENV,
    GRACEFUL_SHUTDOWN_SECONDS,
    OLDER_NODE_ENV,
)
from .databases import seed_databases
from .docker_host import model_docker_host
from .fleet import (
    join_fleet_node,
    mount_fleet_test_seam,
    pretend_older_node,
    run_internal_seal,
    seal_sandbox_before_serving,
)
from .host_profile import PROFILES, use
from .host_reads import model_host_reads
from .machine import Unit
from .recipes_and_export import seed_catalog_apps, seed_exportable_app, seed_fleet_node_app
from .runner import make_runner
from .sandbox import (
    EMPTY_PUBLIC_ADDRESS,
    Sandbox,
    forbid_real_processes,
    make_sandbox_filesystem,
    prepare_environment,
    redirect_system_paths,
    write_config,
)
from .seed_machine import seed_empty_machine, seed_machine
from .seeding import seed_misplaced_backups
from .server_host import model_server_host, seed_server_analysis
from .serving import (
    bind,
    enable_totp,
    use_console_build,
    use_fixed_hostname,
    use_fixed_machine_stats,
)
from .settings_pages import (
    model_telegram_bot_api,
    pin_settings_update_check,
    seed_settings_api_tokens,
    warm_api_schema,
    warm_recipe_library,
)


def serve(args: argparse.Namespace, sandbox: Sandbox) -> None:
    """
    Seed the machine and serve the panel until interrupted.

    Args:
        args: The parsed options.
        sandbox: The sandbox, already prepared.
    """
    import uvicorn

    from noust.core.fs import set_fs
    from noust.core.runner import set_runner
    from noust.web.auth import SecurityConfig
    from noust.web.server import create_app, get_token_manager

    redirect_system_paths(sandbox)
    video = args.showcase and args.profile == "video"
    operator: Any = None
    if args.showcase:
        from tests.support.showcase import AGENCY_DOMAIN

        ssl_email = f"ops@{AGENCY_DOMAIN}"
    else:
        ssl_email = "ops@example.com"
    write_config(
        sandbox,
        central_role=args.central_role,
        ssl_email=ssl_email,
        public_address=EMPTY_PUBLIC_ADDRESS if args.empty else None,
    )
    forbid_real_processes()
    set_fs(make_sandbox_filesystem(sandbox))
    if args.showcase:
        use_showcase_diagnose_paths(sandbox)

    # The runner has to exist before seeding (cron enables its timer through
    # it) and needs the seeded units to answer for, so it is built over empty
    # maps that seeding fills in place.
    units: dict[str, Unit] = {}
    ports: dict[str, int] = {}
    domains: dict[str, str] = {}
    certs: list[str] = []
    profile = PROFILES[args.host_profile]
    use(profile)
    programs = profile.programs
    if args.docker:
        # Docker is deliberately absent from the default machine; this one has it.
        from .machine import INSTALLED_PROGRAMS
        from .server_host import SERVER_PROGRAMS

        programs = (
            *(programs if programs is not None else (*INSTALLED_PROGRAMS, *SERVER_PROGRAMS)),
            "docker",
        )
    set_runner(make_runner(units, ports, domains, certs, sandbox.systemd_dir, programs=programs))
    # Managers report progress on stdout, which belongs to the one JSON line
    # the caller parses.
    with contextlib.redirect_stdout(sys.stderr):
        if args.central_role == "hub":
            # A hub deploys nothing itself: seed_machine's seed is a server's - an
            # application, cron jobs, backups - and creating any of it would hit the
            # same noust.central.require_server_role guard a real hub answers with
            # (RoleError). Nothing the fleet's E2E coverage needs of a hub reads it;
            # the store still has to exist, which is otherwise seed_machine's doing.
            from noust.core.store import get_store

            get_store(sandbox.store_file)
            seeded_units: dict[str, Unit] = {}
            seeded_ports: dict[str, int] = {}
            seeded_domains: dict[str, str] = {}
            seeded_certs: list[str] = []
        elif args.empty:
            seeded_units, seeded_ports, seeded_domains, seeded_certs = seed_empty_machine(sandbox)
        else:
            seeded_units, seeded_ports, seeded_domains, seeded_certs = seed_machine(
                sandbox,
                expired_certificate=args.expired_certificate,
                showcase=args.showcase,
                hostname=args.hostname,
                profile=args.profile,
            )
            if args.misplaced_backups:
                seed_misplaced_backups(sandbox)
            if args.showcase and not video:
                seed_showcase_backup_destination()
            if video and (args.hostname or "fra-1") == "fra-1":
                # The central is where the agency's people sign in.
                from noust.core.store import get_store
                from tests.support.showcase_video import seed_video_people

                operator = seed_video_people(get_store(sandbox.store_file))
    model_telegram_bot_api()
    if video:
        from .video import (
            model_agency_code_host,
            model_agency_databases,
            show_server_paths,
            stream_npm_output,
        )

        model_agency_databases(args.hostname or "fra-1")
        model_agency_code_host(sandbox, units)
        stream_npm_output(sandbox)
        show_server_paths(sandbox)
    elif os.environ.get(CODE_HOST_ENV):
        model_code_host(sandbox)
    units.update(seeded_units)
    ports.update(seeded_ports)
    domains.update(seeded_domains)
    certs.extend(seeded_certs)
    # The VPS the Server area manages (/api/server): see model_server_host.
    host = model_server_host(sandbox, args.hostname or socket.gethostname(), units, profile)
    if args.docker and not args.empty:
        # The engine the Docker zone looks at, over the applications that were just seeded.
        from noust.core.store import get_store

        model_docker_host(
            {
                app.domain: app.app_path
                for app in get_store(sandbox.store_file).list_apps()
                if app.app_path
            }
        )
    if video:
        from .video import settle_server_host

        settle_server_host(host.root)
    with contextlib.redirect_stdout(sys.stderr):
        seed_server_analysis()
    if args.central_role != "hub" and not args.showcase and not args.empty:
        with contextlib.redirect_stdout(sys.stderr):
            seed_databases(sandbox)
    if args.central_role != "hub" and not args.showcase and not args.empty:
        # Deploys tests.support.panel_factory's own "lanzamiento.example.org": nothing the
        # documentation screenshots need, and exactly the example.org the showcase exists
        # to keep out of them.
        with contextlib.redirect_stdout(sys.stderr):
            seed_exportable_app(sandbox)
    if os.environ.get(CATALOG_ENV) and args.central_role != "hub":
        with contextlib.redirect_stdout(sys.stderr):
            seed_catalog_apps(sandbox, units, ports, domains)
    fleet_node_app = os.environ.get(FLEET_NODE_APP_ENV)
    if fleet_node_app:
        with contextlib.redirect_stdout(sys.stderr):
            seed_fleet_node_app(sandbox, units, ports, domains, fleet_node_app)

    # Sealed last, once every seed above has finished writing whatever secrets it
    # needed to (a GitHub app's private key, and so on): sealing before seeding, the
    # way an earlier version of this function did, left seeding itself refused by the
    # very store it was trying to write to. A real central seals only after it holds
    # the secrets it will manage, which this now matches - see seal_sandbox_before_serving.
    if args.seal:
        seal_sandbox_before_serving(sandbox)

    config = SecurityConfig(
        host=args.host,
        port=args.port,
        state_dir=sandbox.state_dir,
        # One browser drives every page of the suite from one address; the
        # production limit would throttle the tests, not an attacker.
        rate_limit_requests=100_000,
    )
    if args.static_dir is not None:
        use_console_build(args.static_dir)
    if args.hostname is not None:
        use_fixed_hostname(args.hostname)
    cpu_held = 12.0
    if args.showcase:
        from tests.support.showcase import NODE_READINGS
        from tests.support.showcase_video import VIDEO_READINGS

        readings = VIDEO_READINGS if video else NODE_READINGS
        cpu_percent, memory_percent, disk_percent, uptime_s = readings.get(
            args.hostname or "", readings["fra-1"]
        )
        cpu_held = cpu_percent
        use_fixed_machine_stats(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            uptime_s=uptime_s,
        )
    # The kernel, the processes and the disks the pages name are the modelled VPS's, never the
    # machine this script runs on.
    model_host_reads(host.root, args.hostname or socket.gethostname(), units, cpu_held, domains)
    app = create_app(config)
    token = get_token_manager().generate_master_token()
    totp_secret, backup_codes = enable_totp(args.backup_codes) if args.totp else (None, [])
    seed_settings_api_tokens()
    pin_settings_update_check(up_to_date=args.shown_version is not None)
    warm_recipe_library()
    warm_api_schema(app)

    sock = bind(args.host, args.port)
    host, port = sock.getsockname()[:2]
    display_host = f"[{host}]" if ":" in host else host
    # Inert unless a central's --fleet-node calls it: see the module comment above
    # mount_fleet_test_seam for why this - and only this - is mounted unconditionally.
    mount_fleet_test_seam(app, console_port=port)
    older_version = os.environ.get(OLDER_NODE_ENV)
    served: Any = pretend_older_node(app, older_version) if older_version else app
    if args.fleet_node:
        display_hosts = None
        if args.showcase:
            from tests.support.showcase import NODE_SSH_HOSTS
            from tests.support.showcase_video import VIDEO_SSH_HOSTS

            display_hosts = VIDEO_SSH_HOSTS if video else NODE_SSH_HOSTS
        join_fleet_node(args.fleet_node, display_hosts=display_hosts)
    server = uvicorn.Server(
        uvicorn.Config(
            served,
            log_level="warning",
            access_log=False,
            server_header=False,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    )

    def announce() -> None:
        """Print the connection details once the server accepts requests."""
        while not server.started and not server.should_exit:
            time.sleep(0.02)
        if server.started:
            line: dict[str, object] = {
                "url": f"http://{display_host}:{port}",
                "token": token,
                "totp_secret": totp_secret,
            }
            if args.backup_codes is not None:
                line["backup_codes"] = backup_codes
            if args.keep:
                line["sandbox"] = str(sandbox.root)
            if operator is not None:
                # How the product video's operator signs in, and the secret Iker's push is
                # signed with: the sandbox's own, like the token above.
                from tests.support.showcase_video import VIDEO_WEBHOOK_SECRET

                line["operator"] = {
                    "username": operator.username,
                    "password": operator.password,
                    "totp_secret": operator.totp_secret,
                }
                line["webhook_secret"] = VIDEO_WEBHOOK_SECRET
            print(json.dumps(line), flush=True)

    threading.Thread(target=announce, name="announce", daemon=True).start()
    # uvicorn shuts down gracefully on SIGTERM and then re-raises it against
    # the previous handler; the default one would kill the process before
    # main() removes the sandbox. Exiting normally instead runs the cleanup.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    server.run(sockets=[sock])


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the console server.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The exit status.
    """
    args = parse_args(argv)
    if args.shown_version is not None:
        # Before anything reads it: every module that shows the version imports it by name.
        import noust

        noust.__version__ = args.shown_version
    if args.internal_seal is not None:
        return run_internal_seal(Path(args.internal_seal))
    sandbox = Sandbox(Path(tempfile.mkdtemp(prefix="noust-console-")).resolve())
    try:
        prepare_environment(sandbox)
        serve(args, sandbox)
    finally:
        if args.keep:
            print(f"console_server: sandbox kept at {sandbox.root}", file=sys.stderr)
        else:
            shutil.rmtree(sandbox.root, ignore_errors=True)
    return 0
