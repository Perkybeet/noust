#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A release built on one server and run on another, driven by a central (checklist A-49).

``noust releases bundle`` and ``receive`` were tested on one machine, with a file carried by hand.
This harness is the fleet doing it: three real containers on a network of their own, each a
systemd machine with Noust installed from the wheel of the working tree, with sshd. A central
and two servers it manages, enrolled the way an operator does it (``fleet authorize`` on the
server, ``node add`` on the central), over the real SSH tunnels and the real restricted key. One
server is the **builder**, the other **runs** the application. The central ships the build:

- the build happens on the builder and nowhere else: the target's repository cache never fetches
  the new commit, and the release it serves says (in a file the build wrote) which machine built it;
- the bundle crosses both tunnels in chunks and the target checks it against the digest;
- an update whose application does not start is refused behind the target's own health gate, and
  what served keeps serving;
- the target goes back to the release it had with one command, instantly.

It is not part of pytest (nothing here is named ``test_*``) and it drives Docker. ``run.py`` and
``fleet_run.py`` are loaded as modules and reused, never edited; the fixtures are
``tests/integration/fixtures/toolchains/ship-app``.

Usage:
    .venv/bin/python tests/integration/ship_run.py
    .venv/bin/python tests/integration/ship_run.py --keep

Everything it creates is named ``<prefix>-...`` (``NOUST_IT_SHIP_PREFIX``, default
``noust-it-ship``) and carries the label ``noust.test=1``: three containers, a network, two
images. Nothing of the host's Docker is shared with them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]

if TYPE_CHECKING:
    from run import HarnessError as HarnessError
    from run import Scenario as Scenario


