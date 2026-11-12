# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shapes the Docker zone's API answers with.

Every object the gate resolves comes with its owner and with what it may be offered
(:class:`DockerCanOut`), and every route that reads the machine says whether Docker is there to be read
(``available``) and, when it is not, what the doctor says (``engine``), so a page of the console can draw
"this machine has no Docker" and how to install it instead of an error. The names are prefixed so that
the schema has no two models of one name.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from noust.core.docker import cleanup as cleanup_module
from noust.core.docker.engine import EngineReport
from noust.core.docker.models import ComposeProject, Container, Image, Network, Owner, Volume
from noust.web.api.deps import CodedText
from noust.web.pydantic_compat import bounded_list


class DockerOwnerOut(BaseModel):
    """
    Whose an object is.

    Attributes:
        kind: ``app`` (an application of Noust: its stack, or its container), ``database`` (a database
            engine Noust runs in a container), ``noust`` (Noust itself) or ``nobody`` (Noust did not
            make it: the instance's).
        subject: The application's domain, the instance's name, ``central`` or ``helper`` for Noust's own;
            empty for nobody.
        project: The Compose project it is part of, when it is part of one.
    """

    kind: str
    subject: str = ""
    project: str = ""


class DockerCanOut(BaseModel):
    """
    What may be done to an object by whoever asks, decided by the judgement an action goes through.

    Attributes:
        start: A container may be started.
        stop: A container may be stopped.
        restart: A container may be restarted.
        remove: The object may be removed.
        console: A shell may be opened in this container: it is not Noust's own, and the credential
            holds the permission a shell in it needs (``apps.terminal`` for an application's,
            ``server.terminal`` otherwise).
        via: When an application, an engine or Noust changes it, the sentence that says where it is
            changed (a command that exists).
        blocked: Why nothing is offered: ``owner``, ``builtin`` (Docker makes it itself), ``in_use``
            (something still uses it), ``permission`` (this credential may not change Docker); empty
            when it is offered.
    """

    start: bool = False
    stop: bool = False
    restart: bool = False
    remove: bool = False
    console: bool = False
    via: str = ""
    blocked: str = ""


class DockerSentenceOut(CodedText):
    """
    One sentence of the engine's doctor.

    Attributes:
        text: It in English.
        hint: How to fix it in English, when it has a fix.
    """

    text: str
    hint: str | None = None


class DockerEngineInfoOut(BaseModel):
    """
    What Docker says of itself.

    Attributes:
        installed: There is a ``docker`` program.
        reachable: Its daemon answered.
        client_version: The program's version.
        server_version: The daemon's version; empty when it did not answer.
        client_api: The newest API the program speaks.
        server_api: The newest API the daemon speaks.
        min_api: The oldest API the daemon accepts.
        api: The API in use: the lower of the two sides.
        compose_version: The Compose plugin's version; empty without it.
        compose_v1: There is a ``docker-compose`` program, which is Compose 1.
        image_store: ``containerd`` or ``classic``.
        storage_driver: The driver that keeps the images.
        rootless: The daemon runs without root.
        host: Where the program reaches the daemon.
        socket: The path of the socket when it is local.
        socket_owner: Whose it is.
        socket_group: Which group's: whoever is in it has root's power.
        socket_mode: Its permission bits.
        root_dir: Where Docker keeps its data.
        os: The operating system Docker runs on.
        error: Docker's own words when the daemon did not answer.
    """

    installed: bool = False
    reachable: bool = False
    client_version: str = ""
    server_version: str = ""
    client_api: str = ""
    server_api: str = ""
    min_api: str = ""
    api: str = ""
    compose_version: str = ""
    compose_v1: bool = False
    image_store: str = ""
    storage_driver: str = ""
    rootless: bool = False
    host: str = ""
    socket: str = ""
    socket_owner: str = ""
    socket_group: str = ""
    socket_mode: str = ""
    root_dir: str = ""
    os: str = ""
    error: str = ""


class DockerEngineOut(BaseModel):
    """
    Which Docker there is and what does not fit in it: the engine's doctor.

    Attributes:
        ok: Noust can use everything of this Docker (Compose included).
        state: ``ok``, ``missing`` (no program), ``unreachable`` (its daemon does not answer) or
            ``too_old`` (its API is below what Noust speaks).
        usable: Containers can be looked at and controlled: the state is ``ok``, with or without Compose.
        facts: What is, as sentences with a code.
        limits: What does not fit, as sentences with a code and the fix of each.
        info: What Docker said.
    """

    ok: bool
    state: str
    usable: bool
    facts: list[DockerSentenceOut]
    limits: list[DockerSentenceOut]
    info: DockerEngineInfoOut


