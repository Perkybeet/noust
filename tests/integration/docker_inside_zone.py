#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scenarios of the Docker zone that need a container to look at, run inside an engine (``docker_run.py``).

A container from the inside (why it ended, what it uses, its restarts, its log, a console), the actions the gate
allows by owner with the cleanup that removes what its plan said, and the guard of privileges over what Compose
resolves a stack to.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from docker_inside_lib import (
    BUSYBOX,
    LABEL,
    SCENARIO_LABEL,
    WORK,
    Run,
    compose_version_short,
    docker,
    make_store,
    must,
    numbers,
    project_down,
    reset,
    runner,
    scenario,
    up,
    wait_for,
    write_stack,
)

# ====================================================================== 4. a container from the inside


def run_labelled(name: str, scenario_name: str, *args: str) -> None:
    """
    Start a container of a scenario.

    Args:
        name: Its name.
        scenario_name: The scenario, for the label that finds it again.
        *args: ``docker run`` options, the image and the command.
    """
    must(
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            LABEL,
            "--label",
            f"{SCENARIO_LABEL}={scenario_name}",
            *args,
        ),
        f"docker run {name}",
    )


def state_of(name: str) -> str:
    """
    Args:
        name: A container.

    Returns:
        What Docker says its state is.
    """
    return docker("inspect", "--format", "{{.State.Status}}", name).stdout.strip()


