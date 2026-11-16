#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scenarios of a machine that only has Docker: the central's image against each engine (``docker_run.py``).

What deploys is deployed through the socket, on the engine, as siblings of the central; its ``docker`` and
``docker compose`` are the ones the image carries (``install_docker_client.py`` says which), so each scenario is also that client meeting
an engine of another age. Each ``scenario_<name>`` takes the harness and the engine and returns what it found.
"""

from __future__ import annotations

import json
from typing import Any

from docker_central_lib import (
    CENTRAL,
    LABEL,
    STACKS,
    Central,
    Found,
    deploy_web,
    ready,
    served,
    start_central,
)

# ====================================================================== 12. the client negotiates


def scenario_client_negotiates(harness: Any, engine: Any) -> Any:
    """The central's own Docker client and Compose meet this engine."""
    found = Found()
    machine, central = start_central(harness, engine)
    try:
        found.check("the central starts and answers", ready(central), central.logs(20))
        version = central.run("docker", "version", "--format", "{{json .}}")
        found.note("docker version, from the central", (version.stdout or version.stderr)[:900])
        try:
            data = json.loads(version.stdout)
        except ValueError:
            data = {}
        server = data.get("Server") or {}
        found.check(
            "the client reaches the engine through the socket",
            version.returncode == 0 and bool(server),
            version.stderr,
        )
        found.check(
            "it is this engine",
            str(server.get("Version", "")).startswith(engine.server)
            or any(
                str(c.get("Version", "")).startswith(engine.server)
                for c in server.get("Components", [])
                if c.get("Name") == "Engine"
            ),
            server,
        )
        compose = central.run("docker", "compose", "version")
        found.check(
            "Compose is the one of the image",
            harness.client_versions[1] in compose.stdout,
            compose.stdout + compose.stderr,
        )
        found.check(
            "the client is the one of the image, and speaks to an engine of another age",
            str((data.get("Client") or {}).get("Version", "")) == harness.client_versions[0],
            data.get("Client"),
        )
        builder = central.run("docker", "buildx", "version")
        found.check(
            "buildx is the one of the image",
            harness.client_versions[2] in builder.stdout,
            builder.stdout + builder.stderr,
        )
        found.note(
            "API in use",
            {
                "client": (data.get("Client") or {}).get("ApiVersion"),
                "engine": server.get("ApiVersion"),
            },
        )
        listed = central.run("docker", "ps", "--format", "{{.Names}}")
        found.check(
            "it lists what runs on the machine, itself included",
            CENTRAL in listed.stdout.split(),
            listed.stderr or listed.stdout,
        )
        # A whole cycle of Compose, from the central's own client: a file at a path both sides see.
        machine.sh(
            "sh", "-c", f"mkdir -p {STACKS}/negotiate && chown 10001:10001 {STACKS}/negotiate"
        )
        compose_file = (
            "services:\n  web:\n    image: " + f"{harness.registry}/w3rd/web:1\n"
            '    ports:\n      - "127.0.0.1:18180:8080"\n    volumes:\n      - data:/data\nvolumes:\n  data: {}\n'
        )
        machine.sh("sh", "-c", f"cat > {STACKS}/negotiate/compose.yaml", input_text=compose_file)
        machine.sh("chown", "10001:10001", f"{STACKS}/negotiate/compose.yaml")
        up = central.run(
            "docker",
            "compose",
            "-p",
            "negotiate",
            "-f",
            f"{STACKS}/negotiate/compose.yaml",
            "up",
            "-d",
            timeout=240,
        )
        found.check(
            "Compose of the image brings a stack up on this engine",
            up.returncode == 0,
            up.stderr[-600:],
        )
        found.check("and it serves", served(machine, 18180, "web v1"), "")
        ls = central.run("docker", "compose", "ls", "--format", "json")
        found.check("it lists the project", "negotiate" in ls.stdout, ls.stdout + ls.stderr)
        down = central.run(
            "docker",
            "compose",
            "-p",
            "negotiate",
            "-f",
            f"{STACKS}/negotiate/compose.yaml",
            "down",
            "-v",
            timeout=120,
        )
        found.check("and takes it down", down.returncode == 0, down.stderr[-400:])
        report = central.json("central", "deploy-here", "check", "--json")
        engine_check = next(
            (c for c in (report or {}).get("checks", []) if c["key"] == "engine"), {}
        )
        found.note("the engine check", engine_check.get("text"))
        found.check(
            "the pre-flight accepts the engine", engine_check.get("status") == "ok", engine_check
        )
    finally:
        central.remove()
    return found