class DockerListOut(BaseModel):
    """
    What every listing says besides its rows.

    Attributes:
        available: Docker could be read. When it is false the rows are empty and ``engine`` says why.
        engine: The doctor's report, only when Docker could not be read.
        total: How many there are in all, after the filters and before the page.
        offset: Where the page starts.
        limit: How many it holds at most; null for all of them.
        can_manage: The one who asks may change Docker at all.
    """

    available: bool = True
    engine: DockerEngineOut | None = None
    total: int = 0
    offset: int = 0
    limit: int | None = None
    can_manage: bool = False


class DockerContainerOut(BaseModel):
    """
    A container, with its owner and what may be done to it.

    Attributes:
        id: The full id.
        name: Its name.
        image: The image it was made from, as it was asked for.
        state: Docker's word for its state.
        status: Docker's sentence for it (``Up 2 hours (healthy)``).
        health: ``healthy``, ``unhealthy``, ``starting`` or empty.
        created: When it was made, as Docker prints it.
        ports: What it publishes, as Docker prints it.
        mounts: The volumes (by name) and the host paths it mounts.
        networks: The networks it is on.
        project: Its Compose project.
        service: Its Compose service.
        working_dir: The directory its project runs from.
        config_files: The compose files Compose recorded for it.
        oneoff: Compose made it for ``run`` and not for ``up``.
        instance: Its database-instance label: a claim, not a proof.
        helper: Its helper label: a claim, not a proof.
        state_kind: Its state as one word: ``running``, ``unhealthy``, ``loop``, ``oom``, ``killed``,
            ``error``, ``dead``, ``terminated`` (somebody stopped it), ``ended``, ``stopped``,
            ``paused`` or ``created``.
        problem: It asks for the operator.
        exit_code: How its last run ended, when the listing says.
        owner: Whose it is.
        can: What may be done to it.
    """

    id: str
    name: str
    image: str = ""
    state: str = ""
    status: str = ""
    health: str = ""
    created: str = ""
    ports: str = ""
    mounts: list[str] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    project: str = ""
    service: str = ""
    working_dir: str = ""
    config_files: list[str] = Field(default_factory=list)
    oneoff: bool = False
    instance: str = ""
    helper: str = ""
    state_kind: str
    problem: bool
    exit_code: int | None = None
    owner: DockerOwnerOut
    can: DockerCanOut


class DockerContainersOut(DockerListOut):
    """
    The containers the principal sees, by name.

    Attributes:
        items: The page.
    """

    items: list[DockerContainerOut] = Field(default_factory=list)


class DockerCopyOut(BaseModel):
    """
    Whether there is a copy of an application's databases that was proven to restore.

    Attributes:
        state: ``ok`` (a restore drill that loaded a database passed within the last day), ``stale`` (there
            is one, older) or ``missing``.
        last_ok_at: When the newest such drill ended.
        databases: How many databases it loaded.
        via: The commands that make one.
    """

    state: str
    last_ok_at: str | None = None
    databases: int = 0
    via: str = ""


