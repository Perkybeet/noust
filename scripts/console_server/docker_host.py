# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker the console's Docker zone looks at: an Ubuntu server's engine with a few stacks on it.

The zone reads Docker through the one door every module uses, so what answers here is
:class:`tests.support.docker_machine.DockerMachine`, the same simulated machine the tests build,
filled with what the zone has to draw: two applications of Noust with their containers (one of them
a container that keeps falling because memory runs out), a stack nobody of Noust made with a job that
ended well, a container somebody stopped, one Noust started for itself, images with no name, volumes
that nothing uses and the disk that comes with it all. An application is the one the store already
has, found by its directory, because a container's owner is decided from what the store registered
and never from what the container says of itself. Nothing is executed.

What the pages of the zone that change something need is seeded with it (:func:`seed_docker_state`): an
application of Noust whose Compose document Noust keeps, with the history of its versions (so the page of a stack
of Noust has its editor and its history), a folder that really exists for the stack nobody of Noust made (so
bringing it to Noust is not refused for lack of a folder), and what the last look at the registries found for the
images in use, in each of the three states (up to date, a newer one, not looked at).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.support.docker_machine import DockerMachine

    from .sandbox import Sandbox

#: The machine the runner consults for every ``docker`` command, once :func:`model_docker_host` has built it.
_DOCKER: DockerMachine | None = None

#: A compose file's name in the folder of an application and of a stack nobody of Noust made.
_COMPOSE = "compose.yaml"

#: The application whose Compose document Noust keeps, with the history of its versions.
STACK_DOMAIN = "stack.example.net"

#: What the document of that application said in each version Noust kept: the file as it was found, an edit
#: that put a newer web server in and a second one that put its port and a limit in.
STACK_DOCUMENTS = (
    'services:\n  web:\n    image: nginx:1.26\n    ports:\n      - "127.0.0.1:8085:80"\n'
    "  db:\n    image: postgres:16\n    volumes:\n      - db-data:/var/lib/postgresql/data\n"
    "volumes:\n  db-data: {}\n",
    'services:\n  web:\n    image: nginx:1.27\n    ports:\n      - "127.0.0.1:8085:80"\n'
    "  db:\n    image: postgres:16\n    volumes:\n      - db-data:/var/lib/postgresql/data\n"
    "volumes:\n  db-data: {}\n",
    "services:\n  web:\n    image: nginx:1.27\n    restart: unless-stopped\n    mem_limit: 256m\n"
    '    ports:\n      - "127.0.0.1:8085:80"\n'
    "  db:\n    image: postgres:16\n    restart: unless-stopped\n    volumes:\n"
    "      - db-data:/var/lib/postgresql/data\nvolumes:\n  db-data: {}\n",
)

#: Who saved each version after the first and what they said it was for.
_STACK_HISTORY = (
    ("found", None, "The document the stack was brought with"),
    ("edit", "marta", "A newer web server"),
    ("edit", "marta", "Restart on its own and a memory limit"),
)

_LOOP_LOG = (
    "2026-11-12T11:40:21.100000000Z [api] listening on :3000\n"
    "2026-11-12T11:40:21.900000000Z [api] connected to redis://redis:6379\n"
    "2026-11-12T11:40:26.300000000Z [api] warming the product cache (48212 rows)\n"
    "2026-11-12T11:40:43.000000000Z <--- Last few GCs --->\n"
    "2026-11-12T11:40:43.400000000Z FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory\n"
)

_WEB_LOG = "".join(
    f"2026-11-12T11:{minute:02d}:{second:02d}.000000000Z GET /{path} 200 {3 + (second % 17)}ms\n"
    for minute, second, path in (
        (30, 1, "health"),
        (30, 14, "api/products"),
        (31, 2, "api/cart"),
        (31, 40, "static/app.js"),
        (32, 7, "api/orders"),
    )
)


