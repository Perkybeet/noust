#!/usr/bin/env python3
"""
Real-engine harness for Noust's database features.

Where ``run.py`` proves deploys and ``fleet_run.py`` proves the fleet, this runner proves what
only a real database engine can: that PostgreSQL archives its write-ahead log to the place
Noust says and can be recovered to an instant, that a replica follows its primary and can be
promoted, that an engine serves TLS with a certificate Noust installed and reloads it, that a
backup encrypted on the server's disk restores, and that an engine installs on a system that
is not Debian's.

It reuses ``run.py``'s systemd-under-Docker image and helpers, and lays two rules on top of it
for a machine that is also somebody's development workstation:

* **Everything it creates is named ``noust-test-ch-*`` and carries the label
  ``noust.test=1``**: the container, the volume the container's own Docker keeps its data in,
  and every image it builds. It removes those by exact name, never by a prune, and never touches
  a container, volume, network or image it did not make.
* **It publishes no port on the host.** The databases run in the container's *own* Docker
  daemon (a daemon per data volume, never the host's ``docker.sock``), and are reached with
  ``docker exec``.

Like the other runners it is not collected by pytest: it drives Docker.

Usage:
    .venv/bin/python tests/integration/engines_run.py --list
    .venv/bin/python tests/integration/engines_run.py --scenario pitr_instance
    .venv/bin/python tests/integration/engines_run.py --dev          # the working tree, mounted
    .venv/bin/python tests/integration/engines_run.py --keep
    .venv/bin/python tests/integration/engines_run.py --purge        # remove everything it made

``--dev`` mounts the working tree read-only and imports Noust from it, so a change is tried
without building a wheel; every run that is reported as proof is a wheel run (the default).
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tomllib

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]

#: Every name this runner creates starts with this, and every object carries the label.
PREFIX = "noust-test-ch"
LABEL = "noust.test=1"
CONTAINER = f"{PREFIX}-engines"
DOCKER_VOLUME = f"{PREFIX}-docker"

#: The working tree as seen inside the container with ``--dev``.
SOURCE_MOUNT = "/src"

#: Where the engines' scenarios keep their scratch files inside the container.
SCRATCH = "/root/engines"


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
HarnessError = run.HarnessError
Scenario = run.Scenario
ScenarioSkipped = run.ScenarioSkipped

ScenarioFn = Callable[[Any], None]
SCENARIOS: list[tuple[str, ScenarioFn]] = []


def engines_scenario(name: str) -> Callable[[ScenarioFn], ScenarioFn]:
    """
    Register a scenario.

    Args:
        name: The name ``--scenario`` takes.

    Returns:
        The decorator.
    """

    def decorator(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS.append((name, fn))
        return fn

    return decorator


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------


def exists(kind: str, name: str) -> bool:
    """
    Say whether a Docker object of this runner's exists.

    Args:
        kind: ``container`` or ``volume``.
        name: Its exact name.

    Returns:
        Whether it does.
    """
    return sh(["docker", kind, "inspect", name], timeout=30, check=False).returncode == 0


def start_machine(*, dev: bool) -> None:
    """
    Start the systemd container, with its own Docker data volume, both labelled.

    Args:
        dev: Mount the working tree read-only at :data:`SOURCE_MOUNT`.
    """
    if exists("container", CONTAINER):
        raise HarnessError(
            f"{CONTAINER} already exists: use --purge, or --keep's container is still there"
        )
    if not exists("volume", DOCKER_VOLUME):
        sh(["docker", "volume", "create", "--label", LABEL, DOCKER_VOLUME], timeout=30)
    argv = [
        "docker",
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--label",
        LABEL,
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
    ]
    if dev:
        argv += ["-v", f"{REPO_ROOT}:{SOURCE_MOUNT}:ro"]
    argv.append(run.IMAGE_TAG)
    print(f"[setup] starting {CONTAINER}")
    sh(argv, timeout=60)


def install_noust_dev() -> None:
    """
    Install Noust's dependencies and import its code from the mounted working tree.

    The dependencies are read from ``pyproject.toml`` (the tree is mounted read-only, so it
    cannot be built in place), and ``noust`` becomes a path file that points at the mount: a
    change to the tree is the next ``noust`` the scenario runs.
    """
    print("[setup] installing the dependencies and pointing Noust at the mounted tree")
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
    requirements = [*project["dependencies"], *project["optional-dependencies"]["all"]]
    docker_exec(CONTAINER, "python3 -m venv /opt/noust", timeout=60)
    docker_exec(CONTAINER, "/opt/noust/bin/pip install --quiet --upgrade pip", timeout=120)
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "/opt/noust/bin/pip",
            "install",
            "--quiet",
            "-r",
            "/dev/stdin",
        ],
        input="\n".join(requirements) + "\n",
        capture_output=True,
        text=True,
        timeout=run.PIP_INSTALL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise HarnessError(f"pip could not install the dependencies:\n{proc.stderr}")
    site = docker_exec(
        CONTAINER,
        "/opt/noust/bin/python -c 'import site; print(site.getsitepackages()[0])'",
        timeout=30,
    ).stdout.strip()
    docker_exec(CONTAINER, f"echo {SOURCE_MOUNT}/src > {site}/noust-dev.pth", timeout=15)
    wrapper = '#!/bin/sh\\nexec /opt/noust/bin/python -m noust "$@"\\n'
    for name in ("noust", "wasm"):
        docker_exec(
            CONTAINER,
            f"printf '{wrapper}' > /usr/local/bin/{name} && chmod 755 /usr/local/bin/{name}",
            timeout=15,
        )
    print(docker_exec(CONTAINER, "noust --version", timeout=30).stdout.strip())


def teardown(*, keep: bool) -> None:
    """
    Remove the container and, unless kept, nothing else: the volume is its cache.

    Args:
        keep: Leave the container running for inspection.
    """
    if keep:
        print(f"[teardown] --keep given, {CONTAINER} stays")
        return
    purge(volume=False)


def purge(*, volume: bool) -> None:
    """
    Remove what this runner made, by exact name.

    Args:
        volume: Also remove the volume the container's own Docker keeps its images in.
    """
    if exists("container", CONTAINER):
        subprocess.run(
            [
                "docker",
                "exec",
                CONTAINER,
                "bash",
                "-lc",
                f"{run.INNER_DOCKER_CLEAR}; systemctl stop docker.socket docker.service",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        sh(["docker", "rm", "-f", CONTAINER], timeout=90, check=False)
    if volume and exists("volume", DOCKER_VOLUME):
        sh(["docker", "volume", "rm", DOCKER_VOLUME], timeout=90, check=False)


# ---------------------------------------------------------------------------
# What scenarios use
# ---------------------------------------------------------------------------


def noust(sc: Any, arguments: str, *, timeout: int = 120, check: bool = True) -> Any:
    """
    Run the real CLI in the container.

    Args:
        sc: The scenario.
        arguments: Everything after ``noust``.
        timeout: Seconds it may take.
        check: Fail the scenario on a non-zero exit.

    Returns:
        The completed process.
    """
    return sc.run(f"noust {arguments}", timeout=timeout, check=check, label=f"noust {arguments}")


def pull(sc: Any, image: str, *, timeout: int = 600) -> None:
    """
    Pull an image into the container's own Docker once; it stays in the data volume.

    Args:
        sc: The scenario.
        image: The reference.
        timeout: Seconds it may take.

    Raises:
        ScenarioSkipped: The registry cannot be reached.
    """
    run.pull_images(sc, image)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@engines_scenario("smoke")
def scenario_smoke(sc: Any) -> None:
    """The container runs Noust, systemd and a Docker of its own."""
    version = noust(sc, "--version")
    sc.check("noust" in version.stdout.lower() or "wasm" in version.stdout.lower(), "version")
    sc.run("docker info --format '{{.ServerVersion}}'", label="the container's own Docker")
    sc.run("systemctl is-system-running || true", label="systemd")


BK_DOMAIN = "bk.test"
BK_APP = "bk-test"
BK_REMOTE = "/srv/noust-test-ch-remote"


def json_of(proc: Any, what: str) -> Any:
    """
    Args:
        proc: A finished command whose stdout is JSON.
        what: What it was, for the failure.

    Returns:
        The parsed output.
    """
    import json

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{what} did not print JSON: {proc.stdout[:300]!r}") from exc


@engines_scenario("remote_only_backups")
def scenario_remote_only_backups(sc: Any) -> None:
    """
    H-13: schedules that keep nothing on the server, against a real rclone destination.

    An application's backup, an application's data and Noust's own state are each taken by their
    schedule, sent to a directory destination and checked on arrival, and then gone from the
    server's disk; the copy on the destination restores. With the destination unreachable the
    local archive is kept and the run fails.
    """
    sc.check(
        sc.run("rclone version | head -1", check=False).returncode == 0, "rclone is not installed"
    )
    create = f"noust create -d {BK_DOMAIN} -s /root/fixtures/static-site -t static --no-ssl"
    sc.run(create, timeout=run.DEPLOY_TIMEOUT, label=create)
    try:
        sc.run(f"rm -rf {BK_REMOTE} && mkdir -p {BK_REMOTE}")
        noust(sc, f"backup destination add chremote --type local --field path={BK_REMOTE}")

        # --- an application's own schedule
        refused = noust(
            sc, f"backup schedule create {BK_DOMAIN} --schedule daily --remote-only", check=False
        )
        sc.check(refused.returncode != 0, "remote-only without a destination must be refused")
        noust(
            sc,
            f"backup schedule create {BK_DOMAIN} --schedule daily --destination chremote "
            "--remote-only",
        )
        noust(sc, f"backup run-schedule {BK_DOMAIN}", timeout=300)
        local = sc.run(
            f"find /var/backups/noust -name '{BK_APP}_*.tar.gz' | wc -l", label="local archives"
        )
        sc.check(local.stdout.strip() == "0", f"the server kept a local archive: {local.stdout}")
        remote = sc.run(f"ls -1 {BK_REMOTE}/{BK_APP}", label="what the destination holds")
        names = remote.stdout.split()
        sc.check(
            any(n.endswith(".tar.gz") for n in names) and any(n.endswith(".json") for n in names),
            f"the destination should hold the archive and its sidecar: {names}",
        )
        archive = next(n for n in names if n.endswith(".tar.gz")).removesuffix(".tar.gz")

        # The copy that remains is the one a restore uses.
        root = sc.run(
            f"readlink -e /var/www/apps/{BK_APP}/current || echo /var/www/apps/{BK_APP}"
        ).stdout.strip()
        sc.run(f"echo tampered > {root}/index.html")
        noust(sc, f"backup restore {archive} --from chremote -f", timeout=300)
        page = sc.run(f"curl -fsS -H 'Host: {BK_DOMAIN}' http://127.0.0.1/").stdout
        sc.check("tampered" not in page, f"the restore did not bring the page back: {page[:200]}")

        # --- the application's data: a directory the application keeps its uploads in
        sc.run(
            f"mkdir -p /var/www/apps/{BK_APP}/shared/uploads && "
            f"echo kept > /var/www/apps/{BK_APP}/shared/uploads/a.txt"
        )
        noust(
            sc,
            f"backup volumes schedule set {BK_DOMAIN} --path uploads --to chremote --remote-only",
        )
        noust(sc, f"backup volumes run {BK_DOMAIN}", timeout=300)
        listed = sc.run(f"noust backup volumes list {BK_DOMAIN} --json", label="data backups here")
        sc.check(
            json_of(listed, "volumes list") in ([], {"backups": [], "total": 0}), listed.stdout
        )
        held = sc.run(f"find {BK_REMOTE}/volumes -name '*.tar.gz' | wc -l").stdout.strip()
        sc.check(held == "1", f"the destination should hold the data backup: {held}")

        # --- Noust's own state, sealed by a passphrase the destination never sees
        sc.run("printf 'correct horse battery staple\\n' > /root/pass && chmod 600 /root/pass")
        noust(
            sc, "self-backup schedule set --to chremote --remote-only --passphrase-file /root/pass"
        )
        noust(sc, "self-backup run", timeout=300)
        here = sc.run("ls /var/backups/noust/self 2>/dev/null | wc -l").stdout.strip()
        sc.check(here == "0", f"the server kept a bundle of its own state: {here}")
        held = sc.run(f"find {BK_REMOTE}/self-backups -name '*.enc' | wc -l").stdout.strip()
        sc.check(held == "1", f"the destination should hold the bundle: {held}")
        leaked = sc.run(
            f"grep -rl 'correct horse' {BK_REMOTE} | wc -l", check=False, label="passphrase sent?"
        )
        sc.check(leaked.stdout.strip() == "0", "the passphrase reached the destination")

        # --- the destination goes away: the archive stays and the run fails
        noust(sc, f"backup volumes schedule remove {BK_DOMAIN} -f")
        noust(sc, "self-backup schedule remove -f")
        noust(sc, "backup destination remove chremote -f")
        sc.run("touch /root/not-a-directory")
        noust(
            sc,
            "backup destination add chbroken --type local --field path=/root/not-a-directory/x",
        )
        noust(
            sc,
            f"backup schedule create {BK_DOMAIN} --schedule daily --destination chbroken "
            "--remote-only",
        )
        failed = noust(sc, f"backup run-schedule {BK_DOMAIN}", timeout=300, check=False)
        sc.check(failed.returncode != 0, "a run that reached no destination must fail")
        kept = sc.run(
            f"find /var/backups/noust -name '{BK_APP}_*.tar.gz' | wc -l", label="local archives"
        )
        sc.check(kept.stdout.strip() == "1", f"the failed run should keep its archive: {kept}")
        noust(sc, "backup destination remove chbroken -f")
    finally:
        sc.run(
            f"noust backup schedule delete {BK_DOMAIN}; "
            f"noust backup volumes schedule remove {BK_DOMAIN} -f; noust self-backup schedule remove -f; "
            "noust backup destination remove chremote -f; "
            "noust backup destination remove chbroken -f; "
            f"noust delete {BK_DOMAIN} -f; rm -rf {BK_REMOTE} /root/not-a-directory "
            f"/var/backups/noust/{BK_APP}",
            timeout=180,
            check=False,
            label="cleanup",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Args:
        argv: Arguments; the process's when None.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", action="append", help="run only this scenario (repeatable)")
    parser.add_argument("--list", action="store_true", help="list the scenarios and exit")
    parser.add_argument("--dev", action="store_true", help="mount the working tree, build no wheel")
    parser.add_argument("--keep", action="store_true", help="leave the container running")
    parser.add_argument(
        "--attach", action="store_true", help="use the container --keep left running (with --dev)"
    )
    parser.add_argument("--purge", action="store_true", help="remove everything this runner made")
    parser.add_argument("--skip-wheel-build", action="store_true", help="reuse the built wheel")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Run the scenarios.

    Args:
        argv: Arguments; the process's when None.

    Returns:
        The exit status: 0 when every scenario that ran passed.
    """
    args = parse_args(argv)
    if args.list:
        for name, fn in SCENARIOS:
            print(f"{name}: {(fn.__doc__ or '').strip().splitlines()[0] if fn.__doc__ else ''}")
        return 0
    if args.purge:
        purge(volume=True)
        print("[purge] removed the container and the volume of this runner")
        return 0

    selected = [(n, f) for n, f in SCENARIOS if not args.scenario or n in args.scenario]
    if args.scenario:
        unknown = set(args.scenario) - {n for n, _ in SCENARIOS}
        if unknown:
            print(f"unknown scenario(s): {sorted(unknown)}")
            return 2

    started = time.monotonic()
    workdir = INTEGRATION_DIR / ".build"
    workdir.mkdir(exist_ok=True)
    wheel: Path | None = None
    if not args.dev and not args.attach:
        if args.skip_wheel_build:
            wheels = sorted((workdir / "dist").glob("noust-*.whl"))
            if not wheels:
                raise HarnessError("--skip-wheel-build given but no wheel exists yet")
            wheel = wheels[-1]
        else:
            wheel = run.build_wheel(workdir)

    failures = 0
    attempted = 0
    skipped: list[tuple[str, str]] = []
    setup_error: str | None = None
    try:
        try:
            if args.attach:
                if not exists("container", CONTAINER):
                    raise HarnessError(f"--attach: {CONTAINER} is not there (use --dev --keep)")
            else:
                start_machine(dev=args.dev)
                run.wait_for_systemd(CONTAINER)
                run.prepare_inner_docker(CONTAINER)
                if args.dev:
                    install_noust_dev()
                else:
                    assert wheel is not None
                    run.install_noust(CONTAINER, wheel)
            docker_exec(CONTAINER, f"mkdir -p {SCRATCH}", timeout=15)
            if docker_exec(CONTAINER, "test -d /root/fixtures", check=False).returncode != 0:
                run.install_fixtures(CONTAINER)
        except HarnessError as exc:
            setup_error = str(exc)
        else:
            for name, fn in selected:
                print(f"\n=== {name} ===")
                attempted += 1
                sc = Scenario(container=CONTAINER)
                try:
                    fn(sc)
                except ScenarioSkipped as exc:
                    skipped.append((name, str(exc)))
                    print(f"SKIP: {name}: {exc}")
                except AssertionError as exc:
                    failures += 1
                    print(f"FAIL: {name}: {exc}")
                except HarnessError as exc:
                    failures += 1
                    print(f"FAIL: {name} (command error): {exc}")
                else:
                    print(f"PASS: {name}")
                finally:
                    if sc.evidence:
                        print("--- evidence ---")
                        print("\n\n".join(sc.evidence))
    finally:
        teardown(keep=args.keep or args.attach)

    elapsed = time.monotonic() - started
    if setup_error is not None:
        print(f"\n[setup] FAILED before any scenario ran: {setup_error}")
        return 1
    for name, reason in skipped:
        print(f"[skipped] {name}: {reason}")
    print(
        f"\n[summary] {attempted} scenario(s) run, {failures} failure(s), "
        f"{len(skipped)} skipped, {elapsed:.1f}s total"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