class DockerUpdateOut(BaseModel):
    """
    What updating an image does, and what the last look at the registries found of it.

    Attributes:
        image: The image as the listing names it.
        reference: The reference a registry is asked about for it; null when it has none.
        mode: ``with_net`` (through its application, behind the health gate, the way back kept),
            ``bring_first`` (bring its stack to Noust, then update), ``loose`` (a container on its own: not
            updated), ``via_owner`` (a database engine's or Noust's: through its own door), ``rollback`` (a
            version an application keeps to go back to), ``unknown`` (nothing to update to: nobody looked,
            or the look could not tell) or ``none`` (it is current).
        state: ``current``, ``newer``, ``unknown`` or ``not_looked``.
        change: What the new version is to the one that runs: ``patch``, ``minor``, ``major`` or
            ``unknown``; empty when there is none.
        floating: The tag is one that moves (``latest``, ``stable``).
        current_version: The version that runs, as far as the image says.
        latest_version: The version behind the tag now.
        latest_created: When that image was built.
        checked_at: When the registries were asked.
        detail: What the registry said, when the look could not tell.
        owner: Whose the image is.
        app: The application that is updated (``with_net``).
        stack: The Compose project to bring to Noust (``bring_first``).
        images: Every image that moves with the update, by reference.
        steps: What it does, in order: ``pull``, ``copy_databases``, ``recreate_behind_health``,
            ``keep_previous``, ``roll_back_on_failure``.
        major: Something that moves is a new major version.
        needs: What is missing before it can go ahead: ``verified_copy`` or ``accept_major``.
        backup: The copy of the application's databases, when a database's major version moves.
        apply: It can go ahead once the person has said so.
        via: Where it is done instead, as a command, when it is not here.
    """

    image: str
    reference: str | None = None
    mode: str
    state: str
    change: str = ""
    floating: bool = False
    current_version: str | None = None
    latest_version: str | None = None
    latest_created: str | None = None
    checked_at: str | None = None
    detail: str = ""
    owner: DockerOwnerOut
    app: str | None = None
    stack: str | None = None
    images: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    major: bool = False
    needs: str | None = None
    backup: DockerCopyOut | None = None
    apply: bool = False
    via: str = ""


class DockerImageRowOut(BaseModel):
    """
    An image, once, with every name it has.

    Attributes:
        id: Its id.
        tags: ``repository:tag`` of each name; none for an image with no name.
        created: When it was made.
        size_bytes: Its size, when Docker said.
        used_by: The containers made from it, running or not.
        way_back: An application keeps it to go back to: never offered as unused.
        dangling: It has no name.
        owner: Whose it is.
        can: What may be done to it.
        update: What the last look at the registries found of it and what updating it would do; null when
            it was not worked out (a listing that cannot ask the registries' last findings).
    """

    id: str
    tags: list[str] = Field(default_factory=list)
    created: str = ""
    size_bytes: int | None = None
    used_by: list[str] = Field(default_factory=list)
    way_back: bool = False
    dangling: bool = False
    owner: DockerOwnerOut
    can: DockerCanOut
    update: DockerUpdateOut | None = None


class DockerImagesOut(DockerListOut):
    """
    The images the principal sees, by name.

    Attributes:
        items: The page.
    """

    items: list[DockerImageRowOut] = Field(default_factory=list)


class DockerVolumeOut(BaseModel):
    """
    A named volume.

    Attributes:
        name: Its name.
        driver: Its driver.
        scope: ``local`` or ``global``.
        mountpoint: Where Docker keeps its data on the host.
        project: Its Compose project.
        key: The name its compose file gave it.
        instance: Its database-instance label.
        size_bytes: Its size when measured; a listing leaves it null (``GET /volumes/sizes`` measures).
        used_by: The containers that mount it.
        anonymous: A container made it without naming it.
        owner: Whose it is.
        can: What may be done to it.
    """

    name: str
    driver: str = "local"
    scope: str = "local"
    mountpoint: str = ""
    project: str = ""
    key: str = ""
    instance: str = ""
    size_bytes: int | None = None
    used_by: list[str] = Field(default_factory=list)
    anonymous: bool = False
    owner: DockerOwnerOut
    can: DockerCanOut


class DockerVolumesOut(DockerListOut):
    """
    The volumes the principal sees, by name, not measured.

    Attributes:
        items: The page.
    """

    items: list[DockerVolumeOut] = Field(default_factory=list)


class DockerVolumeSizesOut(BaseModel):
    """
    How much each volume holds, measured apart because Docker walks every volume to say it.

    Attributes:
        available: Docker could be read.
        engine: The doctor's report, only when it could not.
        sizes: The size in bytes of each volume the principal sees and Docker could measure.
    """

    available: bool = True
    engine: DockerEngineOut | None = None
    sizes: dict[str, int] = Field(default_factory=dict)


class DockerNetworkOut(BaseModel):
    """
    A network.

    Attributes:
        id: Its id.
        name: Its name.
        driver: ``bridge``, ``host``, ``overlay``...
        scope: ``local`` or ``swarm``.
        internal: It has no way out.
        project: Its Compose project.
        created: When it was made.
        containers: The containers on it.
        builtin: Docker makes it itself and it cannot be removed.
        owner: Whose it is.
        can: What may be done to it.
    """

    id: str
    name: str
    driver: str = ""
    scope: str = ""
    internal: bool = False
    project: str = ""
    created: str = ""
    containers: list[str] = Field(default_factory=list)
    builtin: bool = False
    owner: DockerOwnerOut
    can: DockerCanOut


