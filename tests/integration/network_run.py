#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Real-software harness for the network features of Noust: fail2ban, HTTP/3, forward-auth,
prefix routes, the site's history, the Coolify templates and the private certificate authority,
against the real programs.

pytest proves these features with fakes (tests/conftest.py refuses a real process), which proves
that Noust writes what it means to write and says nothing about whether nginx, fail2ban,
Authelia or curl agree. This harness is the other half: it installs Noust from the working
tree into a systemd container, drives it with its own CLI, and checks the result with the real
software and real traffic.

Containers (never more than three at once, all on a network of their own, no published ports):

- ``noust-test-de-server``: Debian 13 with systemd, nginx 1.26 (built with HTTP/3), sshd, an
  inner Docker daemon, and Noust installed from the wheel. fail2ban is installed by Noust.
- ``noust-test-de-client``: curl with HTTP/3 (quiche), an SSH client, and a second address that
  stands for "a CDN in front of the server".
- ``noust-test-de-authelia`` / ``noust-test-de-oauth2``: one at a time, sharing the server's
  network namespace so that the service listens on the server's loopback, where Noust's
  presets expect it.
- ``noust-test-de-trust``: AlmaLinux 9, made by the ``internal_ca_private_names`` scenario only,
  to follow the trust steps Noust prints for a family of systems the server is not.

The Coolify scenarios (``--scenario coolify``) download templates from Coolify's repository at a
pinned commit, check their sha256 and deploy them with the inner Docker's Compose, so they need
the network and pull images; they leave their stacks running (``--keep``) and start from nothing
on a re-run, removing the stack's containers and volumes in the inner Docker only.

Every name carries the prefix ``noust-test-de-`` and the label ``noust.test=1``. Nothing else on
the machine's Docker is read, stopped, started or removed: :func:`owned` refuses any name that is
not ours, and teardown removes by exact name and by label, never with ``prune``.

This file is excluded from pytest's collection (it does not match ``test_*.py``) and is not run
by ``run.py``.

Usage (from the repository root, with the repository's own venv):
    .venv/bin/python tests/integration/network_run.py
    .venv/bin/python tests/integration/network_run.py --scenario fail2ban
    .venv/bin/python tests/integration/network_run.py --keep
    .venv/bin/python tests/integration/network_run.py --reuse --scenario http3 --refresh
    .venv/bin/python tests/integration/network_run.py --teardown

Evidence (every command and its output, per scenario) is written to ``--evidence-dir``.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]
NETWORK_DIR = INTEGRATION_DIR / "network"

if TYPE_CHECKING:
    from run import HarnessError as HarnessError
    from run import Scenario as Scenario


def _load_run() -> Any:
    """
    Load tests/integration/run.py as a module, without touching sys.path.

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
build_wheel = run.build_wheel
wait_for_systemd = run.wait_for_systemd
install_noust = run.install_noust
if not TYPE_CHECKING:
    Scenario = run.Scenario
    HarnessError = run.HarnessError

# ---------------------------------------------------------------------------
# Names. Everything this harness creates starts with PREFIX and has the label.
# ---------------------------------------------------------------------------

#: Overridable, so that two work streams sharing one Docker keep their containers apart
#: (``NOUST_IT_NETWORK_PREFIX=noust-test-w6r-net``); it must still start with ``noust-test-``.
PREFIX = os.environ.get("NOUST_IT_NETWORK_PREFIX", "noust-test-de")
LABEL = "noust.test=1"
NETWORK = f"{PREFIX}-net"
SERVER = f"{PREFIX}-server"
CLIENT = f"{PREFIX}-client"
AUTHELIA = f"{PREFIX}-authelia"
OAUTH2 = f"{PREFIX}-oauth2"
#: A device of another family (AlmaLinux), to follow the trust steps for a system the server is not.
TRUST = f"{PREFIX}-trust"
TRUST_IMAGE = "almalinux:9"
DOCKER_VOLUME = f"{PREFIX}-docker"
SERVER_IMAGE = f"{PREFIX}-server:latest"
CLIENT_IMAGE = f"{PREFIX}-client:latest"

#: What the harness may remove, by exact name. The providers share the server's network
#: namespace, so they go first.
OWNED_CONTAINERS = (TRUST, AUTHELIA, OAUTH2, CLIENT, SERVER)
#: The certificate scenarios' authority and DNS provider, removed with the rest (see DNS).
ACME_CONTAINERS = (
    f"{PREFIX}-dns",
    f"{PREFIX}-fresh",
    f"{PREFIX}-debbuild",
)

#: The fixtures served by git:// inside the server.
ECHO_REPO = "/root/fixtures/echo-app"

#: The identity providers of the forward-auth scenarios, by digest (authelia v4.39.28 and
#: oauth2-proxy v7.15.5 when they were chosen).
AUTHELIA_IMAGE = (
    "authelia/authelia@sha256:bd97cff4fcbf715b5ff1f9ae286afbe6033afce385302520b0368122d43a6f54"
)
OAUTH2_IMAGE = "quay.io/oauth2-proxy/oauth2-proxy@sha256:8498b0d0ef0a7b29686414000a08aee467f02d0299c9ed1e006a8f33fc017916"
ECHO_URL = "git://127.0.0.1/echo-app"

DEPLOY_TIMEOUT = 300


class ScenarioSkipped(Exception):
    """A scenario that cannot run here, with the reason: never a PASS, never a FAIL."""


# ---------------------------------------------------------------------------
# Docker, restricted to what is ours
# ---------------------------------------------------------------------------


def owned(name: str) -> str:
    """
    Refuse any Docker object that is not this harness's.

    Args:
        name: A container, network, volume or image name.

    Returns:
        The same name.

    Raises:
        HarnessError: It does not start with the harness's prefix.
    """
    if not name.startswith(PREFIX):
        raise HarnessError(f"{name!r} is not a {PREFIX}* object: the harness will not touch it")
    return name


_DOCKER_ENV: dict[str, str] | None = None


def docker_env() -> dict[str, str]:
    """
    The environment for docker commands that pull or build.

    Docker Desktop's credential helper (``docker-credential-desktop.exe``) cannot run from some
    WSL shells, which fails every pull of a public image. When the configured helper cannot be
    executed, a private, empty configuration is used for this process: public images need no
    credentials, and nothing of the user's configuration is read or written.

    Returns:
        The variables to pass.
    """
    global _DOCKER_ENV
    if _DOCKER_ENV is not None:
        return _DOCKER_ENV
    env = dict(os.environ)
    config = Path(env.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"
    store = ""
    try:
        store = json.loads(config.read_text()).get("credsStore", "")
    except (OSError, ValueError):
        pass
    if store:
        try:
            subprocess.run(
                [f"docker-credential-{store}", "list"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except OSError:
            private = Path(tempfile.gettempdir()) / f"{PREFIX}-docker-config"
            private.mkdir(exist_ok=True)
            (private / "config.json").write_text("{}")
            env["DOCKER_CONFIG"] = str(private)
    _DOCKER_ENV = env
    return env


def docker(
    *args: str, timeout: int = 120, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """
    Run ``docker`` with the right environment.

    Args:
        *args: Its arguments.
        timeout: Seconds.
        check: Raise on a non-zero exit.
        input_text: Standard input.

    Returns:
        The finished process.

    Raises:
        HarnessError: ``check`` and it failed.
    """
    proc = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=docker_env(),
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise HarnessError(
            f"docker {' '.join(args)} failed (exit {proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def exists(kind: str, name: str) -> bool:
    """
    Whether a Docker object of ours exists.

    Args:
        kind: ``container``, ``network``, ``volume`` or ``image``.
        name: Its name.

    Returns:
        True when ``docker <kind> inspect`` finds it.
    """
    return docker(kind, "inspect", owned(name), check=False).returncode == 0


def is_running(name: str) -> bool:
    """
    Args:
        name: One of our containers.

    Returns:
        Whether it is running.
    """
    proc = docker("inspect", "-f", "{{.State.Running}}", owned(name), check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def remove_container(name: str) -> None:
    """
    Remove one of our containers, after stopping the Docker daemon inside it cleanly.

    Args:
        name: Its exact name; refused unless it carries the prefix and the label.
    """
    owned(name)
    labelled = docker("inspect", "-f", '{{index .Config.Labels "noust.test"}}', name, check=False)
    if labelled.returncode != 0:
        return  # not there
    if labelled.stdout.strip() != "1":
        raise HarnessError(f"{name} does not carry the label {LABEL}: not removing it")
    if name == SERVER:
        docker(
            "exec",
            name,
            "bash",
            "-lc",
            "docker ps -aq | xargs -r docker rm -f >/dev/null 2>&1; "
            "systemctl stop docker.socket docker.service",
            check=False,
            timeout=120,
        )
    # -v: a provider image may declare a volume, and the anonymous one goes with its container
    docker("rm", "-f", "-v", name, check=False, timeout=60)


def remove_network() -> None:
    """Remove our network."""
    docker("network", "rm", owned(NETWORK), check=False, timeout=30)


def remove_volume() -> None:
    """Remove the inner Docker's data volume."""
    docker("volume", "rm", "-f", owned(DOCKER_VOLUME), check=False, timeout=60)


def remove_images() -> None:
    """Remove the images this harness built, by exact name."""
    for image in (SERVER_IMAGE, CLIENT_IMAGE, f"{PREFIX}-dns:latest", f"{PREFIX}-fresh:latest"):
        docker("image", "rm", owned(image), check=False, timeout=60)


def teardown(*, images: bool = False) -> None:
    """
    Remove everything the harness created: containers, network, volume and optionally images.

    Args:
        images: Also remove the two images it built.
    """
    for name in (*ACME_CONTAINERS, *OWNED_CONTAINERS):
        remove_container(name)
    remove_network()
    remove_volume()
    if images:
        remove_images()
    left = docker(
        "ps", "-a", "--filter", f"label={LABEL}", "--format", "{{.Names}}", check=False
    ).stdout.split()
    ours = [name for name in left if name.startswith(PREFIX)]
    if ours:
        print(f"[teardown] still there: {ours}")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def build_images() -> None:
    """Build the server and the client images (cached after the first time)."""
    for tag, dockerfile in (
        (SERVER_IMAGE, "Dockerfile.server"),
        (CLIENT_IMAGE, "Dockerfile.client"),
    ):
        print(f"[setup] building {tag}")
        docker(
            "build",
            "--label",
            LABEL,
            "-t",
            owned(tag),
            "-f",
            str(NETWORK_DIR / dockerfile),
            str(NETWORK_DIR),
            timeout=1200,
        )


def start_server() -> None:
    """Start the systemd container, with the inner Docker's data on a volume of ours."""
    print(f"[setup] starting {SERVER}")
    docker(
        "run",
        "-d",
        "--name",
        owned(SERVER),
        "--label",
        LABEL,
        "--network",
        NETWORK,
        "--privileged",
        "--cgroupns=host",
        "-v",
        "/sys/fs/cgroup:/sys/fs/cgroup:rw",
        "-v",
        f"{DOCKER_VOLUME}:/var/lib/docker",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/run/lock",
        SERVER_IMAGE,
        timeout=60,
    )


def start_client() -> None:
    """Start the visitor: it may add an address to its own interface, nothing else."""
    print(f"[setup] starting {CLIENT}")
    docker(
        "run",
        "-d",
        "--name",
        owned(CLIENT),
        "--label",
        LABEL,
        "--network",
        NETWORK,
        "--cap-add",
        "NET_ADMIN",
        CLIENT_IMAGE,
        timeout=60,
    )


def container_ip(name: str) -> str:
    """
    Args:
        name: One of our containers.

    Returns:
        Its address on our network.
    """
    out = docker(
        "inspect",
        "-f",
        '{{(index .NetworkSettings.Networks "' + NETWORK + '").IPAddress}}',
        owned(name),
    ).stdout.strip()
    if not out:
        raise HarnessError(f"{name} has no address on {NETWORK}")
    return out


def sh_in(container: str, script: str, *, timeout: int = 120, check: bool = True) -> str:
    """
    Run a shell script in one of our containers.

    Args:
        container: Its name.
        script: The script (bash).
        timeout: Seconds.
        check: Raise on a non-zero exit.

    Returns:
        Its standard output.
    """
    return docker(
        "exec", owned(container), "bash", "-lc", script, timeout=timeout, check=check
    ).stdout


def install_fixtures() -> None:
    """Copy the fixtures into the server and serve the echo application over git://."""
    print("[setup] copying the fixtures")
    docker("cp", str(INTEGRATION_DIR / "fixtures"), f"{SERVER}:/root/fixtures")
    sh_in(
        SERVER, "chown -R root:root /root/fixtures && git config --global --add safe.directory '*'"
    )
    sh_in(
        SERVER,
        "cd /root/fixtures/echo-app && npm install --package-lock-only >/dev/null 2>&1; "
        "git init -q && git config user.email noust-it@example.com && "
        "git config user.name 'Noust Integration' && git add -A && "
        "git commit -q -m 'initial fixture'",
        timeout=120,
    )
    sh_in(
        SERVER,
        "git daemon --reuseaddr --export-all --base-path=/root/fixtures "
        "--listen=127.0.0.1 --detach /root/fixtures",
    )
    sh_in(SERVER, f"git ls-remote --exit-code {ECHO_URL} HEAD >/dev/null")


# ---------------------------------------------------------------------------
# The environment a scenario runs in
# ---------------------------------------------------------------------------