def seed_docker_state(sandbox: Sandbox) -> str:
    """
    Seed what the Docker zone's pages that change something work with.

    Three things, each what a page of the zone was drawn without. An application of Docker Compose whose document
    Noust keeps, with three versions of it in the history and the stack it is the document of (its page has an
    editor and a history to go back through). A folder that really exists, with a compose file in it, for the
    stack nobody of Noust made (bringing it to Noust was refused with ``no_folder``, because the folder it ran
    from was a name and not a place). And what the last look at the registries found for the images the machine
    runs: one up to date, some with a newer version (a patch, a minor, a major) and the rest never looked at.

    Args:
        sandbox: The sandbox, whose directories stand in for the machine's.

    Returns:
        The folder of the stack nobody of Noust made, to give :func:`model_docker_host`.
    """
    from noust.core import timeutil
    from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
    from noust.core.docker_stack_versions import StackVersionStore
    from noust.core.recipe_sources import recipe_source_dir
    from noust.core.store import App, get_store
    from noust.recipes.deploy import COMPOSE_FILE

    store = get_store(sandbox.store_file)
    app_name = STACK_DOMAIN.replace(".", "-")
    document = recipe_source_dir(app_name, store)
    document.mkdir(parents=True, exist_ok=True)
    (document / COMPOSE_FILE).write_text(STACK_DOCUMENTS[-1], encoding="utf-8")
    root = sandbox.var / "www" / "apps" / app_name
    root.mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text("DB_PASSWORD=change-me-in-the-console\n", encoding="utf-8")
    if store.get_app(STACK_DOMAIN) is None:
        store.create_app(
            App(
                domain=STACK_DOMAIN,
                app_type="docker-compose",
                source=str(document),
                app_path=str(root),
                port=8085,
                layout="inplace",
            )
        )
        history = StackVersionStore(store)
        for text, (source, actor, reason) in zip(STACK_DOCUMENTS, _STACK_HISTORY, strict=True):
            history.add(STACK_DOMAIN, text, source=source, actor=actor, reason=reason)

    folder = sandbox.var / "srv" / "nextcloud"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / _COMPOSE).write_text(
        "services:\n  app:\n    image: nextcloud:29-apache\n    volumes:\n"
        "      - nextcloud_data:/var/www/html\n  db:\n    image: mariadb:11\n"
        "  redis:\n    image: redis:7\nvolumes:\n  nextcloud_data: {}\n",
        encoding="utf-8",
    )

    looked = timeutil.utc_now_iso()
    versions = ImageVersionStore(store)

    def found(
        reference: str,
        state: str,
        change: str = "",
        current: str | None = None,
        latest: str | None = None,
    ) -> None:
        versions.save(
            VersionRecord(
                reference=reference,
                state=state,
                local_digest="sha256:" + "1" * 64,
                remote_digest="sha256:" + ("2" if state == "newer" else "1") * 64,
                current_version=current,
                latest_version=latest,
                latest_created=looked,
                change=change,
                floating=False,
                method="imagetools",
                detail="",
                checked_at=looked,
            )
        )

    # Up to date, a patch and a minor of what an application of Noust runs (updated behind its health gate),
    # a major of a database (a copy that was proven to restore comes first), and a patch of what nobody of
    # Noust made. What is not listed here (redis, adminer) was never looked at.
    found("mariadb:11", "current", current="11.4.3", latest="11.4.3")
    found("nginx:1.27", "newer", "minor", current="1.27.3", latest="1.28.0")
    found("postgres:16", "newer", "major", current="16.6", latest="17.2")
    found("nextcloud:29-apache", "newer", "patch", current="29.0.9", latest="29.0.11")
    return str(folder)