class DockerNetworksOut(DockerListOut):
    """
    The networks the principal sees, by name.

    Attributes:
        items: The page.
    """

    items: list[DockerNetworkOut] = Field(default_factory=list)


class DockerStackOut(BaseModel):
    """
    A Compose project, as its containers show it.

    Attributes:
        name: The project's name.
        working_dir: The directory it runs from.
        config_files: Its compose files.
        containers: The names of its containers.
        total: How many containers it has.
        running: How many of its workers run.
        workers: How many of its containers are meant to keep running (a job that did its work and
            ended is not one).
        state: ``running``, ``warning`` (a worker is down or unhealthy), ``failed`` (a worker keeps
            falling or ended badly) or ``stopped``.
        owner: Whose it is.
        can: What may be done to it by whoever asks: started, stopped and restarted as a whole when
            nobody of Noust owns it, and where it is done when an application or an engine does (it
            is never removed from here).
    """

    name: str
    working_dir: str = ""
    config_files: list[str] = Field(default_factory=list)
    containers: list[str] = Field(default_factory=list)
    total: int = 0
    running: int = 0
    workers: int = 0
    state: str = ""
    owner: DockerOwnerOut
    can: DockerCanOut = Field(default_factory=DockerCanOut)


class DockerStacksOut(DockerListOut):
    """
    The Compose projects the principal sees, by name.

    Attributes:
        items: The page.
    """

    items: list[DockerStackOut] = Field(default_factory=list)


class DockerStackDetailOut(BaseModel):
    """
    One stack with its containers in full.

    Attributes:
        available: Docker could be read.
        engine: The doctor's report, only when it could not.
        stack: The stack.
        containers: Its containers, by name.
        can_manage: The one who asks may change Docker at all.
    """

    available: bool = True
    engine: DockerEngineOut | None = None
    stack: DockerStackOut | None = None
    containers: list[DockerContainerOut] = Field(default_factory=list)
    can_manage: bool = False


class DockerStateOut(BaseModel):
    """
    A container's state as one word.

    Attributes:
        kind: See :attr:`DockerContainerOut.state_kind`.
        problem: It asks for the operator.
        exit_code: How its last run ended.
    """

    kind: str
    problem: bool
    exit_code: int | None = None


class DockerHealthOut(BaseModel):
    """
    A container's health check.

    Attributes:
        status: ``healthy``, ``unhealthy`` or ``starting``.
        failing_streak: How many probes in a row failed.
        last_output: What the last probe printed, as it printed it.
    """

    status: str
    failing_streak: int = 0
    last_output: str = ""


class DockerWhyOut(BaseModel):
    """
    Why a container is the way it is, as Docker says it.

    Attributes:
        kind: Its state as one word.
        exit_code: How its last run ended.
        oom_killed: Memory ran out in its last run.
        error: Docker's own words when it could not start it.
        restart_count: How many times Docker restarted it in its life.
        restarts_recent: How many times in the last ``window_minutes``.
        window_minutes: The window ``restarts_recent`` counts in.
        looping: It keeps falling: restarting now or restarted three times or more in the window.
        started_at: When it last started, to the second.
        finished_at: When it last ended, to the second.
        memory_limit_bytes: Its memory limit; null for none.
        health: Its health check, when it has one.
        last_lines: What it wrote last, as it wrote it with the credentials masked; empty for a
            container that runs well (its log is read live).
    """

    kind: str
    exit_code: int | None = None
    oom_killed: bool = False
    error: str = ""
    restart_count: int = 0
    restarts_recent: int = 0
    window_minutes: int = 10
    looping: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    memory_limit_bytes: int | None = None
    health: DockerHealthOut | None = None
    last_lines: str = ""


class DockerOriginOut(BaseModel):
    """
    Where a container comes from.

    Attributes:
        image: The image it was made from, as it was asked for.
        image_id: That image's id.
        project: Its Compose project.
        service: Its Compose service.
        working_dir: The directory its project runs from.
        config_files: The compose files Compose recorded for it.
    """

    image: str = ""
    image_id: str = ""
    project: str = ""
    service: str = ""
    working_dir: str = ""
    config_files: list[str] = Field(default_factory=list)


