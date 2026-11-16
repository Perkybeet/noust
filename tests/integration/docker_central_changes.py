#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scenarios of what changes on a machine that only has Docker (``docker_run.py``): an application updated
behind its health gate, a stack's Compose edited, a stack that already ran brought to Noust, a recipe, and a
copy of a volume that runs on the central's own schedule.

Each ``scenario_<name>`` takes the harness and the engine and returns what it found.
"""

from __future__ import annotations

import json
from typing import Any

from docker_central_lib import (
    STACKS,
    Central,
    Found,
    Machine,
    push_image,
    ready,
    served,
    start_central,
)

# ====================================================================== 8. an image updated behind the health gate


def running_image(machine: Machine, domain: str) -> str:
    """
    Args:
        machine: The NAS.
        domain: A container application.

    Returns:
        The image its container runs: the release's, which Noust names after the application.
    """
    return machine.docker(
        "ps", "--filter", f"label=io.noust.app={domain}", "--format", "{{.Image}}"
    ).stdout.split()[0]


def scenario_update_gate(harness: Any, engine: Any) -> Any:
    """An application's image is updated behind its health check; one that does not start leaves what served."""
    found = Found()
    machine, central = start_central(harness, engine)
    domain, port = "gate.nas.test", 18501
    reference = f"{harness.registry}/w3rd/web:live"
    try:
        found.check("the central starts", ready(central), central.logs(20))
        found.check(
            "a first version is in the registry", push_image(harness, engine, "live", "web v1"), ""
        )
        done = central.noust(
            "create", "-d", domain, "--image", reference, "-p", str(port), "--no-ssl", timeout=600
        )
        found.check(
            "the application deploys from the registry's image",
            done.returncode == 0,
            (done.stdout + done.stderr)[-600:],
        )
        found.check("and serves the first version", served(machine, port, "web v1"), "")
        first = running_image(machine, domain)
        found.check(
            "its container runs the image Noust names after the release",
            first.startswith("noust-gate-nas-test:"),
            first,
        )

        found.check(
            "a second version is pushed under the same tag",
            push_image(harness, engine, "live", "web v2"),
            "",
        )
        # buildx asks a registry itself, from where it runs, and accepts plain HTTP only for a loopback address: the
        # local registry of the harness is the machine's loopback, and the central's (on Docker's bridge) is not.
        # What the image's buildx does is asked of a central on the machine's own network.
        ask = Central(
            machine,
            harness.central_image_name,
            name="noust-ask",
            network="host",
            data="/volume1/docker/noust-ask",
            extra_mounts=(),
            run_service=False,
        )
        machine.sh(
            "sh",
            "-c",
            "mkdir -p /volume1/docker/noust-ask/apps && chown -R 10001:10001 /volume1/docker/noust-ask",
        )
        try:
            ask.start()
            digest = ask.run("docker", "buildx", "imagetools", "inspect", reference, timeout=120)
            found.note(
                "the image's buildx asking the local registry",
                (digest.stdout + digest.stderr)[-400:],
            )
            found.check(
                "the buildx the central's image carries asks a registry what a tag is, without downloading it",
                digest.returncode == 0 and "Digest:" in digest.stdout,
                (digest.stdout + digest.stderr)[-400:],
            )
            config = ask.run(
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{json .Image}}",
                reference,
                timeout=120,
            )
            found.check(
                "and reads the image's config the way Noust reads it",
                config.returncode == 0 and '"config"' in config.stdout,
                (config.stdout + config.stderr)[-300:],
            )
        finally:
            ask.remove()
        pulled = central.noust("docker", "images", "check", "--pull", timeout=600)
        found.note("noust docker images check --pull", (pulled.stdout + pulled.stderr)[-700:])
        found.check(
            "looking with --pull works and finds the new version",
            pulled.returncode == 0 and "new version" in (pulled.stdout + pulled.stderr),
            (pulled.stdout + pulled.stderr)[-500:],
        )
        plan = central.noust("docker", "images", "plan", first, timeout=120)
        found.note(
            "noust docker images plan, of the image the application runs",
            (plan.stdout + plan.stderr)[-700:],
        )
        found.check(
            "the plan of the image the application runs is an update through the application",
            plan.returncode == 0 and "new version" in (plan.stdout + plan.stderr).lower(),
            (plan.stdout + plan.stderr)[-400:],
        )
        updated = central.noust(
            "docker", "images", "update", first, "--yes", "--accept-major", timeout=600
        )
        found.note("noust docker images update", (updated.stdout + updated.stderr)[-900:])
        found.check(
            "the application's update runs",
            updated.returncode == 0,
            (updated.stdout + updated.stderr)[-600:],
        )
        found.check("and serves the second version", served(machine, port, "web v2"), "")
        second = running_image(machine, domain)
        found.check("its container runs another release's image", second != first, (first, second))
        kept = machine.docker(
            "images",
            "--format",
            "{{.Repository}}:{{.Tag}}",
            "--filter",
            "reference=noust-gate-nas-test*",
        ).stdout.split()
        found.check("the image of the release before is kept to go back to", first in kept, kept)

        found.check(
            "a third version is pushed that does not listen",
            push_image(harness, engine, "live", "web v3", serves=False),
            "",
        )
        central.noust("docker", "images", "check", "--pull", timeout=600)
        failed = central.noust(
            "docker", "images", "update", second, "--yes", "--accept-major", timeout=600
        )
        said = failed.stdout + failed.stderr
        found.note("an update whose container does not answer", said[-1100:])
        found.check("an update that does not answer fails", failed.returncode != 0, said[-600:])
        found.check(
            "and says it went back to what served",
            "back" in said.lower() or "rolled" in said.lower() or "previous" in said.lower(),
            said[-600:],
        )
        found.check("what served is still serving", served(machine, port, "web v2"), "")
        found.check(
            "on the image it served from",
            running_image(machine, domain) == second,
            running_image(machine, domain),
        )
        status = central.json("status", domain, "--json")
        found.check(
            "and the application is not left failed",
            isinstance(status, dict)
            and "fail" not in json.dumps(status.get("app", {}).get("status", "")).lower(),
            status,
        )
        direct = central.noust(
            "app",
            "container",
            "set",
            domain,
            "--image",
            f"{harness.registry}/w3rd/small:bad",
            timeout=120,
        )
        found.note("an image that exits at once, set", (direct.stdout + direct.stderr)[-200:])
        failing = central.noust("update", domain, timeout=600)
        found.note(
            "an update to an image that exits at once", (failing.stdout + failing.stderr)[-900:]
        )
        found.check(
            "an update to an image that exits at once fails and leaves what served",
            failing.returncode != 0 and served(machine, port, "web v2"),
            (failing.stdout + failing.stderr)[-600:],
        )
    finally:
        central.remove()
    return found