@scenario("container_inside")
def container_inside(run: Run) -> None:
    """A container seen from the inside: why it ended, what it uses, its restarts, its log and a console."""
    from noust.core.docker.gate import DockerGate
    from noust.core.docker.owners import Registry

    reset("container_inside")
    gate = DockerGate(runner=runner(), registry=lambda: Registry())
    try:
        run_labelled(
            "w3rd-ci-err", "container_inside", BUSYBOX, "sh", "-c", "echo boom-line >&2; exit 3"
        )
        run_labelled("w3rd-ci-ok", "container_inside", BUSYBOX, "sh", "-c", "echo done; exit 0")
        run_labelled("w3rd-ci-kill", "container_inside", BUSYBOX, "sleep", "3600")
        run_labelled("w3rd-ci-stop", "container_inside", "--init", BUSYBOX, "sleep", "3600")
        run_labelled(
            "w3rd-ci-secret",
            "container_inside",
            BUSYBOX,
            "sh",
            "-c",
            "echo 'DATABASE_URL=postgres://app:hunter2pass@db:5432/app'; exit 1",
        )
        run_labelled(
            "w3rd-ci-oom",
            "container_inside",
            "-m",
            "32m",
            "--memory-swap",
            "32m",
            BUSYBOX,
            "sh",
            "-c",
            'a=x; while :; do a="$a$a"; done',
        )
        run_labelled(
            "w3rd-ci-health",
            "container_inside",
            "--health-cmd",
            "echo nope; exit 1",
            "--health-interval",
            "1s",
            "--health-retries",
            "1",
            BUSYBOX,
            "sleep",
            "3600",
        )
        run_labelled(
            "w3rd-ci-run",
            "container_inside",
            "-m",
            "64m",
            "--cpus",
            "0.5",
            "--restart",
            "unless-stopped",
            BUSYBOX,
            "sh",
            "-c",
            "i=0; while :; do echo tick-$i; i=$((i+1)); sleep 1; done",
        )
        run_labelled("w3rd-ci-free", "container_inside", BUSYBOX, "sleep", "3600")
        run_labelled("w3rd-ci-pause", "container_inside", BUSYBOX, "sleep", "3600")
        must(
            docker(
                "create",
                "--name",
                "w3rd-ci-created",
                "--label",
                LABEL,
                "--label",
                f"{SCENARIO_LABEL}=container_inside",
                BUSYBOX,
                "true",
            ),
            "create",
        )
        loop_started = int(time.time())
        run_labelled(
            "w3rd-ci-loop",
            "container_inside",
            "--restart",
            "always",
            BUSYBOX,
            "sh",
            "-c",
            "echo looping; exit 1",
        )
        docker("kill", "w3rd-ci-kill")
        docker("stop", "-t", "5", "w3rd-ci-stop")
        docker("pause", "w3rd-ci-pause")
        wait_for(
            lambda: all(
                state_of(n) == "exited" for n in ("w3rd-ci-err", "w3rd-ci-ok", "w3rd-ci-secret")
            ),
            30,
        )

        detail = gate.container_detail("w3rd-ci-err")
        run.check(
            "a container that failed is an error with its exit code",
            detail.state.kind == "error" and detail.state.exit_code == 3,
            detail.state,
        )
        run.check("and says its last lines", "boom-line" in detail.last_lines, detail.last_lines)
        run.check(
            "and is not read for consumption",
            not detail.usage.available and detail.usage.reason == "not_running",
            detail.usage,
        )
        run.check(
            "its end is dated",
            detail.inspected is not None and detail.inspected.finished_at is not None,
            detail.inspected,
        )
        run.check("and it is not looping", not detail.looping, detail.restarts_recent)
        ok = gate.container_detail("w3rd-ci-ok").to_dict()
        run.check(
            "one that did its work is ended, a job",
            ok["why"]["kind"] == "ended" and ok["job"],
            ok["why"],
        )
        killed = gate.container_detail("w3rd-ci-kill")
        run.check("one that was killed is killed", killed.state.kind == "killed", killed.state)
        stopped = gate.container_detail("w3rd-ci-stop")
        run.check(
            "one that was stopped with an init is terminated",
            stopped.state.kind == "terminated",
            stopped.state,
        )
        paused = gate.container_detail("w3rd-ci-pause")
        run.check("a paused one is paused", paused.state.kind == "paused", paused.state)
        created = gate.container_detail("w3rd-ci-created")
        run.check(
            "one that was never started is created", created.state.kind == "created", created.state
        )
        secret_log = gate.container_logs("w3rd-ci-secret", tail=10)
        run.check(
            "a credential in a log is masked",
            "hunter2pass" not in secret_log and "DATABASE_URL" in secret_log,
            secret_log,
        )
        run.check(
            "and every line is dated",
            all(line[:4].isdigit() for line in secret_log.splitlines() if line),
            secret_log,
        )

        if wait_for(lambda: state_of("w3rd-ci-oom") == "exited", 40):
            oom = gate.container_detail("w3rd-ci-oom")
            run.check(
                "a container that ran out of memory says so",
                oom.state.kind == "oom" and bool(oom.inspected and oom.inspected.oom_killed),
                oom.state,
            )
            run.check(
                "and what its limit was",
                oom.inspected is not None and oom.inspected.memory_limit_bytes == 32 * 1024 * 1024,
                oom.inspected and oom.inspected.memory_limit_bytes,
            )
        else:
            run.skip(
                "a container that ran out of memory",
                f"the kernel did not kill it in 40 s here ({state_of('w3rd-ci-oom')})",
            )

        wait_for(
            lambda: docker(
                "inspect", "--format", "{{.State.Health.Status}}", "w3rd-ci-health"
            ).stdout.strip()
            == "unhealthy",
            30,
        )
        unhealthy = gate.container_detail("w3rd-ci-health")
        run.check(
            "a failing health check is unhealthy",
            unhealthy.state.kind == "unhealthy",
            unhealthy.state,
        )
        health = unhealthy.inspected.health if unhealthy.inspected else None
        run.check(
            "with its streak and its last output",
            health is not None and health.failing_streak >= 1 and "nope" in health.last_output,
            health,
        )

        wait_for(
            lambda: int(
                docker("inspect", "--format", "{{.RestartCount}}", "w3rd-ci-loop").stdout.strip()
                or 0
            )
            >= 4,
            40,
        )
        raw_events = docker(
            "events",
            "--since",
            str(loop_started - 1),
            "--until",
            str(int(time.time()) + 1),
            "--filter",
            "type=container",
            "--filter",
            "container=w3rd-ci-loop",
            "--format",
            "{{.Action}}",
        ).stdout.split()
        run.note(
            "docker events of a container its restart policy keeps starting",
            {kind: raw_events.count(kind) for kind in sorted(set(raw_events))},
        )
        as_json = docker(
            "events",
            "--since",
            str(loop_started - 1),
            "--until",
            str(int(time.time()) + 1),
            "--filter",
            "type=container",
            "--filter",
            "container=w3rd-ci-loop",
            "--filter",
            "event=die",
            "--filter",
            "event=start",
            "--format",
            "{{json .}}",
        ).stdout.splitlines()
        run.note("the first events of a policy restart, as Noust asks for them", as_json[:3])
        looped = gate.container_detail("w3rd-ci-loop")
        run.check(
            "a container Docker keeps restarting is a loop",
            looped.looping,
            (looped.state, looped.restarts_recent, sorted(set(raw_events))),
        )
        run.check(
            "its restarts are counted from Docker's events",
            looped.restarts_recent >= 3,
            looped.restarts_recent,
        )
        run.check(
            "its policy is read",
            looped.inspected is not None and looped.inspected.restart_policy.name == "always",
            looped.inspected and looped.inspected.restart_policy,
        )
        run.check(
            "and how many times it fell",
            looped.inspected is not None and looped.inspected.restart_count >= 3,
            looped.inspected and looped.inspected.restart_count,
        )

        wait_for(lambda: gate.container_detail("w3rd-ci-run").usage.reason != "too_young", 20)
        reading = gate.container_detail("w3rd-ci-run")
        run.note(
            "docker stats of a container",
            docker("stats", "--no-stream", "--format", "{{json .}}", "w3rd-ci-run").stdout.strip(),
        )
        usage = reading.usage
        run.check("a reading is taken", usage.available, usage)
        if usage.available:
            run.check(
                "it has a processor, a memory and a count of processes",
                usage.cpu_percent is not None
                and (usage.memory_bytes or 0) > 0
                and (usage.pids or 0) >= 1,
                usage,
            )
            run.check(
                "its memory limit is the one it was given",
                usage.memory_limit_bytes == 64 * 1024 * 1024,
                usage,
            )
            run.check(
                "and what it received and sent",
                usage.net_rx_bytes is not None and usage.net_tx_bytes is not None,
                usage,
            )
        run.check(
            "its limits are read",
            reading.inspected is not None and reading.inspected.cpu_limit == 0.5,
            reading.inspected and reading.inspected.cpu_limit,
        )
        wait_for(lambda: gate.container_detail("w3rd-ci-free").usage.reason != "too_young", 20)
        free = gate.container_detail("w3rd-ci-free")
        run.check(
            "a container with no limit does not say the machine's memory is its limit",
            free.usage.available and free.usage.memory_limit_bytes is None,
            free.usage,
        )
        run.check(
            "its environment is nowhere in what is said of it",
            "PATH=" not in json.dumps(reading.to_dict()),
        )

        arrivals: list[tuple[float, str]] = []
        began = time.monotonic()
        follower = threading.Thread(
            target=lambda: gate.follow_container_logs(
                "w3rd-ci-run",
                lambda line: arrivals.append((time.monotonic() - began, line)),
                tail=1,
                seconds=7,
            )
        )
        follower.start()
        follower.join(25)
        ticks = [(at, line) for at, line in arrivals if "tick-" in line]
        run.check(
            "a log followed arrives as it is written",
            len(ticks) >= 4 and ticks[0][0] < 4,
            [(round(at, 1), line[:40]) for at, line in arrivals][:8],
        )
        run.check("and ends when it is told to", not follower.is_alive(), "still following")

        argv = gate.exec_argv(gate.container("w3rd-ci-run").id, "sh")
        handle = runner().pty(argv, max_seconds=30, idle_seconds=20, env={"TERM": "xterm"})
        collected = b""
        try:
            handle.write(b"echo ans-$((6*7)) $TERM\n")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and b"ans-42" not in collected:
                chunk = handle.read(0.5)
                if chunk:
                    collected += chunk
            run.check(
                "a shell opens in it and answers", b"ans-42 xterm" in collected, collected[-300:]
            )
            handle.write(b"exit\n")
            wait_for(lambda: not handle.is_alive(), 10)
            run.check(
                "and ends with it",
                not handle.is_alive() and handle.exit_code == 0,
                handle.exit_code,
            )
        finally:
            handle.terminate()
            handle.close()
    finally:
        reset("container_inside")


