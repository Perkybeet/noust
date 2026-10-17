#!/usr/bin/env python3
"""
Real-software harness for the network features of Noust: fail2ban, HTTP/3, forward-auth,
prefix routes, the site's history and the Coolify templates,
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
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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

PREFIX = "noust-test-de"
LABEL = "noust.test=1"
NETWORK = f"{PREFIX}-net"
SERVER = f"{PREFIX}-server"
CLIENT = f"{PREFIX}-client"
AUTHELIA = f"{PREFIX}-authelia"
OAUTH2 = f"{PREFIX}-oauth2"
DOCKER_VOLUME = f"{PREFIX}-docker"
SERVER_IMAGE = f"{PREFIX}-server:latest"
CLIENT_IMAGE = f"{PREFIX}-client:latest"

#: What the harness may remove, by exact name. The providers share the server's network
#: namespace, so they go first.
OWNED_CONTAINERS = (AUTHELIA, OAUTH2, CLIENT, SERVER)

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
    for image in (SERVER_IMAGE, CLIENT_IMAGE):
        docker("image", "rm", owned(image), check=False, timeout=60)


def teardown(*, images: bool = False) -> None:
    """
    Remove everything the harness created: containers, network, volume and optionally images.

    Args:
        images: Also remove the two images it built.
    """
    for name in OWNED_CONTAINERS:
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
    wait_for(
        lambda: "Gitea" in through_nginx(env, "git.test", "/install"),
        "gitea's installer to be served",
        timeout=360,
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