# ====================================================================== 11. a stack that already ran, brought to Noust


OLD_STACK = """\
services:
  web:
    image: {registry}/w3rd/web:1
    restart: unless-stopped
    ports:
      - "{port}:8080"
    volumes:
      - data:/data
  worker:
    image: {registry}/w3rd/busybox:1.36
    command: sleep 3600
    restart: "no"
volumes:
  data: {{}}
"""


def ids_of(machine: Machine, project: str) -> dict[str, tuple[str, str]]:
    """
    Args:
        machine: The NAS.
        project: A Compose project.

    Returns:
        For each container of it, its id and when it started.
    """
    rows = machine.docker(
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.Names}}",
    ).stdout.split()
    found = {}
    for name in rows:
        said = machine.docker(
            "inspect", "--format", "{{.Id}} {{.State.StartedAt}}", name
        ).stdout.split()
        found[name] = (said[0], said[1]) if len(said) > 1 else ("", "")
    return found


def make_old_stack(
    machine: Machine,
    harness: Any,
    folder: str,
    project: str,
    central: Any = None,
    port: int = 18601,
) -> None:
    """
    Run a stack the way a person did before Noust: Compose by hand in a folder of the machine.

    Args:
        machine: The NAS.
        harness: The run.
        folder: Where it lives.
        project: Its Compose project.
    """
    machine.sh("sh", "-c", f"mkdir -p {folder}")
    machine.sh(
        "sh",
        "-c",
        f"cat > {folder}/compose.yaml",
        input_text=OLD_STACK.format(registry=harness.registry, port=port),
    )
    machine.sh("chown", "-R", "10001:10001", folder)
    argv = ("compose", "-p", project, "-f", f"{folder}/compose.yaml", "up", "-d")
    if central is None:
        # The machine's own Docker and Compose, the ones a person at the NAS uses.
        machine.docker(*argv, timeout=240)
    else:
        # The central's own: what Noust itself would have started.
        central.run("docker", *argv, timeout=240)