# ====================================================================== 5. actions and the cleanup


@scenario("cleanup")
def cleanup(run: Run) -> None:
    """Actions are judged by owner, and a cleanup removes what its plan said and nothing more."""
    from noust.core.docker.cleanup import parse_reclaimed
    from noust.core.docker.errors import DockerOwnedError, DockerPlanChangedError
    from noust.core.docker.gate import DockerGate
    from noust.core.exceptions import ConfirmationRequired, DockerError, ValidationError
    from noust.core.store import App
    from noust.deployers.docker_compose import compose_project_name

    reset("cleanup")
    store = make_store("cleanup")
    base = WORK / "cleanup"
    shop = base / "apps" / "shop-example-com"
    files: list[tuple[str, Path]] = []
    release = "20260101-000000-nogit"
    try:
        shop_file = write_stack(
            shop,
            f"services:\n  web:\n    image: {BUSYBOX}\n    command: sh -c 'echo precious > /data/file; sleep 3600'\n"
            f"    volumes: [data:/data]\n    labels:\n      {SCENARIO_LABEL}: cleanup\n"
            f"volumes:\n  data:\n    labels:\n      {SCENARIO_LABEL}: cleanup\n",
        )
        project = compose_project_name(shop, shop_file)
        files.append((project, shop_file))
        up(project, shop_file)
        store.create_app(
            App(
                domain="shop.example.com",
                app_type="docker-compose",
                app_path=str(shop),
                layout="inplace",
                port=8081,
            )
        )
        wait_for(
            lambda: docker(
                "run", "--rm", "-v", f"{project}_data:/d", BUSYBOX, "cat", "/d/file"
            ).stdout.strip()
            == "precious",
            20,
        )
        docker("stop", "-t", "1", f"{project}-web-1")

        must(
            docker(
                "create",
                "--name",
                "w3rd-cl-stopped",
                "--label",
                LABEL,
                "--label",
                f"{SCENARIO_LABEL}=cleanup",
                BUSYBOX,
                "true",
            ),
            "create",
        )
        docker("start", "-a", "w3rd-cl-stopped")
        run_labelled("w3rd-cl-running", "cleanup", BUSYBOX, "sleep", "3600")
        docker(
            "volume",
            "create",
            "--label",
            f"{SCENARIO_LABEL}=cleanup",
            "--label",
            LABEL,
            "w3rd-cl-orphan",
        )
        docker("run", "--rm", "-v", "w3rd-cl-orphan:/d", BUSYBOX, "sh", "-c", "echo mine > /d/file")
        run_labelled("w3rd-cl-anon", "cleanup", "-v", "/anon", BUSYBOX, "true")
        # The container goes and its anonymous volume stays: that is what a leftover is.
        docker("wait", "w3rd-cl-anon")
        docker("rm", "w3rd-cl-anon")
        docker(
            "network",
            "create",
            "--label",
            f"{SCENARIO_LABEL}=cleanup",
            "--label",
            LABEL,
            "w3rd_cl_net",
        )
        docker(
            "network",
            "create",
            "--label",
            f"{SCENARIO_LABEL}=cleanup",
            "--label",
            LABEL,
            "w3rd_cl_busy",
        )
        run_labelled(
            "w3rd-cl-onnet", "cleanup", "--network", "w3rd_cl_busy", BUSYBOX, "sleep", "3600"
        )
        context = base / "img"
        context.mkdir(parents=True, exist_ok=True)
        for version in ("one", "two"):
            (context / "Dockerfile").write_text(
                f"FROM {BUSYBOX}\nLABEL {SCENARIO_LABEL}=cleanup\nRUN echo {version} > /v\n",
                encoding="utf-8",
            )
            must(docker("build", "-q", "-t", "w3rd-cl-dangling:latest", str(context)), "build")
        (context / "Dockerfile").write_text(
            f"FROM {BUSYBOX}\nLABEL {SCENARIO_LABEL}=cleanup\nRUN echo three > /v\n",
            encoding="utf-8",
        )
        must(docker("build", "-q", "-t", "w3rd-cl-unused:latest", str(context)), "build")
        # The way back of an application: an image of its own, which no container runs.
        (context / "Dockerfile").write_text(
            f"FROM {BUSYBOX}\nLABEL {SCENARIO_LABEL}=cleanup\nRUN echo old-release > /v\n",
            encoding="utf-8",
        )
        must(
            docker("build", "-q", "-t", f"noust-shop-example-com:{release}", str(context)), "build"
        )

        gate = DockerGate(runner=runner(), store=store)

        def snapshot() -> dict[str, set[str]]:
            return {
                "volumes": {v.name for v in gate.volumes().items},
                "images": {i.id for i in gate.images().items},
                "containers": {c.name for c in gate.containers().items},
                "networks": {n.name for n in gate.networks().items},
            }

        before = snapshot()
        plan = gate.cleanup_plan()
        listed = {(item.kind, item.name) for item in plan.items}
        run.note("the plan", sorted(listed))
        run.check(
            "a stopped container nobody of Noust made is offered",
            ("container", "w3rd-cl-stopped") in listed,
            listed,
        )
        run.check(
            "a running container is not", ("container", "w3rd-cl-running") not in listed, listed
        )
        orphan = next((i for i in plan.items if i.name == "w3rd-cl-orphan"), None)
        run.check(
            "an unused volume nobody made is offered, to be named",
            orphan is not None and orphan.type_name,
            listed,
        )
        run.check(
            "an anonymous volume is offered as one",
            any(i.kind == "volume" and i.anonymous for i in plan.items),
            listed,
        )
        run.check("an unused network is offered", ("network", "w3rd_cl_net") in listed, listed)
        run.check(
            "a network somebody is on is not", ("network", "w3rd_cl_busy") not in listed, listed
        )
        if docker("image", "ls", "-a", "--filter", "dangling=true", "-q").stdout.split():
            run.check(
                "an image with no name is offered and ticked",
                any(i.kind == "image" and i.why == "dangling" and i.tick for i in plan.items),
                [i.to_dict() for i in plan.items if i.kind == "image"],
            )
        else:
            run.skip(
                "an image with no name is offered and ticked",
                "this engine keeps no image without a name after a tag moved (the containerd image store drops it)",
            )
        run.check(
            "an unused image with a name is offered and not ticked",
            any(
                i.kind == "image" and i.name.startswith("w3rd-cl-unused") and not i.tick
                for i in plan.items
            ),
            [i.name for i in plan.items if i.kind == "image"],
        )
        run.check(
            "what an application owns is nowhere in it",
            not any("shop" in item.name for item in plan.items),
            listed,
        )
        run.check(
            "the way back of an application is said to stay",
            any(k.why == "rollback" for k in plan.kept),
            [vars(k) for k in plan.kept],
        )

        chosen = [
            i
            for i in plan.items
            if i.name in ("w3rd-cl-stopped", "w3rd_cl_net")
            or (i.kind == "image" and i.why == "dangling")
        ]
        try:
            assert orphan is not None
            gate.apply_cleanup(plan.token, [orphan.id])
            run.check("a volume is not removed until it is named", False, "it was removed")
        except ConfirmationRequired:
            run.check("a volume is not removed until it is named", True)
        docker(
            "network",
            "create",
            "--label",
            LABEL,
            "--label",
            f"{SCENARIO_LABEL}=cleanup",
            "w3rd_cl_late",
        )
        try:
            gate.apply_cleanup(plan.token, [i.id for i in chosen])
            run.check(
                "a plan that is not the one that was shown is not applied", False, "it was applied"
            )
        except DockerPlanChangedError:
            run.check("a plan that is not the one that was shown is not applied", True)
        run.check(
            "and nothing was touched by it",
            snapshot()["containers"] == before["containers"],
            snapshot()["containers"],
        )
        plan = gate.cleanup_plan()
        chosen = [
            i
            for i in plan.items
            if i.name in ("w3rd-cl-stopped", "w3rd_cl_net", "w3rd_cl_late")
            or (i.kind == "image" and i.why == "dangling")
        ]
        done = gate.apply_cleanup(plan.token, [i.id for i in chosen])
        run.check(
            "the plan chosen is applied",
            len(done.removed) == len(chosen) and not done.failed,
            (done.removed, done.failed),
        )
        after = snapshot()
        gone = {kind: before[kind] - after[kind] for kind in before}
        run.check(
            "what went is what was chosen and no more",
            gone["containers"] == {"w3rd-cl-stopped"}
            and gone["networks"] == {"w3rd_cl_net"}
            and not gone["volumes"]
            and len(gone["images"]) == sum(1 for i in chosen if i.kind == "image"),
            gone,
        )
        run.check(
            "a volume with the data of an application is still there with its data",
            docker(
                "run", "--rm", "-v", f"{project}_data:/d", BUSYBOX, "cat", "/d/file"
            ).stdout.strip()
            == "precious",
            "",
        )
        run.check("so is the orphan volume nobody named", "w3rd-cl-orphan" in after["volumes"], "")
        named = gate.cleanup_plan()
        again = next(i for i in named.items if i.name == "w3rd-cl-orphan")
        removed = gate.apply_cleanup(
            named.token, [again.id], confirm_names={"w3rd-cl-orphan": "w3rd-cl-orphan"}
        )
        run.check(
            "a volume that was named goes",
            bool(removed.removed)
            and "w3rd-cl-orphan" not in {v.name for v in gate.volumes().items},
            removed,
        )

        app_container = f"{project}-web-1"
        for label, call in (
            ("starting a container of an application", lambda: gate.start_container(app_container)),
            ("stopping it", lambda: gate.stop_container(app_container)),
            ("removing it", lambda: gate.remove_container(app_container)),
            (
                "removing the volume of an application",
                lambda: gate.remove_volume(f"{project}_data", confirm_name=f"{project}_data"),
            ),
            (
                "removing the network of an application",
                lambda: gate.remove_network(f"{project}_default"),
            ),
            (
                "removing the way back of an application",
                lambda: gate.remove_image(f"noust-shop-example-com:{release}"),
            ),
        ):
            try:
                call()
                run.check(f"{label} is refused and says where to go", False, "it was done")
            except DockerOwnedError as exc:
                run.check(
                    f"{label} is refused and says where to go",
                    "noust" in exc.details and "shop.example.com" in exc.details,
                    exc.details,
                )
        gate.stop_container("w3rd-cl-running")
        run.check(
            "a container nobody made is stopped",
            state_of("w3rd-cl-running") == "exited",
            state_of("w3rd-cl-running"),
        )
        gate.start_container("w3rd-cl-running")
        run.check(
            "and started", state_of("w3rd-cl-running") == "running", state_of("w3rd-cl-running")
        )
        gate.restart_container("w3rd-cl-running")
        run.check(
            "and restarted", state_of("w3rd-cl-running") == "running", state_of("w3rd-cl-running")
        )
        try:
            gate.remove_container("w3rd-cl-running")
            run.check(
                "a running container is not removed without force, in Docker's own words",
                False,
                "it was removed",
            )
        except DockerError as exc:
            said = getattr(exc, "output", "")
            run.check(
                "a running container is not removed without force, in Docker's own words",
                "running" in said.lower(),
                said,
            )
        try:
            gate.remove_image(BUSYBOX)
            run.check("an image a container uses is not removed", False, "it was removed")
        except (ValidationError, DockerOwnedError):
            run.check("an image a container uses is not removed", True)
        try:
            gate.remove_network("w3rd_cl_busy")
            run.check(
                "a network somebody is on is not removed, in Docker's own words",
                False,
                "it was removed",
            )
        except DockerError as exc:
            said = getattr(exc, "output", "")
            run.check(
                "a network somebody is on is not removed, in Docker's own words",
                "endpoint" in said,
                said,
            )
        try:
            gate.remove_volume("w3rd-cl-not-there", confirm_name="x")
            run.check("an object that is not there is not found", False, "it was removed")
        except DockerError as exc:
            run.check(
                "an object that is not there is not found", "No volume" in exc.message, exc.message
            )
        try:
            gate.remove_network("bridge")
            run.check("Docker's own network stays", False, "it was removed")
        except DockerOwnedError:
            run.check("Docker's own network stays", True)
        pruned = runner().run(gate.prune_build_cache_argv(), timeout=120)
        run.note("docker builder prune", (pruned.stdout or pruned.stderr)[-200:])
        run.check(
            "the build cache is pruned by age and the space it freed is read",
            pruned.success and parse_reclaimed(pruned.stdout) is not None,
            (pruned.stdout, pruned.stderr),
        )
    finally:
        for project_name, file in files:
            project_down(project_name, file)
        docker(
            "rmi",
            f"noust-shop-example-com:{release}",
            "w3rd-cl-dangling:latest",
            "w3rd-cl-unused:latest",
        )
        reset("cleanup")