# ====================================================================== 13. the pre-flight


def scenario_precheck(harness: Any, engine: Any) -> Any:
    """Everything green with the folder mounted right; a folder at another path fails with its sentence and is not created."""
    found = Found()
    machine, central = start_central(harness, engine)
    other = None
    try:
        report = central.json("central", "deploy-here", "check", "--json")
        found.note("the report", report)
        found.check("the check answers JSON", isinstance(report, dict), report)
        checks = {c["key"]: c for c in (report or {}).get("checks", [])}
        found.check(
            "every check is there",
            set(checks) == {"setting", "socket", "engine", "group", "apps_folder", "data_folder"},
            sorted(checks),
        )
        found.check(
            "and every one passes",
            all(c["status"] == "ok" for c in checks.values()),
            {k: (v["status"], v["text"]) for k, v in checks.items()},
        )
        found.check("so the central is ready", bool((report or {}).get("ready")), report)
        found.check(
            "the engine's own words are in the sentence",
            engine.server in checks.get("engine", {}).get("text", "")
            or "Docker" in checks.get("engine", {}).get("text", ""),
            checks.get("engine"),
        )

        # The folder is mounted at another path inside than outside.
        machine.sh(
            "sh",
            "-c",
            "mkdir -p /volume1/docker/elsewhere/apps && chown -R 10001:10001 /volume1/docker/elsewhere && rm -rf /seen",
        )
        other = Central(
            machine,
            harness.central_image_name,
            name="noust-other",
            data="/seen",
            host_data="/volume1/docker/elsewhere",
            extra_mounts=(),
            run_service=False,
        )
        other.start()
        done = other.noust("central", "deploy-here", "check", "--json")
        wrong = json.loads(done.stdout) if done.stdout.strip().startswith("{") else {}
        found.note("the report with the folder at another path", wrong)
        by_key = {c["key"]: c for c in wrong.get("checks", [])}
        found.check(
            "a folder at another path fails the apps folder check",
            by_key.get("apps_folder", {}).get("status") == "fail",
            by_key.get("apps_folder"),
        )
        found.check(
            "with the sentence that says the machine does not have it",
            by_key.get("apps_folder", {}).get("code") == "deploy_here.folder.missing_outside",
            by_key.get("apps_folder"),
        )
        found.check("and the command exits 1", done.returncode == 1, done.returncode)
        found.check(
            "and the witness did not create the folder on the machine",
            machine.sh("test", "-e", "/seen").returncode != 0,
            "",
        )
        found.check(
            "the data folder only warns",
            by_key.get("data_folder", {}).get("status") == "warn",
            by_key.get("data_folder"),
        )
        found.check("and the central is not ready", wrong.get("ready") is False, wrong.get("ready"))
        # Why --mount and not -v: Docker's -v makes a folder that is missing.
        machine.sh("rm", "-rf", "/w3rd-made")
        machine.docker(
            "run", "--rm", "-v", "/w3rd-made/x:/x", f"{harness.registry}/w3rd/busybox:1.36", "true"
        )
        found.note(
            "a -v of a folder the machine does not have",
            "it was created"
            if machine.sh("test", "-d", "/w3rd-made").returncode == 0
            else "it was not created",
        )
        machine.sh("rm", "-rf", "/w3rd-made")
        mount = machine.docker(
            "run",
            "--rm",
            "--mount",
            "type=bind,source=/w3rd-made,target=/x,readonly",
            f"{harness.registry}/w3rd/busybox:1.36",
            "true",
        )
        found.note(
            "a --mount of a folder the machine does not have",
            (mount.stderr or mount.stdout).strip()[:300],
        )
        found.check(
            "--mount refuses it, with the words the check looks for",
            "bind source path does not exist" in (mount.stderr + mount.stdout),
            mount.stderr,
        )
        found.check(
            "and does not create it", machine.sh("test", "-e", "/w3rd-made").returncode != 0, ""
        )
        other.remove()

        # The account cannot open the socket.
        denied = Central(
            machine, harness.central_image_name, name="noust-denied", group=False, run_service=False
        )
        denied.start()
        done = denied.noust("central", "deploy-here", "check", "--json")
        bad = json.loads(done.stdout) if done.stdout.strip().startswith("{") else {}
        by_key = {c["key"]: c for c in bad.get("checks", [])}
        found.note("the report without the socket's group", bad)
        found.check(
            "a socket the account may not open fails the group check",
            by_key.get("group", {}).get("status") == "fail",
            by_key.get("group"),
        )
        found.check(
            "with the group's number",
            machine.socket_group()
            in by_key.get("group", {}).get("text", "") + by_key.get("group", {}).get("hint", ""),
            by_key.get("group"),
        )
        found.check(
            "and what needs the socket is not checked yet, never passed",
            by_key.get("engine", {}).get("status") == "skipped"
            and by_key.get("apps_folder", {}).get("status") == "skipped",
            by_key,
        )
        denied.remove()
        off = Central(
            machine,
            harness.central_image_name,
            name="noust-off",
            deploy_here=False,
            run_service=False,
        )
        off.start()
        done = off.noust("central", "deploy-here", "check", "--json")
        unset = json.loads(done.stdout) if done.stdout.strip().startswith("{") else {}
        by_key = {c["key"]: c for c in unset.get("checks", [])}
        found.check(
            "a central that was not told to deploy here says so",
            by_key.get("setting", {}).get("status") == "fail"
            and "NOUST_DEPLOY_HERE"
            in by_key.get("setting", {}).get("hint", "")
            + by_key.get("setting", {}).get("text", ""),
            by_key.get("setting"),
        )
        off.remove()
        nosock = Central(
            machine,
            harness.central_image_name,
            name="noust-nosock",
            socket=False,
            run_service=False,
        )
        nosock.start()
        done = nosock.noust("central", "deploy-here", "check", "--json")
        gone = json.loads(done.stdout) if done.stdout.strip().startswith("{") else {}
        by_key = {c["key"]: c for c in gone.get("checks", [])}
        found.check(
            "a central with no socket says so",
            by_key.get("socket", {}).get("status") == "fail",
            by_key.get("socket"),
        )
        nosock.remove()
    finally:
        central.remove()
        if other is not None:
            other.remove()
        for name in ("noust-denied", "noust-off", "noust-nosock"):
            machine.docker("rm", "-fv", name)
    return found