class DockerMountOut(BaseModel):
    """
    Something a container touches on the host.

    Attributes:
        kind: ``volume``, ``bind`` or ``tmpfs``.
        name: The volume's name, for a volume.
        source: Where it is on the host.
        destination: Where it is inside the container.
        read_only: The container may not write to it.
    """

    kind: str
    name: str = ""
    source: str = ""
    destination: str = ""
    read_only: bool = False


class DockerTouchesOut(BaseModel):
    """
    What a container touches.

    Attributes:
        mounts: The volumes and host paths it mounts.
        networks: The networks it is on.
        ports: What it publishes, as Docker prints it.
        privileged: It runs with every privilege of the host.
    """

    mounts: list[DockerMountOut] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    ports: str = ""
    privileged: bool = False


class DockerRestartPolicyOut(BaseModel):
    """
    What Docker does when a container ends.

    Attributes:
        name: ``no``, ``always``, ``unless-stopped`` or ``on-failure``.
        max_retries: How many times ``on-failure`` tries; 0 for no limit.
    """

    name: str = "no"
    max_retries: int = 0


class DockerUsageOut(BaseModel):
    """
    What a container uses, or why that is not known.

    Attributes:
        available: There is a reading worth showing.
        reason: Why there is none: ``not_running``, ``restarting``, ``too_young`` or ``no_reading``.
        cpu_percent: Processor use: 100 is one whole core.
        memory_bytes: Memory in use.
        memory_limit_bytes: Its memory limit; null when it has none.
        memory_percent: Use over the limit; null when it has none.
        net_rx_bytes: Received since it started.
        net_tx_bytes: Sent since it started.
        block_read_bytes: Read from disk since it started.
        block_write_bytes: Written to disk since it started.
        pids: Processes in it.
    """

    available: bool
    reason: str = ""
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_percent: float | None = None
    net_rx_bytes: int | None = None
    net_tx_bytes: int | None = None
    block_read_bytes: int | None = None
    block_write_bytes: int | None = None
    pids: int | None = None


class DockerContainerDetailOut(BaseModel):
    """
    One container from the inside.

    Attributes:
        available: Docker could be read.
        engine: The doctor's report, only when it could not.
        container: The container as the listing has it, with its owner and what may be done to it.
        state: Its state as one word.
        owner: Whose it is.
        job: It ended well and was never meant to run again: a job that did its work.
        why: Why it is the way it is.
        origin: Where it comes from.
        touches: What it touches.
        restart_policy: What Docker does when it ends.
        cpu_limit: Its processor limit in CPUs; null for none.
        usage: What it uses now, or why that is not known.
        can_manage: The one who asks may change Docker at all.
    """

    available: bool = True
    engine: DockerEngineOut | None = None
    container: DockerContainerOut | None = None
    state: DockerStateOut | None = None
    owner: DockerOwnerOut | None = None
    job: bool = False
    why: DockerWhyOut | None = None
    origin: DockerOriginOut | None = None
    touches: DockerTouchesOut | None = None
    restart_policy: DockerRestartPolicyOut | None = None
    cpu_limit: float | None = None
    usage: DockerUsageOut | None = None
    can_manage: bool = False


class DockerLogsOut(BaseModel):
    """
    The end of a container's log, as it wrote it.

    Attributes:
        available: Docker could be read.
        engine: The doctor's report, only when it could not.
        container: Its name.
        tail: How many lines were asked for.
        logs: The lines with the time of each, the credentials masked and nothing else changed.
    """

    available: bool = True
    engine: DockerEngineOut | None = None
    container: str = ""
    tail: int = 0
    logs: str = ""


class DockerAttentionOut(BaseModel):
    """
    One thing that asks for the operator.

    Attributes:
        kind: ``loop`` (a container Docker restarts without stopping), ``stopped`` (it is down and
            should not be), or ``disk`` (the disk Docker lives on is filling); the registries' checks
            add ``update`` and ``major``.
        subject: The container it is about; null for the disk or a group.
        owner: Whose the container is.
        count: How many things it stands for.
        facts: What the screen says it with, as data: for a loop ``exit_code``, ``oom``,
            ``memory_limit_bytes``, ``restarts_recent``, ``window_minutes`` and ``restart_count``; for a
            stop ``exit_code``, ``kind``, ``policy`` and ``finished_at``; for the disk ``free_bytes``,
            ``total_bytes`` and ``reclaimable_bytes``.
    """

    kind: str
    subject: str | None = None
    owner: DockerOwnerOut | None = None
    count: int = 1
    facts: dict[str, Any] = Field(default_factory=dict)


