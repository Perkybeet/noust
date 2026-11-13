# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The command line."""

from __future__ import annotations

import argparse
import ipaddress
import re
from collections.abc import Sequence
from pathlib import Path

from .assistant_seed import MODES as ASSISTANT_MODES
from .host_profile import PROFILES

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """
    Parse the command line.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The parsed options.
    """
    parser = argparse.ArgumentParser(
        description="Serve the real Noust panel over a seeded, sandboxed machine."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="loopback address to bind (default 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=0, help="port to bind; 0 picks a free one (default)"
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the sandbox directory on exit and print it"
    )
    parser.add_argument(
        "--totp",
        action="store_true",
        help="enable two-factor sign-in and print its secret as totp_secret",
    )
    parser.add_argument(
        "--backup-codes",
        type=int,
        default=None,
        metavar="N",
        help=(
            "with --totp, enrol N backup codes instead of the usual count and print them as "
            "backup_codes: each is good for one sign-in or confirmation, where a TOTP code is "
            "good for one per 30-second step, which a test suite signing in every few seconds "
            "cannot wait for"
        ),
    )
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=None,
        help=(
            "serve the console build from this directory instead of noust/web/static, "
            "so several builds can be exercised side by side (development only)"
        ),
    )
    parser.add_argument(
        "--misplaced-backups",
        action="store_true",
        help=(
            "also seed backups outside the backup directory, where an empty backup.directory "
            "once sent them, so the Backups page names them and the command that imports them"
        ),
    )
    parser.add_argument(
        "--expired-certificate",
        action="store_true",
        help=(
            "seed the first certificate as expired three days ago, so the health report "
            "turns critical with a reason naming it"
        ),
    )
    parser.add_argument(
        "--showcase",
        action="store_true",
        help=(
            "seed tests.support.showcase's invented agency instead of tests.support.panel_factory's "
            "example.com machine: no real project, domain or person, and no rename-era "
            "example.com sites, for docs/assets/console's screenshots (panel/e2e/docs.spec.ts)"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=("docs", "video"),
        default="docs",
        help=(
            "with --showcase, which of its datasets: docs (the documentation's screenshots, "
            "the default) or video (the product video: four servers, releases, alerts, "
            "nightly backups, the agency's people, and its repositories answering git)"
        ),
    )
    parser.add_argument(
        "--shown-version",
        default=None,
        metavar="VERSION",
        help=(
            "report this Noust version instead of the installed one, with no update to offer "
            "(screenshots and the product video only)"
        ),
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help=(
            "seed a server just installed, with nothing deployed, and a public address "
            "declared (dns.public_addresses), so the first-run walk (the access token, the "
            "first administrator, the demo and the temporary address) has something to start "
            "from (panel/e2e/first-run.spec.ts)"
        ),
    )
    parser.add_argument(
        "--hostname",
        default=None,
        help=(
            "report this hostname from the machine strip instead of this machine's own "
            "(development and screenshots only)"
        ),
    )
    parser.add_argument(
        "--central-role",
        choices=("server", "hub"),
        default=None,
        help="set central.role in the written config (the fleet's E2E suite only)",
    )
    parser.add_argument(
        "--host-profile",
        choices=sorted(PROFILES),
        default="server",
        help=(
            "which machine to model: 'server' (the Ubuntu VPS the sandbox has always been, "
            "administered by root) or 'container' (the Noust central's container on a NAS, as "
            "packaging/container/Dockerfile ships it: no systemd, no journal, no nginx, no Docker, "
            "no sshd, not root; the programs it lacks answer 127). Meant for --central-role hub, "
            "the owner's hub (panel/e2e hub specs)"
        ),
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help=(
            "model a machine that has Docker, with the stacks, images and volumes the console's Docker zone "
            "draws (the default machine has none, so that the Software page offers to install it)"
        ),
    )
    parser.add_argument(
        "--seal",
        action="store_true",
        help=(
            "seal this central's secrets before it serves, with a passphrase read from "
            "standard input (never argv); this process never unlocks them itself, so it "
            "starts genuinely locked, as a central restarted after 'noust central seal' "
            "does (the fleet's E2E suite only)"
        ),
    )
    parser.add_argument(
        "--fleet-node",
        action="append",
        default=[],
        metavar="NAME=HOST:PORT",
        help=(
            "register, on this central, a node console already serving at HOST:PORT "
            "under NAME, without ssh - see join_fleet_node() (the fleet's E2E suite only)"
        ),
    )
    parser.add_argument(
        "--assistant",
        choices=ASSISTANT_MODES,
        default="off",
        help=(
            "the built-in assistant of this console, over a model that answers what a test dictates "
            "(tests/support/scripted_model.py; no provider is ever asked): 'off' is a fresh server; "
            "'ready' starts the model and leaves the assistant unconfigured; 'on' turns it on over a "
            "model that calls tools; 'no-tools' over one that only talks. The address to script the model "
            "with is printed as assistant_model"
        ),
    )
    parser.add_argument(
        "--internal-seal",
        metavar="SANDBOX_ROOT",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.profile != "docs" and not args.showcase:
        parser.error("--profile chooses one of --showcase's datasets; add --showcase")
    if args.shown_version is not None and not re.fullmatch(r"\d+\.\d+\.\d+", args.shown_version):
        parser.error(f"--shown-version takes a version like 4.1.0, not {args.shown_version}")
    try:
        loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = args.host == "localhost"
    if not loopback:
        # The token printed on stdout opens a panel with root semantics over the
        # sandbox; it is never meant to be reachable from another machine.
        parser.error(f"--host must be a loopback address, not {args.host}")
    return args
