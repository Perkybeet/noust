# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a real engine printed for the question of :mod:`noust.core.docker.grants`, and the rows around it.

``WEB`` and ``WORKER`` are the two lines Docker 29.2.0 printed for two containers made with ``docker
create`` (nothing started) and read with ``GRANTS_FORMAT``: the first was given a directory and the
socket of the server, two capabilities, a device and a cgroup rule, the namespaces of the host, its
confinement switched off, another cgroup, and an environment variable that the question must not read;
the second was made ``--privileged``, on the network of the first and with its volumes. They are kept
as the engine wrote them (ids and names are those of that run), but for the two paths Docker Desktop
rewrote for the second, which are those of a machine and are given invented ones.
"""

from __future__ import annotations

import json
from typing import Any

FIRST = "512844a03fc8137f9b7c3018c251b81e86d77229a19b69249e6c45f973e44967"
SECOND = "3aa71e1f251a763a2103202a584d7f46da5fc68dd30f1f029504ae5b84ad26dc"

WEB = (
    '{"id":"512844a03fc8137f9b7c3018c251b81e86d77229a19b69249e6c45f973e44967",'
    '"name":"/noust-test-w6a3-first","service":"web","oneoff":"False","state":"created",'
    '"mounts":[{"Destination":"/data","Mode":"ro","Propagation":"rprivate","RW":false,'
    '"Source":"/tmp","Type":"bind"},{"Destination":"/var/run/docker.sock","Mode":"",'
    '"Propagation":"rprivate","RW":true,"Source":"/var/run/docker.sock","Type":"bind"}],'
    '"host":{"Privileged":false,"CapAdd":["CAP_NET_BIND_SERVICE","CAP_SYS_ADMIN"],'
    '"Devices":[{"CgroupPermissions":"rwm","PathInContainer":"/dev/null2","PathOnHost":"/dev/null"}],'
    '"DeviceCgroupRules":["c 13:* rwm"],"PidMode":"host","IpcMode":"host","UTSMode":"host",'
    '"UsernsMode":"","CgroupnsMode":"host","NetworkMode":"bridge",'
    '"SecurityOpt":["seccomp=unconfined","label=disable"],"CgroupParent":"/noust-test-w6a3",'
    '"Runtime":"runc","VolumesFrom":null}}'
)

WORKER = (
    '{"id":"3aa71e1f251a763a2103202a584d7f46da5fc68dd30f1f029504ae5b84ad26dc",'
    '"name":"/noust-test-w6a3-second","service":"worker","oneoff":null,"state":"created",'
    '"mounts":[{"Destination":"/var/run/docker.sock","Mode":"","Propagation":"rprivate","RW":false,'
    '"Source":"/srv/rewritten/docker.sock","Type":"bind"},{"Destination":"/data","Mode":"",'
    '"Propagation":"rprivate","RW":false,"Source":"/srv/rewritten/data","Type":"bind"}],'
    '"host":{"Privileged":true,"CapAdd":null,"Devices":[],"DeviceCgroupRules":null,"PidMode":"",'
    '"IpcMode":"private","UTSMode":"","UsernsMode":"","CgroupnsMode":"private",'
    '"NetworkMode":"container:512844a03fc8137f9b7c3018c251b81e86d77229a19b69249e6c45f973e44967",'
    '"SecurityOpt":["label=disable"],"CgroupParent":"","Runtime":"runc",'
    '"VolumesFrom":["noust-test-w6a3-first:ro"]}}'
)


def container_id(service: str) -> str:
    """
    Args:
        service: A service of a stack.

    Returns:
        A full container id made from its name, the same one for the listing and for the inspection.
    """
    return (service.encode().hex() * 32)[:64]


def grants_line(
    service: str,
    *,
    ident: str | None = None,
    binds: tuple[str, ...] = (),
    cap_add: tuple[str, ...] = (),
    devices: tuple[str, ...] = (),
    privileged: bool = False,
    pid: str = "",
    ipc: str = "private",
    network: str = "bridge",
    security_opt: tuple[str, ...] = (),
    cgroup_parent: str = "",
    runtime: str = "runc",
    volumes_from: tuple[str, ...] = (),
    state: str = "running",
) -> str:
    """
    What the engine prints for one container of a stack, in the shape of the real lines above.

    Args:
        service: Its ``com.docker.compose.service``.
        ident: Its id; one made from the service when None.
        binds: The directories of the server mounted into it.
        cap_add: The capabilities added, as Docker records them (``CAP_SYS_ADMIN``).
        devices: The devices of the host it is given.
        privileged: It was made privileged.
        pid: Its PID namespace.
        ipc: Its IPC namespace.
        network: Its network mode.
        security_opt: Its confinement options.
        cgroup_parent: Its cgroup.
        runtime: Its runtime.
        volumes_from: The containers whose volumes it was given, as ``name:mode``.
        state: Docker's word for its state.

    Returns:
        The JSON object, as a line.
    """
    ident = ident or container_id(service)
    return json.dumps(
        {
            "id": ident,
            "name": f"/shop-{service}-1",
            "service": service,
            "oneoff": "False",
            "state": state,
            "mounts": [
                {
                    "Type": "bind",
                    "Source": source,
                    "Destination": "/data",
                    "Mode": "",
                    "RW": True,
                    "Propagation": "rprivate",
                }
                for source in binds
            ],
            "host": {
                "Privileged": privileged,
                "CapAdd": list(cap_add) or None,
                "Devices": [
                    {"PathOnHost": path, "PathInContainer": path, "CgroupPermissions": "rwm"}
                    for path in devices
                ],
                "DeviceCgroupRules": None,
                "PidMode": pid,
                "IpcMode": ipc,
                "UTSMode": "",
                "UsernsMode": "",
                "CgroupnsMode": "private",
                "NetworkMode": network,
                "SecurityOpt": list(security_opt) or None,
                "CgroupParent": cgroup_parent,
                "Runtime": runtime,
                "VolumesFrom": list(volumes_from) or None,
            },
        }
    )


def ps_row(
    ident: str,
    name: str,
    *,
    project: str,
    service: str,
    oneoff: bool = False,
    state: str = "running",
) -> str:
    """
    One line of ``docker ps --all`` as the gate asks for it.

    Args:
        ident: The container's id.
        name: Its name.
        project: Its ``com.docker.compose.project`` label.
        service: Its ``com.docker.compose.service`` label.
        oneoff: Compose made it for ``run``.
        state: Docker's word for its state.

    Returns:
        The JSON object, as a line.
    """
    row: dict[str, Any] = {
        "id": ident,
        "name": name,
        "image": "example/app:1",
        "state": state,
        "status": "Up 2 hours" if state == "running" else "Exited (0) 2 hours ago",
        "created": "2026-11-12 08:00:00 +0000 UTC",
        "ports": "",
        "mounts": "",
        "networks": "default",
        "project": project,
        "service": service,
        "working_dir": "",
        "config_files": "",
        "oneoff": "True" if oneoff else "False",
        "instance": "",
        "helper": "",
    }
    return json.dumps(row)
