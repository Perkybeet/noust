#!/usr/bin/env python3
"""
Real two-container fleet harness for Noust: a central and a node.

Builds the wheel from the working tree, reuses tests/integration/run.py's
systemd-under-Docker image, layers OpenSSH onto it, and starts two privileged
containers - a node and a central - on a user-defined Docker network. The
node is enrolled with the real CLI (``noust fleet authorize`` on the node,
``noust node add`` on the central), so every scenario below drives the real
SSH tunnel, the real restricted key and the real HTTP proxy: nothing here
mocks the fleet.

This file is intentionally excluded from pytest's default collection (it does
not match ``test_*.py``) and is not run by ``run.py``: it drives two
containers and a Docker network of its own, prefixed ``noust-fleet-``.

Usage:
    .venv/bin/python tests/integration/fleet_run.py
    .venv/bin/python tests/integration/fleet_run.py --keep
    .venv/bin/python tests/integration/fleet_run.py --scenario enrollment
    .venv/bin/python tests/integration/fleet_run.py --scenario central_image

Scenarios (see the module docstring of each ``@fleet_scenario`` function):
    1. enrollment_end_to_end
    2. central_key_cannot_run_anything
    3. deploy_through_the_central
    4. logs_stream_through_the_proxy
    5. sudo_enforced_on_the_central
    6. fleet_token_rules_on_the_node
    7. host_key_change_detected
    8. central_image (packaging/container/Dockerfile; builds and runs its own
       second central, ``noust-fleet-central2``, against the same node)

Added in 3.1 (run between 5 and 6, while the enrollment is intact):
    - tunnel_account_contained: the node enrolls as ``noust-tunnel`` under
      ``PermitRootLogin no`` (a key rotation), the account runs nothing, and
      ``-L`` to another port and ``-R /etc/nologin:...`` (the 3.0 hole) fail
    - ceiling_enforced_by_the_node: a node held to ``read`` refuses a write
      even when the central claims admin
    - authorize_adopts_a_background_console: ``noust fleet authorize`` moves a
      ``noust web start -d`` console to the service, keeps its token, and
      ``noust node migrate-tunnel`` finishes on the central

Secrets (the master token, the two-factor secret, TOTP codes, the fleet
token, session and CSRF tokens, the sealed-secrets passphrase) never appear in
any argv, on this host or inside a container: they travel over ``docker exec
-i ... <stdin>`` or inside a JSON payload piped to a small Python helper that
builds a curl ``-K`` config file, never as a ``-H``/``-d`` argument. Evidence
blocks that would otherwise carry one are redacted before they are recorded.

Requires Docker (tested against Docker 29) with a working systemd-in-Docker
setup: cgroup v2, ``--privileged``, ``--cgroupns=host`` and the host cgroup
filesystem bind-mounted - exactly what run.py's own containers need, since
this harness's images and containers are built the same way.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import importlib.util
import json
import re
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]
PACKAGING_CONTAINER_DIR = REPO_ROOT / "packaging" / "container"

if TYPE_CHECKING:
    # mypy resolves this against the real tests/integration/run.py on disk
    # (its own analysis, unrelated to the dynamic load below), which is what
    # gives Scenario and HarnessError real attributes under type checking
    # instead of the "Scenario?" pseudo-type a plain `run.Scenario` from a
    # runtime-only `Any` would leave them as.
    from run import HarnessError as HarnessError
    from run import Scenario as Scenario


def _load_run() -> Any:
    """
    Load tests/integration/run.py as a module, without touching sys.path.

    Registering it in ``sys.modules`` before ``exec_module`` matters: some of
    its classes are plain ``@dataclass``, and the dataclass machinery looks
    the defining module up in ``sys.modules`` while it runs.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "noust_integration_run", INTEGRATION_DIR / "run.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {INTEGRATION_DIR / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _load_run()

# Helpers reused verbatim from run.py, per the task brief. run.py is not
# edited: anything it does not offer (a Docker network, a second image layer,
# a container on that network, secrets over stdin, HTTP through curl) is
# written here instead.
sh = run.sh
docker_exec = run.docker_exec
build_wheel = run.build_wheel
build_image = run.build_image
wait_for_systemd = run.wait_for_systemd
install_fixtures = run.install_fixtures
install_noust = run.install_noust
remove_container = run.remove_container
if not TYPE_CHECKING:
    Scenario = run.Scenario
    HarnessError = run.HarnessError
BASE_IMAGE_TAG = run.IMAGE_TAG

# ---------------------------------------------------------------------------
# Names: every container, network and image this harness creates is prefixed
# noust-fleet- (containers) so they are unmistakable in `docker ps`, and the
# image tags say what they are for.
# ---------------------------------------------------------------------------

NETWORK_NAME = "noust-fleet-net"
CENTRAL_NAME = "noust-fleet-central"
NODE_NAME = "noust-fleet-node"
CENTRAL2_NAME = "noust-fleet-central2"
CENTRAL2_VOLUME = "noust-fleet-central2-data"

#: The systemd+sshd image both the node and the harness's own (non-packaged)
#: central run: the same base image run.py builds, with OpenSSH layered on.
FLEET_IMAGE_TAG = "noust-fleet-image:latest"

#: The packaged central image scenario 8 builds from packaging/container/Dockerfile.
CENTRAL_IMAGE_TAG = "noust-fleet-central-packaged:test"

CENTRAL_PORT = 8443
NODE_NAME_ID = "node1"
STATIC_DOMAIN = "fleet-static.test"

#: Where the git daemon install_fixtures() starts serves the static site
#: fixture from - a git source, not a local path, so a fleet token (never
#: exempt from the local-source privilege check; see apps.py
#: _require_local_source_privilege) may deploy it through the proxy.
STATIC_SITE_GIT_URL = "git://127.0.0.1/static-site"

#: The store of a freshly installed Noust (matches run.py's NOUST_DB).
NOUST_DB = "/var/lib/noust/noust.db"

#: The account a 3.1 node gives centrals, and the root-owned file of its keys.
TUNNEL_USER = "noust-tunnel"
TUNNEL_KEYS_FILE = "/etc/ssh/noust/noust-tunnel.keys"

#: What the tunnel account's shell says when sshd hands it any command -
#: ForceCommand included, since sshd runs commands through the account's
#: shell. It is the proof that nothing else ran: no uid from `id`, nothing.
NOLOGIN_MESSAGES = ("", "This account is currently not available.")

DEPLOY_TIMEOUT = 180


class ScenarioSkipped(Exception):
    """Raised by a scenario that cannot run here, with the reason."""


# ---------------------------------------------------------------------------
# TOTP, RFC 6238, stdlib only - mirrors src/noust/core/totp.py exactly (SHA1,
# 30s period, 6 digits), but this harness never imports noust: it drives the
# installed package from the outside, like an operator's terminal would.
# ---------------------------------------------------------------------------

TOTP_PERIOD = 30
TOTP_DIGITS = 6

#: The time step a code was last minted for, so two codes minted moments
#: apart (a login, then an elevate) are never the same one: Noust's TOTP
#: verifier remembers a used step and refuses it a second time.
_last_totp_step: int | None = None


def _hotp(secret_b32: str, counter: int, digits: int = TOTP_DIGITS) -> str:
    compact = secret_b32.strip().replace(" ", "")
    padded = compact + "=" * (-len(compact) % 8)
    key = base64.b32decode(padded, casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


def totp_code(secret_b32: str) -> str:
    """Mint a fresh TOTP code for `secret_b32`, never repeating a time step."""
    global _last_totp_step
    while True:
        now = time.time()
        step = int(now // TOTP_PERIOD)
        if step != _last_totp_step:
            _last_totp_step = step
            return _hotp(secret_b32, step)
        time.sleep(1)


# ---------------------------------------------------------------------------
# Docker: network, the sshd-layered image, containers on that network.
# ---------------------------------------------------------------------------


def remove_network(name: str) -> None:
    """Best-effort network teardown; never raises."""
    subprocess.run(["docker", "network", "rm", name], capture_output=True, text=True, timeout=30)


def create_network(name: str) -> None:
    """Create the user-defined bridge network the central and the node share."""
    remove_network(name)
    sh(["docker", "network", "create", name], timeout=30)


#: Layers OpenSSH client and server onto run.py's systemd image. No COPY, so
#: it builds from a Dockerfile piped on stdin: nothing this harness needs
#: lives outside this file plus the base image.
FLEET_DOCKERFILE = """
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends \\
        openssh-server openssh-client \\
    && rm -rf /var/lib/apt/lists/*
"""


def build_fleet_image(base_image: str, tag: str) -> None:
    """Build the sshd-layered image both the node and this harness's central run."""
    print(f"[setup] building {tag} from {base_image}")
    proc = subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            f"BASE_IMAGE={base_image}",
            "-t",
            tag,
            "-f",
            "-",
            str(INTEGRATION_DIR),
        ],
        input=FLEET_DOCKERFILE,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise HarnessError(f"building {tag} failed:\n{proc.stdout}\n{proc.stderr}")


def start_fleet_container(name: str, image: str, network: str) -> None:
    """Start a privileged, systemd-as-PID-1 container on `network`."""
    print(f"[setup] starting container {name} on {network}")
    sh(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--privileged",
            "--cgroupns=host",
            "-v",
            "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/run/lock",
            image,
        ],
        timeout=60,
    )


