#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scenarios of the door to Docker and of the Docker zone, run inside an engine (``docker_run.py``).

Whose each object is, what a container says of itself, what an action may do and what a cleanup removes,
and what the guard of privileges makes of what Compose resolves, each held to a real engine's own output.
"""

from __future__ import annotations

import os
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
    sh,
    up,
    wait_for,
    web_service,
    write_stack,
)

# ====================================================================== 1. the doctor


def _no_compose(run: Run, info: Any) -> None:
    """A Docker with no Compose: the engine is usable, Compose is a limit that says how to get it."""
    from noust.core.docker.engine import diagnose

    report = diagnose(info)
    limits = {text.code: text for text in report.limits}
    run.check("no Compose version is read", info.compose_version == "", info.compose_version)
    run.check("it is not taken for Compose 1", not info.compose_v1, info.compose_v1)
    run.check(
        "the doctor says that Compose is missing", "docker.compose.missing" in limits, list(limits)
    )
    run.check(
        "the containers can still be looked at: the engine's state is ok",
        report.usable and report.state == "ok",
        (report.state, list(limits)),
    )
    run.check("but Noust cannot use this Docker as it is", not report.ok, report.ok)
    run.note("the doctor with no Compose", [text.text for text in report.facts + report.limits])


@scenario("doctor")
def doctor(run: Run) -> None:
    """The engine's doctor says what Docker there is and what does not fit in it."""
    from noust.core.docker import engine as engine_module
    from noust.core.docker.engine import diagnose, read_engine

    real = runner()
    truth_server = docker("version", "--format", "{{.Server.Version}}").stdout.strip()
    truth_api = docker("version", "--format", "{{.Server.APIVersion}}").stdout.strip()
    truth_client_api = docker("version", "--format", "{{.Client.APIVersion}}").stdout.strip()
    truth_client = docker("version", "--format", "{{.Client.Version}}").stdout.strip()
    truth_compose = compose_version_short()
    truth_root = docker("info", "--format", "{{.DockerRootDir}}").stdout.strip()
    truth_driver = docker("info", "--format", "{{.Driver}}").stdout.strip()
    run.note("docker version (json)", docker("version", "--format", "{{json .}}").stdout[:1200])
    run.note("docker compose version", docker("compose", "version").stdout.strip())

    info = read_engine(real)
    run.check("Docker is found and answers", info.installed and info.reachable, info)
    if not truth_compose:
        _no_compose(run, info)
        return
    run.check(
        "the server's version is the one Docker prints",
        info.server_version == truth_server,
        (info.server_version, truth_server),
    )
    run.check(
        "the client's version is the one Docker prints",
        info.client_version == truth_client,
        (info.client_version, truth_client),
    )
    run.check(
        "the server's API is read", info.server_api == truth_api, (info.server_api, truth_api)
    )
    run.check(
        "the client's API is read",
        info.client_api == truth_client_api,
        (info.client_api, truth_client_api),
    )
    lower = truth_api if numbers(truth_api) < numbers(truth_client_api) else truth_client_api
    run.check("the API in use is the lower of the two", info.api == lower, (info.api, lower))
    run.check("the oldest API the daemon accepts is read", bool(info.min_api), info.min_api)
    run.check(
        "Compose's version is read",
        info.compose_version == truth_compose,
        (info.compose_version, truth_compose),
    )
    run.check(
        "Docker's root directory is read", info.root_dir == truth_root, (info.root_dir, truth_root)
    )
    run.check(
        "the storage driver is read",
        info.storage_driver == truth_driver,
        (info.storage_driver, truth_driver),
    )
    run.check(
        "the image store is named",
        info.image_store in ("classic", "containerd"),
        info.image_store,
    )
    run.note("image store", f"{info.image_store} / {info.storage_driver}")
    run.check(
        "the socket is where Docker says",
        info.socket == "/var/run/docker.sock",
        (info.host, info.socket),
    )
    status = os.stat(info.socket) if info.socket else None
    run.check(
        "whose the socket is, is read",
        status is not None and info.socket_mode == f"{status.st_mode & 0o7777:04o}",
        (info.socket_owner, info.socket_group, info.socket_mode),
    )
    run.check("it is not rootless", not info.rootless, info.rootless)

    report = diagnose(info)
    codes = {text.code for text in report.facts}
    limits = {text.code for text in report.limits}
    run.check("the doctor says the version of the engine", "docker.engine.version" in codes, codes)
    run.check("the doctor says the version of Compose", "docker.compose.version" in codes, codes)
    run.check(
        "Noust can use this Docker",
        report.ok and report.usable and report.state == "ok",
        (report.state, limits),
    )
    old_compose = numbers(truth_compose) < engine_module.ADOPTION_COMPOSE
    run.check(
        "a Compose older than 2.20 says it cannot rehearse an adoption, and one that is not does not",
        ("docker.compose.too_old" in limits) == old_compose,
        (truth_compose, limits),
    )
    for text in report.facts + report.limits:
        run.check(
            f"{text.code} reads as a sentence", bool(text.text) and "{" not in text.text, text.text
        )
    run.note("the doctor", [text.text for text in report.facts + report.limits])

    # A daemon that does not answer: the program's own words, and the command to start it.
    nothing = sh(
        "docker",
        "version",
        "--format",
        "{{json .}}",
        env={"DOCKER_HOST": "unix:///nonexistent/docker.sock"},
    )
    run.note(
        "docker version with no daemon",
        {
            "exit": nothing.returncode,
            "stdout": nothing.stdout[:300],
            "stderr": nothing.stderr[:300],
        },
    )
    os.environ["DOCKER_HOST"] = "unix:///nonexistent/docker.sock"
    try:
        gone = read_engine(runner())
        said = diagnose(gone)
    finally:
        os.environ.pop("DOCKER_HOST", None)
    run.check(
        "a daemon that does not answer is unreachable", gone.installed and not gone.reachable, gone
    )
    run.check("and the state says so", said.state == "unreachable" and not said.ok, said.state)
    run.check(
        "with Docker's own first line as the reason",
        bool(gone.error) and "docker" in gone.error.lower(),
        gone.error,
    )
    run.check("and no server version", gone.server_version == "" and gone.api == "", gone)
    run.note("the unreachable sentence", [text.text for text in said.limits])


