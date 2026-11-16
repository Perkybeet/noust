#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the scenarios that run the central's image in an engine share (``docker_run.py``).

The engine's container is the NAS. The central is a container of that engine's Docker, built from
``packaging/container/`` with the wheel of this tree, and started the way ``docs/central-deploy-here.md`` says: an
unprivileged user (10001), no capabilities, a read-only file system, the engine's socket mounted with its group,
one shared folder at the same path inside and outside, and ``NOUST_DEPLOY_HERE=containers``.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

#: What the central's container is called inside the engine, as the compose file of the guide calls it.
CENTRAL = "noust"

DATA = "/volume1/docker/noust"
STACKS = "/volume1/docker/stacks"
#: The folder a scenario gives a central of its own, beside ``DATA-*`` (``noust-host``, ``noust-ask``): the data of a
#: scenario that ran before on a machine that is kept would otherwise be in it, with the applications it made.
OTHER_DATA = "/volume1/docker/elsewhere"
LABEL = "noust.test=1"


@dataclass
class Found:
    """
    What a scenario found, in the shape ``docker_run.py`` reports.

    Attributes:
        checks: ``(name, ok, detail)``.
        notes: What the real thing said.
        skips: ``(name, why)``.
        status: Set to ``na`` or ``skip`` when the scenario does not apply.
        reason: Why.
    """

    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    skips: list[tuple[str, str]] = field(default_factory=list)
    status: str = "pass"
    reason: str = ""

    def check(self, name: str, condition: bool, detail: Any = "") -> bool:
        """
        Record one thing that must be true.

        Args:
            name: What was checked.
            condition: Whether it held.
            detail: What was found, kept when it did not.

        Returns:
            The condition.
        """
        print(
            f"  {'ok  ' if condition else 'FAIL'} {name}{'' if condition else f': {str(detail)[:300]}'}",
            flush=True,
        )
        self.checks.append((name, bool(condition), "" if condition else str(detail)[:1500]))
        return bool(condition)

    def note(self, key: str, value: Any) -> None:
        """
        Args:
            key: What it is.
            value: What it said.
        """
        self.notes[key] = value

    def skip(self, name: str, why: str) -> None:
        """
        Args:
            name: What was not checked.
            why: The reason.
        """
        self.skips.append((name, why))