# ====================================================================== 6. the guard of privileges

#: The program a ``provider:`` names in the scenario: it only leaves a marker, so what Compose really does with
#: ``provider:`` is seen and not guessed. It lives in the engine's own disposable machine and is removed after.
PROVIDER_PROGRAM = "noust-w6a-provider-canary"
PROVIDER_PATH = Path("/usr/local/bin") / PROVIDER_PROGRAM
PROVIDER_MARKER = WORK / "privileges" / "provider" / "ran"

GUARD_DOCUMENTS: dict[str, tuple[dict[str, str], bool, str]] = {
    "plain": (
        {"compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n"},
        False,
        "a stack that asks for nothing",
    ),
    "privileged": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    privileged: true\n    command: sleep 3600\n"
        },
        True,
        "privileged as written",
    ),
    "variable": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    privileged: ${{PRIV:-true}}\n    command: sleep 3600\n"
        },
        True,
        "privileged behind a variable",
    ),
    "include": (
        {
            "compose.yaml": f"include:\n  - part.yaml\nservices:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n",
            "part.yaml": f"services:\n  hidden:\n    image: {BUSYBOX}\n    privileged: true\n    command: sleep 3600\n",
        },
        True,
        "privileged behind an include",
    ),
    "extends": (
        {
            "compose.yaml": "services:\n  web:\n    extends:\n      file: base.yaml\n      service: base\n    command: sleep 3600\n",
            "base.yaml": f"services:\n  base:\n    image: {BUSYBOX}\n    privileged: true\n",
        },
        True,
        "privileged behind an extends",
    ),
    "socket": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n    command: sleep 3600\n"
        },
        True,
        "Docker's socket mounted",
    ),
    "socketvariable": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    volumes:\n      - ${{SOCK:-/var/run/docker.sock}}:/s\n    command: sleep 3600\n"
        },
        True,
        "Docker's socket behind a variable",
    ),
    "profile": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n  debug:\n    image: {BUSYBOX}\n    profiles: [debug]\n    privileged: ${{DBG:-true}}\n"
        },
        True,
        "privileged in a profile that is not on, behind a variable",
    ),
    "capadd": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    cap_add: [SYS_ADMIN]\n    command: sleep 3600\n"
        },
        True,
        "SYS_ADMIN, a capability that gives the machine",
    ),
    "capnet": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    cap_add: [NET_BIND_SERVICE]\n    command: sleep 3600\n"
        },
        False,
        "a capability that does not give the machine: only said",
    ),
    "device": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    devices: ['/dev/null:/dev/x']\n    command: sleep 3600\n"
        },
        True,
        "a host device",
    ),
    "hostpid": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    pid: host\n    command: sleep 3600\n"
        },
        True,
        "the host's pid namespace",
    ),
    "outside": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    volumes:\n      - /opt:/opt\n    command: sleep 3600\n"
        },
        True,
        "a directory of the server outside the application",
    ),
    "runmount": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    volumes:\n      - /run:/hostrun\n    command: sleep 3600\n"
        },
        True,
        "the run directory, which holds the Docker socket",
    ),
    "link": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    volumes:\n      - ./host:/mnt\n    command: sleep 3600\n"
        },
        True,
        "a link of the repository that leads to /",
    ),
    "envfile": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    env_file: /etc/noust/web-secret\n    command: sleep 3600\n"
        },
        True,
        "an env_file that is Noust's own state",
    ),
    "buildcontext": (
        {"compose.yaml": "services:\n  web:\n    build:\n      context: /etc/noust\n"},
        True,
        "a build context that is Noust's own state",
    ),
    "remoteinclude": (
        {
            "compose.yaml": f"include:\n  - https://127.0.0.1:1/part.yaml\nservices:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n"
        },
        True,
        "an include with an address: refused, though Compose reads an https one as a path",
    ),
    "remotegit": (
        {
            "compose.yaml": f"include:\n  - git://127.0.0.1:1/part.git\nservices:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n"
        },
        True,
        "an include that Compose fetches with git",
    ),
    "remoteoci": (
        {
            "compose.yaml": f"include:\n  - oci://127.0.0.1:1/part:1\nservices:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n"
        },
        True,
        "an include that Compose fetches from a registry",
    ),
    "provider": (
        {
            "compose.yaml": f"services:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n  tool:\n    provider:\n      type: {PROVIDER_PROGRAM}\n"
        },
        True,
        "a provider: a program the machine runs when the stack starts",
    ),
    "volumesfrom": (
        {
            "compose.yaml": f"services:\n  a:\n    image: {BUSYBOX}\n    volumes: [/var/run/docker.sock:/s]\n    command: sleep 3600\n  b:\n    image: {BUSYBOX}\n    volumes_from: [a]\n    command: sleep 3600\n"
        },
        True,
        "the socket brought in by volumes_from",
    ),
}