def docker_exec_stdin(
    container: str, script: str, stdin_text: str, *, timeout: int = 60, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a shell script inside a container, feeding it `stdin_text` on stdin.

    Secrets (a join code, a token, a passphrase) go this way, never in argv.
    """
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "bash", "-lc", script],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise HarnessError(
            f"command failed in container (exit {proc.returncode}): {script}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def run_stdin(
    sc: Scenario,
    script: str,
    stdin_text: str,
    *,
    timeout: int = 60,
    check: bool = True,
    label: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Like `Scenario.run`, but feeds `stdin_text` on stdin instead of taking none.

    The printed evidence never carries `stdin_text`, only the script (which
    references it as a shell variable or `-`, never the value itself).
    """
    proc = docker_exec_stdin(sc.container, script, stdin_text, timeout=timeout, check=False)
    block = [f"$ {label or script}  (secret on stdin)"]
    if proc.stdout:
        block.append(proc.stdout.rstrip("\n"))
    if proc.stderr.strip():
        block.append("--- stderr ---")
        block.append(proc.stderr.rstrip("\n"))
    block.append(f"(exit={proc.returncode})")
    sc.evidence.append("\n".join(block))
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed (exit {proc.returncode}): {label or script}")
    return proc


def redact(sc: Scenario, label: str, note: str) -> None:
    """Record that something ran, without any part of its secret output."""
    sc.evidence.append(f"$ {label}\n<redacted: {note}>")


def as_json(proc: subprocess.CompletedProcess[str], *, what: str) -> Any:
    """Parse a command's stdout as JSON, or fail with a readable message."""
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(
            f"{what}: not JSON: {proc.stdout!r} (stderr: {proc.stderr!r})"
        ) from exc


# ---------------------------------------------------------------------------
# HTTP to the central's API, through Python's own urllib (never a shell, and
# never curl): the packaged central image (packaging/container/Dockerfile) is
# python:3.12-slim-bookworm with no curl binary, and every container this
# harness drives has the python3 that runs Noust itself, so urllib is the one
# HTTP client guaranteed present everywhere the helper runs. The token and
# request body still never appear in any process's argv: they travel to this
# script over stdin, as JSON.
# ---------------------------------------------------------------------------

#: Reads one JSON object from stdin describing a request and performs it with
#: urllib, entirely in-process: no subprocess, so nothing about the request
#: is ever visible in `ps` to another local user.
_HTTP_HELPER = r"""
import json, socket, ssl, sys, time, urllib.error, urllib.request

req = json.loads(sys.stdin.read())
headers = dict(req.get("headers") or {})
if req.get("token"):
    headers["Authorization"] = "Bearer " + req["token"]

data = None
if req.get("json_body") is not None:
    data = json.dumps(req["json_body"]).encode()
    headers.setdefault("Content-Type", "application/json")

timeout = int(req.get("max_time", 30))
# An absolute wall-clock deadline, not just urlopen's timeout: that timeout
# bounds each individual socket read, not the total time this script runs,
# so a live SSE stream that keeps sending a frame (a heartbeat, a comment)
# faster than that per-read window would otherwise let read_bounded() below
# loop forever - which is exactly what escaped as an uncaught
# subprocess.TimeoutExpired on the host and aborted every scenario after it.
deadline = time.monotonic() + timeout
context = ssl._create_unverified_context() if req.get("insecure") else ssl.create_default_context()
request = urllib.request.Request(req["url"], data=data, headers=headers, method=req["method"])


def read_bounded(response):
    # A streamed (SSE) response never closes on its own: read only while
    # there is time left on the deadline above, and treat running out the
    # clock as the normal way an open stream ends here, exactly like a
    # per-read timeout - keeping every chunk read before that rather than
    # losing it.
    chunks = []
    try:
        while time.monotonic() < deadline:
            chunk = response.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except (TimeoutError, socket.timeout):
        pass
    return b"".join(chunks)


try:
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        status = response.status
        body = read_bounded(response)
except urllib.error.HTTPError as exc:
    status = exc.code
    body = read_bounded(exc)
except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
    status = 0
    body = str(exc).encode()

sys.stdout.write("STATUS:%d\n" % status)
sys.stdout.write(body.decode("utf-8", errors="replace"))
"""


def api_call(
    container: str,
    method: str,
    url: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    max_time: int = 30,
    insecure: bool = True,
) -> tuple[int, Any, str]:
    """Make one HTTPS call from inside `container`, with the token and body kept off argv.

    Returns:
        `(status, parsed_json_or_none, raw_body_text)`. `status` is 0 when
        the request itself could not be made at all (never a real HTTP
        status): a connection failure inside the container, not a refusal.
    """
    payload = json.dumps(
        {
            "method": method,
            "url": url,
            "token": token,
            "headers": headers or {},
            "json_body": json_body,
            "max_time": max_time,
            "insecure": insecure,
        }
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "python3", "-c", _HTTP_HELPER],
        input=payload,
        capture_output=True,
        text=True,
        timeout=max_time + 25,
    )
    first_line, _, rest = proc.stdout.partition("\n")
    if not first_line.startswith("STATUS:"):
        raise HarnessError(
            f"api_call {method} {url}: the HTTP helper produced no status "
            f"(exit {proc.returncode}): stdout={proc.stdout[:1000]!r} stderr={proc.stderr[:1000]!r}"
        )
    status = int(first_line[len("STATUS:") :].strip() or "0")
    try:
        body: Any = json.loads(rest) if rest.strip() else None
    except ValueError:
        body = None
    return status, body, rest


def log_api(sc: Scenario, method: str, url: str, status: int, note: str) -> None:
    """Record one API call's outcome in a scenario's evidence, without its secrets."""
    sc.evidence.append(f"$ {method} {url}\n(status={status}) {note}")


def csrf_headers(csrf_token: str) -> dict[str, str]:
    """The header a session-authenticated mutation must carry (X-WASM-CSRF)."""
    return {"X-WASM-CSRF": csrf_token}


# ---------------------------------------------------------------------------
# Shared context every scenario receives.
# ---------------------------------------------------------------------------


@dataclass
class FleetEnv:
    """Collaborator every scenario runs against: the two running containers and what setup learnt."""

    central: Scenario
    node: Scenario
    wheel: Path
    master_token: str
    totp_secret: str
    node_name: str = NODE_NAME_ID
    central_label: str = ""
    node_console_port: int = 0
    node_host_key: str = ""
    node_ssh_target: str = field(default_factory=lambda: f"{TUNNEL_USER}@{NODE_NAME}")
    static_domain: str = STATIC_DOMAIN
    central_secrets_root: str = ""

    def secret_path(self, *leaf: str) -> str:
        """A path under the central's own secrets directory, wherever the store put it.

        NoustStore falls back to a per-user data directory
        (~/.local/share/noust) whenever /var/lib/noust does not already exist
        and is writable (core/store.py's own documented precedence) - which is
        exactly the case right after a bare `pip install`, with nothing else
        having created /var/lib/noust first. Secrets live beside the store, so
        the path is resolved once (`secrets_root`) rather than assumed.
        """
        if not self.central_secrets_root:
            raise HarnessError("central_secrets_root was never resolved; call enroll_node() first")
        return "/".join([self.central_secrets_root, *leaf])


FleetScenarioFn = Callable[[FleetEnv], None]
FLEET_SCENARIOS: list[tuple[str, FleetScenarioFn]] = []


def fleet_scenario(name: str) -> Callable[[FleetScenarioFn], FleetScenarioFn]:
    def decorator(fn: FleetScenarioFn) -> FleetScenarioFn:
        FLEET_SCENARIOS.append((name, fn))
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Setup: the node's sshd, the central's console, two-factor sign-in.
# ---------------------------------------------------------------------------


def provision_ssh(name: str) -> None:
    """Give a container a fresh host key and a running sshd."""
    # The image carries Docker for run.py's Compose scenarios, but a fleet container has no
    # /var/lib/docker volume: docker.service fails there and systemd reports "degraded",
    # which the fleet's own health checks would then read as a sick node.
    docker_exec(
        name,
        "systemctl mask --now docker.service docker.socket containerd.service; "
        "systemctl reset-failed",
        timeout=60,
        check=False,
    )
    docker_exec(name, "rm -f /etc/ssh/ssh_host_*_key*", timeout=15)
    docker_exec(name, "ssh-keygen -A", timeout=30)
    docker_exec(name, "systemctl enable --now ssh", timeout=30)
    status = docker_exec(name, "systemctl is-active ssh", timeout=15, check=False)
    if status.stdout.strip() != "active":
        raise HarnessError(f"sshd did not start in {name}: {status.stdout!r} {status.stderr!r}")


def provision_central(name: str, *, port: int = CENTRAL_PORT) -> tuple[str, str]:
    """Enable the console over TLS and turn on two-factor sign-in.

    A central refuses to register its first node without two-factor sign-in
    (fleet/policy.py); this is done once, in setup, so every scenario finds
    it already on.

    Returns:
        `(master_token, totp_secret)`. Both are secrets: the caller must
        never print them, only use them to build requests.
    """
    enable = docker_exec(
        name,
        f"noust web enable --host 0.0.0.0 --port {port} --self-signed "
        "--allow-ip 0.0.0.0/0 --allow-ip ::/0",
        timeout=60,
    )
    token_match = re.search(r"Access Token:\s*(\S+)", enable.stdout)
    if token_match is None:
        raise HarnessError(
            f"no access token in 'noust web enable' output:\n{enable.stdout}\n{enable.stderr}"
        )
    master_token = token_match.group(1)

    enroll = docker_exec(name, "noust 2fa enroll", timeout=30)
    secret_match = re.search(r"Secret:\s*([A-Z2-7]+)", enroll.stdout)
    if secret_match is None:
        raise HarnessError(
            f"no secret in 'noust 2fa enroll' output:\n{enroll.stdout}\n{enroll.stderr}"
        )
    totp_secret = secret_match.group(1)

    code = totp_code(totp_secret)
    confirm = docker_exec(name, f"noust 2fa confirm {code}", timeout=30, check=False)
    if confirm.returncode != 0:
        raise HarnessError(f"'noust 2fa confirm' failed:\n{confirm.stdout}\n{confirm.stderr}")
    return master_token, totp_secret


def secrets_root(container: str) -> str:
    """Find the central's secrets directory, wherever NoustStore put the store beside it.

    A store created by a bare `pip install` (nothing else has created
    /var/lib/noust yet, and NoustStore never creates it on its own - see
    core/store.py's _resolve_db_path) lands under ~/.local/share/noust
    instead; either is legitimate, so both are checked instead of assuming one.

    Checking mere existence of the two candidate directories is not enough:
    an earlier `noust` command (the console, say) can create an empty
    /var/lib/noust/secrets of its own before the fleet ever writes anything,
    which would win a plain `ls -d ... | head -1` race even though the fleet
    material actually landed under the fallback. Requiring the `fleet`
    namespace to already exist under a candidate is what tells the two apart.
    """
    result = docker_exec(
        container,
        "for d in /var/lib/noust/secrets /root/.local/share/noust/secrets; do "
        'test -d "$d/fleet" && echo "$d" && break; done',
        timeout=10,
    )
    # str(...): result.stdout is a real str at runtime, but the dynamic
    # importlib load of run.py (see _load_run()) leaves docker_exec's return
    # type as Any to mypy, which would otherwise flag this as returning Any
    # from a function declared -> str.
    root = str(result.stdout).strip()
    if not root:
        raise HarnessError(
            f"could not find a secrets directory with fleet/ material in {container}"
        )
    return root