class DockerDiskOut(BaseModel):
    """
    The disk Docker lives on.

    Attributes:
        docker_bytes: What Docker holds.
        reclaimable_bytes: What a cleanup could give back without touching data.
        free_bytes: Free where Docker keeps its data; null when this process cannot see it.
        total_bytes: The size of that disk; null likewise.
        path: Where Docker keeps its data.
    """

    docker_bytes: int | None = None
    reclaimable_bytes: int | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None
    path: str = ""


class DockerSummaryOut(BaseModel):
    """
    The machine, in short.

    Attributes:
        available: Docker could be read.
        engine: Which Docker there is and what does not fit in it (always present).
        counts: How many of each thing: ``containers`` (``total``, ``running``, ``failing``,
            ``stopped``), ``stacks`` (``total``, ``unowned``), ``images``, ``volumes`` and ``networks``
            (``total``). Empty when Docker could not be read.
        attention: What asks for the operator, worst first.
        disk: The disk Docker lives on.
        can_manage: The one who asks may change Docker at all.
    """

    available: bool
    engine: DockerEngineOut
    counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    attention: list[DockerAttentionOut] = Field(default_factory=list)
    disk: DockerDiskOut | None = None
    can_manage: bool = False


class DockerLostOut(BaseModel):
    """
    A container that would not come back after a restart of the machine.

    Attributes:
        name: Its name.
        owner: Whose it is.
        why: ``policy`` (it runs and its policy does not bring it back) or ``stopped`` (somebody stopped
            it under ``unless-stopped``).
        policy: Its restart policy.
        fix: ``via_owner`` (change it where its application or engine is), ``bring_first`` (it is part of
            a stack Noust did not make: bring the stack to Noust) or ``set_policy`` (give it a policy).
        project: The Compose project it is part of, if any.
    """

    name: str
    owner: DockerOwnerOut
    why: str
    policy: str
    fix: str
    project: str = ""


class DockerRestartLossOut(BaseModel):
    """
    What a restart of the machine would leave down.

    Attributes:
        available: Docker could be read.
        engine: The doctor's report, only when it could not.
        items: The containers that would not come back, by name.
    """

    available: bool = True
    engine: DockerEngineOut | None = None
    items: list[DockerLostOut] = Field(default_factory=list)


class DockerPlanItemOut(BaseModel):
    """
    One thing a cleanup would remove.

    Attributes:
        id: What identifies it: the plan is applied by it.
        kind: ``image``, ``container``, ``volume``, ``network`` or ``cache``.
        name: What a person calls it.
        owner: Whose it is.
        size_bytes: How much it holds, when Docker said.
        why: ``dangling`` (no name and no container), ``unused``, ``stopped`` or ``old_cache``.
        tick: Marked when the plan is shown.
        type_name: The person types its name before it goes (every volume).
        anonymous: A volume a container made without naming it.
    """

    id: str
    kind: str
    name: str
    owner: DockerOwnerOut
    size_bytes: int | None = None
    why: str
    tick: bool
    type_name: bool = False
    anonymous: bool = False


class DockerKeptOut(BaseModel):
    """
    Something that looks unused and stays.

    Attributes:
        name: What it is called.
        owner: Whose it is.
        size_bytes: How much it holds.
        why: ``rollback`` (an application keeps it to go back to) or ``noust`` (Noust uses it).
    """

    name: str
    owner: DockerOwnerOut
    size_bytes: int | None = None
    why: str


class DockerCleanupPlanOut(BaseModel):
    """
    What a cleanup of Docker would do, worked out and not done.

    Attributes:
        available: Docker could be read.
        engine: The doctor's report, only when it could not.
        token: What the plan is applied with: a plan that is not the one of now is not applied.
        items: What would go, in the order it goes.
        kept: What looks unused and stays, and why.
        default_bytes: What is recovered with what is ticked.
    """

    available: bool = True
    engine: DockerEngineOut | None = None
    token: str = ""
    items: list[DockerPlanItemOut] = Field(default_factory=list)
    kept: list[DockerKeptOut] = Field(default_factory=list)
    default_bytes: int = 0