# ====================================================================== 15. reaching what was deployed


def scenario_reach(harness: Any, engine: Any) -> Any:
    """From an unprivileged central the health gate reaches the service by the Docker network; the central knows which container it is."""
    found = Found()
    machine, central = start_central(harness, engine)
    extra: list[Central] = []
    try:
        found.check("the central starts", ready(central), central.logs(20))
        done = deploy_web(central, harness, "reach.nas.test", "1", 18201)
        found.note("noust create", (done.stdout + done.stderr)[-700:])
        found.check(
            "an application deploys from the unprivileged central, so its health gate reached the service",
            done.returncode == 0,
            (done.stdout + done.stderr)[-600:],
        )
        found.check("and it serves on the machine's port", served(machine, 18201, "web v1"), "")
        app_containers = machine.docker(
            "ps", "-q", "--filter", "label=io.noust.app=reach.nas.test"
        ).stdout.split()
        found.check("its container is there", len(app_containers) == 1, app_containers)
        theirs: dict[str, Any] = {}
        mine: dict[str, Any] = {}
        if app_containers:
            theirs = json.loads(
                machine.docker(
                    "inspect", "--format", "{{json .NetworkSettings.Networks}}", app_containers[0]
                ).stdout
                or "{}"
            )
            mine = json.loads(
                machine.docker(
                    "inspect", "--format", "{{json .NetworkSettings.Networks}}", CENTRAL
                ).stdout
                or "{}"
            )
        shared = sorted(set(theirs) & set(mine) - {"bridge"})
        found.note("networks", {"application": sorted(theirs), "central": sorted(mine)})
        found.check(
            "the central joined the network the application is on",
            bool(shared) or bool(set(theirs) & set(mine)),
            (sorted(theirs), sorted(mine)),
        )
        again = central.noust("update", "reach.nas.test", timeout=300)
        found.check(
            "joining a network it is already on is not a failure: an update works",
            again.returncode == 0,
            (again.stdout + again.stderr)[-500:],
        )
        shown = central.json("status", "reach.nas.test", "--json")
        found.note("status", shown)

        # Which container is it: by the files Docker mounts, and by the host name.
        truth = machine.docker("inspect", "--format", "{{.Id}}", CENTRAL).stdout.strip()
        found.check("the central's own id is known", len(truth) == 64, truth)
        script = (
            "from noust.core.docker.reach import own_container_id; "
            "from noust.core.docker.owners import own_container_ids; "
            "import json; print(json.dumps({'reach': own_container_id(), 'owners': sorted(own_container_ids())}))"
        )
        for label, options in (
            ("by default", {}),
            ("with a private cgroup namespace", {"cgroupns": "private"}),
            ("with the host's cgroup namespace", {"cgroupns": "host"}),
            ("with a host name of its own", {"hostname": "nas-central"}),
        ):
            name = "noust-own"
            other = Central(
                machine, harness.central_image_name, name=name, run_service=False, **options
            )
            other.start()
            extra.append(other)
            real_id = machine.docker("inspect", "--format", "{{.Id}}", name).stdout.strip()
            mode = machine.docker(
                "inspect", "--format", "{{.HostConfig.CgroupnsMode}}", name
            ).stdout.strip()
            said = other.run("python3", "-c", script)
            try:
                ids = json.loads(said.stdout)
            except ValueError:
                ids = {}
            found.note(f"own id {label}", {"cgroupns": mode, "ids": ids})
            found.note(
                f"mountinfo lines of the container's own files {label}",
                other.run("grep", "/containers/", "/proc/self/mountinfo").stdout[:600],
            )
            found.check(
                f"own_container_id finds it {label} (cgroupns {mode})",
                ids.get("reach") == real_id,
                (ids, real_id, said.stderr[-300:]),
            )
            without_name = other.run("env", "-u", "HOSTNAME", "python3", "-c", script)
            try:
                from_files = json.loads(without_name.stdout)
            except ValueError:
                from_files = {}
            found.check(
                f"and so do the files Docker mounts, alone, in the other reader {label}",
                real_id in from_files.get("owners", [])
                or any(i == real_id for i in from_files.get("owners", [])),
                (from_files, machine.engine.data_root),
            )
            other.remove()
        found.skip(
            "cgroup v1",
            "the machine's kernel runs cgroup v2 only; the two readers use /proc/self/mountinfo, which does not "
            "depend on the cgroup version",
        )

        # A central on the machine's own network: 127.0.0.1 is the machine's, nothing is joined.
        host = Central(
            machine,
            harness.central_image_name,
            name="noust-host",
            network="host",
            data="/volume1/docker/noust-host",
            extra_mounts=(),
            run_service=False,
        )
        machine.sh(
            "sh",
            "-c",
            "mkdir -p /volume1/docker/noust-host/apps && chown -R 10001:10001 /volume1/docker/noust-host",
        )
        host.start()
        extra.append(host)
        mode = machine.docker(
            "inspect", "--format", "{{.HostConfig.NetworkMode}}", "noust-host"
        ).stdout.strip()
        found.check("a central on the machine's network is one", mode == "host", mode)
        done = deploy_web(host, harness, "reachhost.nas.test", "1", 18202)
        found.note(
            "noust create from a central on the host's network", (done.stdout + done.stderr)[-500:]
        )
        found.check(
            "it deploys and the probe asks the machine's loopback",
            done.returncode == 0 and served(machine, 18202, "web v1"),
            (done.stdout + done.stderr)[-500:],
        )

        # A service on the host's network, asked from a central that is not.
        machine.sh("sh", "-c", f"mkdir -p {STACKS}/hostnet && chown 10001:10001 {STACKS}/hostnet")
        machine.sh(
            "sh",
            "-c",
            f"cat > {STACKS}/hostnet/compose.yaml && chown 10001:10001 {STACKS}/hostnet/compose.yaml",
            input_text=(
                "services:\n  web:\n    image: " + f"{harness.registry}/w3rd/web:1\n"
                "    network_mode: host\n    command: httpd -f -p 18204 -h /www\n"
            ),
        )
        done = central.noust(
            "create",
            "-d",
            "hostnet.nas.test",
            "-s",
            f"{STACKS}/hostnet",
            "-t",
            "docker-compose",
            "-p",
            "18204",
            "--no-ssl",
            timeout=300,
        )
        said = (done.stdout + done.stderr)[-900:]
        found.note(
            "a service on the host's network, from a central that is not",
            {"exit": done.returncode, "said": said},
        )
        found.check(
            "a service on the host's network is said in words, not a stack trace",
            done.returncode == 0
            or "network_mode: host" in said
            or "host's network" in said
            or "Docker network" in said,
            said,
        )
    finally:
        for other in extra:
            other.remove()
        central.remove()
    return found