@dataclass
class Env:
    """
    The running containers and what setup learnt. Every command is kept as evidence, in order,
    whichever container ran it.
    """

    server_ip: str
    client_ip: str
    cdn_ip: str
    wheel: Path
    evidence: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)

    def _run(
        self, container: str, tag: str, script: str, timeout: int, check: bool, label: str | None
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["docker", "exec", container, "bash", "-lc", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=docker_env(),
        )
        block = [f"[{tag}] $ {label or script}"]
        if proc.stdout:
            block.append(proc.stdout.rstrip("\n"))
        if proc.stderr.strip():
            block.append("--- stderr ---")
            block.append(proc.stderr.rstrip("\n"))
        block.append(f"(exit={proc.returncode})")
        self.evidence.append("\n".join(block))
        if check and proc.returncode != 0:
            raise AssertionError(
                f"command failed (exit {proc.returncode}): [{tag}] {label or script}"
            )
        return proc

    def srv(
        self, script: str, *, timeout: int = 60, check: bool = True, label: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a script in the server."""
        return self._run(SERVER, "server", script, timeout, check, label)

    def cli(
        self, script: str, *, timeout: int = 60, check: bool = True, label: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a script in the client (the visitor)."""
        return self._run(CLIENT, "client", script, timeout, check, label)

    def dev(
        self, script: str, *, timeout: int = 60, check: bool = True, label: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a script in the device of another family (AlmaLinux), when a scenario started one."""
        return self._run(TRUST, "device", script, timeout, check, label)

    def note(self, text: str) -> None:
        """Add a line of explanation to the evidence."""
        self.evidence.append(f"# {text}")

    def check(self, condition: bool, message: str) -> None:
        """Fail the scenario with ``message`` unless ``condition``."""
        if not condition:
            raise AssertionError(message)


EnvFn = Callable[[Env], None]
SCENARIOS: list[tuple[str, EnvFn]] = []


def scenario(name: str) -> Callable[[EnvFn], EnvFn]:
    """Register a scenario under ``name``."""

    def decorator(fn: EnvFn) -> EnvFn:
        SCENARIOS.append((name, fn))
        return fn

    return decorator


def refresh_noust(wheel: Path) -> None:
    """
    Put the working tree's Noust into the running server, over the one that is there.

    Args:
        wheel: The freshly built wheel.
    """
    docker("cp", str(wheel), f"{SERVER}:/tmp/{wheel.name}")
    sh_in(
        SERVER,
        f"/opt/noust/bin/pip install --quiet --no-deps --force-reinstall '/tmp/{wheel.name}'",
        timeout=300,
    )


def versions_of(env: Env) -> dict[str, str]:
    """
    Read the version of every program the scenarios lean on.

    Args:
        env: The environment.

    Returns:
        Program to version text.
    """
    probes = {
        "debian": (SERVER, ". /etc/os-release && echo $PRETTY_NAME"),
        "nginx": (SERVER, "nginx -v 2>&1"),
        "kernel": (SERVER, "uname -r"),
        "noust": (SERVER, "noust --version 2>&1 | head -1"),
        "docker (inside)": (SERVER, "docker --version"),
        "curl (client)": (CLIENT, "curl -V | head -1"),
    }
    found: dict[str, str] = {}
    for key, (container, command) in probes.items():
        out = docker("exec", container, "bash", "-lc", command, check=False)
        found[key] = (out.stdout or out.stderr).strip()
    return found


# ---------------------------------------------------------------------------
# Helpers the scenarios share
# ---------------------------------------------------------------------------

#: A password for root in the server, so a client can open an SSH session and fail at others.
ROOT_PASSWORD = "noust-test-root-pw"


def json_of(proc: subprocess.CompletedProcess[str], what: str) -> Any:
    """Parse the JSON a Noust command printed, failing the scenario with the text when it is not."""
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(f"{what} did not print JSON: {proc.stdout!r} {proc.stderr!r}") from exc


def ensure_app(env: Env, domain: str, *, extra: str = "") -> None:
    """
    Deploy the echo application under ``domain`` unless it is already deployed.

    Args:
        env: The environment.
        domain: The application's domain (plain HTTP: there is no certificate authority here).
        extra: More options for ``noust create``.
    """
    if env.srv(f"noust status {domain}", check=False).returncode == 0:
        return
    env.srv(
        f"noust create -d {domain} -s {ECHO_URL} -t nodejs --no-ssl {extra}",
        timeout=DEPLOY_TIMEOUT,
    )


def ask(env: Env, domain: str, path: str = "/", *, extra: str = "", label: str | None = None):
    """
    Request ``domain`` from the client, straight at the server's address.

    Args:
        env: The environment.
        domain: The site's name, sent as the Host header.
        path: The path asked for.
        extra: More curl options.
        label: What to call it in the evidence.

    Returns:
        The finished command (the client's curl; a failed connection is exit code 7).
    """
    return env.cli(
        f"curl -sS {extra} -H 'Host: {domain}' http://{env.server_ip}{path}",
        check=False,
        label=label,
    )


def fail2ban_jails(env: Env) -> dict[str, dict[str, Any]]:
    """
    Read fail2ban through Noust's own parser, so that parser meets the real tool's output.

    Returns:
        Jail name to its status as ``noust server security fail2ban status --json`` reports it.
    """
    state = json_of(
        env.srv("noust server security fail2ban status --json"), "fail2ban status --json"
    )
    return {jail["name"]: jail for jail in state["jails"]}


def wait_for(condition: Callable[[], bool], what: str, *, timeout: float = 25.0) -> None:
    """Poll ``condition`` until it holds or ``timeout`` passes (the scenario fails then)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {what}")


def is_banned(env: Env, jail: str, address: str) -> bool:
    """Whether fail2ban, as Noust reads it, has ``address`` banned in ``jail``."""
    found = fail2ban_jails(env).get(jail)
    return bool(found and address in found["banned"])


def neighbour(address: str, offset: int) -> str:
    """An address on the same /24 as ``address``, ``offset`` further along."""
    prefix, _, last = address.rpartition(".")
    return f"{prefix}.{int(last) + offset}"


# ---------------------------------------------------------------------------
# fail2ban: the web jails and the SSH one, with real traffic
# ---------------------------------------------------------------------------


@scenario("fail2ban_jails")
def scenario_fail2ban(env: Env) -> None:
    """Real fail2ban and nginx: bans happen, and a declared proxy or a CDN is never banned."""
    server = env.server_ip
    operator = neighbour(env.client_ip, 101)
    env.cli(f"ip addr show dev eth0 | grep -q ' {operator}/' || ip addr add {operator}/16 dev eth0")
    env.note(
        f"client {env.client_ip} is the offender, {env.cdn_ip} stands for a CDN or a declared "
        f"proxy, {operator} is the operator connected over SSH when fail2ban is installed"
    )

    # -- prelude: a clean slate for what this scenario touches
    env.srv(
        "if command -v fail2ban-client >/dev/null; then "
        "rm -f /etc/fail2ban/jail.d/noust.local /etc/fail2ban/jail.d/noust-nginx.local; "
        "systemctl restart fail2ban; fi",
        label="(a re-run) back to the distribution's own fail2ban settings",
    )
    # The site this scenario ends with declares a proxy; a re-run on the same server would start
    # with the offender already in the jails' ignore list (the proxies of every site are in it).
    env.srv(
        "if noust status jail.test >/dev/null 2>&1; then noust delete jail.test -y; fi",
        label="(a re-run) start without the site the last run left behind a proxy",
        timeout=300,
    )
    env.srv(f"echo 'root:{ROOT_PASSWORD}' | chpasswd")
    env.srv("apt-get update -qq 2>&1 | tail -1", timeout=180)

    # -- the operator's SSH session, open before fail2ban exists
    env.cli(f"pkill -f 'ssh -f -N -b {operator}'", check=False)
    env.cli(
        f"sshpass -p '{ROOT_PASSWORD}' ssh -f -N -b {operator} -o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null -o ServerAliveInterval=5 root@{server}"
    )
    wait_for(
        lambda: (
            operator in env.srv("ss -Htn state established '( sport = :22 )'", check=False).stdout
        ),
        "the operator's SSH session to be established",
    )

    # -- install fail2ban the way an operator does (F1: its unit answers late)
    install = env.srv("noust server security fail2ban install -y", timeout=400)
    env.check(
        "watches SSH" in install.stdout, f"install did not say it watches SSH: {install.stdout}"
    )
    ssh_ignore = env.srv("fail2ban-client get sshd ignoreip").stdout
    env.check(
        operator in ssh_ignore,
        f"the operator's open SSH session {operator} is not in the sshd jail's ignore list:\n"
        f"{ssh_ignore}",
    )
    env.check("sshd" in fail2ban_jails(env), "Noust does not see the sshd jail it just installed")

    # -- SSH: an offender is banned, who is connected is not
    def ssh_attempts(source: str, tries: int) -> None:
        for number in range(1, tries + 1):
            env.cli(
                f"sshpass -p wrong{number} ssh -b {source} -o StrictHostKeyChecking=no "
                "-o UserKnownHostsFile=/dev/null -o ConnectTimeout=4 "
                "-o PreferredAuthentications=password -o PubkeyAuthentication=no "
                f"nobody2@{server} true",
                check=False,
                label=f"ssh as nobody2 from {source}, try {number}",
            )

    ssh_attempts(env.client_ip, 7)
    wait_for(lambda: is_banned(env, "sshd", env.client_ip), "the offender to be banned in sshd")
    refused = env.cli(
        f"timeout 8 ssh -b {env.client_ip} -o BatchMode=yes -o ConnectTimeout=4 "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null nobody2@{server} true",
        check=False,
    )
    env.check(
        "refused" in refused.stderr.lower() or "timed out" in refused.stderr.lower(),
        f"a banned address could still reach sshd: {refused.stderr!r}",
    )
    ssh_attempts(operator, 8)
    time.sleep(2)
    env.check(
        not is_banned(env, "sshd", operator),
        "an address that was connected over SSH when fail2ban was installed was banned",
    )
    env.srv(f"noust server security fail2ban unban {env.client_ip} --jail sshd")
    env.check(not is_banned(env, "sshd", env.client_ip), "unban did not lift the sshd ban")

    # -- the web jails: a site with a password
    # The offender's last attempts can leave a connection half-open on the server's side (its
    # login grace time is two minutes); "who is connected over SSH" would count it, and the web
    # jails would never ban the one address this scenario wants banned.
    wait_for(
        lambda: (
            env.client_ip
            not in env.srv(
                "ss -tn state established '( sport = :22 )'", label="who is connected over SSH now"
            ).stdout
        ),
        "the offender's leftover SSH connections to close",
        timeout=150,
    )
    ensure_app(env, "jail.test")
    env.srv("printf 'good-pass' | noust app policy user add jail.test ana --password-stdin")
    env.srv("noust app policy set jail.test --password --realm Staff")
    env.srv("noust server security fail2ban web on -y")
    env.check(
        {"nginx-http-auth", "nginx-botsearch", "nginx-limit-req"} <= set(fail2ban_jails(env)),
        "Noust does not see the three web jails it turned on",
    )
    right = ask(env, "jail.test", extra="-u ana:good-pass -o /dev/null -w '%{http_code}'")
    env.check(
        right.stdout.strip() == "200",
        f"a protected site refuses the right password ({right.stdout!r}): nginx cannot read "
        "its accounts file",
    )

    def wrong_passwords(source: str, tries: int, forwarded: str = "") -> None:
        header = f"-H 'X-Forwarded-For: {forwarded}'" if forwarded else ""
        for number in range(1, tries + 1):
            ask(
                env,
                "jail.test",
                extra=f"--interface {source} {header} -u ana:wrong{number} -o /dev/null",
                label=f"wrong password {number} from {source} {header}".strip(),
            )

    wrong_passwords(env.client_ip, 7)
    wait_for(
        lambda: is_banned(env, "nginx-http-auth", env.client_ip),
        "the offender to be banned by nginx-http-auth",
    )
    gone = ask(env, "jail.test", extra="--max-time 4 -o /dev/null")
    env.check(gone.returncode != 0, "a banned address could still reach the site")
    env.srv(f"noust server security fail2ban unban {env.client_ip}")

    # -- a proxy declared AFTER the jails were turned on (F3), and a CDN's own traffic
    env.srv(
        "noust app policy set jail.test --real-ip custom --real-ip-header X-Forwarded-For "
        f"--trusted-proxy {env.cdn_ip}"
    )
    http_auth = env.srv("fail2ban-client get nginx-http-auth ignoreip").stdout
    env.check(
        env.cdn_ip in http_auth,
        f"a proxy declared after the jails were on is not in their ignore list:\n{http_auth}",
    )
    wrong_passwords(env.cdn_ip, 8)
    time.sleep(3)
    env.check(
        not is_banned(env, "nginx-http-auth", env.cdn_ip),
        "a declared proxy was banned for its own wrong passwords",
    )

    # -- behind the proxy: an address nginx logs is banned, Cloudflare's edge never is
    offender, edge = "203.0.113.77", "173.245.48.9"
    wrong_passwords(env.cdn_ip, 7, forwarded=offender)
    wait_for(
        lambda: is_banned(env, "nginx-http-auth", offender),
        "a visitor behind the proxy to be banned under the address the proxy forwarded",
    )
    wrong_passwords(env.cdn_ip, 7, forwarded=edge)
    time.sleep(3)
    env.check(
        not is_banned(env, "nginx-http-auth", edge),
        "an address in Cloudflare's published ranges was banned",
    )
    env.check(not is_banned(env, "nginx-http-auth", env.cdn_ip), "the proxy itself was banned")
    env.srv(f"noust server security fail2ban unban {offender}", check=False)

    # -- a site created after the jails started is read (F4): scanners
    ensure_app(env, "bots.test")
    for path in (
        "wp-login.php",
        "wp-signup.php",
        "phpmyadmin/",
        "pma/",
        "cgi-bin/x",
        "mysqladmin/",
        "webmail/",
    ):
        ask(env, "bots.test", f"/{path}", extra="-o /dev/null", label=f"scanner asks for /{path}")
    wait_for(
        lambda: is_banned(env, "nginx-botsearch", env.client_ip),
        "the scanner to be banned by nginx-botsearch on a site created after the jails started",
    )
    env.srv(f"noust server security fail2ban unban {env.client_ip}")

    # -- a client over a rate limit
    env.srv("noust app policy set bots.test --rate-limit 2r/s --rate-burst 2")
    env.cli(
        "seq 1 60 | xargs -P 20 -I{} curl -s -o /dev/null -w '%{http_code}\\n' "
        f"-H 'Host: bots.test' http://{server}/ | sort | uniq -c",
        timeout=120,
        check=False,
    )
    wait_for(
        lambda: is_banned(env, "nginx-limit-req", env.client_ip),
        "the client over the rate limit to be banned by nginx-limit-req",
    )
    env.srv(f"noust server security fail2ban unban {env.client_ip}")

    # -- and off again
    env.srv("noust server security fail2ban web off -y")
    env.check(
        "nginx-http-auth" not in fail2ban_jails(env),
        "the web jails are still there after turning them off",
    )
    env.srv("fail2ban-client unban --all", check=False)
    env.cli(f"pkill -f 'ssh -f -N -b {operator}'", check=False)


# ---------------------------------------------------------------------------
# HTTP/3: the socket's own site, with nginx built with http_v3 and curl speaking QUIC
# ---------------------------------------------------------------------------


def self_signed(env: Env, name: str, *domains: str) -> None:
    """
    Make a self-signed certificate for ``domains`` in the server and give it to Noust.

    Args:
        env: The environment.
        name: What Noust calls the upload.
        *domains: The names it covers (the first is the common name).
    """
    sans = ",".join(f"DNS:{domain}" for domain in domains)
    env.srv(
        f"mkdir -p /root/pki && cd /root/pki && openssl req -x509 -newkey ec "
        f"-pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 30 -subj /CN={domains[0]} "
        f"-addext subjectAltName={sans} -keyout {name}.key -out {name}.crt 2>/dev/null"
    )
    env.srv(f"noust cert remove-upload {name}", check=False)
    env.srv(f"noust cert upload {name} --cert /root/pki/{name}.crt --key /root/pki/{name}.key")


def over_h3(
    env: Env, domain: str, path: str = "/", *, extra: str = ""
) -> subprocess.CompletedProcess[str]:
    """
    Ask for ``domain`` over HTTP/3 only (curl never falls back to TCP).

    Returns:
        The finished curl: its output is ``<http_version> <status> <body>``.
    """
    return env.cli(
        f"curl -sSk --http3-only --max-time 6 {extra} --resolve {domain}:443:{env.server_ip} "
        f"-w ' [%{{http_version}} %{{http_code}}]' https://{domain}{path}",
        check=False,
        label=f"curl --http3-only https://{domain}{path}",
    )


@scenario("http3_quic_anchor")
def scenario_http3(env: Env) -> None:
    """A real nginx 1.26 serves two sites over QUIC through the one socket site Noust writes."""
    self_signed(env, "h3cert", "h3a.test", "h3b.test")
    for domain in ("h3a.test", "h3b.test", "plain.test"):
        ensure_app(env, domain)
        env.srv(f"noust app policy set {domain} --no-http3", check=False, label="(a re-run) off")
    for domain in ("h3a.test", "h3b.test"):
        env.srv(f"noust cert use {domain} --upload h3cert")

    support = json_of(env.srv("noust app policy http3 h3a.test --json"), "policy http3")
    env.check(
        support["available"] and support["version"].startswith("1.2"),
        f"Noust says this nginx cannot serve HTTP/3: {support}",
    )
    env.check(support["udp_allowed"] is not False, f"Noust thinks UDP 443 is closed: {support}")

    # -- the first site: the anchor is written, nginx accepts it, QUIC answers
    env.srv("noust app policy set h3a.test --http3")
    anchor = env.srv("cat /etc/nginx/sites-available/00-noust-quic").stdout
    env.check("listen 443 quic reuseport;" in anchor, "the socket's own site has no reuseport")
    env.srv("nginx -t")
    sites = env.srv("grep -rl 'quic reuseport' /etc/nginx/ | wc -l").stdout.strip()
    env.check(sites == "1", f"reuseport must be in exactly one file, found {sites}")
    answer = over_h3(env, "h3a.test")
    env.check(
        "[3 200]" in answer.stdout and '"service":"app"' in answer.stdout,
        f"h3a.test did not answer over HTTP/3: {answer.stdout!r} {answer.stderr!r}",
    )
    altsvc = env.cli(
        f"curl -sSkI --http2 --resolve h3a.test:443:{env.server_ip} https://h3a.test/"
    ).stdout.lower()
    env.check('alt-svc: h3=":443"' in altsvc, f"TCP answers do not advertise HTTP/3:\n{altsvc}")

    # -- a name nobody serves over QUIC is refused in the handshake
    stranger = over_h3(env, "stranger.test")
    env.check(stranger.returncode != 0, "an unknown name completed a QUIC handshake")

    # -- a second site shares the socket
    env.srv("noust app policy set h3b.test --http3")
    env.srv("nginx -t")
    both = [over_h3(env, domain) for domain in ("h3a.test", "h3b.test")]
    env.check(
        all("[3 200]" in answer.stdout for answer in both),
        f"two sites sharing the socket did not both answer: {[a.stdout for a in both]}",
    )

    # -- turning it off for one leaves the other; for the last takes the socket away
    env.srv("noust app policy set h3a.test --no-http3")
    env.check(over_h3(env, "h3a.test").returncode != 0, "h3a.test still answers over QUIC")
    env.check("[3 200]" in over_h3(env, "h3b.test").stdout, "h3b.test lost HTTP/3 with h3a's")
    env.srv("noust app policy set h3b.test --no-http3")
    env.srv("test ! -e /etc/nginx/sites-available/00-noust-quic")
    udp = env.srv("ss -Hlun | grep -c ':443 ' || true").stdout.strip()
    env.check(udp == "0", f"{udp} UDP 443 sockets remain after the last site left HTTP/3")
    env.check(over_h3(env, "h3b.test").returncode != 0, "QUIC still answers with no socket site")
    still = env.cli(
        f"curl -sSk --resolve h3b.test:443:{env.server_ip} -o /dev/null -w '%{{http_code}}' "
        "https://h3b.test/"
    )
    env.check(still.stdout.strip() == "200", "HTTPS over TCP broke when HTTP/3 was turned off")

    # -- a site with no certificate: kept, said so, and nothing is served over QUIC for it
    ensure_app(env, "plain.test")
    asked = env.srv("noust app policy set plain.test --http3")
    env.check(
        "once this site has a certificate" in asked.stdout,
        f"the answer does not say that HTTP/3 waits for a certificate:\n{asked.stdout}",
    )
    env.srv("! grep -q quic /etc/nginx/sites-available/plain.test")
    env.srv("noust app policy set plain.test --no-http3")
    env.srv("test ! -e /etc/nginx/sites-available/00-noust-quic")


# ---------------------------------------------------------------------------
# Prefix routes, redirects and per-route passwords, through a real nginx
# ---------------------------------------------------------------------------


@dataclass
class Answer:
    """What a request to the server came back with, read from the echo application's JSON."""

    status: int
    service: str = ""
    url: str = ""
    prefix: str = ""
    location: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"{self.status} service={self.service!r} url={self.url!r} "
            f"prefix={self.prefix!r} location={self.location!r}"
        )


def probe(
    env: Env,
    domain: str,
    path: str,
    *,
    auth: str = "",
    tls: bool = False,
    extra: str = "",
) -> Answer:
    """
    Request ``domain`` from the client with the path exactly as given (curl does not tidy it).

    Args:
        env: The environment.
        domain: The Host header.
        path: The path and query.
        auth: ``user:password`` for basic authentication.
        tls: Use HTTPS (the certificate is not checked).
        extra: More curl options.

    Returns:
        The status and what the echo application said it received.
    """
    scheme = "https" if tls else "http"
    resolve = f"--resolve {domain}:443:{env.server_ip}" if tls else f"-H 'Host: {domain}'"
    target = f"{domain}" if tls else env.server_ip
    credentials = f"-u {auth}" if auth else ""
    proc = env.cli(
        f"curl -sk --path-as-is -D - {resolve} {credentials} {extra} {scheme}://{target}{path}",
        check=False,
        label=f"{'https' if tls else 'http'} {domain}{path}" + (f" as {auth}" if auth else ""),
    )
    head, _, body = proc.stdout.replace("\r\n", "\n").partition("\n\n")
    lines = head.splitlines()
    headers = {
        name.strip().lower(): value.strip()
        for name, _, value in (line.partition(":") for line in lines[1:])
    }
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError) as exc:
        raise AssertionError(f"no HTTP status for {domain}{path}: {proc.stdout!r}") from exc
    seen: dict[str, Any] = {}
    try:
        seen = json.loads(body)
    except ValueError:
        pass
    return Answer(
        status=status,
        service=str(seen.get("service", "")),
        url=str(seen.get("url", "")),
        prefix=str(seen.get("headers", {}).get("x-forwarded-prefix", "")),
        location=headers.get("location", ""),
        headers=headers,
    )


def expect(env: Env, answer: Answer, what: str, **wanted: Any) -> None:
    """Fail the scenario unless ``answer`` has each wanted attribute (``status``, ``url``...)."""
    for name, value in wanted.items():
        got = getattr(answer, name)
        env.check(got == value, f"{what}: wanted {name}={value!r}, got {answer}")


@scenario("routes_redirects_and_per_route_auth")
def scenario_routes(env: Env) -> None:
    """Prefix routes: another service, a redirect, a password only at a prefix, headers."""
    pw = "ana:good-pass"
    for domain in ("routes.test", "open.test", "spa.test"):
        if env.srv(f"noust status {domain}", check=False).returncode == 0:
            env.srv(f"noust delete {domain} -y", label="(a re-run) start from nothing")
    ensure_app(env, "routes.test")
    ensure_app(env, "open.test")
    for unit, name, port in (
        ("noust-test-echo-api", "api", 4001),
        ("noust-test-echo-blog", "blog", 4002),
    ):
        env.srv(
            f"systemctl is-active {unit} || systemd-run --quiet --unit={unit} "
            f"-E ECHO_NAME={name} -E PORT={port} node {ECHO_REPO}/server.js"
        )
    for domain in ("routes.test", "open.test"):
        env.srv(f"printf 'good-pass' | noust app policy user add {domain} ana --password-stdin")
    env.srv("noust app policy set routes.test --password --realm Staff")
    routes = [
        "/hooks --open",
        "/hooks/secret --password",
        "/api --proxy-port 4001 --strip",
        "/blog --proxy-port 4002",
        "/docs --redirect-to https://docs.example.org/ --code 308",
        "/old --redirect-to /new --code 301",
        "/gone --redirect-to https://elsewhere.example.org/landing --drop-path",
        "/embed --header 'X-Frame-Options: ALLOWALL'",
    ]
    for route in routes:
        env.srv(f"noust app policy route add routes.test {route}")
    env.srv("noust app policy route add open.test /admin --password")

    def checks() -> None:
        # -- the site asks for the password, except where a route lifts it
        expect(env, probe(env, "routes.test", "/"), "the site's password", status=401)
        expect(env, probe(env, "routes.test", "/", auth=pw), "the right password", status=200)
        expect(env, probe(env, "routes.test", "/hooks"), "an open prefix", status=200)
        expect(
            env, probe(env, "routes.test", "/hooks/anything"), "under an open prefix", status=200
        )
        expect(
            env,
            probe(env, "routes.test", "/hooks/secret"),
            "a password inside an open one",
            status=401,
        )
        expect(env, probe(env, "routes.test", "/hooks/secret", auth=pw), "...with it", status=200)
        expect(
            env, probe(env, "routes.test", "/hooksx"), "a longer name is not the prefix", status=401
        )
        # -- another service, with and without the prefix
        expect(
            env,
            probe(env, "routes.test", "/api/users?x=1", auth=pw),
            "a prefix handed to a service, stripped",
            status=200,
            service="api",
            url="/users?x=1",
            prefix="/api",
        )
        expect(
            env,
            probe(env, "routes.test", "/api", auth=pw),
            "the bare prefix",
            service="api",
            url="/",
        )
        expect(
            env,
            probe(env, "routes.test", "/blog/post", auth=pw),
            "a prefix handed over whole",
            service="blog",
            url="/blog/post",
            prefix="",
        )
        expect(
            env,
            probe(env, "routes.test", "/api/users"),
            "a service behind the site's password",
            status=401,
        )
        # -- redirects
        expect(
            env,
            probe(env, "routes.test", "/docs/a/b?x=1"),
            "a redirect keeps the rest of the path and the query",
            status=308,
            location="https://docs.example.org/a/b?x=1",
        )
        expect(
            env,
            probe(env, "routes.test", "/old/page?y=2"),
            "a redirect to a path on the site",
            status=301,
            location="http://routes.test/new/page?y=2",
        )
        expect(
            env,
            probe(env, "routes.test", "/gone/a/b"),
            "a redirect that drops the path",
            status=301,
            location="https://elsewhere.example.org/landing",
        )
        # -- headers at a prefix replace the site's
        expect(
            env, probe(env, "routes.test", "/embed/x", auth=pw), "a header at a prefix", status=200
        )
        env.check(
            probe(env, "routes.test", "/embed/x", auth=pw).headers.get("x-frame-options")
            == "ALLOWALL"
            and probe(env, "routes.test", "/", auth=pw).headers.get("x-frame-options")
            == "SAMEORIGIN",
            "the route's X-Frame-Options does not replace the site's only under its prefix",
        )
        # -- the certificate authority's path is never closed
        env.check(
            probe(env, "routes.test", "/.well-known/acme-challenge/probe").status != 401,
            "the ACME path asked for the password",
        )
        # -- a password only at a prefix, and every spelling of that prefix
        expect(env, probe(env, "open.test", "/"), "the rest of the site is open", status=200)
        expect(env, probe(env, "open.test", "/administrator"), "a longer name", status=200)
        expect(env, probe(env, "open.test", "/ADMIN"), "nginx paths are case sensitive", status=200)
        for spelling in (
            "/admin",
            "/admin/",
            "/admin/x",
            "/admin?x=1",
            "/%61dmin",
            "/%61dmin/x",
            "/./admin",
            "//admin",
            "/a/../admin",
            "/admin%2fx",
        ):
            expect(
                env,
                probe(env, "open.test", spelling),
                f"{spelling} is the protected prefix",
                status=401,
            )
        expect(
            env,
            probe(env, "open.test", "/admin", auth=pw),
            "the prefix with the password",
            status=200,
        )

    checks()

    # -- a redeploy renders the site again from the store: the routes are still there
    env.srv("noust update routes.test", timeout=DEPLOY_TIMEOUT)
    env.srv("noust update open.test", timeout=DEPLOY_TIMEOUT)
    checks()

    # -- the same routes over HTTPS
    self_signed(env, "routescert", "routes.test", "open.test")
    for domain in ("routes.test", "open.test"):
        env.srv(f"noust cert use {domain} --upload routescert")
    expect(
        env,
        probe(env, "routes.test", "/api/users?x=1", auth=pw, tls=True),
        "https route",
        service="api",
        url="/users?x=1",
    )
    expect(
        env,
        probe(env, "routes.test", "/old/page", tls=True),
        "a redirect over https",
        status=301,
        location="https://routes.test/new/page",
    )
    expect(env, probe(env, "open.test", "/%61dmin", tls=True), "the prefix over https", status=401)
    # a request over plain HTTP is sent to HTTPS before any route is looked at
    expect(env, probe(env, "open.test", "/admin"), "http goes to https first", status=301)

    # -- a single-page site: an open prefix under a protected one asks again at the fallback
    ensure_static = env.srv("noust status spa.test", check=False)
    if ensure_static.returncode != 0:
        env.srv(
            "noust create -d spa.test -s /root/fixtures/static-site -t static --no-ssl",
            timeout=DEPLOY_TIMEOUT,
        )
    env.srv("printf 'good-pass' | noust app policy user add spa.test ana --password-stdin")
    env.srv("noust app policy set spa.test --password")
    env.srv("noust app policy route add spa.test /hooks --open")
    expect(
        env,
        probe(env, "spa.test", "/hooks/anything"),
        "the single-page fallback judges the prefix again, and fails closed",
        status=401,
    )


# ---------------------------------------------------------------------------
# The history of a site's file, its diff and its restore, on a real nginx
# ---------------------------------------------------------------------------

#: Runs inside the server: save a text as a site's configuration through the one writer the
#: console's editor uses (``WebServerManager.replace_site_config``), and print what happened.
EDIT_SITE = r"""
import sys
from noust.core.exceptions import NoustError
from noust.managers.nginx_manager import NginxManager
domain, path = sys.argv[1], sys.argv[2]
text = open(path).read()
try:
    NginxManager().replace_site_config(domain, text)
    print("SAVED")
except NoustError as exc:
    print("REFUSED:", exc.message)
    print((exc.details or "").strip())
"""


def edit_site(env: Env, domain: str, transform: str) -> str:
    """
    Save the site's file after a ``sed`` expression, the way the console's editor does.

    Args:
        env: The environment.
        domain: The site.
        transform: A sed script applied to the current text.

    Returns:
        What the editor's writer printed: ``SAVED`` or ``REFUSED: ...`` with nginx's words.
    """
    env.srv(
        f"sed '{transform}' /etc/nginx/sites-available/{domain} > /tmp/candidate.conf; "
        f"cat > /tmp/edit_site.py <<'PY'\n{EDIT_SITE}\nPY"
    )
    return env.srv(f"/opt/noust/bin/python /tmp/edit_site.py {domain} /tmp/candidate.conf").stdout


def history(env: Env, domain: str) -> dict[str, Any]:
    """The site's history as ``noust site history --json`` reports it."""
    return json_of(env.srv(f"noust site history {domain} --json"), "site history --json")


@scenario("site_history_and_restore")
def scenario_site_history(env: Env) -> None:
    """A site's file: every write kept, a hand edit recovered, a bad text refused, a restore."""
    domain = "hist.test"
    if env.srv(f"noust status {domain}", check=False).returncode == 0:
        env.srv(f"noust delete {domain} -y", label="(a re-run) start from nothing")
    ensure_app(env, domain)
    served = f"curl -sI -H 'Host: {domain}' http://127.0.0.1/"

    # -- what a deploy wrote is the first version, and it is the file now
    first = history(env, domain)
    env.check(
        {v["source"] for v in first["versions"]} == {"generated"}
        and first["versions"][0]["current"],
        f"a deployed site should have generated versions only, the newest current: {first}",
    )
    env.check(
        not first["edited_outside"] and not first["pending_reload"], f"drift at the start: {first}"
    )

    # -- an edit through the editor's writer: tested by the real nginx, kept, effective after a reload
    saved = edit_site(
        env, domain, 's|^\\(\\s*\\)server_tokens off;|&\\n\\1add_header X-History "v2" always;|'
    )
    env.check(saved.startswith("SAVED"), f"nginx refused a valid edit: {saved}")
    env.srv("systemctl reload nginx")
    env.check("x-history: v2" in env.srv(served).stdout.lower(), "the edited header is not served")
    after_edit = history(env, domain)
    env.check(
        [v["source"] for v in after_edit["versions"]][:2] == ["edit", "generated"],
        f"the edit was not kept as a version: {after_edit['versions']}",
    )

    # -- a text nginx refuses: nginx's own words, nothing changes, no version is made
    before = env.srv(f"sha256sum /etc/nginx/sites-available/{domain}").stdout
    refused = edit_site(env, domain, "s|^\\(\\s*\\)server_tokens off;|\\1server_tokenz off;|")
    env.check(
        refused.startswith("REFUSED")
        and "server_tokenz" in refused
        and "unknown directive" in refused,
        f"nginx's own words are missing from the refusal: {refused!r}",
    )
    env.check(
        env.srv(f"sha256sum /etc/nginx/sites-available/{domain}").stdout == before,
        "a refused text changed the file",
    )
    env.check(
        len(history(env, domain)["versions"]) == len(after_edit["versions"]),
        "a refused text made a version",
    )
    # ...and a text that would make nginx read a file it must not (root runs the test)
    hostile = edit_site(env, domain, "s|^\\(\\s*\\)server_tokens off;|&\\n\\1include /etc/shadow;|")
    env.check(
        hostile.startswith("REFUSED"), f"a text that includes /etc/shadow was accepted: {hostile!r}"
    )

    # -- a hand edit nobody told Noust about: drift, then the edit survives in the history
    # A file written within three seconds of the workers starting is the reload's own (the grace
    # the history allows), and the last reload was Noust's own write just above.
    time.sleep(5)
    env.srv(f'sed -i \'s|X-History "v2"|X-History "by-hand"|\' /etc/nginx/sites-available/{domain}')
    drifted = history(env, domain)
    env.check(drifted["edited_outside"], f"a hand edit was not noticed: {drifted}")
    env.check(
        drifted["pending_reload"],
        f"a file written after nginx loaded its configuration is not pending: {drifted}",
    )
    env.srv("systemctl reload nginx")
    env.check(
        "x-history: by-hand" in env.srv(served).stdout.lower(),
        "the hand edit is not served after a reload",
    )
    env.check(not history(env, domain)["pending_reload"], "still pending after a reload")

    # -- the next time Noust renders the site it overwrites the file; the hand edit is kept first
    # as 'found' (an update leaves the site alone: this is a change of policy, a new name, a
    # certificate, anything that renders it again)
    env.srv(f"noust app policy set {domain} --noindex")
    sources = [v["source"] for v in history(env, domain)["versions"]]
    env.check("found" in sources, f"the hand edit was lost to the deploy: {sources}")
    found = next(v for v in history(env, domain)["versions"] if v["source"] == "found")
    env.check(
        "by-hand" in env.srv(f"noust site diff {domain} {found['version']}").stdout,
        "the diff does not show the hand edit that was recovered",
    )
    env.check(
        "x-history" not in env.srv(served).stdout.lower(), "the deploy kept the hand edit's header"
    )

    # -- restoring the hand edit: the same write as any edit, a version of its own, effective on reload
    env.srv(f"noust site restore {domain} {found['version']} --reload")
    env.check(
        "x-history: by-hand" in env.srv(served).stdout.lower(), "the restored version is not served"
    )
    restored = history(env, domain)
    env.check(
        restored["versions"][0]["source"] == "restore",
        f"a restore is not a version of its own: {restored['versions'][0]}",
    )
    env.check(restored["versions"][0]["current"], "the restored version is not the current one")
    env.check(not restored["edited_outside"], "a restore reads as an edit made outside Noust")
    env.check(
        "No difference" in env.srv(f"noust site diff {domain} {found['version']}").stdout,
        "the restored file differs from the version it came from",
    )

    # -- a version nginx would refuse now cannot be put back, and the file stays as it is
    env.srv(
        "mkdir -p /etc/ssl/noust-hist && cd /etc/ssl/noust-hist && openssl req -x509 -newkey ec "
        "-pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3 -subj /CN=hist.test "
        "-keyout hist.key -out hist.crt 2>/dev/null"
    )
    listener = (
        "server { listen 8443 ssl; server_name hist.test; "
        "ssl_certificate /etc/ssl/noust-hist/hist.crt; "
        "ssl_certificate_key /etc/ssl/noust-hist/hist.key; return 204; }"
    )
    saved = edit_site(env, domain, "$a\\" + listener)
    env.check(saved.startswith("SAVED"), f"a valid second listener was refused: {saved}")
    with_cert = history(env, domain)["versions"][0]["version"]
    env.srv("rm -rf /etc/ssl/noust-hist")  # the files that version names are gone
    current = env.srv(f"sha256sum /etc/nginx/sites-available/{domain}").stdout
    bad = env.srv(f"noust site restore {domain} {with_cert}", check=False)
    env.check(
        bad.returncode != 0 and "noust-hist" in (bad.stdout + bad.stderr),
        f"a restore nginx cannot load was accepted or hid nginx's words: {bad.stdout!r} {bad.stderr!r}",
    )
    env.check(
        env.srv(f"sha256sum /etc/nginx/sites-available/{domain}").stdout == current,
        "a refused restore changed the file",
    )
    # -- and the way back: the version before the listener loads again, so the server is whole
    # (one site whose files are gone makes nginx refuse every other site's change too)
    good = history(env, domain)["versions"][1]["version"]
    env.srv(f"noust site restore {domain} {good} --reload")
    env.srv("nginx -t")


# ---------------------------------------------------------------------------
# Forward-auth against the real identity providers
# ---------------------------------------------------------------------------


def start_provider(
    name: str, image: str, args: list[str], files: dict[str, str] | None = None
) -> None:
    """
    Run an identity provider in the server's network namespace, so it listens on the server's
    loopback where the presets expect it (nginx asks ``http://127.0.0.1:<port>``).

    Args:
        name: The container (one of ours).
        image: Its image, by digest.
        args: Its command line.
        files: Container path to the text copied in before it starts (test credentials only).
    """
    remove_container(name)
    docker(
        "create",
        "--name",
        owned(name),
        "--label",
        LABEL,
        "--network",
        f"container:{SERVER}",
        image,
        *args,
        timeout=300,
    )
    if files:
        # One tar on standard input: it creates the directories a path needs (a volume's
        # directory does not exist in a container that was created and never started).
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for target, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(target.lstrip("/"))
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
        copied = subprocess.run(
            ["docker", "cp", "-", f"{owned(name)}:/"],
            input=archive.getvalue(),
            capture_output=True,
            timeout=60,
            env=docker_env(),
        )
        if copied.returncode != 0:
            raise HarnessError(f"copying files into {name} failed: {copied.stderr.decode()}")
    docker("start", name)


def provider_version(name: str, *command: str) -> str:
    """The version a provider prints, for the evidence."""
    return docker("exec", owned(name), *command, check=False).stdout.strip()


@scenario("forward_auth_oauth2_proxy")
def scenario_forward_auth_oauth2(env: Env) -> None:
    """The oauth2-proxy preset against a real oauth2-proxy: sign-in landing, identity, fail closed."""
    domain = "fa.test"
    if env.srv(f"noust status {domain}", check=False).returncode == 0:
        env.srv(f"noust delete {domain} -y", label="(a re-run) start from nothing")
    ensure_app(env, domain)
    env.srv("htpasswd -nbB ana good-pass > /tmp/oauth2.htpasswd")
    htpasswd = env.srv("cat /tmp/oauth2.htpasswd").stdout
    cookie_secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    start_provider(
        OAUTH2,
        OAUTH2_IMAGE,
        [
            "--http-address=127.0.0.1:4180",
            "--provider=oidc",
            "--client-id=noust-test",
            "--client-secret=not-used",
            f"--cookie-secret={cookie_secret}",
            "--cookie-secure=false",
            "--email-domain=*",
            "--oidc-issuer-url=http://127.0.0.1:9/dex",
            "--skip-oidc-discovery=true",
            "--login-url=http://127.0.0.1:9/auth",
            "--redeem-url=http://127.0.0.1:9/token",
            "--oidc-jwks-url=http://127.0.0.1:9/keys",
            "--insecure-oidc-skip-issuer-verification=true",
            f"--redirect-url=http://{domain}/oauth2/callback",
            "--set-xauthrequest=true",
            "--reverse-proxy=true",
            "--upstream=static://202",
            "--whitelist-domain=.test",
            "--htpasswd-file=/htpasswd",
        ],
        {"/htpasswd": htpasswd},
    )
    wait_for(
        lambda: (
            env.srv(
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:4180/ping", check=False
            ).stdout.strip()
            == "200"
        ),
        "oauth2-proxy to answer",
    )
    env.versions["oauth2-proxy"] = provider_version(OAUTH2, "/bin/oauth2-proxy", "--version")

    # -- the preset, as an operator picks it; the service is asked once before it is kept
    env.srv(f"noust app policy set {domain} --forward-auth-preset oauth2-proxy")
    shown = json_of(env.srv(f"noust app policy show {domain} --json"), "policy show")
    auth = shown["policy"]["forward_auth"]
    env.check(
        auth["url"] == "http://127.0.0.1:4180/oauth2/auth"
        and auth["sign_in"] == "/oauth2/start?rd=",
        f"the preset was not kept as it is: {auth}",
    )

    # -- not signed in: sent to the sign-in with the page asked for
    first = probe(env, domain, "/")
    expect(
        env,
        first,
        "not signed in",
        status=302,
        location="http://fa.test/oauth2/start?rd=http://fa.test/",
    )

    # -- the sign-in lands on oauth2-proxy through the same site (the preset's note): no loop
    env.srv(f"noust app policy route add {domain} /oauth2 --proxy-port 4180 --open")
    landing = probe(env, domain, "/oauth2/start?rd=http://fa.test/")
    env.check(
        landing.status == 302 and landing.location.startswith("http://127.0.0.1:9/auth"),
        f"the sign-in did not reach oauth2-proxy and its redirect to the provider: {landing}",
    )

    # -- signed in (basic authentication is oauth2-proxy's own session loader): the application is
    # told who came, in headers a client cannot choose
    ana = probe(env, domain, "/who", auth="ana:good-pass")
    env.check(ana.status == 200, f"a signed-in visitor was refused: {ana}")
    seen = json_of(
        env.cli(f"curl -s -u ana:good-pass -H 'Host: {domain}' http://{env.server_ip}/who"),
        "echo",
    )["headers"]
    env.check(
        seen.get("x-auth-request-user") == "ana",
        f"the application was not told who came: {seen}",
    )
    spoof = json_of(
        env.cli(
            f"curl -s -u ana:good-pass -H 'X-Auth-Request-User: admin' "
            f"-H 'X-Auth-Request-Email: admin@example.test' -H 'Host: {domain}' "
            f"http://{env.server_ip}/who"
        ),
        "echo",
    )["headers"]
    env.check(
        spoof.get("x-auth-request-user") == "ana" and "admin@example.test" not in json.dumps(spoof),
        f"a visitor chose their own identity headers: {spoof}",
    )
    expect(
        env,
        probe(env, domain, "/", auth="ana:wrong"),
        "wrong credentials are not signed in",
        status=302,
    )
    expect(
        env,
        probe(env, domain, "/", extra="-H 'X-Auth-Request-User: admin'"),
        "a spoofed identity header alone opens nothing",
        status=302,
    )

    # -- the service goes down: everybody is refused, the application is not served
    docker("stop", OAUTH2)
    down = probe(env, domain, "/", auth="ana:good-pass")
    env.check(
        down.status in (500, 502, 503, 504),
        f"the site was served while oauth2-proxy was down: {down}",
    )
    refused = env.srv(
        f"noust app policy set {domain} --forward-auth-url http://127.0.0.1:4180/oauth2/auth "
        "--forward-auth-header X-Auth-Request-User --noindex",
        check=False,
    )
    env.check(
        refused.returncode != 0 and "does not answer" in refused.stdout + refused.stderr,
        f"an authentication service that is down was accepted: {refused.stdout!r}",
    )
    docker("start", OAUTH2)
    wait_for(
        lambda: probe(env, domain, "/who", auth="ana:good-pass").status == 200,
        "the site to be served again once oauth2-proxy is back",
    )
    remove_container(OAUTH2)


AUTHELIA_CONFIG = """\
theme: 'light'
server:
  address: 'tcp://127.0.0.1:9091/'
  endpoints:
    authz:
      auth-request:
        implementation: 'AuthRequest'
log:
  level: 'debug'
identity_validation:
  reset_password:
    jwt_secret: 'noust-test-reset-secret-not-used-anywhere-else-1234567890'
authentication_backend:
  file:
    path: '/config/users.yml'
access_control:
  default_policy: 'one_factor'
session:
  secret: 'noust-test-session-secret-not-used-anywhere-else-1234567890'
  cookies:
    - name: 'authelia_session'
      domain: 'example.test'
      authelia_url: 'https://auth.example.test'
      expiration: '1h'
      inactivity: '10m'
storage:
  encryption_key: 'noust-test-storage-key-not-used-anywhere-else-1234567890'
  local:
    path: '/config/db.sqlite3'
notifier:
  filesystem:
    filename: '/config/notification.txt'
"""

AUTHELIA_USERS = """\
users:
  ana:
    disabled: false
    displayname: 'Ana Test'
    password: '{digest}'
    email: 'ana@example.test'
    groups:
      - 'staff'
"""


@scenario("forward_auth_authelia")
def scenario_forward_auth_authelia(env: Env) -> None:
    """The authelia preset against a real Authelia: a session opens the site, nothing else does."""
    domain = "app.example.test"
    if env.srv(f"noust status {domain}", check=False).returncode == 0:
        env.srv(f"noust delete {domain} -y", label="(a re-run) start from nothing")
    ensure_app(env, domain)
    self_signed(env, "autheliacert", domain, "auth.example.test")
    env.srv(f"noust cert use {domain} --upload autheliacert")

    # Authelia wants an argon2 hash: its own tool makes it (a container that exits at once)
    digest_out = docker(
        "run",
        "--rm",
        "--label",
        LABEL,
        "--name",
        owned(f"{PREFIX}-authelia-hash"),
        AUTHELIA_IMAGE,
        "authelia",
        "crypto",
        "hash",
        "generate",
        "argon2",
        "--password",
        "good-pass",
        timeout=120,
    ).stdout
    digest = next(
        line.split("Digest:", 1)[1].strip() for line in digest_out.splitlines() if "Digest:" in line
    )
    start_provider(
        AUTHELIA,
        AUTHELIA_IMAGE,
        [],
        {
            "/config/configuration.yml": AUTHELIA_CONFIG,
            "/config/users.yml": AUTHELIA_USERS.format(digest=digest),
        },
    )
    wait_for(
        lambda: (
            env.srv(
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9091/api/health",
                check=False,
            ).stdout.strip()
            == "200"
        ),
        "Authelia to answer its health check",
        timeout=90,
    )
    env.versions["authelia"] = provider_version(AUTHELIA, "authelia", "--version")

    # -- the preset: its portal is another site, so the sign-in is the operator's to give
    env.srv(
        f"noust app policy set {domain} --forward-auth-preset authelia "
        "--forward-auth-sign-in 'https://auth.example.test/?rd='"
    )
    asked = probe(env, domain, "/", tls=True)
    expect(
        env,
        asked,
        "not signed in",
        status=302,
        location="https://auth.example.test/?rd=https://app.example.test/",
    )

    # -- a session made the way the portal makes one
    def sign_in() -> None:
        login = env.srv(
            "curl -s -i -X POST http://127.0.0.1:9091/api/firstfactor "
            "-H 'Content-Type: application/json' -H 'X-Forwarded-Proto: https' "
            "-H 'X-Forwarded-Host: auth.example.test' -H 'X-Forwarded-URI: /' "
            '-d \'{"username":"ana","password":"good-pass","keepMeLoggedIn":false}\' '
            "| grep -i '^set-cookie: authelia_session' | sed 's/^[Ss]et-[Cc]ookie: //; s/;.*//' "
            "> /tmp/authelia.cookie; wc -c < /tmp/authelia.cookie",
            label="sign in as ana on the portal (the cookie goes to a file, not to the evidence)",
        )
        env.check(int(login.stdout.strip() or 0) > 30, "Authelia did not open a session for ana")
        cookie_text = docker("exec", SERVER, "cat", "/tmp/authelia.cookie").stdout.strip()
        docker("exec", "-i", CLIENT, "sh", "-c", "cat > /tmp/cookie", input_text=cookie_text)

    sign_in()

    def asking(extra: str = "", *, signed_in: bool = True) -> dict[str, Any]:
        header = '-H "Cookie: $(cat /tmp/cookie)"' if signed_in else ""
        proc = env.cli(
            f"curl -sk -D /tmp/h {header} {extra} --resolve {domain}:443:{env.server_ip} "
            f"-w '\\n%{{http_code}}' https://{domain}/who",
            check=False,
            label=f"https://{domain}/who {'with the session' if signed_in else 'without it'} {extra}",
        )
        body, _, code = proc.stdout.rpartition("\n")
        seen: dict[str, Any] = {}
        try:
            seen = json.loads(body)
        except ValueError:
            pass
        return {"status": int(code or 0), "headers": seen.get("headers", {}), "raw": body}

    mine = asking()
    env.check(mine["status"] == 200, f"a signed-in visitor was refused: {mine}")
    headers = mine["headers"]
    env.check(
        headers.get("remote-user") == "ana"
        and headers.get("remote-groups") == "staff"
        and headers.get("remote-email") == "ana@example.test"
        and headers.get("remote-name") == "Ana Test",
        f"the application was not told who came: { {k: v for k, v in headers.items() if k.startswith('remote')} }",
    )
    spoofed = asking("-H 'Remote-User: admin' -H 'Remote-Groups: admins'")
    env.check(
        spoofed["headers"].get("remote-user") == "ana"
        and spoofed["headers"].get("remote-groups") == "staff",
        f"a visitor chose their own identity: {spoofed['headers']}",
    )
    anonymous = asking("-H 'Remote-User: admin'", signed_in=False)
    env.check(anonymous["status"] == 302, f"a spoofed header alone opened the site: {anonymous}")
    forged = env.cli(
        f"curl -sk -o /dev/null -w '%{{http_code}}' -H 'Cookie: authelia_session=forged' "
        f"--resolve {domain}:443:{env.server_ip} https://{domain}/who"
    )
    env.check(forged.stdout.strip() == "302", f"a forged session opened the site: {forged.stdout}")
    posted = env.cli(
        f"curl -sk -X POST -d x=1 -o /dev/null -w '%{{http_code}}' "
        '-H "Cookie: $(cat /tmp/cookie)" '
        f"--resolve {domain}:443:{env.server_ip} https://{domain}/who"
    )
    env.check(posted.stdout.strip() == "200", f"a signed-in POST was refused: {posted.stdout}")

    # -- Authelia down: the site is closed to everybody, the session included
    docker("stop", AUTHELIA)
    env.check(asking()["status"] in (500, 502, 503, 504), "the site was served with Authelia down")
    docker("start", AUTHELIA)
    wait_for(
        lambda: (
            env.srv(
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9091/api/health",
                check=False,
            ).stdout.strip()
            == "200"
        ),
        "Authelia to answer again",
        timeout=90,
    )
    # its sessions lived in its memory: ana signs in again, and the site opens
    env.check(asking()["status"] == 302, "a session outlived Authelia's restart")
    sign_in()
    env.check(asking()["status"] == 200, "the site did not open again once Authelia was back")
    remove_container(AUTHELIA)


# ---------------------------------------------------------------------------
# Coolify templates, converted and deployed with a real Docker Compose
# ---------------------------------------------------------------------------

#: The commit of Coolify's repository the catalogue was read at, and the templates used with the
#: sha256 of each (Coolify and its templates are Apache-2.0: fetched when the scenario runs,
#: never kept in this repository).
COOLIFY_COMMIT = "e79347295ae36881eb239b1962d26d118e35a6bc"
COOLIFY_TEMPLATES = {
    "it-tools": "d7244ebe98e02183511f07feb95f8f442a95f2c419faa5e89ef9d9966cc0bc39",
    "listmonk": "356793cad36ff8d303775347668176eb4e6e840e4cd2bc64c963a26663c5b903",
    "umami": "d65b0d45133c26c36f1aaff7a462800cf032ebe2c1908de8ab49f78d07419007",
    "vaultwarden": "abb5e007a8b43a72fbddaea7b6df30845bd103af04ada437fb49cf2347817f09",
    "wordpress-with-mysql": "819478592769b7b93795474dd4c1544899b1d548ddcd26d1f42c6578ff19905f",
    "gitea-with-mariadb": "b13daecbf63cc5f5c82d6aafaad9da2db82c0125de8f4a2414914dbc5ec8f9e7",
    "docmost": "6daa37d2517dd9a49b999d0c09cb3a83683e58df424d14c429b8759198cc5059",
    "portainer": "ab7ab9db225c11baf2f89214ab1c82fa68f2eb685580c865e38e0c77b5d6a6f7",
}


def fetch_template(env: Env, name: str) -> str:
    """
    Download a template at the pinned commit into the server and check it is the one that was
    read when the scenario was written.

    Returns:
        The path of the file in the server.
    """
    path = f"/root/coolify/{name}.yaml"
    url = f"https://raw.githubusercontent.com/coollabsio/coolify/{COOLIFY_COMMIT}/templates/compose/{name}.yaml"
    env.srv(f"mkdir -p /root/coolify && curl -fsS {url} -o {path}", timeout=120)
    digest = env.srv(f"sha256sum {path}").stdout.split()[0]
    env.check(
        digest == COOLIFY_TEMPLATES[name],
        f"{name}.yaml is not the template this scenario was written against ({digest})",
    )
    return path


def project_of(domain: str) -> str:
    """The Compose project Noust gives an application: its name, the domain with dots as dashes."""
    return domain.replace(".", "-")


def clean_stack(env: Env, domain: str) -> None:
    """
    Start a template from nothing: the application, its containers and its volumes.

    ``noust delete`` keeps a stack's named volumes on purpose (they are the data), and a database
    that kept the previous run's volume refuses the passwords the next run generates. The volumes
    are in the inner Docker, the server container's own, so removing them touches nothing else.
    """
    if env.srv(f"noust status {domain}", check=False).returncode == 0:
        env.srv(f"noust delete {domain} -y", label="(a re-run) start from nothing", timeout=300)
    label = f"label=com.docker.compose.project={project_of(domain)}"
    env.srv(
        f"docker ps -aq --filter {label} | xargs -r docker rm -f; "
        f"docker volume ls -q --filter {label} | xargs -r docker volume rm",
        label="(a re-run) the stack's leftovers in the inner Docker",
        timeout=120,
    )


def deploy_template(
    env: Env, name: str, domain: str, *, give: dict[str, str] | None = None
) -> dict[str, Any]:
    """
    Import a template and deploy it the way the documentation says, and return the import.

    Args:
        env: The environment.
        name: The template.
        domain: The name to serve it on.
        give: Variables the template needs and the operator must provide, by name. The import
            has to have said so: they are looked for among the ones it reports as required.

    Returns:
        ``noust compose import --json``: what was made, never the values.
    """
    clean_stack(env, domain)
    template = fetch_template(env, name)
    folder = f"/root/coolify/{name}-stack"
    env.srv(f"rm -rf {folder}")
    imported = json_of(
        env.srv(f"noust compose import {template} --domain {domain} --out {folder} --json"),
        "compose import --json",
    )
    env.check(imported["deployable"], f"the converted {name} is refused: {imported['refused']}")
    env.check(
        env.srv(f"stat -c '%a' {folder}/.env").stdout.strip() == "600",
        "the generated values are not readable by their owner alone",
    )
    said = {item["name"] for item in imported["required"]}
    env.check(
        set(give or {}) <= said,
        f"the import should have said which variables to give ({sorted(give or {})}), said {sorted(said)}",
    )
    for variable, value in (give or {}).items():
        env.srv(f"printf '%s=%s\\n' {variable} {value} >> {folder}/.env", label=f"give {variable}")
    env.srv(
        f"docker compose -f {folder}/docker-compose.yml config -q", label="docker compose config"
    )
    env.srv(f"noust create -d {domain} -s {folder} --no-ssl", timeout=1500)
    return imported


def stack_state(env: Env, domain: str) -> str:
    """``docker ps`` of the inner Docker, one line per container."""
    return env.srv(
        f"docker ps --filter label=com.docker.compose.project={project_of(domain)} "
        "--format '{{.Names}} {{.Status}} {{.Ports}}'",
        check=False,
    ).stdout


def through_nginx(env: Env, domain: str, path: str = "/", *, extra: str = "") -> str:
    """What the site answers for ``path``, asked of nginx on the server's own loopback."""
    return env.srv(
        f"curl -s {extra} -H 'Host: {domain}' http://127.0.0.1{path}", check=False
    ).stdout


def served(env: Env, domain: str, path: str, expect: str, what: str, *, timeout: float) -> None:
    """Wait until the site's ``path`` answers with ``expect`` in it."""
    wait_for(lambda: expect in through_nginx(env, domain, path), what, timeout=timeout)


def check_published_on_loopback(env: Env, domain: str, container_port: int) -> None:
    """The web service is on a loopback port of its own, not on 80, which nginx holds."""
    ports = stack_state(env, domain)
    env.check(
        "127.0.0.1:" in ports
        and f"->{container_port}/tcp" in ports
        and ":80->" not in ports.replace("127.0.0.1", ""),
        f"{domain} must be published on a loopback port of its own, not on 80: {ports}",
    )


@scenario("coolify_templates_simple")
def scenario_coolify_simple(env: Env) -> None:
    """Templates of one container: an address variable, a long generated secret, a header-only route."""
    tools = deploy_template(env, "it-tools", "tools.test")
    served(env, "tools.test", "/", "IT Tools", "it-tools to be served through nginx", timeout=120)
    env.check(
        tools["routed"] == ["it-tools", 80], f"the web service was not found: {tools['routed']}"
    )
    check_published_on_loopback(env, "tools.test", 80)

    # no routing entry: the header names the port; ADMIN_TOKEN is a 64-character secret made here
    vault = deploy_template(env, "vaultwarden", "vault.test")
    env.check(vault["routed"] == ["vaultwarden", 80], f"routed {vault['routed']}")
    wait_for(
        lambda: through_nginx(env, "vault.test", "/alive").strip().startswith('"20'),
        "vaultwarden to answer /alive",
        timeout=240,
    )
    served(env, "vault.test", "/", "Vaultwarden Web", "vaultwarden's web vault", timeout=60)
    length = env.srv(
        f"docker exec {project_of('vault.test')}-vaultwarden-1 sh -c 'echo ${{#ADMIN_TOKEN}}'"
    ).stdout.strip()
    env.check(length == "64", f"ADMIN_TOKEN should be the 64 characters made for it, not {length}")


@scenario("coolify_templates_postgres")
def scenario_coolify_postgres(env: Env) -> None:
    """Templates with PostgreSQL: generated users, a one-off installer, shared defaults, a required variable."""
    deploy_template(env, "listmonk", "lists.test")
    served(
        env,
        "lists.test",
        "/admin/login",
        "listmonk",
        "listmonk's login page (the application reaches its database to start)",
        timeout=240,
    )
    setup = env.srv(
        "docker inspect -f '{{.State.ExitCode}}' lists-test-listmonk-initial-database-setup-1"
    ).stdout.strip()
    env.check(setup == "0", f"the one-off service that installs the schema exited {setup}")
    env.check(
        "invalid session" in through_nginx(env, "lists.test", "/api/health"),
        "listmonk's API should answer (and ask for a session) through the site",
    )

    # $POSTGRES_DB is read plainly by the application and given a default by the database
    stats = deploy_template(env, "umami", "stats.test")
    env.check(
        any(v["name"] == "POSTGRES_DB" and v["origin"] == "template" for v in stats["variables"]),
        f"the default another service declares was not made a value: {stats['variables']}",
    )
    wait_for(
        lambda: '"ok":true' in through_nginx(env, "stats.test", "/api/heartbeat"),
        "umami to answer its heartbeat",
        timeout=300,
    )
    signed = env.srv(
        "curl -s -X POST -H 'Content-Type: application/json' -H 'Host: stats.test' "
        '-d \'{"username":"admin","password":"umami"}\' http://127.0.0.1/api/auth/login',
        check=False,
    ).stdout
    env.check(
        '"token"' in signed,
        "umami cannot sign in, so it does not reach its database (DATABASE_URL built from "
        f"the template's variables): {signed[:200]}",
    )

    # secrets made for the stacks are in .env only, never in a unit's journal
    leaked = env.srv(
        'journalctl --no-pager -u lists-test | grep -c "$(grep SERVICE_PASSWORD_POSTGRES '
        '/root/coolify/listmonk-stack/.env | cut -d= -f2)" || true'
    ).stdout.split()
    env.check(leaked[-1] == "0", "a generated password reached the unit's journal")


@scenario("coolify_templates_required")
def scenario_coolify_required(env: Env) -> None:
    """A template with a variable it cannot start without: the import says so, the operator gives it."""
    imported = deploy_template(env, "docmost", "docs.test", give={"MAIL_DRIVER": "smtp"})
    env.check(
        imported["routed"] == ["docmost", 3000],
        f"the web service was not found: {imported['routed']}",
    )
    wait_for(
        lambda: "docmost" in through_nginx(env, "docs.test", "/", extra="-L").lower(),
        "docmost to be served",
        timeout=360,
    )
    # the application, its database and its cache are all up behind the one domain
    up = stack_state(env, "docs.test")
    env.check(
        up.count("healthy") >= 3,
        f"the three services of the stack should be healthy (the database and the cache gate the application): {up}",
    )


@scenario("coolify_templates_mysql")
def scenario_coolify_mysql(env: Env) -> None:
    """Templates with MySQL and MariaDB: a database the service only depends_on, a published SSH port."""
    deploy_template(env, "wordpress-with-mysql", "blog.test")
    # WordPress redirects to its installer, by the name it is served on: follow it by hand, since
    # the name only resolves inside the server.
    wait_for(
        lambda: "/wp-admin/install.php" in through_nginx(env, "blog.test", "/", extra="-i"),
        "WordPress to send a visitor to its installer (it reaches its database)",
        timeout=360,
    )
    page = through_nginx(env, "blog.test", "/wp-admin/install.php")
    env.check(
        "Error establishing a database connection" not in page and "WordPress" in page,
        "WordPress does not reach its MySQL with the user and password made for it",
    )

    deploy_template(env, "gitea-with-mariadb", "git.test")
    # until it is set up, Gitea serves its installer at /
    wait_for(
        lambda: "Gitea" in through_nginx(env, "git.test", "/"),
        "gitea's installer to be served",
        timeout=360,
    )
    published = stack_state(env, "git.test")
    env.check(
        "0.0.0.0:22222->22/tcp" in published,
        f"the SSH port the template publishes should be read as 22222:22, not as a number: {published}",
    )


@scenario("coolify_template_refused")
def scenario_coolify_refused(env: Env) -> None:
    """A template that mounts the Docker socket is converted, refused, and nothing is deployed."""
    template = fetch_template(env, "portainer")

    refused = env.srv(f"noust compose import {template} --domain admin.test --json", check=False)

    env.check(refused.returncode == 1, "a template mounting the Docker socket must exit 1")
    body = json_of(refused, "compose import --json")
    env.check(
        body["deployable"] is False and any("Docker socket" in p for p in body["refused"]),
        f"portainer should be refused for the socket: {body['refused']}",
    )
    env.check(
        env.srv("noust status admin.test", check=False).returncode != 0,
        "a refused template must not leave an application behind",
    )


# ---------------------------------------------------------------------------
# The server's own certificate authority, for names that only resolve inside a private network
# ---------------------------------------------------------------------------

WIKI = "wiki.home.arpa"


def put_file(container: str, path: str, text: str) -> None:
    """Write a text file into one of our containers (the public certificate of an authority)."""
    docker("exec", "-i", owned(container), "sh", "-c", f"cat > {path}", input_text=text)


def tls_get(
    env: Env,
    host: str,
    port: int,
    *,
    cacert: str | None = None,
    where: str = "client",
    label: str | None = None,
) -> tuple[int, str]:
    """
    Open ``https://host:port/`` the way a visitor does, with curl, resolving ``host`` to the server.

    Args:
        env: The environment.
        host: The name asked for (and so the name the certificate is judged against).
        port: The port.
        cacert: A file of authorities to trust instead of the system's (None: the system's).
        where: ``client`` (the visitor, BoringSSL), ``server`` (OpenSSL) or ``device`` (AlmaLinux).
        label: What to call it in the evidence.

    Returns:
        curl's exit code (60 is a certificate that did not verify, 0 is trusted and answered) and
        what it printed: the status and curl's verify result (0 is verified).
    """
    target = "127.0.0.1" if where == "server" else env.server_ip
    command = (
        f"curl -sS -o /dev/null -w '%{{http_code}} verify=%{{ssl_verify_result}}' "
        f"{f'--cacert {cacert} ' if cacert else ''}"
        f"--resolve {host}:{port}:{target} https://{host}:{port}/"
    )
    run_in = {"client": env.cli, "server": env.srv, "device": env.dev}[where]
    proc = run_in(command, check=False, label=label)
    return proc.returncode, proc.stdout.strip()


def live_fingerprint(env: Env, host: str) -> str:
    """The SHA-256 of the certificate nginx serves for ``host`` right now, asked of it on 443."""
    out = env.srv(
        f"openssl s_client -connect 127.0.0.1:443 -servername {host} </dev/null 2>/dev/null "
        "| openssl x509 -noout -fingerprint -sha256",
        label=f"the certificate nginx serves for {host}",
    ).stdout.strip()
    return out.split("=", 1)[1].upper()


#: Where the test TLS server of the scenario keeps its process id: it is stopped by that number,
#: because `pkill -f` would also match the shell that runs it.
TLS_SERVER_PID = "/tmp/noust-s-server.pid"


def serve_tls(env: Env, port: int, certificate: str, key: str, *, label: str) -> None:
    """Start an OpenSSL test server on ``port`` in the server container, with this certificate."""
    stop_tls(env)
    env.srv(
        f"nohup openssl s_server -accept {port} -cert {certificate} -key {key} -www "
        f">/tmp/noust-s-server.log 2>&1 & echo $! > {TLS_SERVER_PID}; sleep 1",
        label=label,
    )


def stop_tls(env: Env) -> None:
    """Stop the test TLS server, if one runs."""
    env.srv(
        f"if [ -f {TLS_SERVER_PID} ]; then kill $(cat {TLS_SERVER_PID}) 2>/dev/null; "
        f"rm -f {TLS_SERVER_PID}; fi; true",
        label="stop the test TLS server",
    )


def clean_authority(env: Env) -> None:
    """Start the scenario from a server with no authority, no trust of it and no application."""
    env.srv(
        "noust cert ca trust --here --remove; "
        f"if noust status {WIKI} >/dev/null 2>&1; then noust delete {WIKI} -y; fi; "
        "if noust cert ca status --json | grep -q '\"subject\"'; then noust cert ca remove -y --force; fi; "
        "for name in $(noust cert uploads --json | python3 -c "
        "\"import json,sys; print(' '.join(c['name'] for c in json.load(sys.stdin)['certificates'] "
        "if c['name'].startswith('internal-')))\"); do noust cert remove-upload $name -y; done",
        label="(a re-run) start with no authority",
        timeout=300,
    )


@scenario("internal_ca_private_names")
def scenario_internal_ca(env: Env) -> None:
    """The server signs for a private name, devices trust it, a forged public name fails, it renews."""
    clean_authority(env)
    stop_tls(env)
    ensure_app(env, WIKI)

    # -- make it: what the certificate itself says, and who can read the key
    made = json_of(env.srv("noust cert ca init --json"), "cert ca init --json")
    env.check("home.arpa" in made["permitted_dns"], f"the defaults are missing: {made}")
    text = env.srv("openssl x509 -in /etc/noust/ca/ca.crt -noout -text").stdout
    env.check("Name Constraints: critical" in text, "the limit must be a critical extension")
    env.check("CA:TRUE, pathlen:0" in text, "the authority must not be able to make intermediates")
    env.check(
        env.srv("stat -c '%a %U' /etc/noust/ca/ca.key /etc/noust/ca").stdout.split()
        == ["600", "root", "700", "root"],
        "the key must be root's alone, in a directory of root's alone",
    )
    again = env.srv("noust cert ca init", check=False)
    env.check(again.returncode == 1, "a second authority must be refused, not made over the first")

    # -- serve an application with it, and what nginx really presents
    served = json_of(
        env.srv(f"noust cert use {WIKI} --internal --days 30 --json"), "cert use --internal"
    )
    env.check(served["name"] == "internal-wiki-home-arpa", f"served {served['name']}")
    leaf = env.srv(
        "openssl x509 -in /etc/noust/certs/internal-wiki-home-arpa/fullchain.pem -noout -text"
    ).stdout
    env.check("TLS Web Server Authentication" in leaf, "the leaf must be able to serve")
    env.check("TLS Web Client Authentication" not in leaf, "the leaf must not authenticate clients")
    env.check("CA:FALSE" in leaf, "the leaf must not be a CA")
    root = env.srv("noust cert ca root").stdout
    env.check("PRIVATE KEY" not in root, "the root that is handed out must never hold the key")
    put_file(CLIENT, "/root/ca.crt", root)
    verdict = tls_get(env, WIKI, 443, label="the visitor, before trusting anything")
    env.check(
        verdict[0] == 60,
        f"a visitor that trusts nothing must refuse the certificate (curl 60), got {verdict}",
    )
    code, said = tls_get(env, WIKI, 443, cacert="/root/ca.crt", label="the visitor with the root")
    env.check(
        (code, said) == (0, "200 verify=0"), f"a trusting visitor must get the page: {code} {said}"
    )
    listed = json_of(env.srv("noust cert ca list --json"), "cert ca list --json")["certificates"]
    env.check(
        live_fingerprint(env, WIKI).replace(":", "") == listed[0]["fingerprint"].replace(":", ""),
        "nginx must present the certificate the authority kept",
    )

    # -- a leaked key still cannot impersonate a public name, judged by a real client
    forge = (
        "cd /root && rm -rf forge && mkdir forge && cd forge && "
        "openssl ecparam -genkey -name prime256v1 -noout -out leaf.key && "
        "openssl req -new -key leaf.key -subj /CN=www.bank.com -out leaf.csr && "
        "printf 'basicConstraints=critical,CA:FALSE\\nextendedKeyUsage=serverAuth\\n"
        "subjectAltName=DNS:www.bank.com\\n' > leaf.ext && "
        "openssl x509 -req -in leaf.csr -CA /etc/noust/ca/ca.crt -CAkey /etc/noust/ca/ca.key "
        "-CAcreateserial -days 30 -extfile leaf.ext -out leaf.crt"
    )
    env.srv(forge, label="sign a leaf for www.bank.com with the authority's key (as a thief would)")
    refused = env.srv(
        "openssl verify -CAfile /etc/noust/ca/ca.crt /root/forge/leaf.crt", check=False
    )
    env.check(
        refused.returncode != 0
        and "permitted subtree violation" in refused.stdout + refused.stderr,
        "openssl must refuse the forged leaf for the name constraint",
    )
    serve_tls(
        env, 8443, "/root/forge/leaf.crt", "/root/forge/leaf.key", label="serve the forged leaf"
    )
    code, said = tls_get(
        env, "www.bank.com", 8443, cacert="/root/ca.crt", label="the visitor asks for www.bank.com"
    )
    env.check(
        code == 60,
        f"a client that trusts the root must still refuse a certificate for www.bank.com: {code} {said}",
    )
    # the control: the same server, a leaf the authority was really asked for, a permitted name
    env.srv("noust cert ca issue control ok.home.arpa --days 5 --json")
    serve_tls(
        env,
        8444,
        "/etc/noust/certs/internal-control/fullchain.pem",
        "/etc/noust/certs/internal-control/privkey.pem",
        label="serve the control leaf for ok.home.arpa",
    )
    code, said = tls_get(
        env, "ok.home.arpa", 8444, cacert="/root/ca.crt", label="the visitor asks for ok.home.arpa"
    )
    env.check(
        (code, said) == (0, "200 verify=0"),
        f"the control must verify, or the failure above proves nothing: {code} {said}",
    )
    stop_tls(env)
    env.srv("noust cert remove-upload internal-control -y")

    # -- trust this server, and stop
    env.srv("noust cert ca trust --here")
    env.check(
        env.srv(
            "test -s /usr/local/share/ca-certificates/noust-internal-ca.crt && "
            "ls /etc/ssl/certs | grep -c noust-internal-ca"
        ).stdout.strip()
        == "1",
        "update-ca-certificates must have linked the root into the bundle",
    )
    env.check(
        tls_get(env, WIKI, 443, where="server", label="the server, trusting the root")
        == (0, "200 verify=0"),
        "a server that trusts the root must reach the site by its private name",
    )
    on = json_of(env.srv("noust cert ca status --json"), "status")
    env.check(on["trusted_here"] is True, "status must say this server trusts it")
    env.srv("noust cert ca trust --here --remove")
    env.check(
        tls_get(env, WIKI, 443, where="server", label="the server, after forgetting the root")[0]
        == 60,
        "a server that stopped trusting the root must refuse the site again",
    )

    # -- the steps we print, followed literally on two other kinds of device
    fingerprint = json_of(env.srv("noust cert ca status --json"), "status")["authority"][
        "fingerprint"
    ]
    for target, where in (("alpine", "client"), ("rhel", "device")):
        if target == "rhel":
            docker("rm", "-f", owned(TRUST), check=False)
            docker(
                "run", "-d", "--name", owned(TRUST), "--label", LABEL, "--network", NETWORK,
                TRUST_IMAGE, "sleep", "infinity", timeout=300,
            )  # fmt: skip
            put_file(TRUST, "/root/ca.crt", root)
        steps = json_of(env.srv(f"noust cert ca trust {target} --json"), f"trust {target}")["steps"]
        commands = [step[2:] for step in steps if step.startswith("$ ")]
        env.check(commands, f"the {target} guide has no command to follow")
        run_in = env.cli if where == "client" else env.dev
        for command in commands:
            run_in(
                f"cd /root && {command.replace('sudo ', '')}",
                label=f"follow the {target} guide: {command}",
                timeout=240,
            )
        code, said = tls_get(
            env,
            WIKI,
            443,
            where=where,
            label=f"{target}: the page, trusting nothing but the system store",
        )
        env.check(
            (code, said) == (0, "200 verify=0"),
            f"the {target} steps did not make the device trust the root: {code} {said}",
        )
        shown = run_in(
            "openssl x509 -noout -fingerprint -sha256 -in /root/ca.crt", check=False
        ).stdout
        if shown:
            env.check(
                fingerprint.upper() in shown.upper(),
                f"the {target} fingerprint to compare is wrong",
            )
    docker("rm", "-f", owned(TRUST), check=False)

    # -- renewal by the hourly pass, with nginx reloaded and not restarted
    env.srv(f"noust cert use {WIKI} --internal --days 1 --json", label="a certificate due at once")
    env.srv("noust cert ca issue steady steady.home.arpa --days 90 --json")
    steady = {
        c["name"]: c for c in json_of(env.srv("noust cert ca list --json"), "list")["certificates"]
    }
    before = live_fingerprint(env, WIKI)
    pid = env.srv("systemctl show nginx -p MainPID --value").stdout.strip()
    ran = env.srv(
        "/opt/noust/bin/python -m noust.managers.network_maintenance",
        label="the hourly network pass",
    )
    env.check(
        "Certificate renewed: internal-wiki-home-arpa" in ran.stdout,
        f"the pass did not renew the certificate that was due: {ran.stdout!r}",
    )
    env.check("steady" not in ran.stdout, "a certificate with 90 days left must not be touched")
    after = live_fingerprint(env, WIKI)
    env.check(after != before, "nginx still presents the old certificate: it was not reloaded")
    env.check(
        env.srv("systemctl show nginx -p MainPID --value").stdout.strip() == pid,
        "nginx was restarted, not reloaded",
    )
    now = {
        c["name"]: c for c in json_of(env.srv("noust cert ca list --json"), "list")["certificates"]
    }
    env.check(
        now["internal-steady"]["fingerprint"] == steady["internal-steady"]["fingerprint"],
        "the certificate with time left was signed again",
    )
    code, said = tls_get(
        env, WIKI, 443, cacert="/root/ca.crt", label="the visitor after the renewal"
    )
    env.check(
        (code, said) == (0, "200 verify=0"),
        f"the renewed certificate does not verify: {code} {said}",
    )

    # -- an authority about to expire says so in the health report, without being made again
    env.srv(f"noust cert use {WIKI} --acme --no-order")
    env.srv("noust cert ca remove -y --force")
    short = json_of(env.srv("noust cert ca init --days 30 --json"), "init --days 30")
    env.check(short["warnings"], "an authority with 30 days must warn")
    health = json_of(env.srv("noust health --json", check=False), "health --json")
    said = " ".join([*health.get("warnings", []), *health.get("issues", [])])
    env.check(
        "internal" in said.lower() or "authority" in said.lower(), f"health is silent: {said[:300]}"
    )

    # -- leave the server as it was found
    clean_authority(env)
    env.check(
        env.srv("test ! -e /etc/noust/ca", check=False).returncode == 0,
        "the authority's directory should be gone",
    )


# ---------------------------------------------------------------------------
# The web application firewall, against the real ModSecurity and the real Core Rule Set (E-19)
# ---------------------------------------------------------------------------

WAF_SITE = "waf.test"
WAF_OTHER = "waf-open.test"

#: Attacks the Core Rule Set at its default paranoia level knows by name, as a query string.
WAF_XSS = "/?q=%3Cscript%3Ealert(1)%3C/script%3E"
WAF_SQLI = "/?id=1%27%20OR%20%271%27=%271"
WAF_TRAVERSAL = "/?file=../../../../etc/passwd"
WAF_COMMAND = "/?cmd=;cat%20/etc/passwd"
WAF_CLEAN = "/search?q=blue+shoes&page=2"


def waf_audit_path(env: Env) -> str:
    """
    Read where ModSecurity writes its audit log from the rules file Noust wrote.

    Args:
        env: The environment.

    Returns:
        The path ``SecAuditLog`` names.
    """
    line = env.srv("grep -m1 '^SecAuditLog ' /etc/nginx/noust-waf/rules.conf").stdout.strip()
    return line.split(None, 1)[1]


def status_of(env: Env, domain: str, path: str, *, extra: str = "", label: str = "") -> int:
    """
    Ask for ``domain`` from the client and return the HTTP status alone.

    Args:
        env: The environment.
        domain: The Host header.
        path: The path and query, sent exactly as given.
        extra: More curl options (``-d`` for a body, ``-H`` for a header).
        label: What to call it in the evidence.

    Returns:
        The status code, 0 when nothing answered.
    """
    url = shlex.quote(f"http://{env.server_ip}{path}")
    out = env.cli(
        f"curl -sS --path-as-is -o /dev/null -w '%{{http_code}}' {extra} -H 'Host: {domain}' {url}",
        check=False,
        label=label or f"http {domain}{path} {extra}".strip(),
    ).stdout.strip()
    return int(out) if out.isdigit() else 0


def waf_events(env: Env, domain: str) -> list[dict[str, Any]]:
    """
    Read what the rules judged for a site, through Noust's own command.

    Args:
        env: The environment.
        domain: The site.

    Returns:
        The events, newest first, as ``noust app policy waf events --json`` prints them.
    """
    proc = env.srv(f"noust app policy waf events {domain} --limit 200 --json", check=False)
    if proc.returncode != 0:
        return []
    answer = json_of(proc, f"waf events {domain}")
    events = answer.get("events", answer) if isinstance(answer, dict) else answer
    return list(events)


@scenario("waf_modsecurity_blocks_real_attacks")
def scenario_waf(env: Env) -> None:
    """nginx with ModSecurity and the Core Rule Set: attacks refused, clean traffic served, the log read."""
    for domain in (WAF_SITE, WAF_OTHER):
        if env.srv(f"noust status {domain}", check=False).returncode == 0:
            env.srv(f"noust delete {domain} -y", label="(a re-run) start from nothing")
    env.srv("rm -f /var/log/nginx/noust-waf-audit.*", label="(a re-run) a clean audit log")
    ensure_app(env, WAF_SITE)
    ensure_app(env, WAF_OTHER)

    # -- a server without the module says so, in words, and nothing is written
    shown = json_of(env.srv(f"noust app policy waf show {WAF_SITE} --json"), "waf show")
    if not shown["available"]:
        env.check(
            "ModSecurity" in shown["problem"] and shown["installable"],
            f"an nginx without the module should say so and offer the packages: {shown}",
        )
        refused = env.srv(f"noust app policy set {WAF_SITE} --waf block", check=False)
        env.check(
            refused.returncode != 0, "a policy asking for the WAF without the module was kept"
        )
        env.check(
            "ModSecurity" in refused.stdout + refused.stderr,
            "the refusal does not name what is missing",
        )
        env.check(
            env.srv("test ! -e /etc/nginx/noust-waf", check=False).returncode == 0,
            "a refused policy left the shared rules behind",
        )
        env.srv("noust app policy waf install", timeout=900, label="noust app policy waf install")
    else:
        env.note("the packages were already installed by an earlier run")
    shown = json_of(env.srv(f"noust app policy waf show {WAF_SITE} --json"), "waf show")
    env.check(shown["available"], f"after the install the WAF is still unavailable: {shown}")

    # -- detect: every attack is answered by the application, and written down
    env.srv(f"noust app policy set {WAF_SITE} --waf detect")
    env.check(status_of(env, WAF_SITE, WAF_CLEAN) == 200, "a clean request was refused in detect")
    for name, path in (("xss", WAF_XSS), ("sqli", WAF_SQLI)):
        env.check(
            status_of(env, WAF_SITE, path, label=f"{name} in detect mode") == 200,
            f"{name} was refused in detect mode: nothing may be refused there",
        )
    events = waf_events(env, WAF_SITE)
    would = [e for e in events if e.get("would_block")]
    env.check(len(would) >= 2, f"detect wrote down {len(would)} attacks it would have blocked")
    rule_ids = {rule["id"] for event in events for rule in event["rules"]}
    env.check(
        {"941100", "942100"} <= rule_ids,
        f"the rules that should have matched did not: {sorted(rule_ids)}",
    )

    # -- block: the same attacks get a 403 from the module, clean traffic still passes
    env.srv(f"noust app policy set {WAF_SITE} --waf block")
    for name, path in (
        ("xss", WAF_XSS),
        ("sqli", WAF_SQLI),
        ("path traversal", WAF_TRAVERSAL),
        ("command injection", WAF_COMMAND),
    ):
        got = status_of(env, WAF_SITE, path, label=f"{name} in block mode")
        env.check(got == 403, f"{name} was answered {got}, not refused with 403")
    env.check(status_of(env, WAF_SITE, "/") == 200, "block mode refused the home page")
    env.check(status_of(env, WAF_SITE, WAF_CLEAN) == 200, "block mode refused a search")
    body_attack = status_of(
        env,
        WAF_SITE,
        "/login",
        extra="-d 'user=ana&password=hunter2-body&bio=%3Cscript%3Ealert(1)%3C/script%3E'",
        label="an attack in a POST body",
    )
    env.check(body_attack == 403, f"an attack in a form body was answered {body_attack}")
    env.check(
        status_of(env, WAF_SITE, "/login", extra="-d 'user=ana&password=hunter2-body'") == 200,
        "an ordinary login form was refused",
    )

    # -- the firewall is per site: the other site shares the rules and is not judged
    env.check(
        status_of(env, WAF_OTHER, WAF_XSS, label="the same attack at a site without the WAF")
        == 200,
        "a site that did not turn the WAF on was judged by it",
    )
    env.check(
        env.srv(
            "grep -c '^modsecurity_rules_file' /etc/nginx/sites-enabled/00-noust-waf"
        ).stdout.strip()
        == "1",
        "the rules should be loaded once for every site",
    )

    # -- what the log keeps: blocked, with the rules; never the body; readable by root alone
    audit_log = waf_audit_path(env)
    events = waf_events(env, WAF_SITE)
    blocked = [e for e in events if e.get("blocked")]
    env.check(
        len(blocked) >= 4, f"the log shows {len(blocked)} blocked requests, expected 4 or more"
    )
    env.check(
        env.srv(f"grep -c 'hunter2-body' {audit_log}", check=False).stdout.strip() == "0",
        "a password typed into a form reached the audit log",
    )
    mode = env.srv(f"stat -c '%a' {audit_log}").stdout.strip()
    env.check(
        int(mode, 8) & 0o007 == 0,
        f"the audit log is mode {mode}: it holds the headers of every flagged request, "
        "cookies and credentials included, and other accounts on the server can read it",
    )

    # -- an exclusion is per site and per rule number
    env.srv(f"noust app policy set {WAF_SITE} --waf-exclude 942100")
    env.check(
        status_of(env, WAF_SITE, WAF_SQLI, label="sqli with 942100 left out") == 200,
        "a rule left out still refused the request it alone matched",
    )
    env.check(
        status_of(env, WAF_SITE, WAF_XSS, label="xss with 942100 left out") == 403,
        "leaving a SQL rule out stopped the cross-site scripting rules",
    )
    env.srv(f"noust app policy set {WAF_SITE} --clear-waf-exclusions")
    env.check(
        status_of(env, WAF_SITE, WAF_SQLI, label="sqli, nothing left out") == 403,
        "clearing the exclusions did not bring the rule back",
    )

    # -- the web server's own rotation must not leave the WAF writing to a file nobody reads
    env.srv(
        "command -v logrotate >/dev/null || apt-get install -y --no-install-recommends logrotate",
        timeout=300,
        label="(the image is minimal) logrotate, which a Debian server has",
    )
    env.srv(
        "logrotate -f /etc/logrotate.d/nginx; "
        "if [ -f /etc/logrotate.d/noust-waf ]; then logrotate -f /etc/logrotate.d/noust-waf; fi",
        label="logrotate -f, as the daily run does",
    )
    status_of(env, WAF_SITE, WAF_XSS.replace("alert(1)", "alert(rotated)"), label="after rotation")
    after = [e for e in waf_events(env, WAF_SITE) if "alert(rotated)" in e["uri"]]
    env.check(
        len(after) == 1,
        "after the log was rotated the firewall wrote to a file nobody reads: "
        "`waf events` shows nothing new",
    )

    # -- off: the attack passes again; the shared rules go with the last site that used them
    env.srv(f"noust app policy set {WAF_OTHER} --waf detect")
    env.srv(f"noust app policy set {WAF_SITE} --waf off")
    env.check(status_of(env, WAF_SITE, WAF_XSS) == 200, "the firewall is off and still refuses")
    env.check(
        env.srv("test -f /etc/nginx/noust-waf/rules.conf", check=False).returncode == 0,
        "the shared rules went while another site still used them",
    )
    env.srv(f"noust app policy set {WAF_OTHER} --waf off")
    env.check(
        env.srv(
            "test ! -e /etc/nginx/noust-waf/rules.conf && test ! -e /etc/nginx/sites-enabled/00-noust-waf",
            check=False,
        ).returncode
        == 0,
        "the shared rules stayed after the last site turned the WAF off",
    )
    env.check(
        env.srv(
            "nginx -T 2>/dev/null | grep -c '^modsecurity_rules_file'", check=False
        ).stdout.strip()
        == "0",
        "nginx still has the rules loaded after they were removed",
    )
    env.check(
        env.srv("test ! -e /etc/logrotate.d/noust-waf", check=False).returncode == 0,
        "the WAF's rotation rule stayed after the rules went",
    )
    for domain in (WAF_SITE, WAF_OTHER):
        env.srv(f"noust delete {domain} -y", label=f"(leave the server as found) delete {domain}")


# ---------------------------------------------------------------------------
# A certificate authority and a DNS provider that run for real: Pebble and BIND (E-33, E-34)
# ---------------------------------------------------------------------------

#: One container holds BIND, the DNS provider's stand-in and Pebble (Dockerfile.dns): the harness is
#: held to two live containers, and the authority and the DNS it asks are one stack.
DNS = f"{PREFIX}-dns"
DNS_IMAGE = f"{PREFIX}-dns:latest"

#: Let's Encrypt's own ACME server for tests, from its official image, pinned by digest (the
#: build of 2026-04-20). It is the authority certbot orders from, the same code Boulder's
#: validation follows, with a root it makes up at every start.
PEBBLE_IMAGE = "ghcr.io/letsencrypt/pebble@sha256:ddf230642b1a584f519f32e347de1b05a6e4c1f6c35c1863b33effeab5f78199"

#: The zone BIND is authoritative for, and the names the scenarios use under it.
ACME_ZONE = "acme.test"
ACME_EMAIL = f"admin@{ACME_ZONE}"

#: Pebble asks the authoritative server for the A record that tells it where to validate (80, as a
#: real authority does) and for the TXT record of a DNS-01 challenge. The status 5 seconds it waits
#: between checks is shortened, and an authorization is never reused, so every order is validated.
PEBBLE_CONFIG = {
    "pebble": {
        "listenAddress": "0.0.0.0:14000",
        "managementListenAddress": "0.0.0.0:15000",
        "certificate": "test/certs/localhost/cert.pem",
        "privateKey": "test/certs/localhost/key.pem",
        "httpPort": 80,
        "tlsPort": 443,
        "ocspResponderURL": "",
        "externalAccountBindingRequired": False,
        "domainBlocklist": ["blocked-domain.example"],
        "retryAfter": {"authz": 1, "order": 1},
        "keyAlgorithm": "ecdsa",
        "profiles": {"default": {"description": "default", "validityPeriod": 7776000}},
    }
}

#: Files the server trusts while the stack is up, and what to undo afterwards.
PEBBLE_TRUST = "/usr/local/share/ca-certificates/noust-test-pebble.crt"
PROVIDER_TRUST = "/usr/local/share/ca-certificates/noust-test-dns-provider.crt"
HOSTS_MARK = "# noust-test-acme"


@dataclass
class AcmeStack:
    """
    What the certificate scenarios know about the running Pebble and BIND.

    Attributes:
        pebble_ip: Pebble's address on our network.
        dns_ip: The DNS container's address.
        token: The stand-in provider's token (a throwaway the container made).
        tsig_name: The TSIG key's name.
        tsig_secret: The TSIG key's secret, base64.
    """

    pebble_ip: str
    dns_ip: str
    token: str
    tsig_name: str
    tsig_secret: str


_STACK: AcmeStack | None = None

#: The visitor was stopped to make room for the stack and is started again when the stack goes.
_CLIENT_STOPPED = False

#: The machine the running stack serves; the server unless a scenario brought its own.
_TARGET = SERVER


def pull_pebble() -> None:
    """Pull Pebble's official image when it is not here yet."""
    if docker("image", "inspect", PEBBLE_IMAGE, check=False).returncode == 0:
        return
    print(f"[setup] pulling {PEBBLE_IMAGE}")
    docker("pull", PEBBLE_IMAGE, timeout=600)


def build_dns_image() -> None:
    """Build the BIND, provider stand-in and Pebble image (cached after the first time)."""
    print(f"[setup] building {DNS_IMAGE}")
    docker(
        "build",
        "--build-arg",
        f"PEBBLE_IMAGE={PEBBLE_IMAGE}",
        "--label",
        LABEL,
        "-t",
        owned(DNS_IMAGE),
        "-f",
        str(NETWORK_DIR / "Dockerfile.dns"),
        str(NETWORK_DIR),
        timeout=900,
    )


def start_acme_stack(env: Env, *, target: str = SERVER, configure_noust: bool = True) -> AcmeStack:
    """
    Start Pebble and the DNS container and make a machine use them.

    The machine trusts Pebble's own CA (certbot talks to it over TLS) and the stand-in provider's,
    resolves ``pebble`` and ``api.cloudflare.com`` to the two containers and asks the BIND for the
    names under ``acme.test``; Noust is told to order from Pebble. The visitor is stopped while the
    stack is up: three containers is the most this harness runs at a time.

    Args:
        env: The environment.
        target: The machine that uses the stack: the server, or a fresh one a scenario made.
        configure_noust: Tell the machine's Noust to order from Pebble (installing certbot first).
            False for a machine that has no Noust yet.

    Returns:
        What the scenarios need to know about the stack.
    """
    global _STACK, _CLIENT_STOPPED, _TARGET
    if _STACK is not None:
        return _STACK
    _TARGET = target
    if is_running(CLIENT):
        docker("stop", owned(CLIENT), timeout=60)
        _CLIENT_STOPPED = True
    try:
        _STACK = _start_acme_stack(env, target, configure_noust)
    except BaseException:
        stop_acme_stack(env)
        raise
    return _STACK


def _start_acme_stack(env: Env, target: str, configure_noust: bool) -> AcmeStack:
    """
    Do what :func:`start_acme_stack` describes, with the visitor already stopped.

    Args:
        env: The environment.
        target: The machine that uses the stack.
        configure_noust: Whether its Noust is told to order from Pebble.

    Returns:
        What the scenarios need to know about the stack.
    """
    pull_pebble()
    build_dns_image()
    remove_container(DNS)

    # `pebble` is a name of this same container: the machines reach the authority by it.
    docker(
        "run",
        "-d",
        "--name",
        owned(DNS),
        "--label",
        LABEL,
        "--network",
        NETWORK,
        "--network-alias",
        "pebble",
        DNS_IMAGE,
    )
    dns_ip = container_ip(DNS)
    wait_for(
        lambda: (
            docker(
                "exec", DNS, "dig", "+short", "@127.0.0.1", "SOA", ACME_ZONE, check=False
            ).stdout.strip()
            != ""
        ),
        "BIND to answer for the zone",
        timeout=40,
    )
    wait_for(
        lambda: (
            Path("/dev/null").exists()
            and docker("exec", DNS, "test", "-s", "/srv/token", check=False).returncode == 0
        ),
        "the provider stand-in to start",
        timeout=40,
    )
    token = docker("exec", DNS, "cat", "/srv/token").stdout.strip()
    key_text = docker("exec", DNS, "cat", "/etc/bind/noust.key").stdout
    secret = re.search(r'secret\s+"([^"]+)"', key_text)
    if secret is None:
        raise HarnessError(f"no secret in the TSIG key BIND made: {key_text!r}")

    with tempfile.TemporaryDirectory(prefix="noust-pebble-") as scratch:
        config = Path(scratch) / "pebble-config.json"
        config.write_text(json.dumps(PEBBLE_CONFIG))
        docker("cp", str(config), f"{DNS}:/pebble-config.json")
        minica = Path(scratch) / "pebble.minica.pem"
        docker("cp", f"{DNS}:/test/certs/pebble.minica.pem", str(minica))
        provider_ca = Path(scratch) / "provider-ca.pem"
        docker("cp", f"{DNS}:/srv/ca/ca.pem", str(provider_ca))
        pebble_ip = dns_ip
        docker("cp", str(minica), f"{target}:{PEBBLE_TRUST}")
        docker("cp", str(provider_ca), f"{target}:{PROVIDER_TRUST}")

    sh_in(
        target,
        "update-ca-certificates >/dev/null 2>&1; "
        f"sed -i '/{HOSTS_MARK}/d' /etc/hosts; "
        f"printf '%s pebble {HOSTS_MARK}\\n%s api.cloudflare.com {HOSTS_MARK}\\n' "
        f"'{pebble_ip}' '{dns_ip}' >> /etc/hosts; "
        "cp /etc/resolv.conf /etc/resolv.conf.noust-test-orig; "
        f"(echo 'nameserver {dns_ip}'; cat /etc/resolv.conf.noust-test-orig) > /etc/resolv.conf",
    )
    # The stack is up when Pebble answers its directory over TLS from the server.
    wait_for(
        lambda: (
            docker(
                "exec", target, "curl", "-fsS", "https://pebble:14000/dir", check=False
            ).returncode
            == 0
        ),
        "Pebble to answer its directory from the server",
        timeout=40,
    )
    address = container_ip(target)
    names = {
        "www": address,
        "app": address,
        "fresh": address,
        "dns": "192.0.2.1",  # nothing listens there: only the DNS challenge can prove this name
        "wild": address,
        "plug": "192.0.2.1",
    }
    updates = "\n".join(f"update add {n}.{ACME_ZONE} 60 A {ip}" for n, ip in names.items())
    docker(
        "exec",
        "-i",
        DNS,
        "nsupdate",
        "-k",
        "/etc/bind/noust.key",
        input_text=f"server 127.0.0.1\n{updates}\nsend\n",
    )
    if configure_noust:
        env.srv(
            "command -v certbot >/dev/null || noust setup init -y",
            timeout=600,
            label="(certbot, as `noust setup init` installs it)",
        )
        env.srv(
            f"noust cert acme --ca custom --server https://pebble:14000/dir --email {ACME_EMAIL} "
            "--register",
            timeout=180,
            label="noust cert acme --ca custom --server https://pebble:14000/dir --register",
        )
    return AcmeStack(
        pebble_ip=pebble_ip,
        dns_ip=dns_ip,
        token=token,
        tsig_name="noust-key",
        tsig_secret=secret.group(1),
    )


def stop_acme_stack(env: Env | None) -> None:
    """
    Put the server back as it was and remove Pebble and the DNS container.

    Args:
        env: The environment, or None when only the containers are to go.
    """
    global _STACK, _CLIENT_STOPPED
    _STACK = None
    if env is not None and _TARGET == SERVER and is_running(SERVER):
        env.srv(
            "noust cert acme --ca letsencrypt >/dev/null 2>&1; "
            f"sed -i '/{HOSTS_MARK}/d' /etc/hosts; "
            "if [ -f /etc/resolv.conf.noust-test-orig ]; then "
            "cat /etc/resolv.conf.noust-test-orig > /etc/resolv.conf; "
            "rm -f /etc/resolv.conf.noust-test-orig; fi; "
            f"rm -f {PEBBLE_TRUST} {PROVIDER_TRUST}; update-ca-certificates --fresh >/dev/null 2>&1; "
            "rm -rf /etc/letsencrypt/accounts; true",
            check=False,
            label="(leave the server as found) the authority, hosts, resolver and trust",
        )
    remove_container(DNS)
    if _CLIENT_STOPPED:
        docker("start", owned(CLIENT), check=False)
        _CLIENT_STOPPED = False


def pebble_root(env: Env) -> str:
    """
    Fetch the root certificate Pebble made for this run, the one a visitor must trust.

    Args:
        env: The environment.

    Returns:
        The PEM text.
    """
    return env.srv(
        "curl -fsS https://pebble:15000/roots/0",
        label="Pebble's root for this run (its management API)",
    ).stdout


def provider_requests(stack: AcmeStack) -> list[dict[str, Any]]:
    """
    Read the calls Noust made to the stand-in provider, in order.

    Args:
        stack: The running stack.

    Returns:
        One entry per request: ``method``, ``path``, ``authorized`` and ``body``.
    """
    text = docker("exec", DNS, "cat", "/srv/requests.jsonl").stdout
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def txt_records(stack: AcmeStack, name: str) -> list[str]:
    """
    Ask BIND, as the authority does, for the TXT records of a name.

    Args:
        stack: The running stack.
        name: The name.

    Returns:
        The values, quotes and all as ``dig`` prints them.
    """
    out = docker("exec", DNS, "dig", "+short", "@127.0.0.1", "TXT", name).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def served_chain_verifies(env: Env, host: str, root_path: str) -> str:
    """
    Open ``https://host/`` on the server against Pebble's root and say how it went.

    Args:
        env: The environment.
        host: The name asked for.
        root_path: A file on the server that holds Pebble's root.

    Returns:
        ``<status> verify=<code>`` as curl reports it (``200 verify=0`` is trusted and answering).
    """
    return env.srv(
        f"curl -sS -o /dev/null -w '%{{http_code}} verify=%{{ssl_verify_result}}' "
        f"--cacert {root_path} --resolve {host}:443:127.0.0.1 https://{host}/",
        check=False,
        label=f"https://{host}/ against Pebble's root",
    ).stdout.strip()


def certificate_names(env: Env, host: str) -> list[str]:
    """
    Read the names on the certificate nginx presents for ``host``.

    Args:
        env: The environment.
        host: The SNI name.

    Returns:
        The subject alternative names.
    """
    out = env.srv(
        f"openssl s_client -connect 127.0.0.1:443 -servername {host} </dev/null 2>/dev/null "
        "| openssl x509 -noout -ext subjectAltName",
        label=f"the names on the certificate for {host}",
    ).stdout
    return re.findall(r"DNS:([^,\s]+)", out)


def forget_acme_app(env: Env, domain: str) -> None:
    """
    Start a certificate scenario from a server that has no application and no lineage of ``domain``.

    Args:
        env: The environment.
        domain: The application's domain.
    """
    if env.srv(f"noust status {domain}", check=False).returncode == 0:
        env.srv(f"noust delete {domain} -y", label=f"(a re-run) delete {domain}")
    env.srv(
        f"certbot delete --cert-name {domain} --non-interactive >/dev/null 2>&1; true",
        label=f"(a re-run) no lineage of {domain}",
    )


def lineage_issuer(env: Env, name: str) -> str:
    """
    Read who signed the certificate certbot keeps for ``name``.

    Args:
        env: The environment.
        name: The lineage.

    Returns:
        The issuer line.
    """
    return env.srv(
        f"openssl x509 -noout -issuer -in /etc/letsencrypt/live/{name}/cert.pem",
        label=f"who signed the certificate of {name}",
    ).stdout.strip()


@scenario("acme_http01_issue_serve_renew")
def scenario_acme_http01(env: Env) -> None:
    """Real certbot orders from Pebble over HTTP-01; nginx serves it; a renewal replaces it (E-33)."""
    stack = start_acme_stack(env)
    domain = f"app.{ACME_ZONE}"
    forget_acme_app(env, domain)
    root_file = "/root/pebble-root.pem"
    root = pebble_root(env)
    put_file(SERVER, root_file, root)

    created = env.srv(
        f"noust create -d {domain} -s {ECHO_URL} -t nodejs",
        timeout=DEPLOY_TIMEOUT + 120,
        check=False,
    )
    env.check(
        created.returncode == 0,
        f"noust create with a certificate failed:\n{(created.stdout + created.stderr)[-2500:]}",
    )

    issuer = lineage_issuer(env, domain)
    env.check("Pebble" in issuer, f"the certificate was not signed by Pebble: {issuer}")
    env.check(
        served_chain_verifies(env, domain, root_file) == "200 verify=0",
        "nginx does not serve the certificate certbot ordered, chained to Pebble's root",
    )
    redirect = env.srv(
        f"curl -sS -o /dev/null -w '%{{http_code}} %{{redirect_url}}' -H 'Host: {domain}' "
        "http://127.0.0.1/",
        label="plain HTTP goes to HTTPS",
    ).stdout.strip()
    env.check(redirect.startswith("301 https://"), f"HTTP does not redirect to HTTPS: {redirect!r}")

    # -- what Noust reads back from the real certbot: the listing, the expiry, the names
    listed = json_of(env.srv("noust cert list --json"), "cert list")
    certificates = listed.get("certificates", listed) if isinstance(listed, dict) else listed
    mine = [c for c in certificates if domain in (c.get("domains") or [c.get("name")])]
    env.check(bool(mine), f"noust cert list does not show the lineage of {domain}: {listed}")
    env.check(
        domain in certificate_names(env, domain),
        "the certificate nginx presents does not name the application",
    )

    # -- a renewal replaces the certificate and reloads nginx instead of restarting it
    before = live_fingerprint(env, domain)
    pid = env.srv("systemctl show nginx -p MainPID --value").stdout.strip()
    env.srv(
        f"noust cert renew --domain {domain} --force", timeout=300, label="noust cert renew --force"
    )
    after = live_fingerprint(env, domain)
    env.check(after != before, "nginx still presents the old certificate after a renewal")
    env.check(
        env.srv("systemctl show nginx -p MainPID --value").stdout.strip() == pid,
        "nginx was restarted by the renewal, not reloaded",
    )
    env.check(
        served_chain_verifies(env, domain, root_file) == "200 verify=0",
        "the renewed certificate does not verify",
    )

    # -- an application whose name does not point here is refused by the authority, in its words
    wrong = f"nowhere.{ACME_ZONE}"
    forget_acme_app(env, wrong)
    refused = env.srv(
        f"noust create -d {wrong} -s {ECHO_URL} -t nodejs",
        timeout=DEPLOY_TIMEOUT + 120,
        check=False,
    )
    said = refused.stdout + refused.stderr
    env.check(
        refused.returncode == 0 and "has no DNS record pointing at this machine" in said,
        f"a name with no DNS record should be deployed without TLS and say why: {said[-800:]}",
    )
    env.check(
        "Continuing deployment without SSL" in said
        and not env.srv(f"test -d /etc/letsencrypt/live/{wrong}", check=False).returncode == 0,
        "the deployment of a name the authority cannot reach ordered a certificate anyway",
    )
    forget_acme_app(env, wrong)
    forget_acme_app(env, domain)
    env.check(stack.pebble_ip != "", "the stack did not report Pebble's address")


def srv_stdin(
    env: Env, script: str, text: str, *, label: str, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """
    Run a script in the server with a secret on its standard input, keeping it off the evidence.

    Args:
        env: The environment.
        script: The script (bash); it names the secret's place, never the secret.
        text: What is fed to its standard input.
        label: What to call it in the evidence.
        timeout: Seconds.
        check: Fail the scenario on a non-zero exit.

    Returns:
        The finished command.
    """
    proc = subprocess.run(
        ["docker", "exec", "-i", owned(SERVER), "bash", "-lc", script],
        input=text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=docker_env(),
    )
    block = [f"[server] $ {label}  (secret on stdin)"]
    if proc.stdout:
        block.append(proc.stdout.rstrip("\n"))
    if proc.stderr.strip():
        block.extend(["--- stderr ---", proc.stderr.rstrip("\n")])
    block.append(f"(exit={proc.returncode})")
    env.evidence.append("\n".join(block))
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed (exit {proc.returncode}): [server] {label}")
    return proc


def challenge_records_made(stack: AcmeStack, name: str) -> list[dict[str, Any]]:
    """
    List the records Noust asked the stand-in provider to create under one name, in order.

    Args:
        stack: The running stack.
        name: The record's full name, ``_acme-challenge.`` and all.

    Returns:
        The request bodies (``type``, ``name``, ``content``, ``comment``).
    """
    return [
        dict(request["body"])
        for request in provider_requests(stack)
        if request["authorized"]
        and request["method"] == "POST"
        and (request.get("body") or {}).get("name") == name
    ]


def challenge_records_deleted(stack: AcmeStack) -> int:
    """
    Count the records Noust deleted at the stand-in provider.

    Args:
        stack: The running stack.

    Returns:
        How many authorized DELETE requests it made.
    """
    return sum(1 for r in provider_requests(stack) if r["authorized"] and r["method"] == "DELETE")


def add_cloudflare_account(env: Env, stack: AcmeStack, name: str = "cf") -> None:
    """
    Add the stand-in provider to Noust as a Cloudflare account, the way an operator does.

    Args:
        env: The environment.
        stack: The running stack (its token is the credential).
        name: The account's name.
    """
    env.srv(f"noust dns provider remove {name} -y >/dev/null 2>&1; true", check=False)
    srv_stdin(
        env,
        f"noust dns provider add {name} --kind cloudflare --token-stdin",
        stack.token,
        label=f"noust dns provider add {name} --kind cloudflare --token-stdin",
    )


def token_in_files(env: Env, token: str) -> str:
    """
    Search certbot's files and Noust's configuration for a credential.

    Args:
        env: The environment.
        token: The credential.

    Returns:
        The names of the files that hold it, one per line (empty when none does).
    """
    return srv_stdin(
        env,
        "grep -rIlF -f /dev/stdin /etc/letsencrypt /var/log/letsencrypt /etc/noust/config.yaml "
        "2>/dev/null | head",
        token,
        label="grep the provider's token in certbot's files and in Noust's configuration",
        check=False,
    ).stdout.strip()


@scenario("acme_dns01_noust_hooks")
def scenario_acme_dns01(env: Env) -> None:
    """certbot proves a name with a DNS record Noust publishes and removes, where port 80 never reaches (E-33)."""
    stack = start_acme_stack(env)
    domain = f"dns.{ACME_ZONE}"
    forget_acme_app(env, domain)
    root_file = "/root/pebble-root.pem"
    put_file(SERVER, root_file, pebble_root(env))

    # -- the credential is proven when the account is added: a wrong one is refused, in the
    #    provider's own words, and kept nowhere
    env.srv("noust dns provider remove wrongcf -y >/dev/null 2>&1; true", check=False)
    wrong = srv_stdin(
        env,
        "noust dns provider add wrongcf --kind cloudflare --token-stdin",
        "not-the-provider-token",
        label="noust dns provider add wrongcf (a token the provider does not know)",
        check=False,
    )
    env.check(wrong.returncode != 0, "an account with a token the provider refused was kept")
    said = wrong.stdout + wrong.stderr
    env.check("Invalid access token" in said, f"the provider's own words are lost: {said[-400:]}")
    env.check(
        "wrongcf" not in env.srv("noust dns provider list", check=False).stdout,
        "the refused account is listed",
    )
    add_cloudflare_account(env, stack)
    zones = env.srv("noust dns provider verify cf").stdout
    env.check(ACME_ZONE in zones, f"the account does not list the zone it holds: {zones}")
    sent = [r for r in provider_requests(stack) if r["path"].startswith("/client/v4/zones")]
    env.check(
        sent and all(r["user_agent"].startswith("noust/") for r in sent),
        "Noust does not say who it is to the provider",
    )

    # -- an application on a name nothing answers on port 80 for: only the DNS challenge proves it
    env.srv(
        f"noust create -d {domain} -s {ECHO_URL} -t nodejs --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {domain} --no-ssl (its A record is 192.0.2.1: nothing answers there)",
    )
    ordered = env.srv(f"noust cert use {domain} --acme --dns cf", timeout=600, check=False)
    env.check(
        ordered.returncode == 0,
        f"choosing the DNS challenge failed:\n{(ordered.stdout + ordered.stderr)[-3000:]}",
    )
    env.check(
        "ordered now" not in ordered.stdout,
        "an application with no TLS yet was said to be ordered now: only the choice is kept",
    )
    first = env.srv(f"noust cert create -d {domain}", timeout=600, check=False)
    env.check(
        first.returncode == 0,
        f"the first DNS-01 certificate failed:\n{(first.stdout + first.stderr)[-3000:]}",
    )
    env.check(
        "now serves it over HTTPS" in first.stdout,
        f"the site was not written with the certificate that was just ordered: {first.stdout}",
    )
    env.check("Pebble" in lineage_issuer(env, domain), "the certificate was not signed by Pebble")
    env.check(
        served_chain_verifies(env, domain, root_file) == "200 verify=0",
        "nginx does not serve the certificate that the DNS challenge ordered",
    )

    # -- the record: published with its marker, removed afterwards, nothing left in the zone
    name = f"_acme-challenge.{domain}"
    made = challenge_records_made(stack, name)
    env.check(len(made) == 1, f"expected one challenge record published, found {made}")
    env.check(
        made[0].get("comment") == "managed-by: noust acme challenge" and made[0]["type"] == "TXT",
        f"the record has no ownership marker: {made[0]}",
    )
    env.check(challenge_records_deleted(stack) >= 1, "the challenge record was never removed")
    env.check(txt_records(stack, name) == [], "a challenge record is still in the zone")

    # -- the credential is nowhere certbot or Noust keep anything, and the hooks are
    env.check(
        token_in_files(env, stack.token) == "",
        "the provider's token is written in certbot's files or in Noust's configuration",
    )
    conf = env.srv(f"cat /etc/letsencrypt/renewal/{domain}.conf").stdout
    env.check(
        "manual_auth_hook" in conf and "dns challenge auth" in conf and "--account cf" in conf,
        f"certbot does not keep the hook that renews it:\n{conf}",
    )
    env.check(
        "manual_cleanup_hook" in conf and "dns challenge cleanup" in conf,
        "certbot does not keep the cleanup hook",
    )
    env.check("pref_challs = dns-01" in conf, "the renewal does not name the DNS challenge")

    # -- a renewal runs the hooks again with nobody there, and replaces the certificate
    before = live_fingerprint(env, domain)
    pid = env.srv("systemctl show nginx -p MainPID --value").stdout.strip()
    env.srv(
        f"noust cert renew --domain {domain} --force",
        timeout=600,
        label="noust cert renew --force (the stored hooks run again)",
    )
    env.check(live_fingerprint(env, domain) != before, "the renewal did not change the certificate")
    env.check(
        env.srv("systemctl show nginx -p MainPID --value").stdout.strip() == pid,
        "nginx was restarted by the renewal, not reloaded",
    )
    env.check(
        len(challenge_records_made(stack, name)) == 2,
        "the renewal did not publish a challenge record of its own",
    )
    env.check(txt_records(stack, name) == [], "the renewal left its challenge record in the zone")
    env.check(
        served_chain_verifies(env, domain, root_file) == "200 verify=0",
        "the renewed certificate does not verify",
    )
    audited = env.srv("noust audit list --action dns.challenge --json --limit 50").stdout
    env.check(
        '"present"' in audited and '"cleanup"' in audited,
        "the challenge records are not in the audit log",
    )
    forget_acme_app(env, domain)
    env.srv("noust dns provider remove cf -y")


@scenario("acme_wildcard_certificate")
def scenario_acme_wildcard(env: Env) -> None:
    """A wildcard and its base name are proven together by DNS, served for any label, and renewed (E-34)."""
    stack = start_acme_stack(env)
    base = f"wild.{ACME_ZONE}"
    other = f"api.{base}"
    forget_acme_app(env, base)
    forget_acme_app(env, other)
    root_file = "/root/pebble-root.pem"
    put_file(SERVER, root_file, pebble_root(env))
    add_cloudflare_account(env, stack)
    env.srv(
        f"noust create -d {base} -s {ECHO_URL} -t nodejs --no-ssl",
        timeout=DEPLOY_TIMEOUT,
    )

    # -- the first certificate of the base name is proven by DNS too (nothing answers on its port 80)
    env.srv(f"noust cert use {base} --acme --dns cf", timeout=600)
    first = env.srv(f"noust cert create -d {base}", timeout=600)
    env.check("now serves it over HTTPS" in first.stdout, f"the site stayed plain: {first.stdout}")
    env.check(
        served_chain_verifies(env, base, root_file) == "200 verify=0",
        "the base name is not served with the certificate DNS ordered",
    )
    # -- the wildcard joins it: the certificate is extended with both names, one record name,
    #    two values
    name = f"_acme-challenge.{base}"
    made_before = len(challenge_records_made(stack, name))
    env.srv(f"noust domain add {base} '*.{base}'", timeout=600)

    names = certificate_names(env, base)
    env.check(
        f"*.{base}" in names and base in names,
        f"the certificate does not name the wildcard and its base: {names}",
    )
    # -- any label under it is served by the wildcard, chained to the authority
    for label in ("shop", "anything-at-all"):
        host = f"{label}.{base}"
        env.check(
            served_chain_verifies(env, host, root_file) == "200 verify=0",
            f"{host} is not served with a certificate that verifies",
        )
    # -- the base name and the wildcard share one record name with two values; each was removed
    made = challenge_records_made(stack, name)[made_before:]
    env.check(
        len(made) == 2 and len({record["content"] for record in made}) == 2,
        f"the wildcard and its base share {name} with two values; saw {made}",
    )
    env.check(txt_records(stack, name) == [], "a challenge record of the wildcard is still there")
    env.check(
        challenge_records_deleted(stack) >= 3,
        "the wildcard's records were not both removed after the order",
    )

    # -- a second application under the wildcard is served by it, without ordering anything
    env.srv(
        f"noust create -d {other} -s {ECHO_URL} -t nodejs",
        timeout=DEPLOY_TIMEOUT + 300,
        label=f"noust create -d {other} (under the wildcard)",
    )
    lineages = env.srv("ls /etc/letsencrypt/live").stdout.split()
    env.check(
        other not in lineages, f"an order was spent on a name the wildcard covers: {lineages}"
    )
    env.check(
        served_chain_verifies(env, other, root_file) == "200 verify=0",
        f"{other} is not served under the wildcard's certificate",
    )

    # -- the renewal runs the stored hooks and the wildcard still covers everything
    before = live_fingerprint(env, base)
    env.srv(
        f"noust cert renew --domain {base} --force",
        timeout=900,
        label="noust cert renew --force (the wildcard)",
    )
    env.check(live_fingerprint(env, base) != before, "the wildcard was not renewed")
    env.check(
        f"*.{base}" in certificate_names(env, base),
        "the renewed certificate lost the wildcard",
    )
    env.check(
        served_chain_verifies(env, f"after-renewal.{base}", root_file) == "200 verify=0",
        "a label under the wildcard does not verify after the renewal",
    )
    env.check(txt_records(stack, name) == [], "the renewal left a challenge record in the zone")
    forget_acme_app(env, other)
    forget_acme_app(env, base)
    env.srv("noust dns provider remove cf -y")


@scenario("acme_dns01_rfc2136_plugin")
def scenario_acme_rfc2136(env: Env) -> None:
    """certbot's own RFC 2136 plugin proves a name by updating a real BIND with a TSIG key (E-33)."""
    stack = start_acme_stack(env)
    domain = f"plug.{ACME_ZONE}"
    forget_acme_app(env, domain)
    root_file = "/root/pebble-root.pem"
    put_file(SERVER, root_file, pebble_root(env))

    env.srv(
        "dpkg -s python3-certbot-dns-rfc2136 >/dev/null 2>&1 || "
        "apt-get install -y --no-install-recommends python3-certbot-dns-rfc2136",
        timeout=600,
        label="(the plugin, as `noust dns provider kinds` says to install it)",
    )
    credentials = json.dumps(
        {
            "server": stack.dns_ip,
            "port": "53",
            "name": stack.tsig_name,
            "secret": stack.tsig_secret,
            "algorithm": "HMAC-SHA256",
        }
    )
    env.srv("noust dns provider remove bind -y >/dev/null 2>&1; true", check=False)
    srv_stdin(
        env,
        "noust dns provider add bind --kind rfc2136 --credentials-stdin",
        credentials,
        label="noust dns provider add bind --kind rfc2136 --credentials-stdin",
    )
    env.srv(
        f"noust create -d {domain} -s {ECHO_URL} -t nodejs --no-ssl",
        timeout=DEPLOY_TIMEOUT,
        label=f"noust create -d {domain} --no-ssl (its A record is 192.0.2.1)",
    )
    env.srv(f"noust cert use {domain} --acme --dns bind", timeout=600)
    ordered = env.srv(f"noust cert create -d {domain}", timeout=600, check=False)
    env.check(
        ordered.returncode == 0 and "now serves it over HTTPS" in ordered.stdout,
        f"the RFC 2136 order failed:\n{(ordered.stdout + ordered.stderr)[-3000:]}",
    )
    env.check("Pebble" in lineage_issuer(env, domain), "the certificate was not signed by Pebble")
    env.check(
        served_chain_verifies(env, domain, root_file) == "200 verify=0",
        "nginx does not serve the certificate the RFC 2136 plugin ordered",
    )
    # -- the TSIG secret lives in a 0600 file certbot's plugin reads, and nowhere world-readable
    modes = env.srv(
        "find /var/lib/noust /root/.local/share/noust /etc/noust -path '*/secrets/dns/*' -type f "
        "-printf '%m %p\\n' 2>/dev/null",
        check=False,
    ).stdout
    env.check(
        modes.strip() != "" and all(line.startswith("600 ") for line in modes.splitlines()),
        f"the credentials file is readable beyond root: {modes!r}",
    )
    leak = srv_stdin(
        env,
        "grep -rIlF -f /dev/stdin /etc/letsencrypt /var/log/letsencrypt /etc/noust/config.yaml "
        "2>/dev/null | head",
        stack.tsig_secret,
        label="grep the TSIG secret in certbot's files and in Noust's configuration",
        check=False,
    )
    env.check(leak.stdout.strip() == "", f"the TSIG secret is in: {leak.stdout.strip()}")
    # -- the renewal reuses the plugin and the credentials file
    before = live_fingerprint(env, domain)
    env.srv(f"noust cert renew --domain {domain} --force", timeout=600)
    env.check(live_fingerprint(env, domain) != before, "the renewal did not change the certificate")
    env.check(
        txt_records(stack, f"_acme-challenge.{domain}") == [],
        "the plugin left a challenge record in BIND",
    )
    forget_acme_app(env, domain)
    env.srv("noust dns provider remove bind -y")


# ---------------------------------------------------------------------------
# A certificate for one name of an application, ordered from Pebble (per-name certificates)
# ---------------------------------------------------------------------------

#: Where the visitor loop of a scenario keeps what it saw, and the file that keeps it going.
PROBE_LOG = "/tmp/noust-crt-probe.log"
PROBE_FLAG = "/tmp/noust-crt-probe.run"
PROBE_SCRIPT = "/tmp/noust-crt-probe.sh"


def set_acme_names(names: dict[str, str]) -> None:
    """
    Point some names of the test zone at addresses, replacing what they had.

    Args:
        names: Label under ``acme.test`` to the address its A record gets.
    """
    updates = "\n".join(
        f"update delete {label}.{ACME_ZONE} A\nupdate add {label}.{ACME_ZONE} 60 A {address}"
        for label, address in names.items()
    )
    docker(
        "exec",
        "-i",
        DNS,
        "nsupdate",
        "-k",
        "/etc/bind/noust.key",
        input_text=f"server 127.0.0.1\n{updates}\nsend\n",
    )


def forget_name_certificates(env: Env, app: str, lineages: list[str]) -> None:
    """
    Start a scenario from a server that has neither the application nor the lineages of its names.

    Args:
        env: The environment.
        app: The application's domain.
        lineages: The lineages its names may have left, by their certbot names.
    """
    forget_acme_app(env, app)
    for lineage in lineages:
        env.srv(
            f"certbot delete --cert-name '{lineage}' --non-interactive >/dev/null 2>&1; true",
            label=f"(a re-run) no lineage called {lineage}",
        )


def start_probe(env: Env, host: str, root_file: str) -> None:
    """
    Start visiting ``https://host/`` over and over in the server, against the authority's root.

    Each visit writes what curl saw (the status and whether the certificate verified) to a log, so
    a scenario can prove that no request failed while a name changed certificates.

    Args:
        env: The environment.
        host: The name visited.
        root_file: A file of the server that holds Pebble's root.
    """
    loop = (
        "#!/bin/bash\n"
        f"while [ -e {PROBE_FLAG} ]; do\n"
        '  curl -sS -o /dev/null -m 5 -w "%{http_code} verify=%{ssl_verify_result}\\n" '
        f"--cacert {root_file} --resolve {host}:443:127.0.0.1 https://{host}/ >> {PROBE_LOG} 2>&1 "
        f"|| echo curl-failed >> {PROBE_LOG}\n"
        "  sleep 0.2\n"
        "done\n"
    )
    put_file(SERVER, PROBE_SCRIPT, loop)
    env.srv(
        f"rm -f {PROBE_LOG}; touch {PROBE_FLAG}; "
        f"setsid nohup bash {PROBE_SCRIPT} >/dev/null 2>&1 </dev/null &",
        label=f"(start visiting https://{host}/ over and over)",
    )
    # The first visit has to be in the log before the change starts, or the loop proves nothing.
    wait_for(
        lambda: env.srv(f"test -s {PROBE_LOG}", check=False).returncode == 0,
        f"the first visit to {host}",
        timeout=20,
    )


def stop_probe(env: Env) -> list[str]:
    """
    Stop the visitor loop and say what it saw.

    Args:
        env: The environment.

    Returns:
        One line per visit: ``200 verify=0`` for a page served over a certificate that verified.
    """
    env.srv(f"rm -f {PROBE_FLAG}; sleep 1", label="(stop visiting)")
    log = env.srv(f"cat {PROBE_LOG}", label="what the visits saw").stdout
    return [line.strip() for line in log.splitlines() if line.strip()]


def only_good_visits(seen: list[str]) -> bool:
    """
    Args:
        seen: What :func:`stop_probe` returned.

    Returns:
        True when there were visits during the change and every one was served and verified.
    """
    return len(seen) >= 3 and all(line == "200 verify=0" for line in seen)


def lineages_on_disk(env: Env) -> list[str]:
    """
    Args:
        env: The environment.

    Returns:
        The names of the lineages certbot keeps.
    """
    return env.srv("ls /etc/letsencrypt/live").stdout.split()


def site_digest(env: Env, domain: str) -> str:
    """
    Args:
        env: The environment.
        domain: The application's domain.

    Returns:
        The SHA-256 of the application's site file.
    """
    return env.srv(
        f"sha256sum /etc/nginx/sites-available/{domain}", label=f"the site of {domain}, hashed"
    ).stdout.split()[0]


def nginx_pid(env: Env) -> str:
    """
    Args:
        env: The environment.

    Returns:
        nginx's main process id, which a reload keeps and a restart changes.
    """
    return env.srv("systemctl show nginx -p MainPID --value").stdout.strip()


@scenario("acme_name_certificate_http01")
def scenario_acme_name_certificate_http01(env: Env) -> None:
    """A name takes a certificate of its own over HTTP-01, gives it back, renews and goes with the name, with no request failing."""
    start_acme_stack(env)
    base = f"crt.{ACME_ZONE}"
    shop, admin, lost = (f"{label}.{ACME_ZONE}" for label in ("crtshop", "crtadmin", "crtlost"))
    forget_name_certificates(env, base, [shop, admin, lost])
    root_file = "/root/pebble-root.pem"
    put_file(SERVER, root_file, pebble_root(env))
    # Every name but one points at this server; `crtlost` points where nothing listens.
    set_acme_names(
        {
            "crt": env.server_ip,
            "crtshop": env.server_ip,
            "crtadmin": env.server_ip,
            "crtlost": "192.0.2.1",
        },
    )

    created = env.srv(
        f"noust create -d {base} -s {ECHO_URL} -t nodejs",
        timeout=DEPLOY_TIMEOUT + 120,
        check=False,
    )
    env.check(
        created.returncode == 0,
        f"noust create with a certificate failed:\n{(created.stdout + created.stderr)[-2500:]}",
    )
    env.srv(
        f"noust domain add {base} {shop} --no-dns",
        timeout=600,
        label=f"noust domain add {base} {shop} --no-dns",
    )
    env.check(
        sorted(certificate_names(env, base)) == sorted([base, shop]),
        "the shared certificate does not name the application and its alias",
    )

    # -- a name added with a certificate of its own: its lineage holds it alone, and the shared
    #    certificate never names it
    added = env.srv(
        f"noust domain add {base} {admin} --no-dns --own-certificate",
        timeout=600,
        check=False,
        label=f"noust domain add {base} {admin} --no-dns --own-certificate",
    )
    env.check(
        added.returncode == 0,
        f"adding a name with a certificate of its own failed:\n{(added.stdout + added.stderr)[-2500:]}",
    )
    env.check(admin in lineages_on_disk(env), f"no lineage was kept for {admin}")
    env.check(
        certificate_names(env, admin) == [admin],
        f"the certificate nginx presents for {admin} names more than the name: "
        f"{certificate_names(env, admin)}",
    )
    env.check(
        sorted(certificate_names(env, base)) == sorted([base, shop]),
        "the shared certificate names the name that has one of its own",
    )
    env.check(
        served_chain_verifies(env, admin, root_file) == "200 verify=0",
        f"{admin} is not served with a certificate that verifies",
    )
    env.check("Pebble" in lineage_issuer(env, admin), "the name's certificate is not Pebble's")
    listed = json_of(env.srv(f"noust domain list {base} --json"), "domain list")
    by_name = {item["domain"]: item for item in listed["items"]}
    env.check(
        by_name[admin]["certificate"] == "own" and by_name[admin]["served_by"] == admin,
        f"noust domain list does not say which certificate serves {admin}: {by_name[admin]}",
    )
    env.check(
        by_name[shop]["certificate"] == "shared" and by_name[shop]["served_by"] == base,
        f"noust domain list does not say the shared certificate serves {shop}: {by_name[shop]}",
    )
    certs = json_of(env.srv("noust cert list --json"), "cert list")
    serves = {item["name"]: item.get("serves", []) for item in certs["certificates"]}
    env.check(
        serves.get(admin) == [admin] and sorted(serves.get(base, [])) == sorted([base, shop]),
        f"noust cert list does not say which names each certificate serves: {serves}",
    )
    site = env.srv(f"cat /etc/nginx/sites-available/{base}").stdout
    env.check(
        site.count(f"server_name {admin};") == 2 and f"server_name {base} {shop};" in site,
        "the site has no server block pair of its own for the name",
    )

    # -- a name that shares takes a certificate of its own while visitors keep coming
    start_probe(env, shop, root_file)
    switched = env.srv(
        f"noust domain certificate {base} {shop} --own",
        timeout=900,
        check=False,
        label=f"noust domain certificate {base} {shop} --own",
    )
    seen = stop_probe(env)
    env.check(
        switched.returncode == 0,
        f"taking a certificate of its own failed:\n{(switched.stdout + switched.stderr)[-2500:]}",
    )
    env.check(
        only_good_visits(seen),
        f"a request to {shop} failed or did not verify while it changed certificates: {seen}",
    )
    env.check(
        certificate_names(env, shop) == [shop], f"{shop} does not present its own certificate"
    )
    env.check(
        certificate_names(env, base) == [base],
        f"the shared certificate was not ordered again without the names that have their own: "
        f"{certificate_names(env, base)}",
    )
    env.check(
        served_chain_verifies(env, base, root_file) == "200 verify=0",
        "the application's own name stopped being served",
    )

    # -- a name whose DNS points elsewhere is refused by the authority, in its words, and
    #    nothing changes: not the site, not the choice, not the certificates
    env.srv(f"noust domain add {base} {lost} --no-cert --no-dns", timeout=300)
    digest = site_digest(env, base)
    pid = nginx_pid(env)
    refused = env.srv(
        f"noust domain certificate {base} {lost} --own",
        timeout=600,
        check=False,
        label=f"noust domain certificate {base} {lost} --own (its A record is 192.0.2.1)",
    )
    said = refused.stdout + refused.stderr
    env.check(
        refused.returncode != 0,
        "a certificate was said to be ordered for a name that points elsewhere",
    )
    # Noust names the name and its DNS; certbot's own words follow verbatim, authority's detail and all.
    env.check(
        lost in said and "Detail:" in said,
        f"the refusal does not name the name and carry certbot's own words: {said[-800:]}",
    )
    env.check(lost not in lineages_on_disk(env), f"a lineage was kept for {lost}")
    env.check(site_digest(env, base) == digest, "the site changed although the order was refused")
    env.check(nginx_pid(env) == pid, "nginx was restarted by a change that did not happen")
    listed = json_of(env.srv(f"noust domain list {base} --json"), "domain list")
    env.check(
        {item["domain"]: item["certificate"] for item in listed["items"]}[lost] == "shared",
        "the choice was kept although the order was refused",
    )
    for host in (base, shop, admin):
        env.check(
            served_chain_verifies(env, host, root_file) == "200 verify=0",
            f"{host} stopped being served after a refused order for another name",
        )

    # -- a renewal asked for the application renews every lineage it has, and reloads nginx
    before = {name: live_fingerprint(env, name) for name in (base, shop, admin)}
    pid = nginx_pid(env)
    env.srv(
        f"noust cert renew --domain {base} --force",
        timeout=900,
        label="noust cert renew --force (the application and the names with their own)",
    )
    after = {name: live_fingerprint(env, name) for name in (base, shop, admin)}
    env.check(
        all(after[name] != before[name] for name in before),
        f"a lineage was not renewed: before {before}, after {after}",
    )
    env.check(nginx_pid(env) == pid, "nginx was restarted by the renewal, not reloaded")
    for host in (base, shop, admin):
        env.check(
            served_chain_verifies(env, host, root_file) == "200 verify=0",
            f"{host} does not verify after the renewal",
        )

    # -- removing a name retires its lineage and its block, and orders nothing else again
    shared_before = live_fingerprint(env, base)
    shop_before = live_fingerprint(env, shop)
    env.srv(f"noust domain remove {base} {admin}", timeout=600)
    env.check(admin not in lineages_on_disk(env), f"the lineage of the removed {admin} stayed")
    env.check(
        not env.srv(f"test -e /etc/letsencrypt/renewal/{admin}.conf", check=False).returncode == 0,
        "certbot still has a renewal configuration for the removed name",
    )
    env.check(
        admin not in env.srv(f"cat /etc/nginx/sites-available/{base}").stdout, "the block stayed"
    )
    env.check(
        live_fingerprint(env, base) == shared_before,
        "removing a name that had its own certificate ordered the shared one again",
    )
    env.check(live_fingerprint(env, shop) == shop_before, "another name's certificate changed")

    # -- giving the certificate back: the shared one learns the name first, and the lineage goes
    #    last. A shared name nobody can prove would stop the shared order, so it goes first.
    env.srv(f"noust domain remove {base} {lost}", timeout=600)
    start_probe(env, shop, root_file)
    back = env.srv(
        f"noust domain certificate {base} {shop} --shared",
        timeout=900,
        check=False,
        label=f"noust domain certificate {base} {shop} --shared",
    )
    seen = stop_probe(env)
    env.check(
        back.returncode == 0,
        f"giving the certificate back failed:\n{(back.stdout + back.stderr)[-2500:]}",
    )
    env.check(
        only_good_visits(seen),
        f"a request to {shop} failed or did not verify while it went back: {seen}",
    )
    env.check(
        shop in certificate_names(env, base) and base in certificate_names(env, base),
        "the shared certificate does not name the name that came back",
    )
    env.check(shop not in lineages_on_disk(env), f"the lineage of {shop} stayed after it came back")
    audited = env.srv("noust audit list --action domains.certificate --json --limit 50").stdout
    env.check(
        '"own"' in audited and '"shared"' in audited,
        "the changes of certificate are not in the audit log",
    )
    forget_name_certificates(env, base, [shop, admin, lost])


@scenario("acme_name_certificate_dns01")
def scenario_acme_name_certificate_dns01(env: Env) -> None:
    """A name with a certificate of its own is proven with a DNS record while the application's is proven over HTTP, and each renews its own way."""
    stack = start_acme_stack(env)
    base = f"crtdns.{ACME_ZONE}"
    own = f"crtdnsown.{ACME_ZONE}"
    wild = f"*.crtw.{ACME_ZONE}"
    wild_lineage = f"_wildcard.crtw.{ACME_ZONE}"
    forget_name_certificates(env, base, [own, wild_lineage])
    root_file = "/root/pebble-root.pem"
    put_file(SERVER, root_file, pebble_root(env))
    # The application answers on port 80; the other name points where nothing listens, so only a
    # DNS record can prove it.
    set_acme_names({"crtdns": env.server_ip, "crtdnsown": "192.0.2.1"})
    add_cloudflare_account(env, stack)

    created = env.srv(
        f"noust create -d {base} -s {ECHO_URL} -t nodejs",
        timeout=DEPLOY_TIMEOUT + 120,
        check=False,
    )
    env.check(
        created.returncode == 0,
        f"noust create with a certificate failed:\n{(created.stdout + created.stderr)[-2500:]}",
    )
    added = env.srv(
        f"noust domain add {base} {own} --no-dns --own-certificate --dns cf",
        timeout=900,
        check=False,
        label=f"noust domain add {base} {own} --no-dns --own-certificate --dns cf",
    )
    env.check(
        added.returncode == 0,
        f"a certificate proven by DNS for one name failed:\n{(added.stdout + added.stderr)[-3000:]}",
    )
    env.check(own in lineages_on_disk(env), f"no lineage was kept for {own}")
    env.check(
        certificate_names(env, own) == [own], f"{own} does not present a certificate of its own"
    )
    env.check(
        served_chain_verifies(env, own, root_file) == "200 verify=0",
        f"{own} is not served with the certificate DNS ordered",
    )

    # -- each lineage keeps its own way of being renewed: the hooks are in the name's alone
    conf_own = env.srv(f"cat /etc/letsencrypt/renewal/{own}.conf").stdout
    env.check(
        "manual_auth_hook" in conf_own
        and "dns challenge auth" in conf_own
        and "--account cf" in conf_own
        and "manual_cleanup_hook" in conf_own
        and "pref_challs = dns-01" in conf_own,
        f"certbot does not keep the DNS hooks in the renewal of {own}:\n{conf_own}",
    )
    conf_base = env.srv(f"cat /etc/letsencrypt/renewal/{base}.conf").stdout
    env.check(
        "manual_auth_hook" not in conf_base and "pref_challs = dns-01" not in conf_base,
        f"the application's own renewal was changed to DNS by a name's:\n{conf_base}",
    )
    record = f"_acme-challenge.{own}"
    made = challenge_records_made(stack, record)
    env.check(len(made) == 1, f"expected one challenge record for {own}, found {made}")
    env.check(
        made[0].get("comment") == "managed-by: noust acme challenge",
        f"the record has no ownership marker: {made[0]}",
    )
    env.check(
        challenge_records_made(stack, f"_acme-challenge.{base}") == [],
        "a DNS record was published for the application, which is proven over HTTP",
    )
    env.check(txt_records(stack, record) == [], "the challenge record is still in the zone")
    env.check(
        token_in_files(env, stack.token) == "",
        "the provider's token is written in certbot's files or in Noust's configuration",
    )

    # -- a renewal of the application publishes the name's record again, and only the name's
    before = {name: live_fingerprint(env, name) for name in (base, own)}
    pid = nginx_pid(env)
    env.srv(
        f"noust cert renew --domain {base} --force",
        timeout=900,
        label="noust cert renew --force (HTTP for the application, DNS for the name)",
    )
    after = {name: live_fingerprint(env, name) for name in (base, own)}
    env.check(all(after[name] != before[name] for name in before), "a lineage was not renewed")
    env.check(nginx_pid(env) == pid, "nginx was restarted by the renewal, not reloaded")
    env.check(len(challenge_records_made(stack, record)) == 2, "the renewal published no record")
    env.check(
        challenge_records_made(stack, f"_acme-challenge.{base}") == [],
        "the renewal of the application published a DNS record",
    )
    env.check(txt_records(stack, record) == [], "the renewal left its record in the zone")

    # -- changing how the name is proven to something that cannot work is refused and changes nothing
    digest = site_digest(env, base)
    refused = env.srv(
        f"noust domain certificate {base} {own} --own --http",
        timeout=600,
        check=False,
        label=f"noust domain certificate {base} {own} --own --http (nothing answers on its port 80)",
    )
    env.check(refused.returncode != 0, "a name nothing answers for was said to be proven over HTTP")
    conf_after = env.srv(f"cat /etc/letsencrypt/renewal/{own}.conf").stdout
    env.check("manual_auth_hook" in conf_after, "the failed change took the DNS hooks away")
    env.check(site_digest(env, base) == digest, "the site changed although the order was refused")
    env.check(
        served_chain_verifies(env, own, root_file) == "200 verify=0",
        f"{own} stopped being served after the refused change",
    )

    # -- a wildcard of its own, proven by DNS, is kept in the lineage no host can have
    wildcard = env.srv(
        f"noust domain add {base} '{wild}' --no-dns --own-certificate --dns cf",
        timeout=900,
        check=False,
        label=f"noust domain add {base} '{wild}' --no-dns --own-certificate --dns cf",
    )
    env.check(
        wildcard.returncode == 0,
        f"a wildcard with a certificate of its own failed:\n{(wildcard.stdout + wildcard.stderr)[-3000:]}",
    )
    env.check(wild_lineage in lineages_on_disk(env), f"no lineage {wild_lineage} was kept")
    visited = f"anything.crtw.{ACME_ZONE}"
    env.check(
        certificate_names(env, visited) == [wild],
        f"the certificate presented under the wildcard is not the wildcard's alone: "
        f"{certificate_names(env, visited)}",
    )
    env.check(
        served_chain_verifies(env, visited, root_file) == "200 verify=0",
        f"{visited} is not served with the wildcard's certificate",
    )
    env.srv(f"noust domain remove {base} '{wild}'", timeout=600)
    env.check(wild_lineage not in lineages_on_disk(env), "the wildcard's lineage stayed")

    forget_name_certificates(env, base, [own, wild_lineage])
    env.srv("noust dns provider remove cf -y")


# ---------------------------------------------------------------------------
# The time from `apt install` to the first deploy over HTTPS, measured (N-10)
# ---------------------------------------------------------------------------

FRESH = f"{PREFIX}-fresh"
FRESH_IMAGE = f"{PREFIX}-fresh:latest"
DEB_BUILDER = f"{PREFIX}-debbuild"
DEB_DIR = INTEGRATION_DIR / ".build" / "deb"

#: Where each run's measurement is kept, and what it is held to: ten minutes from a bare
#: server to an application served over HTTPS (docs/superpowers/plans/2026-09-26-wasm-after-2.0-roadmap.md).
FIRST_DEPLOY_RESULT = INTEGRATION_DIR / "results" / "first-deploy.json"
FIRST_DEPLOY_BUDGET_SECONDS = 600

#: What the package's build needs on Debian 13 (obs/debian.control, Build-Depends) and what the
#: build script itself runs.
DEB_BUILD_PACKAGES = (
    "build-essential debhelper dh-python pybuild-plugin-pyproject python3-all "
    "python3-setuptools python3-wheel dpkg-dev fakeroot"
)


def build_noust_deb() -> Path:
    """
    Build the Debian package of the committed tree, the way OBS builds it, in a container.

    The package is staged with ``packaging/obs/stage.sh --snapshot`` (the files an OBS package
    receives) and built with ``packaging/obs/build.sh deb`` on Debian 13, so what a scenario
    installs is the same package ``apt install noust`` would fetch, minus the repository.

    Returns:
        The ``.deb`` on the host. A package already built for this commit is reused.

    Raises:
        HarnessError: The build failed.
    """
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    out = DEB_DIR / head
    built = sorted(out.glob("noust_*.deb"))
    if built:
        print(f"[setup] reusing {built[-1].name} (commit {head})")
        return built[-1]
    print(f"[setup] building the Debian package of commit {head}")
    out.mkdir(parents=True, exist_ok=True)
    remove_container(DEB_BUILDER)
    with tempfile.TemporaryDirectory(prefix="noust-stage-") as scratch:
        stage = Path(scratch) / "stage"
        subprocess.run(
            ["sh", str(REPO_ROOT / "packaging/obs/stage.sh"), "--snapshot", "noust", str(stage)],
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
        docker(
            "run",
            "-d",
            "--name",
            owned(DEB_BUILDER),
            "--label",
            LABEL,
            "debian:13",
            "sleep",
            "infinity",
            timeout=120,
        )
        try:
            docker(
                "exec",
                DEB_BUILDER,
                "bash",
                "-lc",
                f"apt-get update >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y "
                f"--no-install-recommends {DEB_BUILD_PACKAGES} >/dev/null",
                timeout=1200,
            )
            docker("cp", str(stage), f"{DEB_BUILDER}:/stage", timeout=120)
            docker("cp", str(REPO_ROOT / "packaging/obs/build.sh"), f"{DEB_BUILDER}:/build.sh")
            docker(
                "exec",
                DEB_BUILDER,
                "sh",
                "/build.sh",
                "deb",
                "/stage",
                "/out",
                timeout=1800,
            )
            docker("cp", f"{DEB_BUILDER}:/out/.", str(out), timeout=120)
        finally:
            remove_container(DEB_BUILDER)
    built = sorted(out.glob("noust_*.deb"))
    if not built:
        raise HarnessError(f"the package build left no noust_*.deb in {out}")
    print(f"[setup] built {built[-1].name}")
    return built[-1]


def build_fresh_image() -> None:
    """Build the image of a server as a provider hands it over (cached after the first time)."""
    print(f"[setup] building {FRESH_IMAGE}")
    docker(
        "build",
        "--label",
        LABEL,
        "-t",
        owned(FRESH_IMAGE),
        "-f",
        str(NETWORK_DIR / "Dockerfile.fresh"),
        str(NETWORK_DIR),
        timeout=900,
    )


def fresh_run(
    env: Env, label: str, script: str, *, timeout: int, check: bool = True
) -> tuple[float, subprocess.CompletedProcess[str]]:
    """
    Run a script in the fresh server, timed.

    Args:
        env: The environment (for the evidence).
        label: What it is.
        script: The script (bash).
        timeout: Seconds.
        check: Fail the scenario on a non-zero exit.

    Returns:
        The seconds it took and the finished command.
    """
    started = time.monotonic()
    proc = docker("exec", owned(FRESH), "bash", "-lc", script, timeout=timeout, check=False)
    took = time.monotonic() - started
    tail = (proc.stdout + proc.stderr).strip()[-1500:]
    env.evidence.append(f"[fresh] $ {label}  ({took:.1f}s, exit {proc.returncode})\n{tail}")
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed (exit {proc.returncode}): [fresh] {label}\n{tail}")
    return took, proc


@scenario("first_deploy_with_tls_is_timed")
def scenario_first_deploy(env: Env) -> None:
    """From a bare Debian 13 server to an application over HTTPS: `apt install`, `setup init`, `create` (N-10)."""
    # Two live containers at most: the server and the visitor stop before the package is built, and
    # the machine under test and the authority are the two that run after.
    stopped = [name for name in (SERVER, CLIENT) if is_running(name)]
    for name in stopped:
        docker("stop", owned(name), timeout=90)
    domain = f"fresh.{ACME_ZONE}"
    try:
        package = build_noust_deb()
        build_fresh_image()
        remove_container(FRESH)
        docker(
            "run",
            "-d",
            "--name",
            owned(FRESH),
            "--label",
            LABEL,
            "--network",
            NETWORK,
            "--privileged",
            "--cgroupns=host",
            "-v",
            "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/run/lock",
            FRESH_IMAGE,
            timeout=90,
        )
        wait_for_systemd(FRESH)
        # The stack (Pebble standing in for Let's Encrypt, and the DNS that names this server) is
        # what the operator's world looks like; building it is not part of the time.
        stack = start_acme_stack(env, target=FRESH, configure_noust=False)
        env.check(stack.pebble_ip != "", "the stack did not come up")
        copied = docker("cp", str(package), f"{FRESH}:/root/{package.name}", timeout=120)
        env.check(copied.returncode == 0, "the package could not be copied")

        phases: dict[str, float] = {}
        phases["apt_install"], _ = fresh_run(
            env,
            f"apt-get update && apt-get install -y ./{package.name}",
            f"apt-get update >/dev/null && DEBIAN_FRONTEND=noninteractive "
            f"apt-get install -y --no-install-recommends /root/{package.name} 2>&1 | tail -5",
            timeout=1800,
        )
        version = fresh_run(env, "noust --version", "noust --version", timeout=60)[1].stdout.strip()
        phases["setup_init"], _ = fresh_run(
            env, "noust setup init -y", "noust setup init -y 2>&1 | tail -15", timeout=1800
        )

        # The repository: on GitHub for an operator, a git daemon here. Not part of the time.
        sh_in(FRESH, "mkdir -p /root/fixtures")
        docker(
            "cp", str(INTEGRATION_DIR / "fixtures" / "echo-app"), f"{FRESH}:/root/fixtures/echo-app"
        )
        # docker cp keeps the host user's ownership, and git refuses a tree another user owns
        # ("not in a git directory") when it runs as root.
        sh_in(FRESH, "chown -R root:root /root/fixtures")
        fresh_run(
            env,
            "(the repository) git daemon",
            "cd /root/fixtures/echo-app && npm install --package-lock-only >/dev/null 2>&1; "
            "git init -q && git config user.email it@example.com && git config user.name it && "
            "git add -A && git commit -q -m fixture && "
            "git daemon --reuseaddr --export-all --base-path=/root/fixtures --listen=127.0.0.1 "
            "--detach /root/fixtures",
            timeout=300,
        )
        # Pebble stands in for Let's Encrypt: told once, and registered, outside the time.
        fresh_run(
            env,
            "noust cert acme --ca custom (Pebble instead of Let's Encrypt)",
            f"noust cert acme --ca custom --server https://pebble:14000/dir --email {ACME_EMAIL} "
            "--register",
            timeout=300,
        )

        phases["create_with_tls"], created = fresh_run(
            env,
            f"noust create -d {domain} -s {ECHO_URL} -t nodejs",
            f"noust create -d {domain} -s {ECHO_URL} -t nodejs",
            timeout=1800,
            check=False,
        )
        env.check(
            created.returncode == 0,
            f"noust create failed on the fresh server:\n{(created.stdout + created.stderr)[-2500:]}",
        )
        _, answer = fresh_run(
            env,
            f"https://{domain}/ against Pebble's root",
            "curl -fsS https://pebble:15000/roots/0 -o /root/pebble-root.pem && "
            f"curl -sS -o /dev/null -w '%{{http_code}} verify=%{{ssl_verify_result}}' "
            f"--cacert /root/pebble-root.pem https://{domain}/",
            timeout=120,
            check=False,
        )
        env.check(
            answer.stdout.strip() == "200 verify=0",
            f"the application is not served over HTTPS with a certificate that verifies: "
            f"{answer.stdout!r} {answer.stderr!r}",
        )

        total = phases["apt_install"] + phases["setup_init"] + phases["create_with_tls"]
        kernel = sh_in(FRESH, "uname -r").strip()
        record = {
            "measured": "tests/integration/network_run.py first_deploy_with_tls_is_timed",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "noust": version,
            "package": package.name,
            "server": "Debian 13 (a container with systemd, no nginx, certbot, Node.js, git or Noust)",
            "authority": "Pebble (Let's Encrypt's test ACME server), HTTP-01",
            "kernel": kernel,
            "cpus": os.cpu_count(),
            "phases_seconds": {name: round(value, 1) for name, value in phases.items()},
            "total_seconds": round(total, 1),
            "budget_seconds": FIRST_DEPLOY_BUDGET_SECONDS,
            "within_budget": total <= FIRST_DEPLOY_BUDGET_SECONDS,
            "notes": (
                "The clock runs from `apt-get update` to the HTTPS check, over the machine's own "
                "network; the package is installed from a local file, so the repository's "
                "download is not in it. Pebble stands in for Let's Encrypt, so the authority's "
                "latency is not in it either."
            ),
        }
        FIRST_DEPLOY_RESULT.parent.mkdir(parents=True, exist_ok=True)
        FIRST_DEPLOY_RESULT.write_text(json.dumps(record, indent=2) + "\n")
        env.note(
            f"first deploy: {json.dumps(record['phases_seconds'])}, total {record['total_seconds']}s"
        )
        env.check(
            total <= FIRST_DEPLOY_BUDGET_SECONDS,
            f"from `apt install` to the first deploy over HTTPS took {total:.0f}s, over the "
            f"{FIRST_DEPLOY_BUDGET_SECONDS}s the roadmap asks for: {record['phases_seconds']}",
        )
    finally:
        stop_acme_stack(env)
        remove_container(FRESH)
        for name in stopped:
            docker("start", owned(name), check=False, timeout=90)


# ---------------------------------------------------------------------------
# More scenarios are added below, one per proof.
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--keep", action="store_true", help="Leave the containers running.")
    parser.add_argument(
        "--reuse", action="store_true", help="Use the containers a --keep run left, set up nothing."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="With --reuse: rebuild the wheel and install it over the running server first.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="Run only scenarios whose name contains this text (repeatable).",
    )
    parser.add_argument("--list", action="store_true", help="List the scenarios and stop.")
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Bring the containers up (or refresh them with --reuse --refresh) and stop.",
    )
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="Remove every container, the network, the volume and the images the harness made.",
    )
    parser.add_argument(
        "--skip-image-build", action="store_true", help="Use the images already built."
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=INTEGRATION_DIR / ".build" / "network-evidence",
        help="Where each scenario's commands and output are written.",
    )
    return parser.parse_args()


def selected(names: list[str] | None) -> list[tuple[str, EnvFn]]:
    if not names:
        return list(SCENARIOS)
    chosen = [(n, fn) for n, fn in SCENARIOS if any(pattern in n for pattern in names)]
    if not chosen:
        raise HarnessError(f"no scenario matches {names!r}; available: {[n for n, _ in SCENARIOS]}")
    return chosen


def setup(args: argparse.Namespace) -> Env:
    """
    Bring the containers up (or find the ones a previous --keep run left).

    Args:
        args: The command line.

    Returns:
        The environment.
    """
    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)
    wheel = build_wheel(workdir)
    if args.reuse and is_running(SERVER) and is_running(CLIENT):
        print("[setup] reusing the running containers")
        if args.refresh:
            refresh_noust(wheel)
    else:
        teardown()
        if not args.skip_image_build:
            build_images()
        docker("network", "create", "--label", LABEL, owned(NETWORK), timeout=30)
        start_server()
        start_client()
        wait_for_systemd(SERVER)
        install_noust(SERVER, wheel)
        install_fixtures()
    client_ip = container_ip(CLIENT)
    # The "CDN": a second address on the client's interface, in the same network.
    prefix, _, last = client_ip.rpartition(".")
    cdn_ip = f"{prefix}.{int(last) + 100}"
    sh_in(
        CLIENT,
        f"ip addr show dev eth0 | grep -q ' {cdn_ip}/' || ip addr add {cdn_ip}/16 dev eth0",
        check=False,
    )
    env = Env(server_ip=container_ip(SERVER), client_ip=client_ip, cdn_ip=cdn_ip, wheel=wheel)
    env.versions = versions_of(env)
    return env


#: Session cookies the providers hand out: throwaway ones, but evidence is shared, so they never
#: reach a file.
_SESSION_COOKIE = re.compile(r"(authelia_session|_oauth2_proxy)=[^;\"'\s]+")


def redacted(text: str) -> str:
    """The text with every session cookie value replaced."""
    return _SESSION_COOKIE.sub(r"\1=<redacted>", text)


def write_evidence(directory: Path, name: str, status: str, env: Env, detail: str) -> None:
    """Write one scenario's evidence to a file."""
    directory.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    body = [f"scenario: {name}", f"result: {status}"]
    if detail:
        body.append(f"detail: {detail}")
    body.append("versions: " + json.dumps(env.versions, indent=2))
    body.append("")
    body.extend(env.evidence)
    (directory / f"{safe}.txt").write_text(redacted("\n\n".join(body) + "\n"))


def main() -> int:
    args = parse_args()
    if args.list:
        for name, fn in SCENARIOS:
            print(f"{name}: {(fn.__doc__ or '').strip().splitlines()[0] if fn.__doc__ else ''}")
        return 0
    if args.teardown:
        teardown(images=True)
        print("[teardown] done")
        return 0
    started = time.monotonic()
    failures = skipped = attempted = 0
    env: Env | None = None
    try:
        env = setup(args)
        print(f"[setup] {json.dumps(env.versions, indent=2)}")
        to_run = [] if args.setup_only else selected(args.scenario)
        for name, fn in to_run:
            print(f"\n=== {name} ===")
            attempted += 1
            env.evidence = []
            if (_STACK is not None or _CLIENT_STOPPED) and not name.startswith("acme_"):
                # Pebble and the DNS container took the visitor's place: give it back.
                stop_acme_stack(env)
            status, detail = "PASS", ""
            try:
                fn(env)
            except ScenarioSkipped as exc:
                skipped += 1
                status, detail = "SKIP", str(exc)
            except (AssertionError, HarnessError) as exc:
                failures += 1
                status, detail = "FAIL", str(exc)
            except subprocess.TimeoutExpired as exc:
                failures += 1
                status, detail = "FAIL", f"timed out: {exc}"
            print(f"{status}: {name}" + (f": {detail}" if detail else ""))
            write_evidence(args.evidence_dir, name, status, env, detail)
    finally:
        if _STACK is not None or _CLIENT_STOPPED:
            stop_acme_stack(env)
        if args.keep:
            print(f"\n[teardown] --keep given: {SERVER} and {CLIENT} stay up")
        else:
            teardown(images=False)
    print(
        f"\n[summary] {attempted - failures - skipped} passed, {failures} failed, "
        f"{skipped} skipped, in {time.monotonic() - started:.0f}s; "
        f"evidence in {args.evidence_dir}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