# ====================================================================== 2. the readings


def _reading_stack() -> str:
    return (
        "services:\n"
        "  web:\n"
        + web_service(
            "    ports:\n"
            '      - "127.0.0.1:18080:8080"\n'
            "    volumes:\n"
            "      - data:/data\n"
            "      - ./conf:/conf:ro\n"
            "    labels:\n"
            f"      {SCENARIO_LABEL}: reading\n"
            f"      noust.test: '1'\n"
        )
        + "  worker:\n"
        f"    image: {BUSYBOX}\n"
        "    command: sleep 3600\n"
        "    networks: [default, inner]\n"
        "    labels:\n"
        f"      {SCENARIO_LABEL}: reading\n"
        "      noust.test: '1'\n"
        "volumes:\n"
        "  data:\n"
        "    labels:\n"
        f"      {SCENARIO_LABEL}: reading\n"
        "      noust.test: '1'\n"
        "networks:\n"
        "  inner:\n"
        "    internal: true\n"
        "    labels:\n"
        f"      {SCENARIO_LABEL}: reading\n"
        "      noust.test: '1'\n"
    )


def _both_networks(run: Run, readers: Any, real: Any, file: Path) -> None:
    """
    Bring the reading stack up again while Docker leaves its worker on one of the two networks it asked for.

    Docker 24.0.2 with Compose 2.19.1 does it now and then, whatever is read (2 times in 12 on a fresh ``up``, and the
    container is still on one network thirty seconds later; not seen on 20.10 or 29): the engine's own fault, which
    is no reason for the reader to be judged on a container that was never put where it was asked to be.
    """

    def attached() -> bool:
        return any(
            {"plain_default", "plain_inner"} <= set(c.networks)
            for c in readers.list_containers(real)
            if c.project == "plain" and c.service == "worker" and not c.oneoff
        )

    for attempt in (1, 2, 3, 4):
        if wait_for(attached, 6):
            break
        run.note(
            f"the engine left the worker on one of its two networks (attempt {attempt}); the stack is brought up again",
            [c.networks for c in readers.list_containers(real) if c.service == "worker"],
        )
        docker("compose", "-p", "plain", "-f", str(file), "down", timeout=120)
        up("plain", file)