#: The Compose project of the stack that already runs; a run on a machine whose Docker is shared names it apart.
HAD_PROJECT = "privhad"


def what_the_containers_have(run: Run, real: Any) -> None:
    """
    A stack whose mount is made of a variable already had it: its containers say so, and a variable changed
    since is not what they were given.

    The stack runs with a directory of the server that only exists once Compose reads its ``.env``
    (``${DATA_DIR}:/data``), a capability, a device and confinement switched off. What its containers were
    given is read from the engine and judged by the guard of the compose file, and the new file, resolved by
    Compose with the ``.env`` of the moment, is compared with it: the same variable is what the stack had,
    another value (somewhere else, or ``/``) is not.

    Args:
        run: What reports.
        real: The runner Noust runs every command through.
    """
    from noust.core.docker import grants as docker_grants
    from noust.deployers.helpers import compose_running, compose_verdict
    from noust.deployers.helpers.compose_guard import GuardContext, inspect_compose, resolve_model

    folder = WORK / "privileges" / "had"
    data = WORK / "privileges" / "had-data"
    data.mkdir(parents=True, exist_ok=True)
    (folder).mkdir(parents=True, exist_ok=True)
    (folder / ".env").write_text(f"DATA_DIR={data}\n", encoding="utf-8")
    file = write_stack(
        folder,
        f"services:\n  web:\n    image: {BUSYBOX}\n    command: sleep 3600\n    labels: ['{LABEL}']\n"
        "    volumes:\n      - ${DATA_DIR}:/data\n    cap_add: [SYS_ADMIN]\n"
        "    devices:\n      - /dev/null:/dev/null2\n    security_opt:\n      - seccomp:unconfined\n",
    )
    base = ["docker", "compose", "-p", HAD_PROJECT, "-f", str(file)]
    context = GuardContext(app_path=folder, project_dir=folder)
    try:
        up(HAD_PROJECT, file)
        grants = docker_grants.read_project_grants(real, HAD_PROJECT)
        run.check(
            "the containers of a stack are read from the engine by the label Compose writes",
            [g.service for g in grants] == ["web"],
            grants,
        )
        found = compose_running.facts_of(grants, context)
        for needle in (str(data), "SYS_ADMIN", "/dev/null", "seccomp"):
            run.check(
                f"what the engine says the container was given includes {needle}",
                any(needle in sentence for sentence in found.refused),
                found.refused,
            )

        def judged(value: str) -> compose_verdict.Verdict:
            (folder / ".env").write_text(f"DATA_DIR={value}\n", encoding="utf-8")
            model, why = resolve_model(real, [[*base, "config", "--format", "json"]], folder)
            if model is None:
                raise RuntimeError(f"Compose did not resolve the stack: {why}")
            heritage = compose_verdict.Heritage(
                running=lambda: compose_running.read(
                    lambda: docker_grants.read_project_grants(real, HAD_PROJECT), context
                )
            )
            return compose_verdict.decide(
                inspect_compose(model, folder, context=context), already=heritage
            )

        same = judged(str(data))
        run.check(
            "the variable as it was is what the stack already had: warned about, nothing stops it",
            not same.stop and bool(same.had),
            {"stop": same.stop, "had": same.had},
        )
        for other in ("/", str(WORK / "privileges" / "had-elsewhere")):
            moved = judged(other)
            run.check(
                f"the variable changed to {other} is new: refused",
                bool(moved.stop),
                {"stop": moved.stop, "had": moved.had},
            )
    finally:
        project_down(HAD_PROJECT, file)