class Machine:
    """
    The engine's container seen as the NAS: run things on it, and on its Docker.

    Args:
        harness: The run (``docker_run.Harness``).
        engine: The engine (``docker_run.Engine``).
    """

    def __init__(self, harness: Any, engine: Any) -> None:
        self.harness = harness
        self.engine = engine

    def sh(
        self,
        *argv: str,
        timeout: int = 180,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run a program on the machine.

        Args:
            *argv: The program and its arguments.
            timeout: Seconds.
            input_text: Standard input.
            env: Variables for it.

        Returns:
            The completed process.
        """
        return self.harness.inside(
            self.engine, *argv, timeout=timeout, input_text=input_text, env=env
        )

    def docker(self, *argv: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        """
        Args:
            *argv: ``docker`` arguments, for the engine's own client.
            timeout: Seconds.

        Returns:
            The completed process.
        """
        return self.sh("docker", *argv, timeout=timeout)

    def socket_group(self) -> str:
        """
        Returns:
            The group number that owns the engine's socket, as the guide says to look it up.
        """
        return self.sh("stat", "-c", "%g", "/var/run/docker.sock").stdout.strip()

    def reset_nas(self) -> None:
        """Take every container, volume and folder of an earlier scenario away, by the label and the folders."""
        for name in self.docker("ps", "-aq", "--filter", "label=w3rd.keep=no").stdout.split():
            self.docker("rm", "-fv", name)
        ids = self.docker(
            "ps", "-aq", "--filter", f"label={LABEL}", "--filter", "label=w3rd.nas=1"
        ).stdout.split()
        if ids:
            self.docker("rm", "-fv", *ids)
        self.sh(
            "sh",
            "-c",
            f"rm -rf {DATA}/* {DATA}/.[!.]* {STACKS}/* {DATA}-* {OTHER_DATA} 2>/dev/null; "
            f"mkdir -p {DATA}/apps {STACKS}; chown -R 10001:10001 {DATA}",
        )


class Central:
    """
    The central's image, running in an engine the way a NAS runs it.

    Args:
        machine: The NAS.
        image: The central's image.
        name: The container's name.
        deploy_here: Whether it is told to deploy on the machine's Docker.
        socket: Whether the engine's socket is mounted.
        group: Whether the socket's group is given to the account.
        data: The folder it keeps its data in, as it sees it.
        host_data: The folder of the machine that is mounted there (the same path by default).
        network: ``--network`` (the default bridge when empty).
        cgroupns: ``--cgroupns`` when given.
        hostname: ``--hostname`` when given.
        extra_mounts: More ``-v`` mounts.
        run_service: Run ``noust central run`` (the console and the scheduler) as the container's command.
    """

    def __init__(
        self,
        machine: Machine,
        image: str,
        *,
        name: str = CENTRAL,
        deploy_here: bool = True,
        socket: bool = True,
        group: bool = True,
        data: str = DATA,
        host_data: str | None = None,
        network: str = "",
        cgroupns: str = "",
        hostname: str = "",
        extra_mounts: tuple[str, ...] = (f"{STACKS}:{STACKS}",),
        run_service: bool = True,
    ) -> None:
        self.machine = machine
        self.image = image
        self.name = name
        self.deploy_here = deploy_here
        self.socket = socket
        self.group = group
        self.data = data
        self.host_data = host_data or data
        self.network = network
        self.cgroupns = cgroupns
        self.hostname = hostname
        self.extra_mounts = extra_mounts
        self.run_service = run_service

    def start(self) -> None:
        """
        Start the container.

        Raises:
            RuntimeError: Docker did not start it.
        """
        self.machine.docker("rm", "-fv", self.name)
        argv = [
            "run",
            "-d",
            "--name",
            self.name,
            "--label",
            LABEL,
            "--label",
            "w3rd.nas=1",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--read-only",
            "--tmpfs",
            "/tmp:size=16m,mode=1777",
            "-v",
            f"{self.host_data}:{self.data}",
        ]
        for mount in self.extra_mounts:
            argv += ["-v", mount]
        if self.socket:
            argv += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]
            if self.group:
                argv += ["--group-add", self.machine.socket_group()]
        env = {
            "NOUST_DATA_DIR": self.data,
            "NOUST_APPS_DIR": f"{self.data}/apps",
            "HOME": self.data,
            "TZ": "UTC",
        }
        if self.deploy_here:
            env["NOUST_DEPLOY_HERE"] = "containers"
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        if self.network:
            argv += ["--network", self.network]
        if self.cgroupns:
            argv += ["--cgroupns", self.cgroupns]
        if self.hostname:
            argv += ["--hostname", self.hostname]
        if not self.run_service:
            # The image's entrypoint is ``noust``: a container that only has to be there to be asked things
            # is kept by another program, and ``noust`` is what each question runs.
            argv += ["--entrypoint", "sleep", self.image, "3600"]
        else:
            argv += [self.image, "central", "run"]
        started = self.machine.docker(*argv)
        if started.returncode != 0:
            raise RuntimeError(f"the central did not start: {started.stderr.strip()[-500:]}")

    def noust(self, *args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        """
        Run ``noust`` in the central's container, as ``docker exec noust noust ...`` does.

        Args:
            *args: Its arguments.
            timeout: Seconds.

        Returns:
            The completed process.
        """
        return self.machine.docker("exec", self.name, "noust", *args, timeout=timeout)

    def run(self, *argv: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        """
        Args:
            *argv: Any program of the central's image.
            timeout: Seconds.

        Returns:
            The completed process.
        """
        return self.machine.docker("exec", self.name, *argv, timeout=timeout)

    def json(self, *args: str, timeout: int = 300) -> Any:
        """
        Args:
            *args: A ``noust`` command that takes ``--json``.
            timeout: Seconds.

        Returns:
            What it printed, decoded; None when it is not JSON.
        """
        done = self.noust(*args, timeout=timeout)
        try:
            return json.loads(done.stdout)
        except ValueError:
            return None

    def logs(self, tail: int = 60) -> str:
        """
        Args:
            tail: How many lines.

        Returns:
            The end of the container's log.
        """
        done = self.machine.docker("logs", "--tail", str(tail), self.name)
        return done.stdout + done.stderr

    def remove(self) -> None:
        """Remove the container by name."""
        self.machine.docker("rm", "-fv", self.name)


def ready(central: Central, seconds: int = 90) -> bool:
    """
    Wait until the central's console answers on its health route.

    Args:
        central: The central.
        seconds: The longest.

    Returns:
        Whether it did.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        done = central.run(
            "python3",
            "-c",
            "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', "
            "context=ssl._create_unverified_context(), timeout=3)",
            timeout=20,
        )
        if done.returncode == 0:
            return True
        time.sleep(2)
    return False


def served(machine: Machine, port: int, needle: str, seconds: int = 60) -> bool:
    """
    Wait until the machine itself gets a page with some text from a port.

    Args:
        machine: The NAS.
        port: The port on its loopback.
        needle: What the page says.
        seconds: The longest.

    Returns:
        Whether it did.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        done = machine.sh("wget", "-q", "-O", "-", f"http://127.0.0.1:{port}/", timeout=15)
        if done.returncode == 0 and needle in done.stdout:
            return True
        time.sleep(1.5)
    return False


def start_central(harness: Any, engine: Any, **options: Any) -> tuple[Machine, Central]:
    """
    Make the NAS clean and start a central on it.

    Args:
        harness: The run.
        engine: The engine.
        **options: What :class:`Central` takes.

    Returns:
        The machine and its central.
    """
    machine = Machine(harness, engine)
    machine.reset_nas()
    central = Central(machine, f"{harness.central_image_name}", **options)
    central.start()
    return machine, central


def deploy_web(
    central: Central, harness: Any, domain: str, tag: str, port: int, *extra: str
) -> subprocess.CompletedProcess[str]:
    """
    Deploy a container application from an image of the engine's local registry.

    Args:
        central: The central.
        harness: The run.
        domain: The application's name.
        tag: A tag of ``w3rd/web``.
        port: The port of the machine; 0 leaves it to Noust.
        *extra: More arguments for ``noust create``.

    Returns:
        What ``noust create`` did.
    """
    argv = [
        "create",
        "-d",
        domain,
        "--image",
        f"{harness.registry}/w3rd/web:{tag}",
        "--no-ssl",
        *extra,
    ]
    if port:
        argv += ["-p", str(port)]
    return central.noust(*argv, timeout=600)


def push_image(harness: Any, engine: Any, tag: str, page: str, *, serves: bool = True) -> bool:
    """
    Put a ``w3rd/web`` image with a page in the engine's local registry.

    Args:
        harness: The run.
        engine: The engine.
        tag: ``w3rd/web:<tag>``.
        page: What the page says (ending in its version).
        serves: Whether it listens at all.

    Returns:
        Whether it was pushed.
    """
    spec = {"kind": "web", "tag": f"w3rd/web:{tag}", "page": page, "serves": serves}
    outcome = harness.run_inside(
        engine, "_push", extra_env={"W3RD_ARG": json.dumps(spec)}, timeout=300
    )
    return outcome.status == "pass"