# ---------------------------------------------------------------------------
# Enrollment, factored out so scenario 7 can redo it after scenario 6 breaks it.
# ---------------------------------------------------------------------------


def enroll_node(env: FleetEnv) -> None:
    """Run the real enrollment: `noust node key` -> `fleet authorize` on the node -> `node add`.

    Rehearses `fleet authorize` under `--dry-run` first and checks it left
    authorized_keys and the token list untouched, before doing it for real.

    Sets `env.central_label`, `env.node_console_port` and `env.node_host_key`,
    and leaves `env.node_name` registered and reachable on the central.
    """
    central, node = env.central, env.node

    key_result = central.run(
        f"noust node key {env.node_name} --json",
        label=f"noust node key {env.node_name} --json",
    )
    key_info = as_json(key_result, what="noust node key --json")
    central.check(
        str(key_info.get("public_key", "")).startswith("ssh-ed25519"),
        f"unexpected key payload: {key_info!r}",
    )
    authorize_command = str(key_info["authorize_command"])
    name_match = re.search(r"--name (\S+)", authorize_command)
    central.check(name_match is not None, f"authorize_command has no --name: {authorize_command!r}")
    env.central_label = name_match.group(1)  # type: ignore[union-attr]

    # --yes: scenario 7 redoes this enrollment after scenario 6 revoked the
    # previous token but left it on the node's own token list (deauthorize
    # revokes it there without erasing the record), so authorize would
    # otherwise stop at an interactive "already holds a token, replace it?"
    # prompt this non-interactive `docker exec` can never answer. Harmless on
    # a first enrollment, where nothing is there to replace.
    authorize_command_run = (
        authorize_command if "--yes" in authorize_command else f"{authorize_command} --yes"
    )

    # A rehearsal changes nothing: same authorized_keys, same token list
    # (commit 336ca9b - a `--dry-run fleet authorize` used to print a join
    # code carrying a live token even though nothing was saved to redeem it).
    keys_before = docker_exec(
        node.container,
        f"cat /root/.ssh/authorized_keys {TUNNEL_KEYS_FILE} 2>/dev/null; true",
        timeout=15,
    ).stdout
    tokens_before = docker_exec(node.container, "noust token list --json", timeout=15).stdout
    dry_run_command = authorize_command_run.replace("noust ", "noust --dry-run ", 1)
    dry_authorize = docker_exec(node.container, dry_run_command, timeout=60)
    redact(node, dry_run_command, "a rehearsal join code (no live token behind it)")
    node.check(
        dry_authorize.returncode == 0,
        f"'{dry_run_command}' failed: {dry_authorize.stdout}\n{dry_authorize.stderr}",
    )
    keys_after = docker_exec(
        node.container,
        f"cat /root/.ssh/authorized_keys {TUNNEL_KEYS_FILE} 2>/dev/null; true",
        timeout=15,
    ).stdout
    tokens_after = docker_exec(node.container, "noust token list --json", timeout=15).stdout
    node.check(keys_after == keys_before, "a --dry-run fleet authorize changed authorized_keys")
    node.check(tokens_after == tokens_before, "a --dry-run fleet authorize changed the token list")

    # The authorize command's own output holds the join code - a one-time
    # fleet token - so it is run quietly and only a redacted line is kept.
    authorize_result = docker_exec(node.container, authorize_command_run, timeout=60)
    redact(node, authorize_command, "a join code carrying a one-time fleet token")
    join_match = re.search(r"(noust-join:v1:\S+)", authorize_result.stdout)
    if join_match is None:
        raise HarnessError(
            f"'{authorize_command}' printed no join code:\n{authorize_result.stdout}\n{authorize_result.stderr}"
        )
    join_code = join_match.group(1)

    add_script = f"noust node add {env.node_name} --ssh {env.node_ssh_target} --join-code - --json"
    add_result = run_stdin(central, add_script, join_code + "\n", timeout=60, label=add_script)
    record = as_json(add_result, what="noust node add --json")
    central.check(
        record.get("status") == "reachable",
        f"the node was not reachable right after 'noust node add': {record!r}",
    )
    env.node_console_port = int(record["console_port"])
    env.node_host_key = str(record["host_key"])
    if not env.central_secrets_root:
        env.central_secrets_root = secrets_root(central.container)


# ---------------------------------------------------------------------------
# Scenario 1: enrollment end to end.
# ---------------------------------------------------------------------------


@fleet_scenario("enrollment_end_to_end")
def scenario_enrollment(env: FleetEnv) -> None:
    """The central generates a key, the node authorizes it, the central proves the node is healthy.

    Follows exactly the sequence docs/CENTRAL.md describes: `noust node key`
    on the central, the printed command on the node (which itself installs
    the restricted key, keeps the console on loopback and issues a token),
    the join code pasted back with `noust node add`, then `noust node test`
    and `noust fleet status` as the operator's own confirmation.
    """
    central = env.central
    enroll_node(env)

    test_result = central.run(
        f"noust node test {env.node_name} --json", label=f"noust node test {env.node_name} --json"
    )
    test_info = as_json(test_result, what="noust node test --json")
    central.check(test_info.get("reachable") is True, f"noust node test failed: {test_info!r}")

    status_result = central.run("noust fleet status --json", label="noust fleet status --json")
    status_info = as_json(status_result, what="noust fleet status --json")
    nodes = status_info.get("nodes", [])
    match = next((n for n in nodes if n.get("name") == env.node_name), None)
    central.check(
        match is not None and match.get("status") == "reachable" and match.get("reachable") is True,
        f"'noust fleet status' does not show {env.node_name} healthy: {status_info!r}",
    )

    show_result = central.run(
        f"noust node show {env.node_name} --json", label=f"noust node show {env.node_name} --json"
    )
    show_info = as_json(show_result, what="noust node show --json")
    central.check(
        show_info.get("allow_shell") is not True,
        f"a fleet node must never allow a shell: {show_info!r}",
    )


# ---------------------------------------------------------------------------
# Scenario 2: the central's key cannot run anything on the node.
# ---------------------------------------------------------------------------


@fleet_scenario("central_key_cannot_run_anything")
def scenario_key_restrictions(env: FleetEnv) -> None:
    """The central's own private key, used directly, can only forward the console port.

    Reads the private key file this central generated for the node (it never
    leaves the central; this only proves what the *key* can do, independent
    of the tunnel manager, which is scenario 1's job) and drives ssh with it
    by hand: a command attempt runs nothing, `-R` and a `-L` to any port but
    the console are refused, and there is no interactive shell.
    """
    central = env.central
    key_path = env.secret_path("fleet", "nodes", env.node_name, "id_ed25519")
    console_port = env.node_console_port
    ssh_opts = (
        f"-i {key_path} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        "-o BatchMode=yes -o ConnectTimeout=5"
    )
    # user@host: the key is the tunnel account's (3.1), not root's.
    target = env.node_ssh_target

    command_attempt = central.run(
        f"ssh {ssh_opts} {target} id",
        timeout=20,
        check=False,
        label="ssh <central's restricted key> <node> id",
    )
    central.check(
        command_attempt.returncode != 0, "the restricted key was allowed to run a command"
    )
    central.check(
        command_attempt.stdout.strip() in NOLOGIN_MESSAGES,
        f"the forced command produced output: {command_attempt.stdout!r}",
    )

    shell_attempt = central.run(
        f"ssh -tt {ssh_opts} {target} < /dev/null",
        timeout=20,
        check=False,
        label="ssh -tt <central's restricted key> <node>  (interactive shell)",
    )
    central.check(
        shell_attempt.returncode != 0, "the restricted key was allowed an interactive shell"
    )

    remote_forward = central.run(
        f"ssh -N -o ExitOnForwardFailure=yes {ssh_opts} -R 127.0.0.1:9000:127.0.0.1:22 {target}",
        timeout=15,
        check=False,
        label="ssh -N <central's restricted key> -R 127.0.0.1:9000:127.0.0.1:22 <node>",
    )
    central.check(remote_forward.returncode != 0, "a remote forward (-R) was not refused")

    good_port = 39900 + (hash(env.node_name) % 50)
    central.run(
        f"pkill -f '[s]sh .*-L 127.0.0.1:{good_port}' 2>/dev/null; true",
        timeout=10,
        check=False,
        label="(cleanup) kill any stray forward",
    )
    central.run(
        f"ssh -f -N {ssh_opts} -L 127.0.0.1:{good_port}:127.0.0.1:{console_port} {target}",
        timeout=20,
        label=f"ssh -f -N <central's restricted key> -L 127.0.0.1:{good_port}:127.0.0.1:{console_port} <node>",
    )
    time.sleep(1)
    try:
        allowed_probe = central.run(
            f"curl -sS -m 5 -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{good_port}/api/system/version",
            timeout=10,
            check=False,
            label="curl through -L to the console port (any HTTP status proves the forward reaches it)",
        )
        central.check(
            allowed_probe.stdout.strip() not in ("", "000"),
            f"a forward to the console port did not reach it: {allowed_probe.stdout!r}",
        )
    finally:
        central.run(
            f"pkill -f '[s]sh .*-L 127.0.0.1:{good_port}'",
            timeout=10,
            check=False,
            label="(cleanup) kill the console-port forward",
        )

    bad_port = good_port + 1
    central.run(
        f"pkill -f '[s]sh .*-L 127.0.0.1:{bad_port}' 2>/dev/null; true",
        timeout=10,
        check=False,
        label="(cleanup) kill any stray forward",
    )
    try:
        central.run(
            f"ssh -f -N {ssh_opts} -L 127.0.0.1:{bad_port}:127.0.0.1:9999 {target}",
            timeout=20,
            label=f"ssh -f -N <central's restricted key> -L 127.0.0.1:{bad_port}:127.0.0.1:9999 <node>",
        )
        time.sleep(1)
        refused_probe = central.run(
            f"curl -sS -m 5 -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{bad_port}/",
            timeout=10,
            check=False,
            label="curl through -L to a port other than the console (should not connect)",
        )
        central.check(
            refused_probe.returncode != 0 or refused_probe.stdout.strip() in ("000", ""),
            f"a forward to a port other than the console was not refused: {refused_probe.stdout!r}",
        )
    finally:
        central.run(
            f"pkill -f '[s]sh .*-L 127.0.0.1:{bad_port}'",
            timeout=10,
            check=False,
            label="(cleanup) kill the forbidden-port forward",
        )