@scenario("privileges")
def privileges(run: Run) -> None:
    """The guard of privileges reads what Compose resolves a stack to, so nothing hides behind include or a variable."""
    from noust.deployers.helpers.compose_guard import inspect_compose, load_compose, with_resolved

    reset("privileges")
    real = runner()
    run.note("Compose", compose_version_short())
    try:
        for name, (documents, must_refuse, what) in GUARD_DOCUMENTS.items():
            folder = WORK / "privileges" / name
            folder.mkdir(parents=True, exist_ok=True)
            for filename, text in documents.items():
                (folder / filename).write_text(text, encoding="utf-8")
            if name == "link" and not (folder / "host").is_symlink():
                (folder / "host").symlink_to("/")
            file = folder / "compose.yaml"
            document = load_compose(file.read_text(encoding="utf-8"))
            written = inspect_compose(document, folder)
            base = ["docker", "compose", "-p", f"priv{name}", "-f", str(file)]
            commands = [
                [*base, "--profile", "*", "config", "--format", "json"],
                [*base, "config", "--format", "json"],
            ]
            judged, unresolved = with_resolved(written, document, real, commands, folder)
            refused = bool(judged.refused)
            ignored = False
            if name == "include" and numbers(compose_version_short()) < (2, 20):
                # Before 2.20 Compose does not read ``include``: the services it names are not part of what runs, and
                # a guard that judges what Compose resolves rightly finds nothing in them.
                model = docker(
                    "compose", "-p", "privinclude", "-f", str(file), "config", "--format", "json"
                )
                ignored = model.returncode == 0 and '"hidden"' not in model.stdout
                run.note(
                    "include, by a Compose that does not read it",
                    model.stdout[:200] or model.stderr[:300],
                )
            if ignored:
                run.check(
                    f"{what}: nothing runs from it in a Compose that does not read include",
                    not refused,
                    {"judged": judged.refused, "log": unresolved},
                )
            elif name == "capnet":
                run.check(
                    f"{what}",
                    not refused and bool(judged.warned),
                    {"refused": judged.refused, "warned": judged.warned},
                )
            else:
                run.check(
                    f"{what}: {'refused' if must_refuse else 'let through'}",
                    refused == must_refuse,
                    {"as written": written.refused, "judged": judged.refused, "log": unresolved},
                )
            if must_refuse:
                run.note(
                    f"{name}: refused as written"
                    if written.refused
                    else f"{name}: found only by resolving",
                    judged.refused[:2],
                )
        profile = docker(
            "compose",
            "-p",
            "privprofile",
            "-f",
            str(WORK / "privileges" / "profile" / "compose.yaml"),
            "--profile",
            "*",
            "config",
            "--format",
            "json",
        )
        run.note(
            "--profile '*'",
            {
                "exit": profile.returncode,
                "stderr": profile.stderr[:200],
                "has the debug service": '"debug"' in profile.stdout,
            },
        )
        included = docker(
            "compose",
            "-p",
            "privinclude",
            "-f",
            str(WORK / "privileges" / "include" / "compose.yaml"),
            "config",
            "--format",
            "json",
        )
        run.note(
            "an include resolved by this Compose",
            {"exit": included.returncode, "stderr": included.stderr[:200]},
        )
        # What Compose really does with what the guard refuses and could not be shown to do: a remote include
        # is fetched by ``config``, which is before anything of Noust's sees the model; a provider is a program
        # the machine runs. Both are recorded as this Compose does them, and the guard refuses them as written.
        for kind in ("remoteinclude", "remotegit", "remoteoci"):
            remote = docker(
                "compose",
                "-p",
                f"priv{kind}",
                "-f",
                str(WORK / "privileges" / kind / "compose.yaml"),
                "config",
                "--format",
                "json",
            )
            run.note(
                f"{kind}, resolved by this Compose (does it go to the network when it reads the file?)",
                {"exit": remote.returncode, "stderr": remote.stderr[:300]},
            )
        provider_folder = WORK / "privileges" / "provider"
        PROVIDER_PATH.write_text(f"#!/bin/sh\ntouch {PROVIDER_MARKER}\nexit 0\n", encoding="utf-8")
        PROVIDER_PATH.chmod(0o755)
        started = docker(
            "compose",
            "-p",
            "privprovider",
            "-f",
            str(provider_folder / "compose.yaml"),
            "up",
            "-d",
            "--no-build",
        )
        run.note(
            "a provider, started by this Compose",
            {
                "exit": started.returncode,
                "stderr": started.stderr[:300],
                "the program ran": PROVIDER_MARKER.exists(),
            },
        )
        docker(
            "compose",
            "-p",
            "privprovider",
            "-f",
            str(provider_folder / "compose.yaml"),
            "down",
            "--remove-orphans",
        )
        # Noust's own environment. Compose fills ``${NAME}`` and reads ``secrets: environment:`` from the
        # environment of the process that runs it; Noust's runner starts it without ``NOUST_*``. A plain
        # process, which is what the control is, does hand it over.
        environment_folder = WORK / "privileges" / "environment"
        environment_folder.mkdir(parents=True, exist_ok=True)
        environment_file = environment_folder / "compose.yaml"
        environment_file.write_text(
            f"services:\n  web:\n    image: {BUSYBOX}\n    environment:\n"
            "      LEAK: ${NOUST_W6A_CANARY:-nothing}\n    secrets: [leak]\n"
            "secrets:\n  leak:\n    environment: NOUST_W6A_CANARY\n",
            encoding="utf-8",
        )
        canary = "w6a-canary-value-9f3c"
        os.environ["NOUST_W6A_CANARY"] = canary
        try:
            asked = ["compose", "-p", "privenv", "-f", str(environment_file), "config"]
            control = docker(*asked)
            through = real.run(["docker", *asked], timeout=60)
        finally:
            os.environ.pop("NOUST_W6A_CANARY", None)
        if control.returncode != 0:
            # A Compose that does not know ``secrets: environment:`` (before 2.23) cannot show it.
            run.note(
                "this Compose does not read secrets: environment:",
                {"exit": control.returncode, "stderr": control.stderr[:200]},
            )
        else:
            run.check(
                "a plain process hands Compose its environment (so the check below can fail)",
                canary in control.stdout,
                {"exit": control.returncode, "stderr": control.stderr[:200]},
            )
            run.check(
                "Noust's own environment does not reach Compose: ${NOUST_...} and secrets: environment: get nothing",
                canary not in through.stdout + through.stderr,
                {"exit": through.exit_code, "stderr": through.stderr[:200]},
            )
        what_the_containers_have(run, real)
    finally:
        PROVIDER_PATH.unlink(missing_ok=True)
        reset("privileges")