def _load(name: str, file: str) -> Any:
    """
    Load a sibling harness as a module, without touching sys.path.

    Args:
        name: The name to register it under.
        file: Its file name in this directory.

    Returns:
        The module.
    """
    spec = importlib.util.spec_from_file_location(name, INTEGRATION_DIR / file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {INTEGRATION_DIR / file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _load("noust_integration_run", "run.py")
fleet = _load("noust_integration_fleet_run", "fleet_run.py")
sh = run.sh
docker_exec = run.docker_exec
if not TYPE_CHECKING:
    Scenario = run.Scenario
    HarnessError = run.HarnessError

PREFIX = os.environ.get("NOUST_IT_SHIP_PREFIX", "noust-it-ship")
LABEL = "noust.test=1"
NETWORK = f"{PREFIX}-net"
BASE_IMAGE = f"{PREFIX}-base:latest"
FLEET_IMAGE = f"{PREFIX}-image:latest"
CENTRAL = f"{PREFIX}-central"
BUILDER = f"{PREFIX}-build"
TARGET = f"{PREFIX}-web"

#: How the central names the two servers.
BUILDER_NAME = "build-1"
TARGET_NAME = "web-1"

DOMAIN = "ship.test"
APP = "ship-test"
ROOT = f"/var/www/apps/{APP}"
FIXTURE = "/root/fixtures/toolchains/ship-app"
REPO = "/root/fixtures/ship-app"
#: The repository both servers deploy from: the builder serves it to the network. A git:// source
#: names a host and no port, so its daemon is the standard one, on every address.
SOURCE = f"git://{BUILDER}/ship-app"

JOB_TIMEOUT = 900


# ---------------------------------------------------------------------------
# Docker: the images, the network and the three containers
# ---------------------------------------------------------------------------


def build_images(*, rebuild: bool) -> None:
    """Build the harness image and the sshd layer both servers and the central run."""
    have = sh(["docker", "image", "inspect", FLEET_IMAGE], check=False)
    if have.returncode == 0 and not rebuild:
        print(f"[setup] reusing {FLEET_IMAGE}")
        return
    print(f"[setup] building {BASE_IMAGE}")
    sh(
        [
            "docker",
            "build",
            "--label",
            LABEL,
            "-t",
            BASE_IMAGE,
            "-f",
            str(run.DOCKERFILE),
            str(INTEGRATION_DIR),
        ],
        timeout=1800,
    )
    print(f"[setup] building {FLEET_IMAGE}")
    proc = subprocess.run(
        [
            "docker",
            "build",
            "--label",
            LABEL,
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE}",
            "-t",
            FLEET_IMAGE,
            "-f",
            "-",
            str(INTEGRATION_DIR),
        ],
        input=fleet.FLEET_DOCKERFILE,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise HarnessError(f"building {FLEET_IMAGE} failed:\n{proc.stdout}\n{proc.stderr}")


def create_network() -> None:
    """Make the network the three containers share; one a run left behind is replaced."""
    subprocess.run(["docker", "network", "rm", NETWORK], capture_output=True, text=True, timeout=30)
    sh(["docker", "network", "create", "--label", LABEL, NETWORK], timeout=30)


def start_container(name: str) -> None:
    """Start a privileged, systemd-as-PID-1 container on the network, with nothing of the host."""
    print(f"[setup] starting container {name}")
    sh(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
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
            FLEET_IMAGE,
        ],
        timeout=60,
    )


def remove_containers() -> None:
    """Remove the three containers (and their anonymous volumes); never raises."""
    for name in (CENTRAL, BUILDER, TARGET):
        subprocess.run(
            ["docker", "rm", "-f", "-v", name], capture_output=True, text=True, timeout=120
        )


def teardown(*, remove_images: bool) -> None:
    """Remove everything this harness made, by exact name."""
    remove_containers()
    subprocess.run(["docker", "network", "rm", NETWORK], capture_output=True, text=True, timeout=30)
    if remove_images:
        for tag in (FLEET_IMAGE, BASE_IMAGE):
            subprocess.run(["docker", "rmi", tag], capture_output=True, text=True, timeout=120)


# ---------------------------------------------------------------------------
# The fleet
# ---------------------------------------------------------------------------


class Fleet:
    """The three machines, as scenarios run against them."""

    def __init__(self, central: Scenario, builder: Scenario, target: Scenario, token: str) -> None:
        self.central = central
        self.builder = builder
        self.target = target
        self.token = token

    @property
    def api(self) -> str:
        """The central's API."""
        return f"https://127.0.0.1:{fleet.CENTRAL_PORT}/api"


def make_repository(builder: Scenario) -> None:
    """
    Make the sample's repository on the builder and serve it to the network.

    Both servers deploy from it, so they share its commits: the target never needs the builder's
    commit from the repository, only from the bundle.
    """
    builder.run(
        f"rm -rf {REPO} && cp -a {FIXTURE} {REPO} && cd {REPO} && "
        "npm install --package-lock-only >/dev/null && printf 'built.txt\\nbuilt-on.txt\\n' > "
        f".gitignore && git init -q -b main && {run.GIT_IDENTITY} && git add -A && "
        "git commit -q -m 'version 1'",
        timeout=120,
        label=f"(fixture repo) {REPO}: the ship sample at version 1",
    )
    builder.run(
        "git daemon --reuseaddr --export-all --base-path=/root/fixtures --listen=0.0.0.0 "
        "--detach /root/fixtures",
        timeout=30,
        label="git daemon  (the network can fetch the sample from the builder)",
    )


def commit(builder: Scenario, change: str, message: str) -> str:
    """Change the sample's repository and return the new commit."""
    return builder.run(
        f"cd {REPO} && {change} && git add -A && git commit -q -m '{message}' && "
        "git rev-parse HEAD",
        timeout=30,
        label=f"(fixture repo) {change}; git commit -m '{message}'",
    ).stdout.strip()


def create_app(server: Scenario) -> None:
    """Create the sample on a server, from the builder's repository."""
    created = server.run(
        f"noust create -d {DOMAIN} -s {SOURCE} --no-ssl --layout releases",
        timeout=run.DEPLOY_TIMEOUT,
        check=False,
        label=f"noust create -d {DOMAIN} -s {SOURCE} --no-ssl",
    )
    server.check(
        created.returncode == 0,
        f"noust create failed on {server.container}:\n{(created.stdout + created.stderr)[-2500:]}",
    )


def serving(server: Scenario) -> str:
    """What the sample answers on a server, waiting a moment for a restart."""
    deadline = time.monotonic() + 60
    body = ""
    while time.monotonic() < deadline:
        body = docker_exec(
            server.container,
            f"curl -sS -m 5 -H 'Host: {DOMAIN}' http://127.0.0.1/",
            timeout=30,
            check=False,
        ).stdout.strip()
        if body.startswith("ship "):
            break
        time.sleep(1)
    server.evidence.append(
        f"$ curl -H 'Host: {DOMAIN}' http://127.0.0.1/  ({server.container})\n{body}"
    )
    return body


def api(sc: Scenario, token: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    """One call to the central's API, with the token and body kept off argv."""
    status, parsed, raw = fleet.api_call(
        CENTRAL,
        method,
        f"https://127.0.0.1:{fleet.CENTRAL_PORT}{path}",
        token=token,
        json_body=body,
        max_time=120,
    )
    sc.evidence.append(f"$ {method} {path}\n(status={status}) {raw[:600]}")
    return status, parsed


def wait_for_job(sc: Scenario, token: str, job_id: str) -> dict[str, Any]:
    """Poll a job of the central to a finished state."""
    deadline = time.monotonic() + JOB_TIMEOUT
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, body = api(sc, token, "GET", f"/api/jobs/{job_id}")
        if status == 200 and isinstance(body, dict):
            job = body
            if job.get("status") in ("completed", "failed", "cancelled"):
                return job
        time.sleep(3)
    raise HarnessError(f"job {job_id} did not finish within {JOB_TIMEOUT}s: {job!r}")


def states(job: dict[str, Any]) -> dict[str, str]:
    """The state of each server in a finished fleet job."""
    result = job.get("result") or {}
    return {node["node"]: node["state"] for node in result.get("nodes", [])}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_the_central_ships_a_build(env: Fleet) -> None:
    """
    The builder builds, the central carries, the target runs it without building.

    Both servers run version 1 (each built it). Version 2 is committed; the central ships it:
    the target ends on version 2 although its own repository cache never fetched it, the release
    names the machine that built it, and the builder keeps neither the bundle nor the release.
    """
    central, builder, target = env.central, env.builder, env.target
    make_repository(builder)
    create_app(builder)
    create_app(target)
    central.check(serving(target) == "ship v1 built v1", "the target does not serve version 1")
    cache_before = target.run(
        f"git -C {ROOT}/repo rev-parse HEAD",
        timeout=15,
        label=f"git -C {ROOT}/repo rev-parse HEAD  (on the target, before)",
    ).stdout.strip()

    status, _ = api(
        central,
        env.token,
        "PUT",
        f"/api/fleet/placements/{DOMAIN}",
        {"nodes": [TARGET_NAME], "labels": {}},
    )
    central.check(status == 200, "recording the placement failed")
    status, body = api(
        central, env.token, "PUT", f"/api/fleet/builders/{DOMAIN}", {"node": BUILDER_NAME}
    )
    central.check(
        status == 200 and body["node"] == BUILDER_NAME, f"recording the builder: {body!r}"
    )

    new_commit = commit(builder, "echo v2 > VERSION", "version 2")
    status, body = api(
        central, env.token, "POST", f"/api/fleet/placements/{DOMAIN}/ship", {"plan": True}
    )
    central.check(status == 200, f"the plan was refused: {body!r}")
    central.check(
        body["plan"]["batches"] == [[TARGET_NAME]] and body["job"] is None,
        f"the plan is not one server and no job: {body!r}",
    )

    status, body = api(central, env.token, "POST", f"/api/fleet/placements/{DOMAIN}/ship", {})
    central.check(status == 200 and body["job"] is not None, f"the ship was not started: {body!r}")
    job = wait_for_job(central, env.token, body["job"]["job_id"])
    central.check(job["status"] == "completed", f"the ship failed: {json.dumps(job)[:2500]}")
    central.check(states(job) == {TARGET_NAME: "succeeded"}, f"states: {states(job)}")

    central.check(serving(target) == "ship v2 built v2", "the target does not serve version 2")
    # Built on the builder, run on the target: the file the build wrote names its machine.
    built_on = target.run(
        f"cat {ROOT}/current/built-on.txt", timeout=15, label="cat current/built-on.txt (target)"
    ).stdout.strip()
    builder_host = builder.run("hostname", timeout=15, label="hostname (builder)").stdout.strip()
    target_host = target.run("hostname", timeout=15, label="hostname (target)").stdout.strip()
    central.check(
        built_on == builder_host and built_on != target_host,
        f"the release was built on {built_on!r}, not on the builder {builder_host!r}",
    )
    # The target built nothing and fetched nothing: its repository cache is where it was.
    cache_after = target.run(
        f"git -C {ROOT}/repo rev-parse HEAD",
        timeout=15,
        label=f"git -C {ROOT}/repo rev-parse HEAD  (on the target, after)",
    ).stdout.strip()
    central.check(
        cache_after == cache_before and cache_after != new_commit,
        "the target fetched the new commit: it built the release itself",
    )
    # The builder served version 1 all along, and let go of the release and of the bundle.
    central.check(serving(builder) == "ship v1 built v1", "the builder switched to version 2")
    leftovers = builder.run(
        "find / -xdev -name .release-bundles -type d 2>/dev/null | xargs -r ls -A | wc -l",
        timeout=60,
        check=False,
        label="(builder) bundles still waiting in the spool",
    ).stdout.strip()
    central.check(leftovers == "0", f"the builder kept {leftovers} bundle(s)")
    releases = builder.run(
        f"ls {ROOT}/releases | wc -l", timeout=15, label="(builder) ls releases | wc -l"
    ).stdout.strip()
    central.check(releases == "1", f"the builder kept the release it built: {releases} releases")
    target_releases = target.run(
        f"ls {ROOT}/releases | wc -l", timeout=15, label="(target) ls releases | wc -l"
    ).stdout.strip()
    central.check(target_releases == "2", f"the target has {target_releases} releases, not two")


def scenario_the_target_goes_back_instantly(env: Fleet) -> None:
    """
    The release the target had is one command away, and nothing is rebuilt to go back to it.
    """
    central, target = env.central, env.target
    central.check(serving(target) == "ship v2 built v2", "the target does not serve version 2")
    started = time.monotonic()
    rolled = target.run(
        f"noust releases rollback {DOMAIN}",
        timeout=300,
        check=False,
        label=f"noust releases rollback {DOMAIN}  (on the target)",
    )
    took = time.monotonic() - started
    central.check(rolled.returncode == 0, f"the rollback failed:\n{rolled.stdout}{rolled.stderr}")
    central.check(took < 60, f"the rollback took {took:.0f}s: it rebuilt something")
    central.check(serving(target) == "ship v1 built v1", "the target does not serve version 1")
    # Back on version 2 for the next scenario: the other release is a swap away too.
    forward = target.run(
        f"noust releases list {DOMAIN} --json",
        timeout=30,
        label=f"noust releases list {DOMAIN} --json",
    )
    # Newest first: the release the target had before it went back is the one just created.
    ids = [item["id"] for item in json.loads(forward.stdout)["items"]]
    newest = ids[0]
    again = target.run(
        f"noust releases rollback {DOMAIN} {newest}",
        timeout=300,
        check=False,
        label=f"noust releases rollback {DOMAIN} {newest}",
    )
    central.check(again.returncode == 0, f"going forward failed:\n{again.stdout}{again.stderr}")
    central.check(serving(target) == "ship v2 built v2", "the target does not serve version 2")


def scenario_a_release_that_does_not_start_is_refused(env: Fleet) -> None:
    """
    The builder builds a release whose application dies at start. The target's own health gate
    refuses it, what served keeps serving, and the central's job says so with the target's words.
    """
    central, builder, target = env.central, env.builder, env.target
    commit(builder, "echo crash > VERSION", "a release that does not start")
    status, body = api(central, env.token, "POST", f"/api/fleet/placements/{DOMAIN}/ship", {})
    central.check(status == 200 and body["job"] is not None, f"the ship was not started: {body!r}")
    job = wait_for_job(central, env.token, body["job"]["job_id"])

    central.check(job["status"] == "failed", f"a release that does not start was accepted: {job}")
    node_states = states(job)
    central.check(
        node_states.get(TARGET_NAME) in ("failed", "rolled_back"), f"states: {node_states}"
    )
    words = json.dumps(job)
    central.check(
        "health check" in words or "did not pass" in words,
        f"the refusal does not say why: {words[:2500]}",
    )
    central.check(serving(target) == "ship v2 built v2", "the target stopped serving version 2")
    link = target.run(
        f"readlink {ROOT}/current", timeout=15, label="(target) readlink current"
    ).stdout.strip()
    central.check(link.startswith("releases/"), f"current is not a release: {link!r}")
    count = target.run(
        f"ls {ROOT}/releases | wc -l", timeout=15, label="(target) ls releases | wc -l"
    ).stdout.strip()
    central.check(count == "2", f"the refused release was left on the target: {count} releases")


SCENARIOS = [
    ("the_central_ships_a_build", scenario_the_central_ships_a_build),
    ("the_target_goes_back_instantly", scenario_the_target_goes_back_instantly),
    ("a_release_that_does_not_start_is_refused", scenario_a_release_that_does_not_start_is_refused),
]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def setup(wheel: Path) -> Fleet:
    """Start the three machines, install Noust, enroll both servers in the central."""
    teardown(remove_images=False)
    create_network()
    for name in (BUILDER, TARGET, CENTRAL):
        start_container(name)
    for name in (BUILDER, TARGET, CENTRAL):
        run.wait_for_systemd(name)
    for name in (BUILDER, TARGET, CENTRAL):
        run.install_noust(name, wheel)
    # Only the sample, and git on every address: not run.install_fixtures, whose daemon listens on
    # loopback and whose repositories are the other scenarios'.
    sh(["docker", "cp", str(run.FIXTURES_DIR), f"{BUILDER}:/root/fixtures"], timeout=60)
    docker_exec(BUILDER, "chown -R root:root /root/fixtures", timeout=15)
    docker_exec(BUILDER, "git config --global --add safe.directory '*'", timeout=15)
    # The Compose scenarios' Docker is not here, and a degraded systemd is read as a sick node.
    for name in (BUILDER, TARGET):
        fleet.provision_ssh(name)
    token, totp = fleet.provision_central(CENTRAL)
    central = Scenario(container=CENTRAL)
    for server_name, container in ((BUILDER_NAME, BUILDER), (TARGET_NAME, TARGET)):
        env = fleet.FleetEnv(
            central=central,
            node=Scenario(container=container),
            wheel=wheel,
            master_token=token,
            totp_secret=totp,
            node_name=server_name,
            node_ssh_target=f"{fleet.TUNNEL_USER}@{container}",
        )
        fleet.enroll_node(env)
    return Fleet(central, Scenario(container=BUILDER), Scenario(container=TARGET), token)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Leave the containers running.")
    parser.add_argument("--skip-wheel-build", action="store_true", help="Reuse the newest wheel.")
    parser.add_argument("--skip-image-build", action="store_true", help="Reuse the images.")
    parser.add_argument(
        "--remove-images", action="store_true", help="Remove the two images at the end."
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = time.monotonic()
    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)
    if args.skip_wheel_build:
        wheels = sorted((workdir / "dist").glob("noust-*.whl"))
        if not wheels:
            raise HarnessError("--skip-wheel-build given but no wheel exists yet")
        wheel = wheels[-1]
    else:
        wheel = run.build_wheel(workdir)
    build_images(rebuild=not args.skip_image_build)

    failures = 0
    setup_error: str | None = None
    environment: Fleet | None = None
    try:
        try:
            environment = setup(wheel)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            for name, function in SCENARIOS:
                print(f"\n=== {name} ===")
                for machine in (environment.central, environment.builder, environment.target):
                    machine.evidence = []
                try:
                    function(environment)
                except AssertionError as exc:
                    failures += 1
                    print(f"FAIL: {name}: {exc}")
                except (HarnessError, subprocess.TimeoutExpired) as exc:
                    failures += 1
                    print(f"FAIL: {name} (command error): {exc}")
                except (KeyError, TypeError, ValueError) as exc:
                    # What a command printed was not what the scenario reads: a failure of the
                    # scenario, reported as one, so the teardown below still runs.
                    failures += 1
                    print(f"FAIL: {name} (unexpected output): {type(exc).__name__}: {exc}")
                else:
                    print(f"PASS: {name}")
                finally:
                    evidence = (
                        environment.central.evidence
                        + environment.builder.evidence
                        + environment.target.evidence
                    )
                    if evidence:
                        print("--- evidence ---")
                        print("\n\n".join(evidence))
    finally:
        if args.keep:
            print(f"\n[teardown] --keep given, leaving {CENTRAL}, {BUILDER} and {TARGET} running")
        else:
            print("\n[teardown] removing the containers and the network")
            teardown(remove_images=args.remove_images)

    elapsed = time.monotonic() - started_at
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        return 1
    print(f"\n[summary] {len(SCENARIOS)} scenario(s), {failures} failure(s), {elapsed:.1f}s total")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