def scenario_bring(harness: Any, engine: Any) -> Any:
    """A stack that already ran is registered without a restart, with what would not come back after one said first."""
    found = Found()
    machine, central = start_central(harness, engine)
    try:
        found.check("the central starts", ready(central), central.logs(20))
        folder = f"{STACKS}/old"
        make_old_stack(machine, harness, folder, "old", central)
        found.check("the stack runs", served(machine, 18601, "web v1"), "")
        before = ids_of(machine, "old")
        found.check("with two containers", len(before) == 2, before)
        listed = central.json("docker", "stack", "list", "--json")
        found.note(
            "noust docker stack list",
            listed
            if listed is not None
            else central.noust("docker", "stack", "list").stdout[-500:],
        )
        preview = central.noust("docker", "stack", "bring", "old", "--preview", timeout=300)
        said = preview.stdout + preview.stderr
        found.note("noust docker stack bring --preview", said[-1200:])
        found.check("the preview works", preview.returncode == 0, said[-700:])
        found.check(
            "it names what would not come back after a restart, and the line that fixes it",
            "unless-stopped" in said and "worker" in said,
            said[-900:],
        )
        found.check(
            "and registers nothing",
            "old" not in json.dumps(central.json("list", "--json") or []),
            "",
        )
        done = central.noust(
            "docker", "stack", "bring", "old", "--name", "oldapp", "--yes", timeout=600
        )
        said = done.stdout + done.stderr
        found.note("noust docker stack bring", said[-1200:])
        found.check("bringing it works", done.returncode == 0, said[-800:])
        after = ids_of(machine, "old")
        found.check(
            "no container was recreated",
            {k: v[0] for k, v in before.items()} == {k: v[0] for k, v in after.items()},
            (before, after),
        )
        found.check(
            "and none was restarted",
            {k: v[1] for k, v in before.items()} == {k: v[1] for k, v in after.items()},
            (before, after),
        )
        found.check("it keeps serving", served(machine, 18601, "web v1"), "")
        names = json.dumps(central.json("list", "--json") or [])
        found.check(
            "the application is registered under the name typed", "oldapp" in names, names[:300]
        )
        again = central.noust("docker", "stack", "bring", "old", "--name", "oldapp2", timeout=120)
        found.check(
            "a stack that is Noust's already is refused with the reason",
            again.returncode != 0 and "already" in (again.stdout + again.stderr).lower(),
            (again.stdout + again.stderr)[-400:],
        )
        # A stack the machine's own Compose started (the NAS's Container Manager, a person at the shell): another
        # version of Compose than the central's.
        make_old_stack(machine, harness, f"{STACKS}/older", "older", None, 18609)
        other_before = ids_of(machine, "older")
        older = central.noust(
            "docker", "stack", "bring", "older", "--name", "olderapp", "--yes", timeout=300
        )
        said = older.stdout + older.stderr
        found.note(
            "a stack started by the machine's own Compose, brought by the central's", said[-1200:]
        )
        found.check(
            "a stack another Compose started is either brought with nothing restarted or refused with the reason",
            ids_of(machine, "older") == other_before,
            (other_before, ids_of(machine, "older")),
        )
        found.check(
            "and if it is refused, the words say that the Compose differs",
            older.returncode == 0 or "were made by Docker Compose" in said,
            said[-900:],
        )
        # A stack in a folder the central does not see, and a compose file it cannot read.
        machine.sh("sh", "-c", "mkdir -p /srv/outside")
        machine.sh(
            "sh",
            "-c",
            "cat > /srv/outside/compose.yaml",
            input_text=OLD_STACK.format(registry=harness.registry, port=18602)
            .replace("data:", "datao:")
            .replace("  data: {}", "  datao: {}"),
        )
        machine.docker(
            "compose", "-p", "outside", "-f", "/srv/outside/compose.yaml", "up", "-d", timeout=240
        )
        refused = central.noust(
            "docker", "stack", "bring", "outside", "--name", "outsideapp", "--yes", timeout=120
        )
        said = refused.stdout + refused.stderr
        found.note("a stack in a folder the central does not see", said[-600:])
        found.check(
            "a folder the central does not see is refused with the line to add",
            refused.returncode != 0 and "/srv/outside" in said,
            said[-600:],
        )
        machine.sh(
            "sh",
            "-c",
            f"mkdir -p {STACKS}/locked && cp /srv/outside/compose.yaml {STACKS}/locked/compose.yaml && chmod 600 {STACKS}/locked/compose.yaml && chown root:root {STACKS}/locked/compose.yaml && chmod 700 {STACKS}/locked",
        )
        machine.docker(
            "compose",
            "-p",
            "locked",
            "-f",
            f"{STACKS}/locked/compose.yaml",
            "up",
            "-d",
            timeout=240,
        )
        locked = central.noust(
            "docker", "stack", "bring", "locked", "--name", "lockedapp", "--yes", timeout=120
        )
        said = locked.stdout + locked.stderr
        found.note("a compose file the central cannot read", said[-600:])
        found.check(
            "a file the central cannot read is refused and says which",
            locked.returncode != 0
            and ("cannot read" in said or "docker-compose.yml" in said or "compose.yaml" in said),
            said[-600:],
        )
    finally:
        for project, file in (
            ("old", f"{STACKS}/old/compose.yaml"),
            ("outside", "/srv/outside/compose.yaml"),
            ("locked", f"{STACKS}/locked/compose.yaml"),
        ):
            machine.docker("compose", "-p", project, "-f", file, "down", "-v", "-t", "1")
        central.remove()
    return found


