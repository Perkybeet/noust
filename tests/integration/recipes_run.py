#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Start every recipe of the catalogue for real, one after another, and keep what each one did.

``scripts/recipe_smoke.py`` starts one Compose recipe on the Docker it finds. This runner puts that
Docker where a shared machine can afford it: inside the harness container (``run.py``'s systemd
image, privileged, with a Docker daemon of its own on a labelled volume), so nothing of the
machine's Docker is shared, named or touched. For each recipe it

1. runs ``recipe_smoke.py`` inside the container (the recipe's variables resolved by Noust's own
   code, the stack started as a throwaway Compose project, the health gate asked as the gate asks);
2. appends what happened to ``tests/integration/results/recipes.json`` (recipe, version, result,
   seconds, date) before anything else runs, so a machine that dies in the middle loses one recipe;
3. removes the recipe's images from the container's Docker by name, keeping only the few that most
   recipes share (a PostgreSQL, a Redis) until the end of the batch, because a registry that
   counts pulls per address would otherwise stop the run long before it ends.

Everything it creates on the machine's Docker is named ``noust-test-recetas-*`` and carries the
label ``noust.test=1`` (the container, the volume under the inner Docker, the image). It removes
those by exact name, never with a prune, and never touches anything it did not make. One recipe at
a time, on purpose: the machine has been brought down twice by a sustained load.

Usage:
    tests/integration/recipes_run.py up                  # image, container, inner Docker
    tests/integration/recipes_run.py sync                # this tree's package into the container
    tests/integration/recipes_run.py run NAME [NAME...]  # sync, then one recipe after another
    tests/integration/recipes_run.py run --pending       # every installable Compose recipe with no result
    tests/integration/recipes_run.py shared-clean        # the images kept between recipes
    tests/integration/recipes_run.py status              # the totals of recipes.json
    tests/integration/recipes_run.py purge               # remove the container, volume and image

Run it through ``/home/yago/.local/bin/noust-docker 2400 ...`` (one Docker harness at a time), and
look at the free space of C: before a batch: with less than 30 GB, do not start.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INTEGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = INTEGRATION_DIR.parents[1]
RESULTS = INTEGRATION_DIR / "results" / "recipes.json"

#: Every name this runner creates starts with this, and every object carries the label.
PREFIX = os.environ.get("NOUST_RECIPES_PREFIX", "noust-test-recetas")
os.environ.setdefault("NOUST_IT_PREFIX", PREFIX)
os.environ.setdefault("NOUST_IT_IMAGE", f"{PREFIX}-image:latest")
os.environ.setdefault("NOUST_IT_DOCKER_VOLUME", f"{PREFIX}-docker")
os.environ.setdefault("NOUST_IT_MEMORY", "10g")

CONTAINER = f"{PREFIX}-box"

#: Where the package and the smoke tool are copied inside the container.
WORK = "/work"

#: Seconds one recipe may take, pull included. A recipe that needs more is not a smoke test.
RECIPE_TIMEOUT = 1800

#: Images that more than a handful of recipes share, and so stay in the container's Docker until
#: ``shared-clean``: pulling a PostgreSQL forty times is how a registry's per-address limit stops a run.
SHARED_REPOSITORIES = (
    "postgres",
    "redis",
    "valkey/valkey",
    "mariadb",
    "mysql",
    "mongo",
    "bitnami/postgresql",
    "pgvector/pgvector",
)

#: What a registry that counts pulls says when it has had enough.
RATE_LIMITED = re.compile(r"toomanyrequests|rate limit", re.IGNORECASE)

#: Resolvers the container's Docker falls back on when the machine's times out under load.
FALLBACK_RESOLVERS = ("1.1.1.1", "8.8.8.8")


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
    sys.modules.setdefault("run", module)
    spec.loader.exec_module(module)
    return module


run = _load_run()