class DockerCleanupRequest(BaseModel):
    """
    Apply the cleanup plan that was shown.

    Attributes:
        token: The token of the plan that was shown.
        select: The ``id`` of each item to remove.
        confirm_names: For each volume chosen, its name as the person typed it.
    """

    token: str = Field(min_length=1, max_length=128)
    select: list[str] = bounded_list(1000)
    confirm_names: dict[str, str] = Field(default_factory=dict)


class DockerRemoveVolumeRequest(BaseModel):
    """
    Remove a volume: its name is typed a second time.

    Attributes:
        confirm_name: The volume's name again.
    """

    confirm_name: str | None = Field(default=None, max_length=255)


class DockerActionOut(BaseModel):
    """
    What an action on one object did.

    Attributes:
        kind: ``container``, ``image``, ``volume`` or ``network``.
        name: What it is called.
        action: ``start``, ``stop``, ``restart`` or ``remove``.
        size_bytes: What a removal freed, when known (a volume's data is gone with it).
    """

    kind: str
    name: str
    action: str
    size_bytes: int | None = None


def owner_out(owner: Owner) -> DockerOwnerOut:
    """
    Args:
        owner: Whose an object is.

    Returns:
        Its API shape.
    """
    return DockerOwnerOut(**owner.to_dict())


def engine_out(report: EngineReport) -> DockerEngineOut:
    """
    Args:
        report: The engine's doctor.

    Returns:
        Its API shape.
    """
    body = report.to_dict()
    return DockerEngineOut(
        ok=body["ok"],
        state=body["state"],
        usable=body["usable"],
        facts=[DockerSentenceOut(**fact) for fact in body["facts"]],
        limits=[DockerSentenceOut(**limit) for limit in body["limits"]],
        info=DockerEngineInfoOut(**body["info"]),
    )


def container_out(container: Container, can: DockerCanOut) -> DockerContainerOut:
    """
    Args:
        container: A container with its owner.
        can: What may be done to it.

    Returns:
        Its API shape.
    """
    body = container.to_dict()
    body["owner"] = owner_out(container.owner)
    return DockerContainerOut(**body, can=can)


def image_out(
    image: Image, can: DockerCanOut, update: DockerUpdateOut | None = None
) -> DockerImageRowOut:
    """
    Args:
        image: An image with its owner.
        can: What may be done to it.
        update: What the last look at the registries found of it and what updating it would do.

    Returns:
        Its API shape.
    """
    body = image.to_dict()
    body["owner"] = owner_out(image.owner)
    return DockerImageRowOut(**body, dangling=not image.tags, can=can, update=update)


def volume_out(volume: Volume, can: DockerCanOut) -> DockerVolumeOut:
    """
    Args:
        volume: A volume with its owner.
        can: What may be done to it.

    Returns:
        Its API shape.
    """
    import re

    body = volume.to_dict()
    body["owner"] = owner_out(volume.owner)
    anonymous = re.fullmatch(r"[0-9a-f]{64}", volume.name) is not None
    return DockerVolumeOut(**body, anonymous=anonymous, can=can)


def network_out(network: Network, can: DockerCanOut) -> DockerNetworkOut:
    """
    Args:
        network: A network with its owner.
        can: What may be done to it.

    Returns:
        Its API shape.
    """
    body = network.to_dict()
    body["owner"] = owner_out(network.owner)
    return DockerNetworkOut(**body, can=can)


def stack_out(project: ComposeProject, can: DockerCanOut | None = None) -> DockerStackOut:
    """
    Args:
        project: A Compose project with its owner.
        can: What may be done to it; nothing when not said.

    Returns:
        Its API shape.
    """
    body = project.to_dict()
    body["owner"] = owner_out(project.owner)
    return DockerStackOut(**body, can=can if can is not None else DockerCanOut())


def plan_out(plan: cleanup_module.CleanupPlan) -> DockerCleanupPlanOut:
    """
    Args:
        plan: A cleanup plan.

    Returns:
        Its API shape.
    """
    return DockerCleanupPlanOut(
        token=plan.token,
        items=[
            DockerPlanItemOut(**{**item.to_dict(), "owner": owner_out(item.owner)})
            for item in plan.items
        ],
        kept=[
            DockerKeptOut(**{**kept.to_dict(), "owner": owner_out(kept.owner)})
            for kept in plan.kept
        ],
        default_bytes=plan.default_bytes,
    )