# ====================================================================== 10. editing a stack's Compose


def scenario_compose_edit(harness: Any, engine: Any) -> Any:
    """A stack's Compose is edited: judged, kept as a version, applied behind the gate, and a document that does not start is undone."""
    found = Found()
    # On the machine's own network, so that buildx (which asks a registry from where it runs) reaches the local
    # registry, which plain HTTP allows only on a loopback address.
    machine, central = start_central(harness, engine, network="host")
    try:
        found.check("the central starts", ready(central), central.logs(20))
        folder = f"{STACKS}/edit"
        make_old_stack(machine, harness, folder, "edit", central)
        found.check("the stack runs", served(machine, 18601, "web v1"), "")
        brought = central.noust(
            "docker", "stack", "bring", "edit", "--name", "editapp", "--yes", timeout=600
        )
        found.check(
            "it is brought to Noust",
            brought.returncode == 0,
            (brought.stdout + brought.stderr)[-600:],
        )
        shown = central.noust("docker", "stack", "compose", "show", "edit", timeout=120)
        found.check(
            "its document can be read",
            shown.returncode == 0 and "services:" in shown.stdout,
            (shown.stdout + shown.stderr)[-400:],
        )
        text = shown.stdout
        changed = text.replace(
            "restart: unless-stopped",
            "restart: unless-stopped\n    environment:\n      GREETING: hello",
        )
        machine.sh("sh", "-c", f"cat > {STACKS}/new.yml", input_text=changed)
        machine.sh("chown", "10001:10001", f"{STACKS}/new.yml")
        check = central.noust(
            "docker", "stack", "compose", "check", "edit", f"{STACKS}/new.yml", timeout=300
        )
        said = check.stdout + check.stderr
        found.note("noust docker stack compose check", said[-1200:])
        found.check(
            "a changed document is judged valid and says what it would recreate",
            check.returncode == 0 and "web" in said,
            said[-800:],
        )
        before = ids_of(machine, "edit")
        applied = central.noust(
            "docker",
            "stack",
            "compose",
            "apply",
            "edit",
            f"{STACKS}/new.yml",
            "--reason",
            "harness",
            "--yes",
            timeout=600,
        )
        said = applied.stdout + applied.stderr
        found.note("noust docker stack compose apply", said[-900:])
        found.check("applying it works", applied.returncode == 0, said[-700:])
        after = ids_of(machine, "edit")
        web_before = next((v for k, v in before.items() if "web" in k), ("", ""))
        web_after = next((v for k, v in after.items() if "web" in k), ("", ""))
        found.check(
            "the service that changed was recreated", web_before[0] != web_after[0], (before, after)
        )
        found.check("and serves", served(machine, 18601, "web v1"), "")
        env = machine.docker(
            "inspect",
            "--format",
            "{{json .Config.Env}}",
            next(iter(k for k in after if "web" in k)),
        ).stdout
        found.check("with the change in it", "GREETING=hello" in env, env[:300])
        history = central.noust("docker", "stack", "compose", "history", "edit", timeout=120)
        found.note("noust docker stack compose history", history.stdout[-600:])
        found.check(
            "two versions are kept: what the stack had and what was saved",
            history.stdout.count("\n") >= 2,
            history.stdout[-400:],
        )

        broken = changed.replace(
            "image: " + f"{harness.registry}/w3rd/web:1",
            "image: " + f"{harness.registry}/w3rd/web:broken",
        )
        machine.sh("sh", "-c", f"cat > {STACKS}/broken.yml", input_text=broken)
        machine.sh("chown", "10001:10001", f"{STACKS}/broken.yml")
        bad = central.noust(
            "docker",
            "stack",
            "compose",
            "apply",
            "edit",
            f"{STACKS}/broken.yml",
            "--reason",
            "harness",
            "--yes",
            timeout=600,
        )
        said = bad.stdout + bad.stderr
        found.note(
            "noust docker stack compose apply, a document that does not answer", said[-1000:]
        )
        found.check("a document that does not start fails", bad.returncode != 0, said[-700:])
        found.check("and the stack serves what it did", served(machine, 18601, "web v1"), "")
        again = central.noust("docker", "stack", "compose", "show", "edit", timeout=120)
        found.check(
            "and the document before is the one kept",
            "web:broken" not in again.stdout and "GREETING" in again.stdout,
            again.stdout[-500:],
        )
        missing = changed.replace(
            "image: " + f"{harness.registry}/w3rd/web:1",
            "image: " + f"{harness.registry}/w3rd/web:nowhere",
        )
        machine.sh("sh", "-c", f"cat > {STACKS}/missing.yml", input_text=missing)
        machine.sh("chown", "10001:10001", f"{STACKS}/missing.yml")
        gone = central.noust(
            "docker", "stack", "compose", "check", "edit", f"{STACKS}/missing.yml", timeout=300
        )
        said = gone.stdout + gone.stderr
        found.note("noust docker stack compose check, an image that does not exist", said[-700:])
        found.check(
            "a tag that does not exist in its registry stops it before anything is applied",
            gone.returncode != 0 and ("nowhere" in said or "not found" in said.lower()),
            said[-600:],
        )
        restored = central.noust(
            "docker",
            "stack",
            "compose",
            "restore",
            "edit",
            "1",
            "--reason",
            "harness",
            "--yes",
            timeout=600,
        )
        found.check(
            "the first version is put back",
            restored.returncode == 0,
            (restored.stdout + restored.stderr)[-600:],
        )
        env = machine.docker(
            "inspect",
            "--format",
            "{{json .Config.Env}}",
            next(iter(k for k in ids_of(machine, "edit") if "web" in k)),
        ).stdout
        found.check("and the change is gone", "GREETING=hello" not in env, env[:300])
    finally:
        machine.docker(
            "compose", "-p", "edit", "-f", f"{STACKS}/edit/compose.yaml", "down", "-v", "-t", "1"
        )
        central.remove()
    return found