# ====================================================================== 16. a port the machine already has


def scenario_busy_port(harness: Any, engine: Any) -> Any:
    """A port the machine already uses is found when Docker refuses it, and the error proposes another."""
    from noust.deployers.helpers import edge

    found = Found()
    machine, central = start_central(harness, engine)
    try:
        found.check("the central starts", ready(central), central.logs(20))
        # Occupied by a process of the machine, and by another container: the two ways Docker says it.
        machine.sh("sh", "-c", "nohup setsid nc -l -p 18301 >/dev/null 2>&1 &")
        machine.docker(
            "run",
            "-d",
            "--name",
            "w3rd-occupant",
            "--label",
            LABEL,
            "--label",
            "w3rd.nas=1",
            "-p",
            "18302:8080",
            f"{harness.registry}/w3rd/web:1",
        )
        for label, port in (("a process of the machine", 18301), ("another container", 18302)):
            raw = machine.docker(
                "run", "--rm", "-p", f"{port}:8080", f"{harness.registry}/w3rd/web:1", "true"
            )
            said = (raw.stderr + raw.stdout).strip()
            found.note(f"Docker's own words for a port held by {label}", said[:400])
            found.check(
                f"the port held by {label} is read out of Docker's words",
                edge.port_taken(said) == port,
                said,
            )
            done = deploy_web(central, harness, f"busy{port}.nas.test", "1", port)
            out = done.stdout + done.stderr
            found.check(
                f"an application on the port held by {label} fails with the port and another to try",
                done.returncode != 0 and f"port {port}" in out and "Try " in out,
                out[-700:],
            )
            listed = central.json("list", "--json")
            names = json.dumps(listed)
            found.check(
                "and nothing half-made is left registered",
                f"busy{port}.nas.test" not in names,
                names[:300],
            )
        left = machine.docker(
            "ps",
            "-aq",
            "--filter",
            "label=io.noust.app=busy18301.nas.test",
            "--filter",
            "label=io.noust.app=busy18302.nas.test",
        ).stdout.split()
        found.check("and no container of it", not left, left)
        proposed = deploy_web(central, harness, "free.nas.test", "1", 0)
        found.note("a port left to Noust", (proposed.stdout + proposed.stderr)[-300:])
    finally:
        machine.sh("sh", "-c", "pkill nc || true")
        machine.docker("rm", "-fv", "w3rd-occupant")
        central.remove()
    return found