# ---------------------------------------------------------------------------
# Scenario 3: deploy through the central.
# ---------------------------------------------------------------------------


def wait_for_job(
    container: str,
    job_id: str,
    token: str,
    *,
    node: str | None = None,
    timeout: int = DEPLOY_TIMEOUT,
) -> dict[str, Any]:
    """Poll a job to a finished state, through the node proxy when `node` is given."""
    base = f"https://127.0.0.1:{CENTRAL_PORT}"
    url = f"{base}/api/nodes/{node}/api/jobs/{job_id}" if node else f"{base}/api/jobs/{job_id}"
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        status, body, raw = api_call(container, "GET", url, token=token)
        if status != 200 or not isinstance(body, dict):
            raise HarnessError(f"GET {url} answered {status}: {raw[:500]!r}")
        last = body
        if body.get("status") in ("completed", "failed", "cancelled"):
            return body
        time.sleep(2)
    raise HarnessError(f"job {job_id} did not finish within {timeout}s: {last!r}")


@fleet_scenario("deploy_through_the_central")
def scenario_deploy_through_central(env: FleetEnv) -> None:
    """Create a static app through the central's proxy, and see it served by the node's own nginx.

    Deployed from a git source (not a local path): apps.py's
    `_require_local_source_privilege` reserves a local path to the master
    token or an elevated session in person, and a fleet token - a standing
    credential a script holds - is neither, so it would be refused. A git
    source has no such restriction, and is exactly what an operator deploying
    through a central actually uses.
    """
    central, node = env.central, env.node
    base = f"https://127.0.0.1:{CENTRAL_PORT}"
    proxy = f"{base}/api/nodes/{env.node_name}/api"

    status, body, raw = api_call(
        CENTRAL_NAME,
        "POST",
        f"{proxy}/apps",
        token=env.master_token,
        json_body={
            "domain": env.static_domain,
            "source": STATIC_SITE_GIT_URL,
            "app_type": "static",
            "ssl": False,
        },
    )
    log_api(central, "POST", f"/api/nodes/{env.node_name}/api/apps", status, raw[:500])
    central.check(
        status == 202, f"create app through the proxy: expected 202, got {status}: {raw[:500]!r}"
    )
    job_id = body["job_id"]

    job = wait_for_job(CENTRAL_NAME, job_id, env.master_token, node=env.node_name)
    log_api(
        central, "GET", f"/api/nodes/{env.node_name}/api/jobs/{job_id}", 200, json.dumps(job)[:800]
    )
    central.check(job.get("status") == "completed", f"the app job did not complete: {job!r}")
    actor = str(job.get("actor") or "")
    central.check(
        "on behalf of" in actor and "master" in actor,
        f"the job's actor does not name the central operator: {actor!r}",
    )

    status, body, raw = api_call(CENTRAL_NAME, "GET", f"{proxy}/apps", token=env.master_token)
    log_api(central, "GET", f"/api/nodes/{env.node_name}/api/apps", status, raw[:500])
    central.check(status == 200, f"list apps through the proxy: expected 200, got {status}")
    domains = [a.get("domain") for a in (body or {}).get("apps", [])]
    central.check(
        env.static_domain in domains, f"the new app is not listed through the proxy: {domains!r}"
    )

    page = node.run(
        f"curl -sS -H 'Host: {env.static_domain}' http://127.0.0.1/",
        timeout=15,
        label=f"curl -H 'Host: {env.static_domain}' http://127.0.0.1/  (on the node itself)",
    )
    node.check(
        "Noust Integration Static Fixture" in page.stdout,
        f"the node's nginx is not serving the app deployed through the proxy: {page.stdout!r}",
    )

    audit = node.run(
        "F=$(find /etc/noust /var/lib/noust -name web-audit.log 2>/dev/null | head -1); "
        'test -n "$F" && tail -c 4000 "$F" || echo NO_AUDIT_LOG',
        timeout=15,
        check=False,
        label="tail the node's own audit log",
    )
    node.check(
        "on behalf of" in audit.stdout and "master" in audit.stdout,
        f"the node's audit log does not show the fleet actor on behalf of the central operator: "
        f"{audit.stdout[-800:]!r}",
    )


# ---------------------------------------------------------------------------
# Scenario 4: logs stream through the forwarded stream.
# ---------------------------------------------------------------------------


@fleet_scenario("logs_stream_through_the_proxy")
def scenario_events_stream(env: FleetEnv) -> None:
    """The proxied SSE `/events` delivers the node's own stream, not an empty 200.

    The node's own event source (`web/events.py`) writes `: connected` the
    instant a client subscribes, before any heartbeat; seeing it here proves
    the frame travelled node -> tunnel -> central -> this call, unmodified.
    """
    central = env.central
    url = f"https://127.0.0.1:{CENTRAL_PORT}/api/nodes/{env.node_name}/events"
    status, _body, raw = api_call(CENTRAL_NAME, "GET", url, token=env.master_token, max_time=6)
    log_api(central, "GET", f"/api/nodes/{env.node_name}/events", status, raw[:300])
    central.check(
        status == 200, f"the proxied event stream answered {status}, not 200: {raw[:300]!r}"
    )
    central.check(
        ": connected" in raw or "event:" in raw or "data:" in raw,
        f"no real SSE framing came through the proxy: {raw[:300]!r}",
    )


# ---------------------------------------------------------------------------
# Scenario 5: sudo enforced on the central.
# ---------------------------------------------------------------------------


@fleet_scenario("sudo_enforced_on_the_central")
def scenario_sudo_enforced(env: FleetEnv) -> None:
    """A destructive proxied call needs the central operator's own sudo mode.

    Logs in as a session (not the elevation-exempt master token), attempts to
    delete the app scenario 3 created through the proxy, and confirms it is
    refused with `elevation_required` - not forwarded to the node at all -
    until `POST /api/auth/elevate` confirms sudo mode on the central.
    """
    central = env.central
    base = f"https://127.0.0.1:{CENTRAL_PORT}"
    proxy = f"{base}/api/nodes/{env.node_name}/api"

    login_code = totp_code(env.totp_secret)
    status, body, raw = api_call(
        CENTRAL_NAME,
        "POST",
        f"{base}/api/auth/login",
        json_body={"token": env.master_token, "totp_code": login_code, "bearer": True},
    )
    log_api(central, "POST", "/api/auth/login", status, "<redacted: session + csrf tokens>")
    central.check(status == 200, f"session login failed: {status} {raw[:300]!r}")
    session_token = str(body["session_token"])
    csrf_token = str(body["csrf_token"])

    status, body, raw = api_call(
        CENTRAL_NAME,
        "DELETE",
        f"{proxy}/apps/{env.static_domain}",
        token=session_token,
        headers=csrf_headers(csrf_token),
    )
    log_api(
        central,
        "DELETE",
        f"/api/nodes/{env.node_name}/api/apps/{env.static_domain}",
        status,
        raw[:400],
    )
    central.check(
        status == 403,
        f"an unelevated delete through the proxy was not refused: {status} {raw[:300]!r}",
    )
    central.check(
        isinstance(body, dict) and body.get("error") == "elevation_required",
        f"the refusal is not elevation_required: {body!r}",
    )

    elevate_code = totp_code(env.totp_secret)
    status, body, raw = api_call(
        CENTRAL_NAME,
        "POST",
        f"{base}/api/auth/elevate",
        token=session_token,
        headers=csrf_headers(csrf_token),
        json_body={"code": elevate_code},
    )
    log_api(central, "POST", "/api/auth/elevate", status, "<redacted>")
    central.check(status == 200, f"elevation failed: {status} {raw[:300]!r}")

    status, body, raw = api_call(
        CENTRAL_NAME,
        "DELETE",
        f"{proxy}/apps/{env.static_domain}",
        token=session_token,
        headers=csrf_headers(csrf_token),
    )
    log_api(
        central,
        "DELETE",
        f"/api/nodes/{env.node_name}/api/apps/{env.static_domain}",
        status,
        raw[:400],
    )
    central.check(status == 202, f"the elevated delete was not accepted: {status} {raw[:300]!r}")
    job_id = body["job_id"]

    job = wait_for_job(CENTRAL_NAME, job_id, env.master_token, node=env.node_name)
    central.check(job.get("status") == "completed", f"the delete job did not complete: {job!r}")

    page = env.node.run(
        f"curl -sS -o /dev/null -w '%{{http_code}}' -H 'Host: {env.static_domain}' http://127.0.0.1/",
        timeout=15,
        check=False,
        label=f"curl -H 'Host: {env.static_domain}' http://127.0.0.1/  (after the elevated delete)",
    )
    env.node.check(
        page.stdout.strip() != "200",
        f"the app is still served after an elevated delete: {page.stdout!r}",
    )


# ---------------------------------------------------------------------------
# 3.1: the tunnel account, the access ceiling, adopting a background console.
# ---------------------------------------------------------------------------


def central_key_ssh_options(env: FleetEnv) -> str:
    """ssh options that use the central's own key for the node, and nothing else."""
    key_path = env.secret_path("fleet", "nodes", env.node_name, "id_ed25519")
    return (
        f"-i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null -o BatchMode=yes -o ConnectTimeout=5"
    )


def rejoin(env: FleetEnv, command: str, *, how: str) -> None:
    """Run the node's half of `noust node rekey|migrate-tunnel`, and paste its code back.

    `how` is `rekey` or `migrate-tunnel`. The node's output carries a join
    code (a one-time token), so it is recorded redacted.
    """
    node_result = docker_exec(NODE_NAME, f"{command} --yes", timeout=90)
    redact(env.node, command, "a join code carrying a one-time fleet token")
    join_match = re.search(r"(noust-join:v1:\S+)", node_result.stdout)
    if join_match is None:
        raise HarnessError(
            f"'{command}' printed no join code:\n{node_result.stdout}\n{node_result.stderr}"
        )
    script = f"noust node {how} {env.node_name} --join-code - --json"
    finished = run_stdin(env.central, script, join_match.group(1) + "\n", timeout=60, label=script)
    record = as_json(finished, what=f"noust node {how} --json")
    env.central.check(
        record.get("status") == "reachable" and record.get("ssh_user") == TUNNEL_USER,
        f"'noust node {how}' did not leave the node reachable as {TUNNEL_USER}: {record!r}",
    )