def host(
    argv: list[str], *, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """
    Run a command on the machine (``docker`` only, in this file).

    Args:
        argv: The command.
        timeout: Seconds.
        check: Raise on a non-zero exit.

    Returns:
        The finished process.
    """
    return run.sh(argv, timeout=timeout, check=check)


def inside(
    script: str, *, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """
    Run a shell script in the harness container.

    Args:
        script: The script.
        timeout: Seconds.
        check: Raise on a non-zero exit.

    Returns:
        The finished process.
    """
    return run.docker_exec(CONTAINER, script, timeout=timeout, check=check)


def container_state() -> str:
    """
    Tell what the harness container is doing.

    Returns:
        ``running``, ``exited`` or another Docker state, or ``absent``.
    """
    found = host(
        ["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER], check=False, timeout=30
    )
    return found.stdout.strip() if found.returncode == 0 else "absent"


def free_space_gib() -> float | None:
    """
    Read the free space of the drive the Docker data lives on, in GiB.

    Returns:
        The figure from ``df`` on the drive's mount in WSL, or None when it cannot be read.
    """
    for mount in ("/mnt/c", "/"):
        found = subprocess.run(
            ["df", "--output=avail", "-B1G", mount], capture_output=True, text=True, check=False
        )
        lines = found.stdout.split()
        if found.returncode == 0 and len(lines) >= 2 and lines[-1].isdigit():
            return float(lines[-1])
    return None


def up() -> None:
    """Build the image if it is missing, start the container and wait for its Docker."""
    space = free_space_gib()
    print(f"[recipes] free space of C: {space} GiB")
    if space is not None and space < 30:
        raise SystemExit("less than 30 GB free on C: do not start a batch")
    image = host(["docker", "image", "inspect", run.IMAGE_TAG], check=False, timeout=30)
    if image.returncode != 0:
        run.build_image()
    state = container_state()
    if state == "absent":
        run.start_container(CONTAINER)
    elif state != "running":
        host(["docker", "start", CONTAINER], timeout=60)
    run.wait_for_systemd(CONTAINER)
    if not run.prepare_inner_docker(CONTAINER):
        raise SystemExit("the Docker inside the container did not answer")
    # The daemon looks a registry up through the container's resolv.conf, and the containers it
    # runs get their resolvers from daemon.json: both fall back on public ones when the machine's
    # times out under load (as scenarios_real.ensure_inner_dns does).
    resolvers = ", ".join(f'"{address}"' for address in FALLBACK_RESOLVERS)
    inside(
        "grep -q 1.1.1.1 /etc/resolv.conf || "
        "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' >> /etc/resolv.conf\n"
        "grep -q '\"dns\"' /etc/docker/daemon.json || {\n"
        "cat > /etc/docker/daemon.json <<'EOF'\n"
        "{\n"
        '  "storage-driver": "overlay2",\n'
        '  "features": {"containerd-snapshotter": false},\n'
        f'  "dns": [{resolvers}]\n'
        "}\n"
        "EOF\n"
        "systemctl restart docker\n"
        "for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done\n"
        "}",
        timeout=120,
    )
    inside(
        "python3 -c 'import yaml, jinja2' 2>/dev/null || { apt-get update -qq && "
        "apt-get install -y -qq --no-install-recommends python3-yaml python3-jinja2; }",
        timeout=300,
    )
    print(f"[recipes] {CONTAINER} is ready")


def sync() -> None:
    """Copy this tree's package (without the console's build) and the smoke tool into the container."""
    inside(
        f"rm -rf {WORK}/src {WORK}/scripts && mkdir -p {WORK}/src {WORK}/scripts {WORK}/evidence"
    )
    with tempfile.NamedTemporaryFile(suffix=".tar") as archive:
        subprocess.run(
            [
                "tar",
                "-C",
                str(REPO_ROOT / "src"),
                "--exclude=noust/web/static",
                "--exclude=__pycache__",
                "-cf",
                archive.name,
                "noust",
            ],
            check=True,
            timeout=120,
        )
        host(["docker", "cp", archive.name, f"{CONTAINER}:{WORK}/src.tar"], timeout=120)
    inside(f"tar -C {WORK}/src -xf {WORK}/src.tar && rm {WORK}/src.tar", timeout=120)
    for script in ("recipe_smoke.py", "recipe_pins.py"):
        host(["docker", "cp", str(REPO_ROOT / "scripts" / script), f"{CONTAINER}:{WORK}/scripts/"])


def now() -> str:
    """
    Name the moment, in UTC, to the second.

    Returns:
        ISO 8601 with a Z.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_results() -> dict[str, Any]:
    """
    Read the results file.

    Returns:
        Its document, or an empty one.
    """
    if RESULTS.is_file():
        loaded = json.loads(RESULTS.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return {"results": [], "candidates": []}


def write_results(document: dict[str, Any]) -> None:
    """
    Write the results file whole and atomically, with the totals up to date.

    Args:
        document: What :func:`read_results` returned, changed.
    """
    results = sorted(document.get("results", []), key=lambda entry: entry["recipe"])
    candidates = sorted(document.get("candidates", []), key=lambda entry: entry["recipe"])
    counts: dict[str, int] = {}
    for entry in results:
        counts[entry["result"]] = counts.get(entry["result"], 0) + 1
    kept = {
        "measured": "tests/integration/recipes_run.py: scripts/recipe_smoke.py in the harness "
        "container, one recipe at a time",
        "harness": "Ubuntu 24.04 with systemd and a Docker of its own (Dockerfile.systemd)",
        "updated": now(),
        "totals": counts,
        "results": results,
        "candidates": candidates,
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    temporary = RESULTS.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(RESULTS)


def record(entry: dict[str, Any]) -> None:
    """
    Put one recipe's result in the file, replacing an earlier one of the same recipe.

    Args:
        entry: ``recipe``, ``version``, ``result``, ``seconds``, ``date`` and what else it knows.
    """
    document = read_results()
    document["results"] = [e for e in document.get("results", []) if e["recipe"] != entry["recipe"]]
    document["results"].append(entry)
    write_results(document)


def images_of(name: str) -> list[str]:
    """
    List the images a recipe's stack pulls or builds.

    Args:
        name: The recipe.

    Returns:
        References as the document writes them, and the names Compose gives built images.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from noust.core.compose_yaml import load_compose
    from noust.recipes import get_recipe

    recipe = get_recipe(name)
    document = load_compose(recipe.compose or "")
    found: list[str] = []
    for service, spec in (document.get("services") or {}).items():
        if isinstance(spec, dict) and isinstance(spec.get("image"), str):
            found.append(spec["image"])
        if isinstance(spec, dict) and spec.get("build"):
            found.append(f"{PREFIX}-{name}-{service}")
    return found


def is_shared(reference: str) -> bool:
    """
    Tell whether an image stays between recipes.

    Args:
        reference: An image reference.

    Returns:
        True for the PostgreSQL, Redis and the like most recipes bring.
    """
    repository = reference.split("@", 1)[0].rsplit(":", 1)[0]
    repository = repository.removeprefix("docker.io/").removeprefix("library/")
    return repository in SHARED_REPOSITORIES


def remove_images(name: str) -> list[str]:
    """
    Remove from the container's Docker the images a recipe brought, by reference.

    Args:
        name: The recipe.

    Returns:
        The references removed.
    """
    removed: list[str] = []
    for reference in images_of(name):
        if is_shared(reference):
            continue
        done = inside(f"docker image rm -f {shlex.quote(reference)}", check=False, timeout=120)
        if done.returncode == 0:
            removed.append(reference)
    # Layers a failed pull left, with no tag to name them, are the container's own: only the ones
    # this Docker holds, and only the dangling ones.
    inside("docker image ls -q --filter dangling=true | xargs -r docker image rm -f", check=False)
    return removed


def shared_clean() -> None:
    """Remove the images kept between recipes (the container's Docker only)."""
    inside("docker ps -aq | xargs -r docker rm -f >/dev/null 2>&1", check=False)
    for repository in SHARED_REPOSITORIES:
        inside(
            f"docker image ls --format '{{{{.Repository}}}}:{{{{.Tag}}}}' "
            f"| grep -E '^(docker.io/)?(library/)?{re.escape(repository)}:' "
            "| xargs -r docker image rm -f",
            check=False,
            timeout=300,
        )
    inside("docker image ls -q --filter dangling=true | xargs -r docker image rm -f", check=False)


def smoke(name: str, *, extra: list[str]) -> dict[str, Any]:
    """
    Start one recipe in the container and say what happened.

    Args:
        name: The recipe.
        extra: More arguments for ``recipe_smoke.py`` (``--var K=V``, ``--wait N``...).

    Returns:
        The entry for the results file.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from noust.recipes import get_recipe

    recipe = get_recipe(name)
    started = time.monotonic()
    command = (
        f"cd {WORK} && rm -f evidence/{shlex.quote(name)}.json && "
        f"PYTHONPATH={WORK}/src python3 scripts/recipe_smoke.py {shlex.quote(name)} "
        f"--project-prefix {PREFIX}- --evidence {WORK}/evidence "
        + " ".join(shlex.quote(item) for item in extra)
    )
    entry: dict[str, Any] = {"recipe": name, "version": recipe.version, "date": now()}
    try:
        finished = inside(command, check=False, timeout=RECIPE_TIMEOUT)
        output = finished.stdout + finished.stderr
    except subprocess.TimeoutExpired:
        output = f"timed out after {RECIPE_TIMEOUT}s"
        finished = None
        inside("docker ps -aq | xargs -r docker rm -f >/dev/null 2>&1", check=False)
        inside("docker volume ls -q | xargs -r docker volume rm -f >/dev/null 2>&1", check=False)
    entry["seconds"] = int(time.monotonic() - started)
    report = inside(f"cat {WORK}/evidence/{shlex.quote(name)}.json", check=False)
    if report.returncode == 0 and report.stdout.strip():
        data = json.loads(report.stdout)
        entry["result"] = "pass" if not data["problems"] else "fail"
        entry["gate"] = (
            f"{data['gate_path']} -> {data['gate_status']} after {data['gate_seconds']}s"
        )
        if data["problems"]:
            entry["problems"] = data["problems"]
            entry["log_tail"] = (data.get("log_tail") or "")[-1500:]
        if data.get("notes"):
            entry["notes"] = data["notes"]
        entry["error_lines"] = data.get("error_lines", {}).get("count", 0)
    else:
        entry["result"] = "fail"
        entry["problems"] = [output.strip()[-600:] or "no report was written"]
    entry["rate_limited"] = bool(RATE_LIMITED.search(output + " ".join(entry.get("problems", []))))
    entry["images_removed"] = len(remove_images(name))
    return entry


def pending() -> list[str]:
    """
    List the installable Compose recipes that have no result yet.

    Returns:
        Recipe names, sorted.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from noust.recipes import list_recipes

    done = {entry["recipe"] for entry in read_results().get("results", [])}
    return sorted(
        r.name for r in list_recipes() if r.kind == "compose" and r.available and r.name not in done
    )


#: The exit status of ``run`` when it stopped before the end of its list (a registry that
#: counts pulls, a drive short of space), and when there was nothing to run.
EXIT_STOPPED = 3
EXIT_NOTHING = 4


def run_batch(names: list[str], extra: list[str]) -> int:
    """
    Run recipes one after another, recording each before the next starts.

    Args:
        names: The recipes.
        extra: Arguments passed to ``recipe_smoke.py`` for each.

    Returns:
        0 when the list was run to the end (a recipe that fails is a result, not an error), or
        :data:`EXIT_STOPPED` when it was cut short.
    """
    up()
    sync()
    for position, name in enumerate(names, start=1):
        space = free_space_gib()
        if space is not None and space < 30:
            print(f"[recipes] {space} GiB free on C:, stopping before {name}")
            return EXIT_STOPPED
        print(f"[recipes] ({position}/{len(names)}) {name} (free: {space} GiB)", flush=True)
        entry = smoke(name, extra=extra)
        record(entry)
        verdict = entry["result"].upper()
        print(
            f"[recipes] {name}: {verdict} in {entry['seconds']}s"
            + (f" - {entry['problems'][0][:200]}" if entry.get("problems") else ""),
            flush=True,
        )
        if entry.get("rate_limited"):
            print("[recipes] the registry is rate limiting this address: stopping", flush=True)
            return EXIT_STOPPED
    return 0


def status() -> None:
    """Print the totals of the results file."""
    document = read_results()
    print(json.dumps(document.get("totals", {}), indent=2))
    for entry in document.get("results", []):
        if entry["result"] != "pass":
            print(
                f"  {entry['result']:5} {entry['recipe']}: {(entry.get('problems') or [''])[0][:140]}"
            )


def purge() -> None:
    """Remove the container, the volume under its Docker and the image, by name."""
    run.remove_container(CONTAINER)
    host(["docker", "volume", "rm", "-f", run.DOCKER_DATA_VOLUME], check=False)
    host(["docker", "image", "rm", run.IMAGE_TAG], check=False)
    print("[recipes] removed", CONTAINER, run.DOCKER_DATA_VOLUME, run.IMAGE_TAG)


def main(argv: list[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0, :data:`EXIT_STOPPED` or :data:`EXIT_NOTHING`.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for plain in ("up", "sync", "shared-clean", "status", "purge"):
        sub.add_parser(plain)
    runner = sub.add_parser("run")
    runner.add_argument("names", nargs="*")
    runner.add_argument("--pending", action="store_true")
    runner.add_argument("--limit", type=int, default=0, help="stop after this many recipes")
    runner.add_argument("--var", action="append", default=[])
    runner.add_argument("--wait", type=int, default=None)
    args = parser.parse_args(argv)

    if args.command == "up":
        up()
    elif args.command == "sync":
        sync()
    elif args.command == "shared-clean":
        shared_clean()
    elif args.command == "status":
        status()
    elif args.command == "purge":
        purge()
    else:
        names = list(args.names) or (pending() if args.pending else [])
        if args.limit:
            names = names[: args.limit]
        if not names:
            print("nothing to run")
            return EXIT_NOTHING
        extra = [item for var in args.var for item in ("--var", var)]
        if args.wait:
            extra += ["--wait", str(args.wait)]
        return run_batch(names, extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