# ====================================================================== 14. a recipe from deploy to delete


def scenario_recipe(harness: Any, engine: Any) -> Any:
    """A recipe of the catalogue: deployed, stopped, started, updated while its registry cannot be reached, and deleted."""
    found = Found()
    machine, central = start_central(harness, engine)
    domain, port = "whoami.nas.test", 18701
    try:
        found.check("the central starts", ready(central), central.logs(20))
        done = central.noust(
            "recipe",
            "install",
            "whoami",
            "-d",
            domain,
            "--port",
            str(port),
            "--no-ssl",
            "--no-backups",
            timeout=900,
        )
        said = done.stdout + done.stderr
        found.note("noust recipe install whoami", said[-900:])
        found.check(
            "a recipe deploys on a machine that has only Docker", done.returncode == 0, said[-900:]
        )
        found.check("and answers on the port of the machine", served(machine, port, "Hostname"), "")
        containers = machine.docker(
            "ps", "-q", "--filter", "label=io.noust.app=" + domain
        ).stdout.split()
        project_containers = machine.docker(
            "ps", "-q", "--filter", "label=com.docker.compose.project.working_dir"
        ).stdout.split()
        found.check(
            "its container is Docker's, kept alive by its restart policy",
            bool(containers or project_containers),
            "",
        )
        policy = machine.docker(
            "inspect",
            "--format",
            "{{.HostConfig.RestartPolicy.Name}}",
            (containers or project_containers)[0],
        ).stdout.strip()
        found.check(
            "with a policy that brings it back after the machine restarts",
            policy in ("unless-stopped", "always"),
            policy,
        )

        stopped = central.noust("stop", domain, timeout=180)
        found.check("it stops", stopped.returncode == 0, (stopped.stdout + stopped.stderr)[-500:])
        found.check("and no longer answers", not served(machine, port, "Hostname", seconds=6), "")
        started = central.noust("start", domain, timeout=180)
        found.check(
            "it starts again",
            started.returncode == 0 and served(machine, port, "Hostname"),
            (started.stdout + started.stderr)[-500:],
        )

        # An update while the registry cannot be reached: what served keeps serving.
        machine.sh(
            "sh",
            "-c",
            "cp /etc/hosts /etc/hosts.w3rd && echo '127.0.0.1 registry-1.docker.io auth.docker.io index.docker.io' >> /etc/hosts",
        )
        try:
            failed = central.noust("update", domain, timeout=600)
        finally:
            machine.sh(
                "sh",
                "-c",
                "[ -f /etc/hosts.w3rd ] && cat /etc/hosts.w3rd > /etc/hosts && rm -f /etc/hosts.w3rd; true",
            )
        said = failed.stdout + failed.stderr
        found.note("noust update with the registry unreachable", said[-900:])
        found.check(
            "an update that cannot get its image says so in words",
            failed.returncode != 0 or "up to date" in said.lower(),
            said[-700:],
        )
        found.check("and what served still serves", served(machine, port, "Hostname"), "")

        rolled = central.noust("rollback", domain, timeout=300)
        found.note("noust rollback", (rolled.stdout + rolled.stderr)[-600:])
        found.check(
            "going back is answered in words", bool((rolled.stdout + rolled.stderr).strip()), ""
        )
        found.check("and it still serves after it", served(machine, port, "Hostname"), "")

        gone = central.noust("delete", domain, "-f", timeout=300)
        found.check("it is deleted", gone.returncode == 0, (gone.stdout + gone.stderr)[-500:])
        left = machine.docker(
            "ps", "-aq", "--filter", "label=io.noust.app=" + domain
        ).stdout.split()
        names = json.dumps(central.json("list", "--json") or [])
        found.check(
            "with no container left",
            not left and not served(machine, port, "Hostname", seconds=6),
            left,
        )
        found.check("and not in the list", domain not in names, names[:300])
        found.check(
            "nor its folder",
            machine.sh(
                "sh", "-c", "ls /volume1/docker/noust/apps | grep -c whoami || true"
            ).stdout.strip()
            in ("0", ""),
            "",
        )
    finally:
        machine.sh(
            "sh",
            "-c",
            "[ -f /etc/hosts.w3rd ] && cat /etc/hosts.w3rd > /etc/hosts && rm -f /etc/hosts.w3rd; true",
        )
        central.remove()
    return found


