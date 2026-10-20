#!/usr/bin/env python3
"""
The installer and the doctor on real machines, and what Noust costs idle (checklist N-02, N-04).

``packaging/install.sh`` is the first thing anybody runs, and ``noust setup doctor`` the first thing
they run when something is wrong; their unit tests answer for a model of the machine. This harness
runs them on machines. It builds the wheel from the working tree and starts:

* one privileged container with systemd as PID 1 (``Dockerfile.systemd``), where the installer runs
  unattended with an administrator, an address, the automatic update and ``--json``, runs a second
  time, and where the doctor and the installer are asked who holds ports 80 and 443 (the web server,
  a program of its own, a container that publishes the port) and the idle cost of the two long
  running units is measured, as the installation left them;
* plain containers with a memory limit of their own (the cgroup file the installer reads is the
  real one), a ``tmpfs`` of 64 MB where Noust writes, and one processor: the machine the installer
  must refuse, ask about, or accept.

The wheel stands in for the one step of the installer that downloads: the virtual environment of the
pip method is made with the working tree's wheel inside, and the installer is given its version, so
that ``pip install noust[all]==VERSION`` has nothing to fetch (the published package would not have
the commands this tree's installer drives). Everything after that step is the real installer on a
real systemd.

It is not part of pytest (nothing here is named ``test_*``): it drives Docker and the network (the
distribution's packages for ``noust setup init``). ``run.py`` is loaded as a module and reused, never
edited. Everything it creates is named ``<prefix>-...`` (``NOUST_IT_PREFIX``) and carries the label
``noust.test=1``; nothing of the host's Docker is shared, and what belongs to anything else is never
listed, stopped or removed.

Usage:
    .venv/bin/python tests/integration/onboard_run.py
    .venv/bin/python tests/integration/onboard_run.py --scenario refuses --scenario asks
    .venv/bin/python tests/integration/onboard_run.py --keep --skip-image-build
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import secrets
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
INSTALLER = REPO_ROOT / "packaging" / "install.sh"
MEASURE_IDLE = REPO_ROOT / "scripts" / "measure_idle.py"

if TYPE_CHECKING:
    # The real run.py on disk, for the checker; the module itself is loaded below.
    from run import HarnessError as HarnessError
    from run import Scenario as Scenario
    from run import ScenarioSkipped as ScenarioSkipped


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
sh = run.sh
docker_exec = run.docker_exec
if not TYPE_CHECKING:
    Scenario = run.Scenario
    HarnessError = run.HarnessError
    ScenarioSkipped = run.ScenarioSkipped

PREFIX = run.RESOURCE_PREFIX
LABEL = run.RESOURCE_LABEL
IMAGE_TAG = run.IMAGE_TAG
CONTAINER_NAME = f"{PREFIX}-onboard"

#: The image a plain container starts from: the one the systemd image is itself built on, so it is
#: already on the machine after the first build.
PLAIN_IMAGE = "ubuntu:24.04"

#: A password that exists only in a throwaway container, written to a file and never to a command line.
ADMIN_USER = "ops"
ADMIN_EMAIL = "ops@example.com"

INSTALL_TIMEOUT = 1500
IDLE_WARMUP = 120
IDLE_WINDOW = 60


# ---------------------------------------------------------------------------
# The systemd container
# ---------------------------------------------------------------------------


def wheel_version(wheel: Path) -> str | None:
    """
    Read the version a wheel was built as, when the installer will take it as ``--version``.

    Args:
        wheel: The wheel.

    Returns:
        ``X.Y.Z``, or None for anything else (a development version).
    """
    match = re.match(r"noust-(\d+\.\d+\.\d+)-", wheel.name)
    return match.group(1) if match else None


def stand_in_for_the_download(name: str, wheel: Path) -> None:
    """
    Make the virtual environment of the pip method, with the working tree's wheel inside.

    The installer makes the same environment itself, with the same commands, and finds the version it
    asks for already installed. ``noust`` is not linked onto the PATH here: the installer does that.

    Args:
        name: The container.
        wheel: The wheel built from the working tree.
    """
    print("[setup] making /opt/noust with the working tree's wheel (the installer's download step)")
    sh(["docker", "cp", str(wheel), f"{name}:/tmp/{wheel.name}"], timeout=60)
    docker_exec(name, "python3 -m venv /opt/noust", timeout=60)
    docker_exec(name, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=180)
    docker_exec(
        name,
        f"/opt/noust/bin/pip install --quiet '/tmp/{wheel.name}[all]'",
        timeout=run.PIP_INSTALL_TIMEOUT,
    )


def stage_files(name: str) -> None:
    """
    Put the installer, the measuring script and a throwaway password in the container.

    Args:
        name: The container.
    """
    sh(["docker", "cp", str(INSTALLER), f"{name}:/root/install.sh"], timeout=30)
    sh(["docker", "cp", str(MEASURE_IDLE), f"{name}:/root/measure_idle.py"], timeout=30)
    with tempfile.TemporaryDirectory() as scratch:
        password = Path(scratch) / "admin.pw"
        password.write_text(secrets.token_urlsafe(24) + "\n", encoding="utf-8")
        password.chmod(0o600)
        sh(["docker", "cp", str(password), f"{name}:/root/onboard-admin.pw"], timeout=30)
    docker_exec(name, "chown root:root /root/onboard-admin.pw && chmod 600 /root/onboard-admin.pw")


def container_running(name: str) -> bool:
    """Whether a container of this name is running."""
    probe = sh(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    return bool(probe.returncode == 0 and probe.stdout.strip() == "true")


def parse_json(text: str, what: str) -> dict[str, Any]:
    """
    Read the one JSON document a command printed.

    Args:
        text: Standard output.
        what: What printed it, for the message.

    Returns:
        The document.

    Raises:
        AssertionError: It is not one JSON object.
    """
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{what} did not print one JSON document: {text[:400]!r}") from exc
    if not isinstance(document, dict):
        raise AssertionError(f"{what} printed JSON that is not an object: {text[:400]!r}")
    return document


def installer_command(wheel_ver: str | None, extra: str) -> str:
    """
    Write the unattended installer command of the harness.

    Args:
        wheel_ver: The version to hand the pip method, so it fetches nothing.
        extra: More options.

    Returns:
        The shell command.
    """
    version = f"--version {wheel_ver} " if wheel_ver else ""
    return (
        "NOUST_ASSUME_YES=1 sh /root/install.sh --method pip --json "
        f"{version}"
        f"--admin-user {ADMIN_USER} --admin-password-file /root/onboard-admin.pw "
        f"--admin-email {ADMIN_EMAIL} --auto-update=security "
        f"--auto-update-window 02:00-03:00 --auto-update-days mon,thu {extra}"
    )


#: Set by the first scenario when the installation succeeded: the rest read a machine that has it.
INSTALLED = False
#: The version the wheel was built as, found in main().
WHEEL_VERSION: str | None = None


def require_installed() -> None:
    """Skip the scenario, saying why, when the installer scenario did not leave Noust installed."""
    if not INSTALLED:
        raise ScenarioSkipped("the installer scenario did not leave Noust installed")


def scenario_installer_unattended(sc: Scenario) -> None:
    """
    The installer, unattended: an administrator, an address, the automatic update, one JSON line.

    What this proves for real: the script runs on a systemd machine without being asked a question,
    makes the console and the account, sets the address and the update through Noust's own commands
    (so the values are what ``noust user list`` and ``noust self-update`` report), prints the link and
    the tunnel for the console, and keeps its standard output to the one document.
    """
    global INSTALLED
    started = sc.run(
        installer_command(WHEEL_VERSION, "> /root/install.json 2> /root/install.log"),
        timeout=INSTALL_TIMEOUT,
        check=False,
        label=installer_command(WHEEL_VERSION, "> install.json 2> install.log"),
    )
    log = sc.run("tail -n 40 /root/install.log", timeout=15, check=False, label="tail install.log")
    sc.check(started.returncode == 0, f"the installer failed ({started.returncode}):\n{log.stdout}")
    document = parse_json(
        sc.run("cat /root/install.json", timeout=15, label="cat install.json").stdout,
        "the installer",
    )

    sc.check(document["status"] == "ok", f"status is {document['status']!r}")
    sc.check(document["dry_run"] is False, "this was not a dry run")
    sc.check(document["method"] == "pip", f"method is {document['method']!r}")
    resources = document["resources"]
    sc.check(resources["memory_mb"] and resources["disk_free_mb"], f"resources: {resources}")
    console = document["console"]
    sc.check(console["state"] in ("new", "existing"), f"console is {console['state']!r}")
    sc.check(
        console["url"] == "http://127.0.0.1:8080/login?with=token",
        f"the link is {console['url']!r}",
    )
    sc.check(
        str(console["tunnel"]).startswith("ssh -N -L 8080:127.0.0.1:8080 root@"),
        f"the tunnel is {console['tunnel']!r}",
    )
    admin = document["admin"]
    sc.check(admin["username"] == ADMIN_USER and admin["state"] == "created", f"admin: {admin}")
    sc.check(
        admin["email"] == ADMIN_EMAIL and admin["email_state"] == "set", f"admin address: {admin}"
    )
    update = document["auto_update"]
    sc.check(
        update
        == {
            "state": "enabled",
            "channel": "security",
            "window": "02:00-03:00",
            "days": "mon,thu",
        },
        f"auto_update: {update}",
    )
    sc.check(
        "token" not in json.dumps(document).lower().replace("with=token", ""),
        "the document names a token",
    )

    accounts = parse_json(
        sc.run("noust user list --json", timeout=60, label="noust user list --json").stdout,
        "noust user list",
    )["accounts"]
    ours = [a for a in accounts if a["username"] == ADMIN_USER]
    sc.check(len(ours) == 1, f"accounts: {[a['username'] for a in accounts]}")
    sc.check(ours[0]["email"] == ADMIN_EMAIL, f"the account's address is {ours[0]['email']!r}")
    sc.check(ours[0]["role"] == "admin", f"the account's role is {ours[0]['role']!r}")

    settings = parse_json(
        sc.run(
            "noust self-update show --json", timeout=60, label="noust self-update show --json"
        ).stdout,
        "noust self-update show",
    )["settings"]
    sc.check(
        settings["enabled"] is True
        and settings["channel"] == "security"
        and settings["window"] == "02:00-03:00",
        f"self-update settings: {settings}",
    )
    timers = sc.run(
        "systemctl list-timers --all --no-pager | grep -i 'noust-auto-update' || true",
        timeout=30,
        label="systemctl list-timers | grep noust-auto-update",
    ).stdout
    sc.check("noust-auto-update" in timers, "the automatic update has no timer")

    deadline = time.monotonic() + 60
    code = ""
    while time.monotonic() < deadline:
        code = sc.run(
            "curl -s -o /dev/null -w '%{http_code}' -m 5 'http://127.0.0.1:8080/login?with=token'",
            timeout=30,
            check=False,
            label="curl http://127.0.0.1:8080/login?with=token",
        ).stdout.strip()
        if code == "200":
            break
        time.sleep(3)
    sc.check(code == "200", f"the link the installer printed answered {code!r}")
    INSTALLED = True


def scenario_installer_again(sc: Scenario) -> None:
    """
    Run again with the same options: it upgrades nothing it must not, repeats nothing it must not.

    What this proves for real: the account is left alone (``existing``), the address and the update
    are set again without an error, the console is the one already there, and the document keeps its
    shape.
    """
    require_installed()
    started = sc.run(
        installer_command(WHEEL_VERSION, "> /root/install2.json 2> /root/install2.log"),
        timeout=INSTALL_TIMEOUT,
        check=False,
        label=installer_command(WHEEL_VERSION, "> install2.json 2> install2.log"),
    )
    log = sc.run(
        "tail -n 30 /root/install2.log", timeout=15, check=False, label="tail install2.log"
    )
    sc.check(
        started.returncode == 0, f"the second run failed ({started.returncode}):\n{log.stdout}"
    )
    document = parse_json(
        sc.run("cat /root/install2.json", timeout=15, label="cat install2.json").stdout,
        "the second run",
    )
    sc.check(document["status"] == "ok", f"status is {document['status']!r}")
    sc.check(document["console"]["state"] == "existing", f"console: {document['console']}")
    sc.check(document["admin"]["state"] == "existing", f"admin: {document['admin']}")
    sc.check(document["admin"]["email_state"] == "set", f"address: {document['admin']}")
    sc.check(document["auto_update"]["state"] == "enabled", f"update: {document['auto_update']}")
    names = sc.run("noust user list --json", timeout=60, label="noust user list --json").stdout
    count = len(
        [a for a in parse_json(names, "noust user list")["accounts"] if a["username"] == ADMIN_USER]
    )
    sc.check(count == 1, f"{count} accounts named {ADMIN_USER} after the second run")


def doctor_text(sc: Scenario) -> str:
    """
    Run the doctor and return what it printed.

    Args:
        sc: The scenario.

    Returns:
        Its output; the exit status is not judged here (other sections may fail on a bare image).
    """
    proc = sc.run("noust setup doctor", timeout=180, check=False, label="noust setup doctor")
    return str(proc.stdout) + str(proc.stderr)


def scenario_doctor_reads_the_machine(sc: Scenario) -> None:
    """
    ``noust setup doctor`` shows the machine against the declared sizes and names the web ports' holders.

    What this proves for real: the section is there on a real machine, with the processors, the memory
    and the free disk read from the kernel, and port 80 either free or held by the web server that
    ``noust setup init`` installed.
    """
    require_installed()
    text = doctor_text(sc)
    for needle in ("Resources", "Memory", "Processors", "Free disk", "Port 80"):
        sc.check(needle in text, f"the doctor does not mention {needle!r}")
    sc.check(
        "Port 80: held by" not in text, "port 80 is held by something that is not the web server"
    )


def start_stray_listener(sc: Scenario, port: int) -> None:
    """Free the port from the web server and hold it with a program of its own."""
    sc.run(
        "systemctl stop nginx || true; "
        f"systemd-run --unit=onboard-stray --property=Type=simple "
        f"/usr/bin/python3 -m http.server {port} --bind 0.0.0.0",
        timeout=60,
        label=f"(a program of its own on port {port}) python3 -m http.server {port}",
    )
    sc.run("sleep 2; ss -ltnp 'sport = :80'", timeout=15, label="ss -ltnp 'sport = :80'")


def stop_stray_listener(sc: Scenario) -> None:
    """Give the port back to the web server."""
    sc.run(
        "systemctl stop onboard-stray || true; systemctl reset-failed onboard-stray || true; "
        "systemctl start nginx || true",
        timeout=60,
        check=False,
        label="(clean up) stop the program, start nginx",
    )


def scenario_a_program_of_its_own_on_port_80(sc: Scenario) -> None:
    """
    Another program on port 80 is named by the installer and by the doctor.

    What this proves for real: ``ss`` reports the listener, the installer's dry run lists it as a
    warning in ``resources`` and the doctor says it is not the web server, with the command that finds
    it.
    """
    require_installed()
    start_stray_listener(sc, 80)
    try:
        planned = sc.run(
            "NOUST_ASSUME_YES=1 sh /root/install.sh --dry-run --json --skip-setup --no-console "
            "--method pip 2>/dev/null",
            timeout=120,
            check=False,
            label="sh install.sh --dry-run --json (with the program on port 80)",
        )
        document = parse_json(planned.stdout, "the dry run")
        port = document["resources"]["ports"]["80"]
        sc.check(str(port["state"]).startswith("other"), f"port 80: {port}")
        sc.check("python3" in str(port["holders"]), f"port 80 holders: {port}")
        warnings = " ".join(document["resources"]["warnings"])
        sc.check("Port 80 is held by python3" in warnings, f"warnings: {warnings!r}")
        text = doctor_text(sc)
        sc.check(
            "Port 80: held by" in text and "python3" in text, "the doctor does not name python3"
        )
    finally:
        stop_stray_listener(sc)


def scenario_a_container_publishing_port_80(sc: Scenario) -> None:
    """
    A container that publishes port 80 is told apart from a program.

    What this proves for real: with the container's own Docker holding the port through
    ``docker-proxy``, the installer's dry run and the doctor say it is a container and give the
    ``docker ps`` command that finds it.
    """
    require_installed()
    run.require_docker(sc)
    name = f"{PREFIX}-onboard-held"
    sc.run("systemctl stop nginx || true", timeout=60, check=False, label="systemctl stop nginx")
    started = sc.run(
        f"docker rm -f {name} >/dev/null 2>&1; "
        f"docker run -d --name {name} -p 80:80 busybox:stable httpd -f -p 80",
        timeout=300,
        check=False,
        label=f"docker run -d --name {name} -p 80:80 busybox:stable httpd -f -p 80",
    )
    if started.returncode != 0:
        sc.run(
            "systemctl start nginx || true", timeout=60, check=False, label="systemctl start nginx"
        )
        raise ScenarioSkipped(
            f"could not start a container publishing port 80: {started.stderr[-300:]}"
        )
    try:
        sc.run("sleep 3; ss -ltnp 'sport = :80'", timeout=15, label="ss -ltnp 'sport = :80'")
        planned = sc.run(
            "NOUST_ASSUME_YES=1 sh /root/install.sh --dry-run --json --skip-setup --no-console "
            "--method pip 2>/dev/null",
            timeout=120,
            check=False,
            label="sh install.sh --dry-run --json (with a container on port 80)",
        )
        document = parse_json(planned.stdout, "the dry run")
        warnings = " ".join(document["resources"]["warnings"])
        sc.check(
            "published by a container" in warnings,
            f"the dry run does not say container: {warnings!r}",
        )
        text = doctor_text(sc)
        sc.check("published by a container" in text, "the doctor does not say it is a container")
        sc.check(
            "docker ps --filter publish=80" in text,
            "the doctor does not give the docker ps command",
        )
    finally:
        sc.run(
            f"docker rm -f {name} >/dev/null 2>&1; systemctl start nginx || true",
            timeout=120,
            check=False,
            label=f"(clean up) docker rm -f {name}; start nginx",
        )


def scenario_idle_footprint(sc: Scenario) -> None:
    """
    What the two long-running units use when nothing is happening, on the installation just made.

    Method: ``scripts/measure_idle.py --attach`` reads the main process of ``noust-web`` and of
    ``noust-monitor`` (found through systemd) once a second for a minute, after a two minute warm-up,
    and sums the resident memory of the process and its children. The medians are the numbers
    ``docs/requirements.md`` publishes.
    """
    require_installed()
    sc.run("noust monitor enable", timeout=120, label="noust monitor enable")
    for unit in ("noust-web", "noust-monitor"):
        state = sc.run(
            f"systemctl is-active {unit}",
            timeout=15,
            check=False,
            label=f"systemctl is-active {unit}",
        ).stdout.strip()
        sc.check(state == "active", f"{unit} is {state!r}")
    measured = sc.run(
        f"/opt/noust/bin/python /root/measure_idle.py --attach --warmup {IDLE_WARMUP} "
        f"--seconds {IDLE_WINDOW} --json",
        timeout=2 * (IDLE_WARMUP + IDLE_WINDOW) + 300,
        label=f"measure_idle.py --attach --warmup {IDLE_WARMUP} --seconds {IDLE_WINDOW} --json",
    )
    result = parse_json(measured.stdout, "measure_idle.py")
    web, monitor = result["console"], result["monitor"]
    total = web["memory_mb_median"] + monitor["memory_mb_median"]
    sc.evidence.append(
        "IDLE-RESULT "
        + json.dumps(
            {
                "environment": result["environment"],
                "noust-web": web,
                "noust-monitor": monitor,
                "total_resident_mb": round(total, 1),
            }
        )
    )
    sc.check(
        web["memory_mb_median"] > 20, f"noust-web reads {web['memory_mb_median']} MB: not measured"
    )
    # A ceiling far above what the docs publish, so a regression is caught and noise is not.
    sc.check(total < 450, f"the two units use {total:.0f} MB resident when idle")
    sc.check(
        web["cpu_percent_of_one_core"] < 10 and monitor["cpu_percent_of_one_core"] < 10,
        f"idle processor use: web {web['cpu_percent_of_one_core']} %, monitor "
        f"{monitor['cpu_percent_of_one_core']} %",
    )
    stats = sh(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", CONTAINER_NAME], check=False
    )
    sc.evidence.append(
        f"$ docker stats --no-stream {CONTAINER_NAME} (the whole machine)\n{stats.stdout.strip()}"
    )


SYSTEMD_SCENARIOS: list[tuple[str, Callable[[Any], None]]] = [
    ("installer_unattended", scenario_installer_unattended),
    ("installer_again", scenario_installer_again),
    ("doctor_reads_the_machine", scenario_doctor_reads_the_machine),
    ("a_program_of_its_own_on_port_80", scenario_a_program_of_its_own_on_port_80),
    ("a_container_publishing_port_80", scenario_a_container_publishing_port_80),
    ("idle_footprint", scenario_idle_footprint),
]


# ---------------------------------------------------------------------------
# Plain containers: the machine the installer refuses, asks about, or accepts
# ---------------------------------------------------------------------------


def run_installer_on(
    sc: Scenario, suffix: str, docker_args: list[str], installer_args: str
) -> subprocess.CompletedProcess[str]:
    """
    Run the installer in a throwaway container with the given limits, and keep what it printed.

    The container is created, given the script, started attached, and removed by its exact name.

    Args:
        sc: The scenario (its evidence receives the command and the output).
        suffix: Distinguishes the container's name.
        docker_args: Limits and mounts for ``docker create``.
        installer_args: Arguments of ``sh /install.sh``.

    Returns:
        The completed process: ``stdout`` is the JSON document, ``stderr`` what it said.
    """
    name = f"{PREFIX}-onboard-{suffix}"
    sh(["docker", "rm", "-f", name], check=False, timeout=60)
    try:
        sh(
            [
                "docker",
                "create",
                "--name",
                name,
                "--label",
                LABEL,
                *docker_args,
                PLAIN_IMAGE,
                "sh",
                "/install.sh",
                *installer_args.split(),
            ],
            timeout=60,
        )
        sh(["docker", "cp", str(INSTALLER), f"{name}:/install.sh"], timeout=30)
        proc = sh(["docker", "start", "-a", name], check=False, timeout=300)
    finally:
        sh(["docker", "rm", "-f", name], check=False, timeout=60)
    block = [
        f"$ docker create {' '.join(docker_args)} {PLAIN_IMAGE} sh /install.sh {installer_args}"
    ]
    block.append(proc.stdout.rstrip())
    if proc.stderr.strip():
        block += ["--- stderr ---", proc.stderr.rstrip()[-1500:]]
    block.append(f"(exit={proc.returncode})")
    sc.evidence.append("\n".join(block))
    return proc


def scenario_refuses_memory_under_the_floor(sc: Scenario) -> None:
    """
    300 MB is refused with a document and exit status 3; ``--force`` goes on.

    What this proves for real: the installer reads the container's own memory limit from its cgroup
    (the host has many times more) and judges that.
    """
    refused = run_installer_on(
        sc, "memory", ["--memory", "300m", "--memory-swap", "300m"], "--dry-run --json --yes"
    )
    sc.check(refused.returncode == 3, f"exit status {refused.returncode}, not 3")
    document = parse_json(refused.stdout, "the installer")
    sc.check(document["status"] == "refused", f"status {document['status']!r}")
    memory = document["resources"]["memory_mb"]
    sc.check(memory is not None and 200 <= memory <= 320, f"the installer read {memory} MB")
    forced = run_installer_on(
        sc,
        "memory-force",
        ["--memory", "300m", "--memory-swap", "300m"],
        "--dry-run --json --yes --force",
    )
    sc.check(forced.returncode == 0, f"--force exit status {forced.returncode}")
    sc.check(
        any(
            "Memory" in w for w in parse_json(forced.stdout, "forced run")["resources"]["warnings"]
        ),
        "the forced run does not list the memory warning",
    )


def scenario_asks_between_the_floor_and_the_minimum(sc: Scenario) -> None:
    """
    700 MB is under the minimum: unattended it is a question that says how to go on, ``--yes`` accepts.

    What this proves for real: with nobody at a terminal (a container without ``-t``) the installer
    stops before it changes anything and names ``--yes``, instead of waiting.
    """
    limits = ["--memory", "700m", "--memory-swap", "700m"]
    asked = run_installer_on(sc, "asks", limits, "--json")
    sc.check(asked.returncode == 1, f"exit status {asked.returncode}, not 1")
    document = parse_json(asked.stdout, "the installer")
    sc.check(document["status"] == "error", f"status {document['status']!r}")
    sc.check(
        "--yes" in json.dumps(document) + asked.stderr, "the refusal does not say how to go on"
    )
    accepted = run_installer_on(sc, "accepts", limits, "--dry-run --json --yes")
    sc.check(accepted.returncode == 0, f"--yes exit status {accepted.returncode}")
    warnings = parse_json(accepted.stdout, "the accepted run")["resources"]["warnings"]
    sc.check(any("Memory" in w for w in warnings), f"warnings: {warnings}")


def scenario_refuses_disk_under_the_floor(sc: Scenario) -> None:
    """
    A 64 MB filesystem where Noust writes is refused.

    What this proves for real: ``df`` is asked about the place the applications will live, not about
    the root, so a small volume under ``/var/www`` is what is judged.
    """
    refused = run_installer_on(
        sc, "disk", ["--tmpfs", "/var/www:size=64m"], "--dry-run --json --yes"
    )
    sc.check(refused.returncode == 3, f"exit status {refused.returncode}, not 3")
    document = parse_json(refused.stdout, "the installer")
    sc.check(document["status"] == "refused", f"status {document['status']!r}")
    disk = document["resources"]["disk_free_mb"]
    sc.check(disk is not None and disk <= 64, f"the installer read {disk} MB free")


def scenario_counts_the_processors_it_may_use(sc: Scenario) -> None:
    """
    One processor (a cpuset of one) is read as one, and is enough.

    What this proves for real: ``nproc`` follows the container's cpuset, which is what a build gets.
    """
    accepted = run_installer_on(sc, "cpu", ["--cpuset-cpus", "0"], "--dry-run --json --yes")
    sc.check(accepted.returncode == 0, f"exit status {accepted.returncode}")
    sc.check(
        parse_json(accepted.stdout, "the installer")["resources"]["cpus"] == 1,
        "the cpuset of one is not read",
    )


PLAIN_SCENARIOS: list[tuple[str, Callable[[Any], None]]] = [
    ("refuses_memory_under_the_floor", scenario_refuses_memory_under_the_floor),
    ("asks_between_the_floor_and_the_minimum", scenario_asks_between_the_floor_and_the_minimum),
    ("refuses_disk_under_the_floor", scenario_refuses_disk_under_the_floor),
    ("counts_the_processors_it_may_use", scenario_counts_the_processors_it_may_use),
]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """What a run did."""

    attempted: int = 0
    failures: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--keep", action="store_true", help="Leave the container running.")
    parser.add_argument(
        "--scenario",
        action="append",
        default=None,
        metavar="SUBSTRING",
        help="Run only the scenarios whose name contains this. May repeat.",
    )
    parser.add_argument("--skip-wheel-build", action="store_true", help="Reuse the newest wheel.")
    parser.add_argument(
        "--skip-image-build", action="store_true", help="Reuse the image if it exists."
    )
    parser.add_argument("--remove-image", action="store_true", help="Remove the image afterwards.")
    return parser.parse_args()


def run_scenarios(to_run: list[tuple[str, Callable[[Any], None]]]) -> Outcome:
    """
    Run scenarios and print each one's evidence.

    Args:
        to_run: The scenarios.

    Returns:
        What happened.
    """
    outcome = Outcome()
    for name, fn in to_run:
        print(f"\n=== {name} ===")
        outcome.attempted += 1
        sc = Scenario(container=CONTAINER_NAME)
        try:
            fn(sc)
        except ScenarioSkipped as exc:
            outcome.skipped.append((name, str(exc)))
            print(f"SKIP: {name}: {exc}")
        except AssertionError as exc:
            outcome.failures += 1
            outcome.failed.append(name)
            print(f"FAIL: {name}: {exc}")
        except (HarnessError, subprocess.TimeoutExpired) as exc:
            outcome.failures += 1
            outcome.failed.append(name)
            print(f"FAIL: {name} (command error): {exc}")
        else:
            outcome.passed.append(name)
            print(f"PASS: {name}")
        finally:
            if sc.evidence:
                print("--- evidence ---")
                print("\n\n".join(sc.evidence))
    return outcome


def main() -> int:
    global INSTALLED, WHEEL_VERSION
    args = parse_args()
    started_at = time.monotonic()
    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)

    if args.skip_wheel_build:
        wheels = sorted((workdir / "dist").glob("noust-*.whl"))
        if not wheels:
            raise HarnessError("--skip-wheel-build given but no wheel exists yet")
        wheel = wheels[-1]
        print(f"[setup] reusing existing wheel {wheel.name}")
    else:
        wheel = run.build_wheel(workdir)
    WHEEL_VERSION = wheel_version(wheel)
    if WHEEL_VERSION is None:
        print(f"[setup] {wheel.name} is a development version: the installer will not be given one")

    have = sh(["docker", "image", "inspect", IMAGE_TAG], check=False)
    if have.returncode != 0 or not args.skip_image_build:
        run.build_image()

    wanted = args.scenario
    to_run = [
        (name, fn)
        for name, fn in [*SYSTEMD_SCENARIOS, *PLAIN_SCENARIOS]
        if not wanted or any(part in name for part in wanted)
    ]
    needs_machine = any((name, fn) in SYSTEMD_SCENARIOS for name, fn in to_run)

    outcome = Outcome()
    setup_error: str | None = None
    run.remove_container(CONTAINER_NAME)
    try:
        try:
            if needs_machine:
                run.start_container(CONTAINER_NAME)
                run.wait_for_systemd(CONTAINER_NAME)
                stand_in_for_the_download(CONTAINER_NAME, wheel)
                stage_files(CONTAINER_NAME)
                run.prepare_inner_docker(CONTAINER_NAME)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            if needs_machine and not any(name == "installer_unattended" for name, _ in to_run):
                # Asked for one of the later scenarios alone: install first, without the checks.
                installed = docker_exec(
                    CONTAINER_NAME,
                    installer_command(WHEEL_VERSION, "> /root/install.json 2> /root/install.log"),
                    timeout=INSTALL_TIMEOUT,
                    check=False,
                )
                INSTALLED = installed.returncode == 0
            print(f"\n[scenarios] running {len(to_run)}: {[n for n, _ in to_run]}")
            outcome = run_scenarios(to_run)
    finally:
        if args.keep:
            print(f"\n[teardown] --keep given, leaving {CONTAINER_NAME} running")
        else:
            print(f"\n[teardown] removing {CONTAINER_NAME}")
            run.remove_container(CONTAINER_NAME)
        if args.remove_image:
            subprocess.run(
                ["docker", "rmi", IMAGE_TAG], capture_output=True, text=True, timeout=120
            )

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        return 1
    for name, reason in outcome.skipped:
        print(f"[skipped] {name}: {reason}")
    print(f"\n[passed] {outcome.passed}")
    print(f"[failed] {outcome.failed}")
    print(
        f"\n[summary] {outcome.attempted} scenario(s) run, {outcome.failures} failure(s), "
        f"{len(outcome.skipped)} skipped, {elapsed:.1f}s total"
    )
    return 1 if outcome.failures else 0


if __name__ == "__main__":
    sys.exit(main())