# ====================================================================== 18. choosing how it runs


def scenario_how_it_runs(harness: Any, engine: Any) -> Any:
    """On a machine that has only Docker, a source with a Dockerfile deploys as a container and one without says what is missing."""
    found = Found()
    machine, central = start_central(harness, engine)
    try:
        found.check("the central starts", ready(central), central.logs(20))
        with_file = f"{STACKS}/with-dockerfile"
        without = f"{STACKS}/without"
        machine.sh("sh", "-c", f"mkdir -p {with_file} {without}")
        machine.sh(
            "sh",
            "-c",
            f"cat > {with_file}/Dockerfile",
            input_text=(
                f"FROM {harness.registry}/w3rd/busybox:1.36\nEXPOSE 8080\n"
                "RUN mkdir -p /www && echo 'built here' > /www/index.html\n"
                'CMD ["httpd", "-f", "-p", "8080", "-h", "/www"]\n'
            ),
        )
        machine.sh("sh", "-c", f"echo hello > {without}/index.html")
        machine.sh("chown", "-R", "10001:10001", with_file, without)
        asked = central.noust(
            "create",
            "-d",
            "built.nas.test",
            "-s",
            with_file,
            "--run-as",
            "container",
            "-p",
            "18401",
            "--no-ssl",
            timeout=600,
        )
        found.note(
            "noust create --run-as container, with a Dockerfile",
            (asked.stdout + asked.stderr)[-700:],
        )
        found.check(
            "a source with a Dockerfile deploys as a container on this machine",
            asked.returncode == 0 and served(machine, 18401, "built here"),
            (asked.stdout + asked.stderr)[-700:],
        )
        shown = central.json("app", "container", "show", "built.nas.test", "--json")
        found.check(
            "built here, from its Dockerfile",
            isinstance(shown, dict) and shown.get("method") == "dockerfile",
            shown,
        )
        none = central.noust(
            "create",
            "-d",
            "nothing.nas.test",
            "-s",
            without,
            "--run-as",
            "container",
            "-p",
            "18402",
            "--no-ssl",
            timeout=300,
        )
        said = none.stdout + none.stderr
        found.note("noust create --run-as container, with no Dockerfile", said[-700:])
        found.check(
            "a source with no Dockerfile is refused and says what is missing",
            none.returncode != 0 and "Dockerfile" in said,
            said[-700:],
        )
        found.check(
            "and does not offer Railpack, which cannot build here",
            "railpack" not in said.lower()
            or "not available" in said.lower()
            or "cannot" in said.lower(),
            said[-700:],
        )
        native = central.noust(
            "create",
            "-d",
            "native.nas.test",
            "-s",
            without,
            "--run-as",
            "server",
            "-p",
            "18403",
            "--no-ssl",
            timeout=300,
        )
        said = native.stdout + native.stderr
        found.note("noust create --run-as server", said[-500:])
        found.check(
            "running directly on the machine is refused in words that say this machine deploys containers",
            native.returncode != 0 and ("container" in said.lower()),
            said[-500:],
        )
    finally:
        central.remove()
    return found