@scenario("reading")
def reading(run: Run) -> None:
    """The listings of containers, images, volumes and networks are read the way Docker prints them."""
    from noust.core.docker import reading as readers

    reset("reading")
    stack = WORK / "reading" / "plain"
    (stack / "conf").mkdir(parents=True, exist_ok=True)
    file = write_stack(stack, _reading_stack())
    real = runner()
    try:
        up("plain", file)
        _both_networks(run, readers, real, file)
        healthy = wait_for(
            lambda: any(
                c.health == "healthy" for c in readers.list_containers(real) if c.service == "web"
            ),
            60,
        )
        docker(
            "run",
            "-d",
            "--name",
            "w3rd-reading-trap",
            "--label",
            f"{SCENARIO_LABEL}=reading",
            "--label",
            LABEL,
            "--label",
            "trap=x,com.docker.compose.project=hijack",
            "--label",
            "com.docker.compose.project.working_dir.x=/y",
            BUSYBOX,
            "sleep",
            "3600",
        )
        docker(
            "compose",
            "-p",
            "plain",
            "-f",
            str(file),
            "run",
            "-d",
            "--no-deps",
            "--name",
            "w3rd-reading-oneoff",
            "worker",
            "sleep",
            "30",
        )

        raw = docker("ps", "--all", "--no-trunc", "--format", readers.CONTAINER_FORMAT).stdout
        run.note("a line of docker ps", raw.splitlines()[0] if raw.splitlines() else "")
        listed = readers.list_containers(real)
        by_service = {c.service: c for c in listed if c.project == "plain" and not c.oneoff}
        web, worker = by_service.get("web"), by_service.get("worker")
        run.check(
            "the containers of the stack are listed with their service",
            web is not None and worker is not None,
            [(c.name, c.project, c.service) for c in listed],
        )
        if web is not None and worker is not None:
            run.check(
                "a container's name has no slash",
                not web.name.startswith("/") and "," not in web.name,
                web.name,
            )
            run.check(
                "it runs",
                web.state == "running" and worker.state == "running",
                (web.state, worker.state),
            )
            run.check(
                "the health check is read out of its status",
                healthy and web.health == "healthy",
                (web.status, web.health),
            )
            run.check(
                "a container with no health check has none", worker.health == "", worker.health
            )
            run.check("the image is as it was named", web.image == BUSYBOX, web.image)
            run.check(
                "what it publishes is said", "18080" in web.ports and "8080" in web.ports, web.ports
            )
            run.check("the project, from its label", web.project == "plain", web.project)
            run.check(
                "the working directory, from its label",
                web.working_dir == str(stack),
                web.working_dir,
            )
            run.check(
                "the compose files, from their label",
                web.config_files == (str(file),),
                web.config_files,
            )
            run.check("a named volume is among its mounts", "plain_data" in web.mounts, web.mounts)
            run.note("the mounts column of a container that binds a folder", list(web.mounts))
            run.check(
                "its networks",
                "plain_default" in web.networks and "plain_default" in worker.networks,
                (web.networks, worker.networks),
            )
            run.check(
                "the worker is on the internal one too",
                "plain_inner" in worker.networks,
                worker.networks,
            )
            run.check("it was created at a time", bool(web.created), web.created)
        oneoff = next((c for c in listed if c.name == "w3rd-reading-oneoff"), None)
        run.check(
            "a one-off container is known as one", oneoff is not None and oneoff.oneoff, oneoff
        )
        trap = next((c for c in listed if c.name == "w3rd-reading-trap"), None)
        run.check(
            "a label whose value holds another label's text is one label, not two",
            trap is not None and trap.project == "" and trap.working_dir == "",
            trap,
        )

        images = readers.list_images(real)
        mine = [image for image in images if BUSYBOX in image.tags]
        run.check("an image is listed with its name", len(mine) == 1, [i.tags for i in images])
        if mine:
            run.check(
                "with a size and a date",
                bool(mine[0].size_bytes) and bool(mine[0].created),
                mine[0],
            )
            run.check(
                "and its full id",
                mine[0].id.startswith("sha256:") or len(mine[0].id) >= 12,
                mine[0].id,
            )
        raw_images = docker("image", "ls", "--no-trunc", "--format", "{{json .}}").stdout
        run.note(
            "a line of docker image ls",
            raw_images.splitlines()[0] if raw_images.splitlines() else "",
        )

        volumes = readers.list_volumes(real)
        data = next((v for v in volumes if v.name == "plain_data"), None)
        run.check("a volume is listed", data is not None, [v.name for v in volumes])
        if data is not None:
            root = docker("info", "--format", "{{.DockerRootDir}}").stdout.strip()
            run.check(
                "with its mountpoint under Docker's root",
                data.mountpoint.startswith(root),
                (data.mountpoint, root),
            )
            run.check(
                "its project and its key, from its labels",
                data.project == "plain" and data.key == "data",
                data,
            )
            run.check("local, and said so", data.driver == "local" and data.scope == "local", data)

        networks = readers.list_networks(real)
        inner = next((n for n in networks if n.name == "plain_inner"), None)
        default = next((n for n in networks if n.name == "plain_default"), None)
        run.check(
            "an internal network is known as one", inner is not None and inner.internal, inner
        )
        run.check(
            "a network of the stack carries its project",
            default is not None and default.project == "plain",
            default,
        )
        builtin = {n.name for n in networks if n.name in ("bridge", "host", "none")}
        run.check(
            "Docker's own networks are listed", builtin == {"bridge", "host", "none"}, builtin
        )
        run.check("with a date", bool(default and default.created), default)

        usage = readers.disk_usage(real)
        run.note(
            "docker system df (json)", docker("system", "df", "--format", "{{json .}}").stdout[:900]
        )
        run.check(
            "the disk Docker uses is read",
            usage is not None and {"Images", "Containers", "Local Volumes"} <= set(usage),
            usage,
        )
        if usage:
            run.check(
                "with sizes and counts",
                (usage["Images"]["size"] or 0) > 0 and (usage["Images"]["count"] or 0) >= 1,
                usage["Images"],
            )
        verbose = docker("system", "df", "-v", "--format", "{{json .}}")
        run.note("docker system df -v (json)", (verbose.stdout or verbose.stderr)[:700])
        sizes = readers.volume_sizes(real)
        run.check(
            "a volume's size is read out of docker system df -v",
            "plain_data" in sizes,
            (sizes, (verbose.stdout or verbose.stderr)[:300]),
        )
    finally:
        docker("rm", "-fv", "w3rd-reading-trap", "w3rd-reading-oneoff")
        project_down("plain", file)
        reset("reading")