@fleet_scenario("tunnel_account_contained")
def scenario_tunnel_account(env: FleetEnv) -> None:
    """The central reaches the node as noust-tunnel, which can forward the console port and nothing else.

    The account is what `noust fleet authorize` made it (no home, nologin,
    password `*`) and sshd applies Noust's Match block to it. Then, with
    `PermitRootLogin no` on the node, the central rotates its key (`noust node
    rekey`): enrollment needs no root login at all. Finally the central's own
    key, driven by hand: root is refused, a command runs nothing, a terminal
    is refused, `-L` to another port reaches nothing, and `-R` to a Unix
    socket path - which in 3.0 created `/etc/nologin` as root - is refused
    and creates nothing.
    """
    central, node = env.central, env.node

    account = node.run(f"getent passwd {TUNNEL_USER}", label=f"getent passwd {TUNNEL_USER}")
    fields = account.stdout.strip().split(":")
    node.check(
        len(fields) == 7 and fields[5] == "/nonexistent" and fields[6].endswith("nologin"),
        f"{TUNNEL_USER} is not a no-home, nologin account: {account.stdout!r}",
    )
    password = node.run(
        f"getent shadow {TUNNEL_USER} | cut -d: -f2", label=f"password field of {TUNNEL_USER}"
    )
    node.check(
        password.stdout.strip() == "*", f"password field is {password.stdout.strip()!r}, not '*'"
    )
    effective = node.run(
        f"sshd -T -C user={TUNNEL_USER},host=localhost,addr=127.0.0.1",
        label=f"sshd -T -C user={TUNNEL_USER},host=localhost,addr=127.0.0.1",
    )
    applied = {line.strip().lower() for line in effective.stdout.splitlines()}
    for expected in (
        "allowtcpforwarding local",
        "allowstreamlocalforwarding no",
        f"permitopen 127.0.0.1:{env.node_console_port}",
        "permittty no",
        "x11forwarding no",
        "allowagentforwarding no",
        "forcecommand /usr/bin/false",
    ):
        node.check(expected in applied, f"sshd does not apply '{expected}' to {TUNNEL_USER}")

    node.run(
        "printf 'PermitRootLogin no\\n' > /etc/ssh/sshd_config.d/99-harness-no-root.conf "
        "&& systemctl reload ssh",
        label="PermitRootLogin no on the node",
    )
    try:
        root_login = node.run(
            "sshd -T -C user=root,host=localhost,addr=127.0.0.1 | grep -i '^permitrootlogin'",
            label="sshd -T -C user=root | grep permitrootlogin",
        )
        node.check(
            root_login.stdout.strip().lower() == "permitrootlogin no",
            f"PermitRootLogin no is not in force: {root_login.stdout!r}",
        )

        rekey = central.run(
            f"noust node rekey {env.node_name} --json",
            label=f"noust node rekey {env.node_name} --json",
        )
        rejoin(
            env,
            str(as_json(rekey, what="noust node rekey --json")["authorize_command"]),
            how="rekey",
        )
        tested = as_json(
            central.run(
                f"noust node test {env.node_name} --json",
                label=f"noust node test {env.node_name} --json (new key, PermitRootLogin no)",
            ),
            what="noust node test --json",
        )
        central.check(
            tested.get("reachable") is True, f"the rotated key does not reach the node: {tested!r}"
        )

        opts = central_key_ssh_options(env)
        as_root = central.run(
            f"ssh {opts} root@{NODE_NAME} true",
            timeout=20,
            check=False,
            label="ssh <central's key> root@<node> true",
        )
        central.check(
            as_root.returncode != 0 and "permission denied" in as_root.stderr.lower(),
            f"the central's key logged in as root: {as_root.returncode} {as_root.stderr!r}",
        )
        command = central.run(
            f"ssh {opts} {TUNNEL_USER}@{NODE_NAME} id",
            timeout=20,
            check=False,
            label=f"ssh <central's key> {TUNNEL_USER}@<node> id",
        )
        central.check(
            command.returncode != 0 and command.stdout.strip() in NOLOGIN_MESSAGES,
            f"{TUNNEL_USER} ran a command: {command.returncode} {command.stdout!r}",
        )
        terminal = central.run(
            f"ssh -tt {opts} {TUNNEL_USER}@{NODE_NAME} < /dev/null",
            timeout=20,
            check=False,
            label=f"ssh -tt <central's key> {TUNNEL_USER}@<node>",
        )
        central.check(terminal.returncode != 0, f"{TUNNEL_USER} got a terminal")

        bad_port = 39800 + (hash(env.node_name) % 50)
        central.run(
            f"pkill -f '[s]sh .*-L 127.0.0.1:{bad_port}' 2>/dev/null; true",
            timeout=10,
            check=False,
            label="(cleanup) kill any stray forward",
        )
        try:
            central.run(
                f"ssh -f -N {opts} -L 127.0.0.1:{bad_port}:127.0.0.1:22 {TUNNEL_USER}@{NODE_NAME}",
                timeout=20,
                label=f"ssh -f -N -L 127.0.0.1:{bad_port}:127.0.0.1:22 {TUNNEL_USER}@<node>",
            )
            time.sleep(1)
            through = central.run(
                f"timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/{bad_port} && head -c 64 <&3'",
                timeout=15,
                check=False,
                label="read the node's sshd banner through -L to port 22 (must get nothing)",
            )
            central.check(
                "ssh-" not in through.stdout.lower(),
                f"-L reached a port other than the console: {through.stdout!r}",
            )
        finally:
            central.run(
                f"pkill -f '[s]sh .*-L 127.0.0.1:{bad_port}'",
                timeout=10,
                check=False,
                label="(cleanup) kill the forbidden-port forward",
            )

        node.run("rm -f /etc/nologin", label="(setup) no /etc/nologin")
        socket_forward = central.run(
            f"timeout 10 ssh -N -o ExitOnForwardFailure=yes {opts} "
            f"-R /etc/nologin:127.0.0.1:22 {TUNNEL_USER}@{NODE_NAME}",
            timeout=20,
            check=False,
            label=f"ssh -N -R /etc/nologin:127.0.0.1:22 {TUNNEL_USER}@<node>  (the 3.0 hole)",
        )
        created = node.run(
            "test -e /etc/nologin && echo present || echo absent",
            label="is there an /etc/nologin now?",
        )
        node.check(created.stdout.strip() == "absent", "a remote forward created /etc/nologin")
        central.check(
            socket_forward.returncode not in (0, 124),
            f"-R to a Unix socket path was not refused (exit {socket_forward.returncode})",
        )
    finally:
        node.run(
            "rm -f /etc/ssh/sshd_config.d/99-harness-no-root.conf /etc/nologin "
            "&& systemctl reload ssh",
            check=False,
            label="(cleanup) PermitRootLogin back to the image's default",
        )


@fleet_scenario("ceiling_enforced_by_the_node")
def scenario_ceiling(env: FleetEnv) -> None:
    """A node held to `read` refuses a write, even when the central claims admin for its operator.

    The node's own admission applies `noust.fleet.policy.permits` to the
    ceiling it keeps, never to anything the central sends. The central learns
    the ceiling from `GET /api/auth/fleet/self` when it tests the node.

    Relies on workstream B1's side: the fleet admission in noust.web.auth
    (`fleet_ceiling`, which calls `fleet.policy.permits`) and
    `/api/auth/fleet/self` in its `FLEET_AUTH_PATHS`. The two checks marked
    B1 fail with the status the node answered if either is missing.
    """
    central, node = env.central, env.node
    port = env.node_console_port
    fleet_token = docker_exec(
        CENTRAL_NAME,
        f"cat {env.secret_path('fleet', 'nodes', env.node_name, 'token')}",
        timeout=15,
    ).stdout.strip()
    redact(central, f"cat the fleet token stored for {env.node_name}", "the fleet token itself")

    access = as_json(
        node.run("noust fleet access --level read --json", label="noust fleet access --level read"),
        what="noust fleet access --json",
    )
    node.check(access == {"level": "read", "host_access": False}, f"unexpected ceiling: {access!r}")
    try:
        claims_admin = {
            "X-Noust-Actor": "harness",
            "X-Noust-Actor-Scope": "admin",
            "X-Noust-Elevated": "1",
        }
        status, _body, raw = api_call(
            NODE_NAME,
            "GET",
            f"http://127.0.0.1:{port}/api/apps",
            token=fleet_token,
            headers=claims_admin,
            max_time=10,
        )
        log_api(node, "GET", "/api/apps (fleet token, ceiling read)", status, raw[:300])
        node.check(status == 200, f"a read ceiling refused a read: {status} {raw[:300]!r}")

        # An invalid body: admitted, it would be a 422 and change nothing; the
        # ceiling must answer 403 before the body is even looked at.
        status, _body, raw = api_call(
            NODE_NAME,
            "POST",
            f"http://127.0.0.1:{port}/api/apps",
            token=fleet_token,
            headers=claims_admin,
            json_body={"domain": "not a domain"},
            max_time=10,
        )
        log_api(
            node, "POST", "/api/apps (fleet token claiming admin, ceiling read)", status, raw[:300]
        )
        node.check(
            status == 403,
            "(B1) a node held to read did not refuse a write the central claimed admin for: "
            f"{status} {raw[:300]!r}",
        )

        central.run(
            f"noust node test {env.node_name} --json", label=f"noust node test {env.node_name}"
        )
        shown = as_json(
            central.run(
                f"noust node show {env.node_name} --json",
                label=f"noust node show {env.node_name} --json",
            ),
            what="noust node show --json",
        )
        central.check(
            shown.get("access_level") == "read",
            f"(B1) the central did not learn the node's ceiling: {shown.get('access_level')!r}",
        )
    finally:
        node.run(
            "noust fleet access --level admin",
            check=False,
            label="noust fleet access --level admin",
        )