# ====================================================================== 17. a scheduled copy of a volume


def scenario_volume_copy(harness: Any, engine: Any) -> Any:
    """A copy of a volume scheduled on the central runs by itself; one that came due while it was off runs once on its return and says so."""
    import time

    found = Found()
    machine, central = start_central(harness, engine)
    try:
        found.check("the central starts", ready(central), central.logs(20))
        folder = f"{STACKS}/copy"
        make_old_stack(machine, harness, folder, "copy", central)
        brought = central.noust(
            "docker", "stack", "bring", "copy", "--name", "copyapp", "--yes", timeout=600
        )
        found.check(
            "a stack with a volume is brought to Noust",
            brought.returncode == 0,
            (brought.stdout + brought.stderr)[-500:],
        )
        machine.docker("exec", "copy-web-1", "sh", "-c", "echo precious > /data/file")
        targets = central.noust("backup", "volumes", "targets", "copyapp", "--json", timeout=120)
        found.note("noust backup volumes targets", targets.stdout[-500:])
        found.check(
            "its volume is a target",
            "data" in targets.stdout,
            targets.stdout[-400:] + targets.stderr[-200:],
        )
        policy = central.noust(
            "backup",
            "volumes",
            "schedule",
            "set",
            "copyapp",
            "--schedule",
            "*-*-* *:*:00",
            "--keep",
            "5",
            "--json",
            timeout=120,
        )
        found.note("noust backup volumes schedule set", (policy.stdout + policy.stderr)[-600:])
        found.check(
            "a schedule is set", policy.returncode == 0, (policy.stdout + policy.stderr)[-500:]
        )

        def rows() -> list[dict[str, Any]]:
            report = central.json("central", "schedule", "status", "--json", timeout=60)
            if isinstance(report, dict):
                report = report.get("items") or report.get("tasks") or []
            return [row for row in (report or []) if "copy" in json.dumps(row)]

        listed = rows()
        found.note("noust central schedule status", listed)
        found.check(
            "the central lists it with when it runs next",
            bool(listed) and bool(listed[0].get("next_run")),
            listed,
        )
        began = time.monotonic()
        ran = False
        while time.monotonic() - began < 150 and not ran:
            now = rows()
            ran = bool(now) and now[0].get("last_status") == "ok" and bool(now[0].get("last_run"))
            if not ran:
                time.sleep(5)
        found.check("the central runs it by itself, within a couple of minutes", ran, rows())
        archives = central.noust("backup", "volumes", "list", "--json", timeout=120)
        found.note("noust backup volumes list", archives.stdout[-500:])
        found.check(
            "an archive of the volume exists",
            "copy" in archives.stdout and archives.returncode == 0,
            archives.stdout[-400:] + archives.stderr[-200:],
        )
        count_before = archives.stdout.count('"id"')

        # The central is off when the copy comes due. It says it missed runs only when the LAST run due is more
        # than five minutes old (``noust.central.scheduler.LATE_AFTER``; a minutely schedule is never that late),
        # so the copy is set to two minutes of the hour that both pass while it is off.
        from datetime import datetime, timedelta, timezone

        soon = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        first, second = soon + timedelta(minutes=2), soon + timedelta(minutes=3)
        again = central.noust(
            "backup",
            "volumes",
            "schedule",
            "set",
            "copyapp",
            "--schedule",
            f"*-*-* *:{first.minute:02d},{second.minute:02d}:00",
            "--keep",
            "9",
            "--json",
            timeout=120,
        )
        found.check(
            "a schedule of two minutes of the hour is set",
            again.returncode == 0,
            (again.stdout + again.stderr)[-300:],
        )
        machine.docker("stop", "-t", "10", "noust")
        time.sleep(max(0.0, (second - datetime.now(timezone.utc)).total_seconds()) + 330)
        machine.docker("start", "noust")
        ready(central)
        resumed = time.monotonic()
        came_back = False
        while time.monotonic() - resumed < 120 and not came_back:
            log = central.logs(5000)
            came_back = any(
                "missed" in line.lower() for line in log.splitlines() if not line.startswith("{")
            )
            if not came_back:
                time.sleep(5)
        log = central.logs(5000)
        said = [
            line
            for line in log.splitlines()
            if "missed" in line.lower() and not line.startswith("{")
        ]
        found.note("what the central says after it was off", said)
        found.check(
            "it says that it missed two runs and runs once",
            came_back and any("missed 2 runs" in line and "once" in line for line in said),
            said or log[-400:],
        )
        time.sleep(40)
        archives = central.noust("backup", "volumes", "list", "--json", timeout=120)
        found.check(
            "and one run was made for the two it missed",
            archives.stdout.count('"id"') - count_before == 1,
            (count_before, archives.stdout.count('"id"')),
        )
        status = rows()
        found.check(
            "the row says that it missed one run besides the one it made",
            bool(status) and status[0].get("missed") == 1,
            status,
        )
    finally:
        machine.docker(
            "compose", "-p", "copy", "-f", f"{STACKS}/copy/compose.yaml", "down", "-v", "-t", "1"
        )
        central.remove()
    return found