# ====================================================================== 3. whose each thing is


@scenario("owners")
def owners(run: Run) -> None:
    """Whose each object is comes out of what the store registered, in the real labels Compose writes."""
    from noust.core.db_instance_store import DbInstanceStore
    from noust.core.docker import names
    from noust.core.docker.gate import DockerGate
    from noust.core.docker.models import OwnerKind
    from noust.core.store import App

    reset("owners")
    store = make_store("owners")
    base = WORK / "owners"
    shop = base / "apps" / "shop-example-com"
    blog = base / "apps" / "blog-example-com"
    foreign = base / "stacks" / "foreign"
    evil = base / "apps" / "shop-example-com-evil"
    engine_name = "pg16"
    files: list[tuple[str, Path]] = []
    try:

        def stack(folder: Path, volumes: bool = True) -> Path:
            return write_stack(
                folder,
                "services:\n  web:\n"
                f"    image: {BUSYBOX}\n    command: sleep 3600\n"
                + ("    volumes: [data:/data]\n" if volumes else "")
                + f"    labels:\n      {SCENARIO_LABEL}: owners\n      noust.test: '1'\n"
                + (
                    f"volumes:\n  data:\n    labels:\n      {SCENARIO_LABEL}: owners\n"
                    if volumes
                    else ""
                ),
            )

        # A container application: Noust writes the compose file in a release's directory.
        container_file = stack(shop / "current")
        project = "shop-example-com"
        files.append((project, container_file))
        up(project, container_file)
        store.create_app(
            App(
                domain="shop.example.com",
                app_type="container",
                app_path=str(shop),
                layout="releases",
                port=8081,
            )
        )
        # A stack of Noust's own (type docker-compose), in place.
        stack_file = stack(blog)
        stack_project = "blog-example-com"
        files.append((stack_project, stack_file))
        up(stack_project, stack_file)
        store.create_app(
            App(
                domain="blog.example.com",
                app_type="docker-compose",
                app_path=str(blog),
                layout="inplace",
                port=8082,
            )
        )
        # A stack nobody of Noust made.
        foreign_file = stack(foreign)
        files.append(("foreign", foreign_file))
        up("foreign", foreign_file)
        # A container that only wears the label of an application, from a directory that is not its own.
        evil_file = stack(evil, volumes=False)
        files.append(("evil", evil_file))
        up("evil", evil_file)
        # A database engine Noust runs itself: a row, a container with the name the row gives it, its volume.
        digest = "sha256:" + "ab" * 32
        DbInstanceStore(store).create(
            name=engine_name,
            flavour="postgresql",
            version="16",
            image="postgres",
            digest=digest,
            host_port=15432,
            memory_limit_mb=256,
            created_by="harness",
        )
        engine_container = names.instance_container(engine_name)
        docker(
            "volume",
            "create",
            "--label",
            LABEL,
            "--label",
            f"{SCENARIO_LABEL}=owners",
            names.instance_data_volume(engine_name),
        )
        must(
            docker(
                "run",
                "-d",
                "--name",
                engine_container,
                "--label",
                LABEL,
                "--label",
                f"{SCENARIO_LABEL}=owners",
                "--label",
                f"io.noust.db-instance={engine_name}",
                "-v",
                f"{names.instance_data_volume(engine_name)}:/var/lib/data",
                BUSYBOX,
                "sleep",
                "3600",
            ),
            "the engine's container",
        )
        # And a container that claims to be the engine's, which no row names.
        docker(
            "run",
            "-d",
            "--name",
            "w3rd-owners-claim",
            "--label",
            LABEL,
            "--label",
            f"{SCENARIO_LABEL}=owners",
            "--label",
            f"io.noust.db-instance={engine_name}",
            BUSYBOX,
            "sleep",
            "3600",
        )
        # A copy's helper.
        docker(
            "run",
            "-d",
            "--name",
            "w3rd-owners-helper",
            "--label",
            LABEL,
            "--label",
            f"{SCENARIO_LABEL}=owners",
            "--label",
            "io.noust.helper=volume-copy",
            BUSYBOX,
            "sleep",
            "3600",
        )

        gate = DockerGate(runner=runner(), store=store)
        found = gate.containers().items

        def owner_of(service_project: str, working: str) -> Any:
            return next(
                (
                    c.owner
                    for c in found
                    if c.project == service_project and c.working_dir.startswith(working)
                ),
                None,
            )

        got = owner_of(project, str(shop))
        run.check(
            "the container of a container application is that application's",
            got is not None and got.kind is OwnerKind.APP and got.subject == "shop.example.com",
            got,
        )
        got = owner_of(stack_project, str(blog))
        run.check(
            "the container of a stack of Noust's is that application's",
            got is not None and got.kind is OwnerKind.APP and got.subject == "blog.example.com",
            got,
        )
        got = owner_of("foreign", str(foreign))
        run.check(
            "a stack Noust did not make is nobody's",
            got is not None and got.kind is OwnerKind.NOBODY,
            got,
        )
        got = owner_of("evil", str(evil))
        run.check(
            "a directory that only starts with an application's name is not that application's",
            got is not None and got.kind is OwnerKind.NOBODY,
            got,
        )
        by_name = {c.name: c for c in found}
        got = by_name[engine_container].owner
        run.check(
            "the container a database engine's row names is that engine's",
            got.kind is OwnerKind.DATABASE and got.subject == engine_name,
            got,
        )
        got = by_name["w3rd-owners-claim"].owner
        run.check(
            "a container that claims an engine no row names is nobody's",
            got.kind is OwnerKind.NOBODY,
            got,
        )
        got = by_name["w3rd-owners-helper"].owner
        run.check("what Noust started for a copy is Noust's", got.kind is OwnerKind.NOUST, got)

        volumes = {v.name: v for v in gate.volumes().items}
        for label, name, kind, subject in (
            (
                "a volume Compose made for an application",
                f"{project}_data",
                OwnerKind.APP,
                "shop.example.com",
            ),
            (
                "a volume Compose made for a stack of Noust's",
                f"{stack_project}_data",
                OwnerKind.APP,
                "blog.example.com",
            ),
            (
                "a volume Compose made for a stack nobody of Noust made",
                "foreign_data",
                OwnerKind.NOBODY,
                "",
            ),
            (
                "the volume of a database engine",
                names.instance_data_volume(engine_name),
                OwnerKind.DATABASE,
                engine_name,
            ),
        ):
            volume = volumes.get(name)
            run.check(
                f"{label} has the owner the store says",
                volume is not None
                and volume.owner.kind is kind
                and (not subject or volume.owner.subject == subject),
                volume and (volume.owner, volume.project),
            )
        networks = {n.name: n for n in gate.networks().items}
        for label, name, kind in (
            ("the network of an application", f"{project}_default", OwnerKind.APP),
            ("the network of a stack nobody of Noust made", "foreign_default", OwnerKind.NOBODY),
            ("a network Docker made", "bridge", OwnerKind.NOBODY),
        ):
            network = networks.get(name)
            run.check(
                f"{label} is whose it should be",
                network is not None and network.owner.kind is kind,
                network and network.owner,
            )
        run.check(
            "Docker's own network is built in", networks["bridge"].builtin, networks["bridge"]
        )
        stacks = {s.name: s for s in gate.projects().items}
        run.check(
            "a stack is its containers' owner",
            stacks[project].owner.kind is OwnerKind.APP
            and stacks["foreign"].owner.kind is OwnerKind.NOBODY,
            {k: v.owner for k, v in stacks.items()},
        )
        run.check(
            "and says where it runs from and which file",
            stacks["foreign"].working_dir == str(foreign)
            and stacks["foreign"].config_files == (str(foreign_file),),
            stacks["foreign"],
        )
    finally:
        for project_name, file in files:
            project_down(project_name, file)
        reset("owners")