@fleet_scenario("authorize_adopts_a_background_console")
def scenario_adopt_background_console(env: FleetEnv) -> None:
    """`noust fleet authorize` moves a background console to the service, and keeps its token.

    The node's console is put in the background on its port, as an operator
    who ran `noust web start -d` would have it; authorizing again (the node's
    half of `noust node migrate-tunnel`) stops it, runs the service on the
    same port, prints no banner and no new console token, and the token the
    background console printed still signs in.
    """
    node, central = env.node, env.central
    port = env.node_console_port
    node.run("noust web disable", label="noust web disable")
    # The service's closed connections hold the port in TIME_WAIT for up to a
    # minute, and 'noust web start' refuses a port it cannot bind exclusively.
    started = docker_exec(
        NODE_NAME,
        f"for attempt in $(seq 1 45); do noust web start -d --port {port} && exit 0; "
        "sleep 2; done; exit 1",
        timeout=150,
    )
    redact(node, f"noust web start -d --port {port}", "the console's access token")
    token_match = re.search(r"Access Token:\s*(\S+)", started.stdout)
    if token_match is None:
        raise HarnessError(
            f"'noust web start -d' printed no token:\n{started.stdout}\n{started.stderr}"
        )
    operator_token = token_match.group(1)

    command = central.run(
        f"noust node migrate-tunnel {env.node_name} --json",
        label=f"noust node migrate-tunnel {env.node_name} --json",
    )
    authorize_command = str(
        as_json(command, what="noust node migrate-tunnel --json")["authorize_command"]
    )
    node_result = docker_exec(NODE_NAME, f"{authorize_command} --yes", timeout=120, check=False)
    redact(node, authorize_command, "a join code carrying a one-time fleet token")
    output = node_result.stdout + node_result.stderr
    node.check(
        node_result.returncode == 0,
        f"authorize over a background console failed:\n{output[-2000:]}",
    )
    node.check("Access Token" not in output, "authorize printed the console's banner")
    node.check("Console access token" not in output, "authorize issued a new console token")
    join_match = re.search(r"(noust-join:v1:\S+)", node_result.stdout)
    if join_match is None:
        raise HarnessError("authorize over a background console printed no join code")
    script = f"noust node migrate-tunnel {env.node_name} --join-code - --json"
    record = as_json(
        run_stdin(central, script, join_match.group(1) + "\n", timeout=60, label=script),
        what="noust node migrate-tunnel --json",
    )
    central.check(record.get("status") == "reachable", f"the node is not reachable: {record!r}")

    active = node.run(
        "systemctl is-active noust-web", check=False, label="systemctl is-active noust-web"
    )
    node.check(
        active.stdout.strip() == "active", f"noust-web is {active.stdout.strip()!r}, not active"
    )
    pid_file = node.run(
        "test -e /run/noust-web.pid && echo present || echo absent",
        label="is the daemon's PID file gone?",
    )
    node.check(
        pid_file.stdout.strip() == "absent", "the background console's PID file is still there"
    )
    status, _body, raw = api_call(
        NODE_NAME,
        "GET",
        f"http://127.0.0.1:{port}/api/auth/verify",
        token=operator_token,
        max_time=10,
    )
    log_api(node, "GET", "/api/auth/verify (the background console's own token)", status, raw[:200])
    node.check(status == 200, f"the operator's console token stopped working: {status}")


# ---------------------------------------------------------------------------
# Scenario 6: fleet token rules on the node.
# ---------------------------------------------------------------------------


@fleet_scenario("fleet_token_rules_on_the_node")
def scenario_fleet_token_rules(env: FleetEnv) -> None:
    """A fleet token is refused off-loopback, and revoking or deauthorizing cuts the central.

    Four checks: the token is refused the moment it looks like it travelled
    through a reverse proxy rather than the SSH tunnel (the same guard that
    would catch it leaking beyond loopback), `noust token revoke` on the node
    turns the next proxied call into a clear `node_refused`, `noust fleet
    deauthorize` on top of that removes the authorized_keys line too, and -
    once that forces a fresh SSH connection - the central's own `noust node
    test` reports the node `refused` (ssh's "Permission denied (publickey)",
    caught by `tunnels.key_was_revoked`), never merely `unreachable`, so an
    operator is not left retrying a node that will never come back on its own.
    """
    central, node = env.central, env.node

    # 1) Off-loopback (or proxied) use of the fleet token is refused, even
    #    from the node's own loopback: a forwarding header is what marks a
    #    request as having gone through something other than the SSH tunnel.
    fleet_token_probe = node.run(
        f"find /var/lib/noust /root/.local/share/noust -path '*fleet/nodes/{env.node_name}*' "
        "-name token 2>/dev/null; true",
        timeout=10,
        check=False,
        label="(diagnostic) locate any fleet token cached on the node itself (there should be none)",
    )
    node.check(
        fleet_token_probe.stdout.strip() == "",
        "the node must never hold a copy of its own fleet token file "
        f"(that belongs to the central): {fleet_token_probe.stdout!r}",
    )
    fleet_token = docker_exec(
        CENTRAL_NAME,
        f"cat {env.secret_path('fleet', 'nodes', env.node_name, 'token')}",
        timeout=15,
    ).stdout.strip()
    redact(central, f"cat the fleet token stored for {env.node_name}", "the fleet token itself")
    central.check(fleet_token.startswith("noust_tok_"), "could not read the stored fleet token")

    # Through api_call(), never a shell: the token travels to the curl helper
    # over stdin JSON and into a -K config file, so it never lands in any
    # process's argv (rule 1) - not even a curl running inside the node.
    status, _body, raw = api_call(
        NODE_NAME,
        "GET",
        f"http://127.0.0.1:{env.node_console_port}/api/system/version",
        token=fleet_token,
        headers={"X-Forwarded-For": "8.8.8.8"},
        max_time=10,
    )
    log_api(
        node,
        "GET",
        f"http://127.0.0.1:{env.node_console_port}/api/system/version "
        "(fleet token + X-Forwarded-For, as if through a reverse proxy)",
        status,
        raw[:300],
    )
    node.check(
        status == 401,
        f"a fleet token presented as if through a reverse proxy was not refused: {status} {raw[:300]!r}",
    )

    # 1b) A fleet token presented with no X-Noust-Actor-Scope at all - what
    #     an older central would send, or this harness calling the node
    #     directly rather than through the proxy - is admitted as read only
    #     (auth.py admit_fleet: missing or invalid grants read, never admin
    #     by omission). A read succeeds; a mutation is refused with 403
    #     before it touches anything.
    status, _body, raw = api_call(
        NODE_NAME,
        "GET",
        f"http://127.0.0.1:{env.node_console_port}/api/apps",
        token=fleet_token,
        max_time=10,
    )
    log_api(node, "GET", "/api/apps (fleet token, no X-Noust-Actor-Scope)", status, raw[:300])
    node.check(
        status == 200,
        f"a fleet token with no actor scope could not even read: {status} {raw[:300]!r}",
    )
    status, body, raw = api_call(
        NODE_NAME,
        "POST",
        f"http://127.0.0.1:{env.node_console_port}/api/apps",
        token=fleet_token,
        json_body={
            "domain": "scope-probe.test",
            "source": STATIC_SITE_GIT_URL,
            "app_type": "static",
        },
        max_time=10,
    )
    log_api(node, "POST", "/api/apps (fleet token, no X-Noust-Actor-Scope)", status, raw[:300])
    node.check(
        status == 403,
        "a fleet token with no actor scope (read only, never admin by default) was not refused a "
        f"mutation: {status} {raw[:300]!r}",
    )

    # 2) Revoke just the token on the node: the SSH key still opens the
    #    tunnel, but the API refuses it.
    tokens_result = node.run("noust token list --json", label="noust token list --json")
    tokens_info = as_json(tokens_result, what="noust token list --json")
    fleet_records = [
        t
        for t in tokens_info.get("tokens", [])
        if str(t.get("name", "")).startswith("fleet-") and t.get("revoked_at") is None
    ]
    node.check(len(fleet_records) == 1, f"expected exactly one live fleet token: {fleet_records!r}")
    token_id = int(fleet_records[0]["id"])
    node.run(f"noust token revoke {token_id} -f", label=f"noust token revoke {token_id} -f")

    status, body, raw = api_call(
        CENTRAL_NAME,
        "GET",
        f"https://127.0.0.1:{CENTRAL_PORT}/api/nodes/{env.node_name}/api/system/version",
        token=env.master_token,
    )
    log_api(central, "GET", f"/api/nodes/{env.node_name}/api/system/version", status, raw[:400])
    central.check(
        status == 502,
        f"a call with a revoked fleet token was not refused with 502: {status} {raw[:300]!r}",
    )
    central.check(
        isinstance(body, dict) and body.get("error") == "node_refused",
        f"the refusal is not node_refused: {body!r}",
    )

    # 3) Deauthorize on the node: the authorized_keys line goes too.
    # `grep -c` alone: it already prints "0" (not nothing) when the pattern
    # is absent, so a trailing `|| echo 0` doubles that into "0\n0\n" and the
    # count is normalized with a trailing `; true` instead, so a `check=True`
    # run never fails on the "not found" case (exit 1).
    key_line_before = node.run(
        f"grep -c 'noust-central:{env.central_label}' {TUNNEL_KEYS_FILE} 2>/dev/null; true",
        timeout=10,
        label="grep the central's key line in authorized_keys before deauthorize",
    )
    node.check(
        key_line_before.stdout.strip() not in ("", "0"),
        "the central's key line was already missing before deauthorize",
    )

    deauth_result = node.run(
        f"noust fleet deauthorize --name {env.central_label} --json",
        label=f"noust fleet deauthorize --name {env.central_label} --json",
    )
    deauth_info = as_json(deauth_result, what="noust fleet deauthorize --json")
    node.check(
        deauth_info.get("removed_keys", 0) >= 1, f"deauthorize removed no key line: {deauth_info!r}"
    )

    key_line_after = node.run(
        f"grep -c 'noust-central:{env.central_label}' {TUNNEL_KEYS_FILE} 2>/dev/null; true",
        timeout=10,
        check=False,
        label="grep the central's key line in authorized_keys after deauthorize",
    )
    node.check(
        key_line_after.stdout.strip() in ("", "0"),
        f"the central's authorized_keys line survived deauthorize: {key_line_after.stdout!r}",
    )

    # 4) The tunnel opened by enroll_node() is still alive - a running tunnel
    #    is reused as-is and never re-presents the key - so it is killed to
    #    force the next call to open a fresh connection, the one that
    #    actually hits authorized_keys and gets "Permission denied".
    central.run(
        "pkill -f '[s]sh -N -T -F /dev/null' 2>/dev/null; true",
        timeout=10,
        label="kill the open tunnel to force a reconnect (so the revoked key is really presented)",
    )
    time.sleep(1)
    retest = central.run(
        f"noust node test {env.node_name} --json",
        timeout=30,
        check=False,
        label=f"noust node test {env.node_name} --json (after fleet deauthorize on the node)",
    )
    retest_info = as_json(retest, what="noust node test --json (after deauthorize)")
    central.check(
        retest_info.get("reachable") is not True,
        f"the central still considers a deauthorized node reachable: {retest_info!r}",
    )
    central.check(
        retest_info.get("status") == "refused",
        f"a revoked SSH key should mark the node refused, not merely unreachable: {retest_info!r}",
    )


