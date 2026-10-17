#!/usr/bin/env python3
"""
Real-software harness for the network features of Noust: fail2ban, HTTP/3, forward-auth,
prefix routes, the site's history and the Coolify templates, against the real programs.

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
import importlib.util
import json
import os
import re
import subprocess
import sys
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

#: What the harness may remove, by exact name.
OWNED_CONTAINERS = (SERVER, CLIENT, AUTHELIA, OAUTH2)

#: The fixtures served by git:// inside the server.
ECHO_REPO = "/root/fixtures/echo-app"
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
    docker("rm", "-f", name, check=False, timeout=60)


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
    (directory / f"{safe}.txt").write_text("\n\n".join(body) + "\n")


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
