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

#: Where recipes that are not in the catalogue yet wait to be tried: ``<name>.yaml`` and
#: ``assets/<name>/`` as the catalogue lays them out. A recipe that passes is accepted into
#: the catalogue (``accept``); one that does not never enters the repository.
DRAFTS = Path(os.environ.get("NOUST_RECIPES_DRAFTS", str(Path.home() / "recetas-work" / "drafts")))

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

#: What a pull says when the network, and not the recipe, failed: a name that did not resolve, a
#: connection that timed out. The run is not a result about the recipe and is made again.
TRANSIENT = re.compile(
    r"no such host|i/o timeout|TLS handshake timeout|connection reset by peer|"
    r"temporary failure in name resolution|context deadline exceeded",
    re.IGNORECASE,
)

#: What a registry that counts pulls says when it has had enough.
RATE_LIMITED = re.compile(r"toomanyrequests|pull rate limit|429 Too Many Requests", re.IGNORECASE)

#: Pull-through mirrors of Docker Hub's public images the container's Docker asks first.
REGISTRY_MIRRORS = ("mirror.gcr.io",)

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
    # times out under load (as scenarios_real.ensure_inner_dns does). Docker Hub counts anonymous
    # pulls per address (100 an hour) and a catalogue pulls hundreds: the daemon reads Hub's public
    # images through a pull-through mirror first, which serves the same digests, and falls back on
    # Hub for what the mirror does not hold.
    resolvers = ", ".join(f'"{address}"' for address in FALLBACK_RESOLVERS)
    mirrors = ", ".join(f'"https://{host_name}"' for host_name in REGISTRY_MIRRORS)
    inside(
        "grep -q 1.1.1.1 /etc/resolv.conf || "
        "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' >> /etc/resolv.conf\n"
        "grep -q 'registry-mirrors' /etc/docker/daemon.json || {\n"
        "cat > /etc/docker/daemon.json <<'EOF'\n"
        "{\n"
        '  "storage-driver": "overlay2",\n'
        '  "features": {"containerd-snapshotter": false},\n'
        f'  "dns": [{resolvers}],\n'
        f'  "registry-mirrors": [{mirrors}]\n'
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


def is_draft(name: str) -> bool:
    """
    Args:
        name: A recipe.

    Returns:
        Whether it waits in the drafts folder rather than in the catalogue.
    """
    return (DRAFTS / f"{name}.yaml").is_file()


def sync_drafts(names: list[str]) -> None:
    """
    Put the drafts among the names into the package inside the container.

    Args:
        names: The recipes about to run.
    """
    for name in names:
        if not is_draft(name):
            continue
        target = f"{CONTAINER}:{WORK}/src/noust/recipes"
        host(["docker", "cp", str(DRAFTS / f"{name}.yaml"), f"{target}/{name}.yaml"])
        if (DRAFTS / "assets" / name).is_dir():
            inside(f"rm -rf {WORK}/src/noust/recipes/assets/{shlex.quote(name)}")
            host(["docker", "cp", str(DRAFTS / "assets" / name), f"{target}/assets/{name}"])


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


def recipe_facts(name: str) -> tuple[str, str]:
    """
    Read a recipe's version and Compose document, from the drafts or the catalogue.

    Args:
        name: The recipe.

    Returns:
        The version and the document.
    """
    import yaml

    if is_draft(name):
        data = yaml.safe_load((DRAFTS / f"{name}.yaml").read_text(encoding="utf-8"))
        return str(data["version"]), str(data["compose"])
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from noust.recipes import get_recipe

    recipe = get_recipe(name)
    return recipe.version, recipe.compose or ""


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

    _, compose = recipe_facts(name)
    document = load_compose(compose)
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


def check_bookstack(project: str, directory: str) -> list[str]:
    """
    Prove what BookStack's recipe says it does: the factory account is gone and the generated one
    signs in.

    A new BookStack can be signed into as admin@admin.com with the password ``password``. The
    recipe's second service replaces that account; this reads the database of the running stack and
    asks PHP, in the container that holds the generated password, whether it matches the hash.

    Args:
        project: The Compose project of the stack kept running.
        directory: Where its ``.env`` is, inside the harness container.

    Returns:
        What is wrong; empty when the account was replaced and the new password verifies.
    """
    values = dict(
        line.partition("=")[::2]
        for line in inside(f"cat {directory}/.env").stdout.splitlines()
        if "=" in line
    )
    database = f"{project}-db-1"
    query = (
        f"docker exec {database} mariadb -ubookstack -p{shlex.quote(values['DB_PASSWORD'])} "  # noqa: S608 - a shell command that runs a fixed query
        "bookstack -N -e 'SELECT email FROM users'"
    )
    emails = inside(query, check=False).stdout.split()
    problems = []
    if "admin@admin.com" in emails:
        problems.append("the factory account admin@admin.com is still there")
    if values.get("ADMIN_EMAIL") not in emails:
        problems.append(f"the generated administrator {values.get('ADMIN_EMAIL')} is not there")
    verify = (
        "$p = new PDO('mysql:host=db;dbname=bookstack', 'bookstack', getenv('DB_PASSWORD'));"
        "$s = $p->prepare('SELECT password FROM users WHERE email = ?');"
        "$s->execute([getenv('ADMIN_EMAIL')]); $h = (string) $s->fetchColumn();"
        "echo (password_verify(getenv('ADMIN_PASSWORD'), $h) ? 'generated-ok ' : 'generated-bad ')"
        ".(password_verify('password', $h) ? 'factory-still-works' : 'factory-refused');"
    )
    answer = inside(
        f"docker exec {project}-bookstack-admin-1 php -r {shlex.quote(verify)}", check=False
    ).stdout
    if "generated-ok" not in answer or "factory-refused" not in answer:
        problems.append(f"the administrator's password does not verify as it should: {answer!r}")
    return problems


def check_openbao(project: str, directory: str) -> list[str]:
    """
    Walk the opening ceremony the recipe's first notes describe, on the running stack.

    A new OpenBao is uninitialised and sealed and the gate accepts that. The notes tell the operator
    to initialise it and unseal it with the commands ``bao operator init`` and ``bao operator
    unseal KEY`` (run through ``noust app exec``, which runs them in the service's container with
    its environment): this runs the same commands in the same container and asks whether the server
    ends up unsealed.

    Args:
        project: The Compose project of the stack kept running.
        directory: Where its ``.env`` is, inside the harness container (unused).

    Returns:
        What is wrong; empty when the server initialises, unseals with the key it printed and
        answers as unsealed.
    """
    container = f"{project}-openbao-1"
    started = inside(
        f"docker exec {container} bao operator init -key-shares=1 -key-threshold=1 -format=json",
        check=False,
    )
    try:
        key = json.loads(started.stdout)["unseal_keys_b64"][0]
    except (ValueError, KeyError, IndexError):
        return [f"bao operator init, as the notes say, printed no key: {started.stdout[-300:]!r}"]
    inside(f"docker exec {container} bao operator unseal {shlex.quote(key)}", check=False)
    status = inside(f"docker exec {container} bao status -format=json", check=False).stdout
    try:
        sealed = json.loads(status)["sealed"]
    except (ValueError, KeyError):
        return [f"bao status answered {status[-300:]!r}"]
    return [] if sealed is False else ["the server is still sealed after the key the notes use"]


#: Checks beyond the gate, for a recipe that promises something the gate cannot see: they run on
#: the stack while it is still up (``recipe_smoke.py --keep``) and the stack is removed after.
CHECKS = {"bookstack": check_bookstack, "openbao": check_openbao}

#: What ``recipe_smoke.py --keep`` prints when it leaves a stack up.
KEPT = re.compile(r"kept: (?P<directory>\S+) \(project (?P<project>\S+)\)")


def smoke(name: str, *, extra: list[str]) -> dict[str, Any]:
    """
    Start one recipe in the container and say what happened.

    Args:
        name: The recipe.
        extra: More arguments for ``recipe_smoke.py`` (``--var K=V``, ``--wait N``...).

    Returns:
        The entry for the results file.
    """
    version, _ = recipe_facts(name)
    started = time.monotonic()
    command = (
        f"cd {WORK} && rm -f evidence/{shlex.quote(name)}.json && "
        f"PYTHONPATH={WORK}/src python3 scripts/recipe_smoke.py {shlex.quote(name)} "
        f"--project-prefix {PREFIX}- --evidence {WORK}/evidence "
        + ("--keep " if name in CHECKS else "")
        + " ".join(shlex.quote(item) for item in extra)
    )
    entry: dict[str, Any] = {"recipe": name, "version": version, "date": now()}
    if is_draft(name):
        header = (DRAFTS / f"{name}.yaml").read_text(encoding="utf-8").splitlines()[0]
        entry["source"] = (
            "dokploy" if "Dokploy" in header else "coolify" if "Coolify" in header else "own"
        )
    else:
        entry["source"] = "catalogue"
    try:
        finished = inside(command, check=False, timeout=RECIPE_TIMEOUT)
        output = finished.stdout + finished.stderr
    except subprocess.TimeoutExpired:
        output = f"timed out after {RECIPE_TIMEOUT}s"
        finished = None
        inside("docker ps -aq | xargs -r docker rm -f >/dev/null 2>&1", check=False)
        inside("docker volume ls -q | xargs -r docker volume rm -f >/dev/null 2>&1", check=False)
    kept = KEPT.search(output)
    if kept is not None:
        entry_checks: list[str] = []
        try:
            report_up = inside(f"cat {WORK}/evidence/{shlex.quote(name)}.json", check=False)
            if report_up.stdout.strip() and not json.loads(report_up.stdout)["problems"]:
                entry_checks = CHECKS[name](kept["project"], kept["directory"])
        finally:
            inside(
                f"cd {kept['directory']} && docker compose -p {kept['project']} "
                f"-f docker-compose.yml -f noust-test.override.yml --env-file .env "
                "down -v --remove-orphans --timeout 10; "
                f"rm -rf {kept['directory']}",
                check=False,
                timeout=300,
            )
        checked = entry_checks
    else:
        checked = []
    entry["seconds"] = int(time.monotonic() - started)
    report = inside(f"cat {WORK}/evidence/{shlex.quote(name)}.json", check=False)
    if report.returncode == 0 and report.stdout.strip():
        data = json.loads(report.stdout)
        entry["result"] = "pass" if not data["problems"] else "fail"
        entry["gate"] = (
            f"{data['gate_path']} -> {data['gate_status']} after {data['gate_seconds']}s"
        )
        entry["gate_status"] = data["gate_status"]
        if data.get("suggested_health"):
            entry["suggested_health"] = data["suggested_health"]
        # A stack whose container keeps restarting or has left is not a slow one.
        entry["unsteady"] = any(
            container.get("restarts", 0) > 0
            or container.get("state") not in ("running",)
            or container.get("health") == "unhealthy"
            for container in data.get("containers") or ()
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
    if checked:
        entry["result"] = "fail"
        entry["problems"] = [*entry.get("problems", []), *checked]
    elif kept is not None and entry["result"] == "pass":
        entry["verified"] = (
            "what the recipe promises beyond the gate was checked on the running stack"
        )
    entry["transient"] = bool(
        entry["result"] == "fail"
        and "docker compose up failed" in " ".join(entry.get("problems", []))
        and TRANSIENT.search(" ".join(entry.get("problems", [])))
    )
    entry["rate_limited"] = bool(RATE_LIMITED.search(output + " ".join(entry.get("problems", []))))
    entry["images_removed"] = len(remove_images(name))
    return entry


def committed() -> set[str]:
    """
    List the recipes the repository tracks.

    Returns:
        Their names. A recipe that is a draft in the working tree is not among them: it is run
        by name, and kept or removed by what the run says.
    """
    listed = host(
        ["git", "-C", str(REPO_ROOT), "ls-files", "src/noust/recipes/*.yaml"], timeout=30
    ).stdout.split()
    return {Path(path).stem for path in listed}


def pending() -> list[str]:
    """
    List the installable Compose recipes of the repository that have no result yet.

    Returns:
        Recipe names, sorted.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from noust.recipes import list_recipes

    # A recipe a registry refused to serve is not a result about the recipe: it runs again.
    done = {
        entry["recipe"]
        for entry in read_results().get("results", [])
        if not entry.get("rate_limited") and not entry.get("transient")
    }
    tracked = committed()
    return sorted(
        r.name
        for r in list_recipes()
        if r.kind == "compose" and r.available and r.name not in done and r.name in tracked
    )


def pending_drafts() -> list[str]:
    """
    List the drafts that have no result yet, the most wanted first.

    Returns:
        Draft names: in the order of ``queue.txt`` next to the drafts folder when there is one
        (most starred projects first), else sorted.
    """
    done = {
        entry["recipe"]
        for entry in read_results().get("results", [])
        if not entry.get("rate_limited") and not entry.get("transient")
    }
    waiting = [p.stem for p in DRAFTS.glob("*.yaml") if p.stem not in done]
    queue = DRAFTS.parent / "queue.txt"
    if queue.is_file():
        order = {name: place for place, name in enumerate(queue.read_text().split())}
        return sorted(waiting, key=lambda name: (order.get(name, len(order)), name))
    return sorted(waiting)


def next_names(count: int) -> list[str]:
    """
    Choose what the next batch runs: the recipes waiting on a person's say-so first, then the
    catalogue's own that have no result, then the drafts, most wanted first.

    Args:
        count: How many.

    Returns:
        Up to ``count`` recipe names, none repeated.
    """
    done = {
        entry["recipe"]
        for entry in read_results().get("results", [])
        if not entry.get("rate_limited") and not entry.get("transient")
    }
    priority = DRAFTS.parent / "priority.txt"
    first = priority.read_text().split() if priority.is_file() else []
    chosen: list[str] = []
    for name in [*first, *pending(), *pending_drafts()]:
        if name not in done and name not in chosen:
            chosen.append(name)
    return chosen[:count]


#: The exit status of ``run`` when it stopped before the end of its list (a registry that
#: counts pulls, a drive short of space), and when there was nothing to run.
EXIT_STOPPED = 3
EXIT_NOTHING = 4


#: What a stack that is still starting answers, or does not: worth a second look with more time.
SLOW_ANSWERS = ("502", "503", "504")

#: The longest a gate may wait (``health.timeout`` is held to 600).
SLOW_WAIT = 600


def set_draft_health(name: str, path: str, expect: str, timeout: int) -> None:
    """
    Point a draft's health check where the application answered.

    Args:
        name: The draft.
        path: The path the gate asks.
        expect: The statuses it accepts.
        timeout: Seconds the gate waits.
    """
    target = DRAFTS / f"{name}.yaml"
    line = f'health: {{path: {path}, expect: "{expect}", timeout: {timeout}}}'
    target.write_text(
        re.sub(r"^health: .*$", lambda _: line, target.read_text(encoding="utf-8"), flags=re.M),
        encoding="utf-8",
    )


def draft_health(name: str) -> tuple[str, str, int]:
    """
    Args:
        name: A draft.

    Returns:
        Its health check's path, expectation and timeout.
    """
    import yaml

    health = yaml.safe_load((DRAFTS / f"{name}.yaml").read_text(encoding="utf-8"))["health"]
    return health["path"], str(health["expect"]), int(health.get("timeout", 180))


def retry_draft(name: str, first: dict[str, Any], extra: list[str]) -> dict[str, Any]:
    """
    Try a draft that failed once more, when the first run says what to change.

    The run saw the application answer somewhere other than the draft's path (``suggested_health``),
    or a proxy-like answer from a stack that may only be slow. The draft is corrected and run again;
    what is recorded is the second run, with the first one's problem beside it.

    Args:
        name: The draft.
        first: The entry of the run that failed.
        extra: Arguments for ``recipe_smoke.py``.

    Returns:
        The entry to record: the second run's, or the first when nothing was worth changing.
    """
    suggestion = first.get("suggested_health")
    status = str(first.get("gate_status", ""))
    slow = (status in SLOW_ANSWERS or status.startswith("conn:")) and not first.get("unsteady")
    if not suggestion and not slow:
        return first
    path, expect, timeout = draft_health(name)
    if suggestion:
        path, expect = suggestion["path"], suggestion["expect"]
    else:
        timeout = SLOW_WAIT
    set_draft_health(name, path, expect, timeout)
    sync_drafts([name])
    second = smoke(name, extra=extra)
    second["attempts"] = 2
    second["first_attempt"] = (first.get("problems") or [""])[0][:200]
    return second


def importer() -> Any:
    """
    Load scripts/recipe_import.py, which knows how a draft is written.

    Returns:
        The module.
    """
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    sys.path.insert(0, str(REPO_ROOT / "src"))
    import recipe_import

    return recipe_import


def accept(names: list[str]) -> list[str]:
    """
    Move drafts that passed into the catalogue's folder of this tree.

    Args:
        names: The drafts; every one with a passing result when empty.

    Returns:
        The names moved.
    """
    import shutil

    passed = {e["recipe"] for e in read_results().get("results", []) if e["result"] == "pass"}
    moved: list[str] = []
    for name in names or sorted(p.stem for p in DRAFTS.glob("*.yaml")):
        if name not in passed or not is_draft(name):
            continue
        if importer().FORBIDDEN_WORDS.search((DRAFTS / f"{name}.yaml").read_text(encoding="utf-8")):
            print(f"{name}: not accepted, it names a word the history may not carry")
            continue
        draft = (DRAFTS / f"{name}.yaml").read_text(encoding="utf-8")
        (REPO_ROOT / "src/noust/recipes" / f"{name}.yaml").write_text(
            importer().credit_header(draft), encoding="utf-8"
        )
        if (DRAFTS / "assets" / name).is_dir():
            target = REPO_ROOT / "src/noust/recipes/assets" / name
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(DRAFTS / "assets" / name, target)
        moved.append(name)
    return moved


def run_batch(names: list[str], extra: list[str], budget_minutes: int = 0) -> int:
    """
    Run recipes one after another, recording each before the next starts.

    Args:
        names: The recipes.
        extra: Arguments passed to ``recipe_smoke.py`` for each.
        budget_minutes: Start no recipe after the batch has run this long (0 for no limit), so
            that the machine's one Docker place is given back on a schedule and not when a run
            of failures ends.

    Returns:
        0 when the list was run to the end (a recipe that fails is a result, not an error), or
        :data:`EXIT_STOPPED` when it was cut short.
    """
    up()
    sync()
    sync_drafts(names)
    began = time.monotonic()
    for position, name in enumerate(names, start=1):
        if budget_minutes and time.monotonic() - began > budget_minutes * 60:
            print(f"[recipes] {budget_minutes} minutes used: leaving the place, {name} next time")
            return 0
        space = free_space_gib()
        if space is not None and space < 30:
            print(f"[recipes] {space} GiB free on C:, stopping before {name}")
            return EXIT_STOPPED
        print(f"[recipes] ({position}/{len(names)}) {name} (free: {space} GiB)", flush=True)
        entry = smoke(name, extra=extra)
        if entry["result"] == "fail" and is_draft(name):
            entry = retry_draft(name, entry, extra)
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
    accepting = sub.add_parser("accept")
    accepting.add_argument("names", nargs="*")
    runner = sub.add_parser("run")
    runner.add_argument("names", nargs="*")
    runner.add_argument("--pending", action="store_true")
    runner.add_argument(
        "--drafts", action="store_true", help="the drafts with no result yet, most wanted first"
    )
    runner.add_argument(
        "--next",
        type=int,
        default=0,
        metavar="N",
        help="the next N recipes to try, whichever kind (priority.txt, then the catalogue's "
        "recipes with no result, then the drafts)",
    )
    runner.add_argument("--limit", type=int, default=0, help="stop after this many recipes")
    runner.add_argument("--var", action="append", default=[])
    runner.add_argument("--wait", type=int, default=None)
    runner.add_argument(
        "--budget", type=int, default=0, metavar="MINUTES", help="start no recipe after this long"
    )
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
    elif args.command == "accept":
        print(" ".join(accept(list(args.names))))
    else:
        names = list(args.names) or (
            next_names(args.next)
            if args.next
            else pending()
            if args.pending
            else pending_drafts()
            if args.drafts
            else []
        )
        if args.limit:
            names = names[: args.limit]
        if not names:
            print("nothing to run")
            return EXIT_NOTHING
        extra = [item for var in args.var for item in ("--var", var)]
        if args.wait:
            extra += ["--wait", str(args.wait)]
        return run_batch(names, extra, args.budget)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