# ---------------------------------------------------------------------------
# Scenario 7: host key change detected.
# ---------------------------------------------------------------------------


@fleet_scenario("host_key_change_detected")
def scenario_host_key_change(env: FleetEnv) -> None:
    """A node's changed SSH host key stops the tunnel instead of being trusted silently.

    Scenario 6 deliberately ends the enrollment, so this one starts by
    redoing it (root cause: a fresh key and token are needed to have
    something to break). It then regenerates the node's host keys, kills the
    open tunnel so the next call must renegotiate, and checks the central
    refuses it - loudly, and without re-pinning on its own.
    """
    central, node = env.central, env.node

    central.run(
        f"noust node remove {env.node_name} --no-revoke -f",
        timeout=30,
        check=False,
        label=f"noust node remove {env.node_name} --no-revoke -f  (clean slate after scenario 6)",
    )
    enroll_node(env)
    pinned_before = env.node_host_key

    node.run("rm -f /etc/ssh/ssh_host_*_key*", timeout=15, label="rm the node's SSH host keys")
    node.run("ssh-keygen -A", timeout=30, label="ssh-keygen -A  (regenerate them)")
    node.run("systemctl restart ssh", timeout=30, label="systemctl restart ssh")

    # The tunnel opened by enroll_node() above is still alive; a running
    # tunnel is reused as-is and never re-checks the host key. Killing it is
    # what forces the next call to renegotiate against the new key.
    central.run(
        "pkill -f '[s]sh -N -T -F /dev/null' 2>/dev/null; true",
        timeout=10,
        label="kill the open tunnel to force a reconnect",
    )
    time.sleep(1)

    test_result = central.run(
        f"noust node test {env.node_name} --json",
        timeout=30,
        check=False,
        label=f"noust node test {env.node_name} --json  (after the node's host key changed)",
    )
    test_info = as_json(test_result, what="noust node test --json")
    central.check(
        test_info.get("reachable") is not True,
        f"a changed host key was not detected: {test_info!r}",
    )
    central.check(
        "does not match the one pinned" in (test_info.get("error") or ""),
        f"the refusal does not say the host key changed: {test_info!r}",
    )

    show_result = central.run(
        f"noust node show {env.node_name} --json",
        label=f"noust node show {env.node_name} --json  (unchanged pin)",
    )
    show_info = as_json(show_result, what="noust node show --json")
    central.check(
        show_info.get("host_key") == pinned_before,
        "the central accepted the node's new host key on its own instead of keeping the old pin",
    )

    retry_result = central.run(
        f"noust node test {env.node_name} --json",
        timeout=30,
        check=False,
        label=f"noust node test {env.node_name} --json  (again: still refused, not self-healed)",
    )
    retry_info = as_json(retry_result, what="noust node test --json")
    central.check(
        retry_info.get("reachable") is not True,
        "the central started trusting the new host key by itself",
    )


# ---------------------------------------------------------------------------
# Scenario 8: the central image.
# ---------------------------------------------------------------------------