def model_docker_host(apps: Mapping[str, str], nextcloud: str = "/srv/nextcloud") -> DockerMachine:
    """
    Build the engine the Docker zone reads, over the applications the store has.

    Args:
        apps: Each application's domain and its directory, as the store records them. The containers
            of an application run from its directory, which is how Noust knows they are its own.
        nextcloud: Where the stack nobody of Noust made runs from; a folder that exists when
            :func:`seed_docker_state` made it, so that bringing the stack to Noust has a folder to register.

    Returns:
        The machine, which :func:`answer` consults for every ``docker`` command.
    """
    # Here and not at the top: the repository reaches the path when the sandbox is prepared, after this module
    # has loaded, and `python scripts/console_server.py` has to start from anywhere.
    from tests.support.docker_machine import DockerMachine

    global _DOCKER
    folder_of_nextcloud = str(Path(nextcloud))
    machine = DockerMachine()
    machine.engine["version"] = "29.1.3"
    machine.engine["compose"] = "2.40.3"

    def project_of(domain: str) -> str:
        return domain.replace(".", "-")

    # The three applications of the seed that run as stacks: a shop with a worker and a database, a plain one, and one whose
    # service keeps falling because its memory runs out.
    shop = "shop.example.net" if "shop.example.net" in apps else None
    blog = "example.net" if "example.net" in apps else None
    api = "tools.example.net" if "tools.example.net" in apps else None
    stack = STACK_DOMAIN if STACK_DOMAIN in apps else None

    if shop is not None:
        project = project_of(shop)
        directory = apps[shop]
        base = {
            "project": project,
            "working_dir": directory,
            "config_files": f"{directory}/{_COMPOSE}",
            "networks": f"{project}_default",
        }
        machine.image("a", f"{project}-web", "release-41", "412MB")
        machine.image("b", f"{project}-worker", "release-41", "398MB")
        machine.image("c", "postgres", "16", "431MB")
        machine.box(
            f"{project}-web-1",
            image=f"{project}-web:release-41",
            service="web",
            ports="127.0.0.1:3004->3000/tcp",
            mounts=f"{project}_uploads",
            log=_WEB_LOG,
            memory=805306368,
            policy="unless-stopped",
            **base,
        )
        machine.box(
            f"{project}-worker-1",
            image=f"{project}-worker:release-41",
            service="worker",
            policy="unless-stopped",
            log=_WEB_LOG,
            **base,
        )
        machine.box(
            f"{project}-db-1",
            image="postgres:16",
            service="db",
            mounts=f"{project}_db-data",
            policy="unless-stopped",
            **base,
        )
        machine.network(f"{project}_default", project=project)
        machine.volume(f"{project}_uploads", size="1.1GB", project=project, volume="uploads")
        machine.volume(f"{project}_db-data", size="2.3GB", project=project, volume="db-data")

    if blog is not None:
        project = project_of(blog)
        directory = apps[blog]
        machine.image("d", f"{project}-app", "release-12", "296MB")
        machine.box(
            f"{project}-app-1",
            image=f"{project}-app:release-12",
            service="app",
            project=project,
            working_dir=directory,
            config_files=f"{directory}/{_COMPOSE}",
            networks=f"{project}_default",
            policy="unless-stopped",
            log=_WEB_LOG,
        )
        machine.network(f"{project}_default", project=project)

    if api is not None:
        project = project_of(api)
        directory = apps[api]
        base = {
            "project": project,
            "working_dir": directory,
            "config_files": f"{directory}/{_COMPOSE}",
            "networks": f"{project}_default",
        }
        machine.image("e", f"{project}-api", "release-8", "355MB")
        machine.image("f", "redis", "7-alpine", "41MB")
        machine.box(
            f"{project}-api-1",
            image=f"{project}-api:release-8",
            service="api",
            state="restarting",
            status="Restarting (137) 8 seconds ago",
            exit_code=137,
            oom=True,
            memory=268435456,
            restart_count=14,
            restarts_recent=6,
            policy="unless-stopped",
            log=_LOOP_LOG,
            stats={
                "CPUPerc": "0.00%",
                "MemUsage": "0B / 256MiB",
                "MemPerc": "0.00%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
                "PIDs": "0",
            },
            **base,
        )
        machine.box(
            f"{project}-redis-1",
            image="redis:7-alpine",
            service="redis",
            policy="unless-stopped",
            **base,
        )
        machine.network(f"{project}_default", project=project)

    if stack is not None:
        # The application whose document Noust keeps: a web server and a database.
        project = project_of(stack)
        directory = apps[stack]
        base = {
            "project": project,
            "working_dir": directory,
            "config_files": f"{directory}/{_COMPOSE}",
            "networks": f"{project}_default",
        }
        machine.image("8", "nginx", "1.27", "192MB")
        machine.image("9", "postgres", "16", "431MB")
        machine.box(
            f"{project}-web-1",
            image="nginx:1.27",
            service="web",
            ports="127.0.0.1:8085->80/tcp",
            policy="unless-stopped",
            memory=268435456,
            log=_WEB_LOG,
            **base,
        )
        machine.box(
            f"{project}-db-1",
            image="postgres:16",
            service="db",
            mounts=f"{project}_db-data",
            policy="unless-stopped",
            **base,
        )
        machine.network(f"{project}_default", project=project)
        machine.volume(f"{project}_db-data", size="640MB", project=project, volume="db-data")

    # A stack somebody started with docker compose, in a folder of its own: nobody of Noust made it.
    nextcloud = {
        "project": "nextcloud",
        "working_dir": folder_of_nextcloud,
        "config_files": f"{folder_of_nextcloud}/{_COMPOSE}",
        "networks": "nextcloud_default",
    }
    machine.image("1", "nextcloud", "29-apache", "1.3GB")
    machine.image("2", "mariadb", "11", "401MB")
    machine.image("3", "redis", "7", "117MB")
    machine.box(
        "nextcloud-app-1",
        image="nextcloud:29-apache",
        service="app",
        mounts="nextcloud_data",
        policy="unless-stopped",
        **nextcloud,
    )
    machine.box(
        "nextcloud-db-1",
        image="mariadb:11",
        service="db",
        mounts="nextcloud_db",
        policy="unless-stopped",
        **nextcloud,
    )
    machine.box(
        "nextcloud-redis-1", image="redis:7", service="redis", policy="unless-stopped", **nextcloud
    )
    machine.box(
        "nextcloud-setup-1",
        image="nextcloud:29-apache",
        service="setup",
        oneoff=True,
        state="exited",
        status="Exited (0) 3 days ago",
        exit_code=0,
        finished_at="2026-11-09T10:00:00.000000000Z",
        **nextcloud,
    )
    machine.network("nextcloud_default", project="nextcloud")
    machine.volume("nextcloud_data", size="9.1GB", project="nextcloud", volume="data")
    machine.volume("nextcloud_db", size="1.4GB", project="nextcloud", volume="db")

    # Loose containers: one somebody stopped that its policy says should run, one a test left behind, and Noust's own helper.
    machine.image("4", "adminer", "4", "98MB")
    machine.box(
        "adminer",
        image="adminer:4",
        state="exited",
        status="Exited (143) 3 days ago",
        exit_code=143,
        policy="unless-stopped",
        finished_at="2026-11-09T08:12:41.000000000Z",
    )
    machine.box(
        "old-test",
        image="adminer:4",
        state="exited",
        status="Exited (0) 41 days ago",
        exit_code=0,
        finished_at="2026-10-02T09:00:00.000000000Z",
    )
    machine.image("5", "moby/buildkit", "buildx-stable-1", "187MB")
    machine.box(
        "noust-buildkit",
        image="moby/buildkit:buildx-stable-1",
        helper="buildkit",
        policy="unless-stopped",
    )

    # What nothing uses: two images that lost their name, a volume of a wiki that no longer runs, one Docker made for a container.
    machine.image("6", "<none>", "<none>", "380MB")
    machine.image("7", "<none>", "<none>", "260MB")
    machine.volume("wiki_data", size="3.4GB")
    machine.volume("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", size="12MB")
    machine.network("bridge", driver="bridge")
    machine.network("host", driver="host")
    machine.network("none", driver="null")
    machine.df = [
        {
            "Type": "Images",
            "TotalCount": str(len(machine.images)),
            "Size": "9.5GB",
            "Reclaimable": "1.8GB (18%)",
        },
        {
            "Type": "Containers",
            "TotalCount": str(len(machine.boxes)),
            "Size": "120MB",
            "Reclaimable": "40MB (33%)",
        },
        {
            "Type": "Local Volumes",
            "TotalCount": str(len(machine.volumes)),
            "Size": "17.3GB",
            "Reclaimable": "3.4GB (19%)",
        },
        {"Type": "Build Cache", "TotalCount": "0", "Size": "1.2GB", "Reclaimable": "1.2GB"},
    ]
    # The Software page and the machine's own `which` agree that Docker is installed.
    from . import server_host

    if server_host._SERVER_HOST is not None:
        packages = server_host._SERVER_HOST.packages.versions
        packages.setdefault("docker.io", "24.0.7-0ubuntu4.1")
        packages.setdefault("docker-compose-v2", "2.24.6-0ubuntu1~24.04.1")
    _DOCKER = machine
    return machine


def answer(args: Sequence[str]) -> tuple[int, str, str] | None:
    """
    Answer one ``docker`` command the way the engine does.

    Args:
        args: The command.

    Returns:
        ``(exit code, stdout, stderr)``, or None when no engine was modelled (the sandbox has no Docker)
        or the command is not one.
    """
    if _DOCKER is None:
        return None
    return _DOCKER.answer(args)