def pyproject_version() -> str:
    """Read `[project].version` from pyproject.toml, the one source of truth."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise HarnessError("could not read the version from pyproject.toml")
    return match.group(1)


@fleet_scenario("central_image")
def scenario_central_image(env: FleetEnv) -> None:
    """Build packaging/container/Dockerfile, run it, and register the node from it.

    Builds the exact image the release workflow publishes, runs it as
    documented (a volume, `noust central run`, self-signed TLS), checks the
    console answers over TLS on the Docker network and that the allowlist
    genuinely refuses an address outside it, registers scenario 1's node
    through this containerised central, and - if sealing works - starts it
    sealed, confirms 423 `central_locked`, unlocks it and confirms tunnels
    open again.
    """
    version = pyproject_version()
    wheel_name = f"noust-{version}-py3-none-any.whl"
    dist_dir = PACKAGING_CONTAINER_DIR / "dist"
    dist_dir.mkdir(exist_ok=True)
    dest = dist_dir / wheel_name
    created_dist_dir = not any(dist_dir.iterdir())
    dest.write_bytes(env.wheel.read_bytes())

    try:
        print(f"[setup] building {CENTRAL_IMAGE_TAG} from packaging/container/Dockerfile")
        build = subprocess.run(
            [
                "docker",
                "build",
                "--build-arg",
                f"NOUST_VERSION={version}",
                "-t",
                CENTRAL_IMAGE_TAG,
                str(PACKAGING_CONTAINER_DIR),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if build.returncode != 0:
            raise HarnessError(
                f"building the central image failed:\n{build.stdout}\n{build.stderr}"
            )
    finally:
        dest.unlink(missing_ok=True)
        if created_dist_dir:
            try:
                dist_dir.rmdir()
            except OSError:
                pass

    remove_container(CENTRAL2_NAME)
    subprocess.run(
        ["docker", "volume", "rm", "-f", CENTRAL2_VOLUME],
        capture_output=True,
        text=True,
        timeout=30,
    )
    sh(["docker", "volume", "create", CENTRAL2_VOLUME], timeout=15)

    def run_central2(*, allow_ip: str | None = None) -> None:
        remove_container(CENTRAL2_NAME)
        argv = [
            "docker",
            "run",
            "-d",
            "--name",
            CENTRAL2_NAME,
            "--network",
            NETWORK_NAME,
            "-v",
            f"{CENTRAL2_VOLUME}:/data",
            "--read-only",
            "--tmpfs",
            "/tmp:size=16m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
        ]
        if allow_ip is not None:
            argv += ["-e", f"NOUST_ALLOW_IP={allow_ip}"]
        argv.append(CENTRAL_IMAGE_TAG)
        sh(argv, timeout=30)

    run_central2()
    central2 = Scenario(container=CENTRAL2_NAME)
    try:
        deadline = time.time() + 60
        healthy = False
        while time.time() < deadline:
            status, _body, _raw = api_call(
                NODE_NAME, "GET", f"https://{CENTRAL2_NAME}:{CENTRAL_PORT}/health", max_time=5
            )
            if status == 200:
                healthy = True
                break
            time.sleep(2)
        central2.check(
            healthy, "the packaged central never answered /health over TLS on the Docker network"
        )

        logs = subprocess.run(
            ["docker", "logs", CENTRAL2_NAME], capture_output=True, text=True, timeout=15
        ).stdout
        token_match = re.search(r"Access Token:\s*(\S+)", logs)
        if token_match is None:
            raise HarnessError("the packaged central's first-start log carries no access token")
        master_token2 = token_match.group(1)
        redact(central2, "docker logs noust-fleet-central2", "the first-start access token")

        enroll = docker_exec(CENTRAL2_NAME, "noust 2fa enroll", timeout=30)
        secret_match = re.search(r"Secret:\s*([A-Z2-7]+)", enroll.stdout)
        if secret_match is None:
            raise HarnessError("no secret in the packaged central's 'noust 2fa enroll' output")
        totp_secret2 = secret_match.group(1)
        docker_exec(CENTRAL2_NAME, f"noust 2fa confirm {totp_code(totp_secret2)}", timeout=30)
        central2.evidence.append("$ noust 2fa enroll && noust 2fa confirm <code>\n(exit=0)")

        key_result = central2.run(
            f"noust node key {env.node_name} --json", label=f"noust node key {env.node_name} --json"
        )
        key_info = as_json(key_result, what="noust node key --json")
        authorize_command = str(key_info["authorize_command"])
        # --yes: harmless on a first enrollment, and keeps a rerun of just
        # this scenario (--scenario central_image, node already holding a
        # stale token from an earlier run with the same container hostname)
        # from blocking on a confirmation prompt this non-interactive exec
        # can never answer.
        authorize_result = docker_exec(NODE_NAME, f"{authorize_command} --yes", timeout=60)
        redact(env.node, authorize_command, "a join code carrying a one-time fleet token")
        join_match = re.search(r"(noust-join:v1:\S+)", authorize_result.stdout)
        if join_match is None:
            raise HarnessError(
                f"'{authorize_command}' on the node printed no join code for the packaged central"
            )

        add_script = (
            f"noust node add {env.node_name} --ssh {env.node_ssh_target} --join-code - --json"
        )
        add_result = run_stdin(
            central2, add_script, join_match.group(1) + "\n", timeout=60, label=add_script
        )
        record = as_json(add_result, what="noust node add --json")
        central2.check(
            record.get("status") == "reachable",
            f"the packaged central could not reach the node: {record!r}",
        )

        # ServiceManager refuses on a hub (commit 336ca9b): the packaged
        # image ships NOUST_CENTRAL_ROLE=hub (packaging/container/Dockerfile),
        # and the guard lives at ServiceManager.runner - the chokepoint every
        # caller passes through, including the console's own API and not
        # only the CLI dispatch. get_status() always shells out to inspect
        # the unit, even for a domain nobody deployed, so this never touches
        # a real service and cannot be confused with a plain 404.
        status, body, raw = api_call(
            CENTRAL2_NAME,
            "POST",
            f"https://127.0.0.1:{CENTRAL_PORT}/api/apps/hub-probe.test/restart",
            token=master_token2,
        )
        central2.evidence.append(
            "$ POST /api/apps/hub-probe.test/restart  (a hub deploys nothing locally)\n"
            f"(status={status}) {raw[:300]}"
        )
        central2.check(
            status == 409 and isinstance(body, dict) and body.get("error") == "hub_role",
            f"a hub did not refuse a local service action with 409 hub_role: {status} {raw[:300]!r}",
        )

        # The allowlist: an address definitely outside it is refused, the
        # Docker network (inside the default private ranges) is allowed.
        # NOUST_ALLOW_IP replaces the whole allowlist rather than adding to
        # it (central/setup.py's allowlist()), so 203.0.113.0/24 blocks the
        # container's own loopback too: readiness here means "the TLS server
        # answered something" (403 counts), never specifically 200.
        run_central2(allow_ip="203.0.113.0/24")
        # The packaged central image has no curl (packaging/container/Dockerfile
        # is python:3.12-slim-bookworm), so the readiness probe from its own
        # loopback uses urllib, the same client its own HEALTHCHECK uses.
        loopback_probe_script = (
            "python3 -c '\n"
            "import ssl, urllib.error, urllib.request\n"
            "try:\n"
            f'    urllib.request.urlopen("https://127.0.0.1:{CENTRAL_PORT}/health", '
            "context=ssl._create_unverified_context(), timeout=4)\n"
            "    print(200)\n"
            "except urllib.error.HTTPError as exc:\n"
            "    print(exc.code)\n"
            "except Exception:\n"
            '    print("000")\n'
            "'"
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            loopback_probe = docker_exec(
                CENTRAL2_NAME, loopback_probe_script, timeout=10, check=False
            )
            probe_code = loopback_probe.stdout.strip()
            if probe_code and probe_code != "000":
                break
            time.sleep(2)
        status, _body, _raw = api_call(
            NODE_NAME, "GET", f"https://{CENTRAL2_NAME}:{CENTRAL_PORT}/health", max_time=5
        )
        central2.evidence.append(
            f"$ GET /health from the node, with NOUST_ALLOW_IP=203.0.113.0/24\n(status={status})"
        )
        central2.check(
            status == 403, f"an address outside NOUST_ALLOW_IP was not refused: {status}"
        )

        run_central2()
        deadline = time.time() + 30
        allowed_again = False
        while time.time() < deadline:
            status, _body, _raw = api_call(
                NODE_NAME, "GET", f"https://{CENTRAL2_NAME}:{CENTRAL_PORT}/health", max_time=5
            )
            if status == 200:
                allowed_again = True
                break
            time.sleep(2)
        central2.check(
            allowed_again, "the central did not answer again once the allowlist reverted to default"
        )

        # Sealed secrets: sealed, restarted, locked, unlocked.
        passphrase = "fleet-harness-passphrase-" + str(int(time.time()))
        seal = docker_exec_stdin(
            CENTRAL2_NAME, "noust central seal", passphrase + "\n", timeout=30, check=False
        )
        redact(central2, "noust central seal", "the sealing passphrase")
        if seal.returncode != 0:
            print(
                f"[scenario central_image] sealing is not available here, skipping the locked/unlocked check: {seal.stderr}"
            )
        else:
            sh(["docker", "restart", CENTRAL2_NAME], timeout=60)
            deadline = time.time() + 60
            locked_seen = False
            while time.time() < deadline:
                status, body, _raw = api_call(
                    CENTRAL2_NAME,
                    "GET",
                    f"https://127.0.0.1:{CENTRAL_PORT}/api/auth/session",
                    token=master_token2,
                    max_time=5,
                    insecure=True,
                )
                if status == 200 and isinstance(body, dict) and body.get("central"):
                    locked_seen = bool(body["central"].get("locked"))
                    break
                time.sleep(2)
            central2.check(
                locked_seen, "the restarted, sealed central did not report itself locked"
            )

            status, body, raw = api_call(
                CENTRAL2_NAME,
                "GET",
                f"https://127.0.0.1:{CENTRAL_PORT}/api/nodes/{env.node_name}/api/system/version",
                token=master_token2,
            )
            central2.evidence.append(
                f"$ GET /api/nodes/{env.node_name}/api/system/version  (central locked)\n(status={status}) {raw[:300]}"
            )
            central2.check(
                status == 423 and isinstance(body, dict) and body.get("error") == "central_locked",
                f"a locked central did not answer 423 central_locked: {status} {raw[:300]!r}",
            )

            docker_exec_stdin(CENTRAL2_NAME, "noust central unlock", passphrase + "\n", timeout=30)
            redact(central2, "noust central unlock", "the sealing passphrase")

            status, body, raw = api_call(
                CENTRAL2_NAME,
                "GET",
                f"https://127.0.0.1:{CENTRAL_PORT}/api/nodes/{env.node_name}/api/system/version",
                token=master_token2,
            )
            central2.evidence.append(
                f"$ GET /api/nodes/{env.node_name}/api/system/version  (after unlock)\n(status={status}) {raw[:300]}"
            )
            central2.check(
                status == 200,
                f"the tunnel did not open again once unlocked: {status} {raw[:300]!r}",
            )
    finally:
        env.central.evidence.append(
            f"--- evidence from {CENTRAL2_NAME} ---\n" + "\n\n".join(central2.evidence)
        )


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--keep", action="store_true", help="Leave the containers, network and volume running."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="Run only scenarios whose name contains this substring. May be given more than once.",
    )
    parser.add_argument(
        "--skip-wheel-build",
        action="store_true",
        help="Reuse the newest wheel already in the working directory instead of rebuilding.",
    )
    parser.add_argument(
        "--skip-image-build",
        action="store_true",
        help="Reuse the existing images instead of rebuilding them.",
    )
    return parser.parse_args()


def selected_scenarios(names: list[str] | None) -> list[tuple[str, FleetScenarioFn]]:
    if not names:
        return list(FLEET_SCENARIOS)
    selected = [
        (name, fn) for name, fn in FLEET_SCENARIOS if any(pattern in name for pattern in names)
    ]
    if not selected:
        raise HarnessError(
            f"no scenario matches {names!r}; available: {[n for n, _ in FLEET_SCENARIOS]}"
        )
    return selected


def teardown(keep: bool) -> None:
    if keep:
        print(
            f"\n[teardown] --keep given, leaving {CENTRAL_NAME}, {NODE_NAME}, {CENTRAL2_NAME} and {NETWORK_NAME} up"
        )
        return
    print("\n[teardown] removing containers, network and volume")
    remove_container(CENTRAL2_NAME)
    remove_container(CENTRAL_NAME)
    remove_container(NODE_NAME)
    remove_network(NETWORK_NAME)
    subprocess.run(
        ["docker", "volume", "rm", "-f", CENTRAL2_VOLUME],
        capture_output=True,
        text=True,
        timeout=30,
    )


def main() -> int:
    args = parse_args()
    started_at = time.monotonic()

    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)

    if args.skip_wheel_build:
        wheels = sorted((workdir / "dist").glob("*.whl"))
        if not wheels:
            raise HarnessError("--skip-wheel-build given but no wheel exists yet")
        wheel = wheels[-1]
        print(f"[setup] reusing existing wheel {wheel.name}")
    else:
        wheel = build_wheel(workdir)

    if not args.skip_image_build:
        build_image()
        build_fleet_image(BASE_IMAGE_TAG, FLEET_IMAGE_TAG)
    else:
        print(f"[setup] reusing existing images {BASE_IMAGE_TAG} and {FLEET_IMAGE_TAG}")

    remove_container(NODE_NAME)
    remove_container(CENTRAL_NAME)
    remove_container(CENTRAL2_NAME)
    remove_network(NETWORK_NAME)
    create_network(NETWORK_NAME)

    failures = 0
    attempted = 0
    skipped = 0
    setup_error: str | None = None
    env: FleetEnv | None = None

    try:
        try:
            start_fleet_container(NODE_NAME, FLEET_IMAGE_TAG, NETWORK_NAME)
            start_fleet_container(CENTRAL_NAME, FLEET_IMAGE_TAG, NETWORK_NAME)
            wait_for_systemd(NODE_NAME)
            wait_for_systemd(CENTRAL_NAME)
            install_noust(NODE_NAME, wheel)
            install_noust(CENTRAL_NAME, wheel)
            install_fixtures(NODE_NAME)
            provision_ssh(NODE_NAME)
            master_token, totp_secret = provision_central(CENTRAL_NAME)
            env = FleetEnv(
                central=Scenario(container=CENTRAL_NAME),
                node=Scenario(container=NODE_NAME),
                wheel=wheel,
                master_token=master_token,
                totp_secret=totp_secret,
            )
            to_run = selected_scenarios(args.scenario)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            print(f"\n[scenarios] running {len(to_run)} scenario(s): {[n for n, _ in to_run]}")
            for sc_name, fn in to_run:
                print(f"\n=== {sc_name} ===")
                attempted += 1
                assert env is not None
                env.central.evidence = []
                env.node.evidence = []
                try:
                    fn(env)
                except ScenarioSkipped as exc:
                    skipped += 1
                    print(f"SKIP: {sc_name}: {exc}")
                except AssertionError as exc:
                    failures += 1
                    print(f"FAIL: {sc_name}: {exc}")
                except HarnessError as exc:
                    failures += 1
                    print(f"FAIL: {sc_name} (command error): {exc}")
                except Exception as exc:
                    # The harness's own error boundary: one scenario raising
                    # something nobody anticipated (a library's own timeout,
                    # a KeyError from an API response shaped differently than
                    # expected, ...) must be recorded as that scenario's
                    # failure, not escape main() and abort every scenario
                    # still to run - which is what let one hung SSE read take
                    # out scenarios 7 and 8 with it.
                    failures += 1
                    print(f"FAIL: {sc_name} (unexpected error): {exc!r}")
                else:
                    print(f"PASS: {sc_name}")
                finally:
                    evidence = env.central.evidence + env.node.evidence
                    if evidence:
                        print("--- evidence ---")
                        print("\n\n".join(evidence))
    finally:
        teardown(args.keep)

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        return 1

    print(
        f"\n[summary] {attempted - failures - skipped} passed, {failures} failed, "
        f"{skipped} skipped, in {elapsed:.1f}s"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
