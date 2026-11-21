# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The keys of a compose file that the guard knows, and what each one is.

The guard used to know the keys that give the machine and let every other key through, and Compose
gains keys: ``use_api_socket`` mounts the Docker socket without a line of ``volumes``, ``post_start``
and ``pre_stop`` run a command with ``privileged``, ``pre_start`` is a whole container, ``label_file``
reads a file, ``build.entitlements`` lets a build reach the server. Each of them passed because nobody
had written them down yet. The list is closed the other way round: this module is **every key of the
Compose specification** the guard has decided about, section by section (the top level, a service or a
job, a build, a network, a volume, a secret or a config, a model, a hook, an ``include``, an
``extends``, ``deploy`` and the long form of a mount), and a key that is not here is not let through:
it is refused for a new stack, with the exception as the way out, and warned about for a stack that
already had it (:func:`judge_keys`).

Each key is one of three things:

- :data:`INERT`: whatever its value, it changes what the container does inside itself and nothing of
  this server (a limit, a name, a command, a label, a health check).
- :data:`JUDGED`: its value decides, and something reads it: a path is followed by
  :mod:`~noust.deployers.helpers.compose_paths`, a name is asked of the store, a capability or an
  option is weighed in :mod:`~noust.deployers.helpers.compose_host`. A test holds each of them to a
  hostile value that is found and an ordinary one that is not.
- :data:`GIVES`: being on is the machine: ``privileged``, ``use_api_socket``, a device. A test holds
  each of them to a finding when it is on and none when it is off.

``tests/fixtures/compose_spec_keys.json`` is the list of keys of the specification
(``scripts/compose_spec_keys.py`` writes it from the schema that the Compose project publishes) and a
test fails on a key it has that this list has not decided about, so a new key of Compose is a test that
fails, not a hole. Keys that start with ``x-`` are extensions, which Compose itself ignores.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from noust.core.server_text import say

if TYPE_CHECKING:
    from noust.deployers.helpers.compose_guard import ComposeFindings

#: What a key is: see the module's text.
INERT, JUDGED, GIVES = "inert", "judged", "gives"


def _keys(inert: str = "", judged: str = "", gives: str = "") -> dict[str, str]:
    """
    Args:
        inert: The keys that are :data:`INERT`, separated by spaces.
        judged: The keys that are :data:`JUDGED`.
        gives: The keys that are :data:`GIVES`.

    Returns:
        Each key with what it is.
    """
    table: dict[str, str] = {}
    for kind, names in ((INERT, inert), (JUDGED, judged), (GIVES, gives)):
        for name in names.split():
            table[name] = kind
    return table


#: The top level of a document. ``version`` is obsolete and ignored; ``name`` is the project (judged
#: against the store); ``include`` and every map of things are read entry by entry.
TOP_LEVEL = _keys(
    inert="version",
    judged="name include services jobs networks volumes secrets configs models",
)

#: A service, a job and a ``pre_start`` container: what a container can be given.
#:
#: Inert: nothing of these reaches past the container (``sysctls`` is limited by Docker to the
#: namespaced ones, ``ulimits`` and ``oom_score_adj`` can starve the machine but not read it, ``develop``
#: is read only by ``compose watch``, which Noust never runs, ``credential_spec`` is a Windows setting).
#: Judged: ``build`` and its keys, the paths (``env_file``, ``label_file``, ``volumes``, ``secrets``,
#: ``configs``, ``extends``), the names (``image``, ``container_name``), the namespaces and the
#: capabilities, ``security_opt``, ``runtime``, ``volumes_from``, the hooks, ``gpus`` and ``deploy``'s
#: devices, ``models``. Gives: ``privileged``, ``use_api_socket``, ``provider``, ``devices``,
#: ``device_cgroup_rules`` and ``cgroup_parent``.
SERVICE = _keys(
    inert=(
        "annotations attach blkio_config cap_drop command cpu_count cpu_percent cpu_period cpu_quota "
        "cpu_rt_period cpu_rt_runtime cpu_shares cpus cpuset credential_spec depends_on develop dns "
        "dns_opt dns_search domainname entrypoint environment expose external_links extra_hosts "
        "group_add healthcheck hostname init isolation labels links logging mac_address mem_limit "
        "mem_reservation mem_swappiness memswap_limit networks oom_kill_disable oom_score_adj "
        "pids_limit platform ports profiles pull_policy pull_refresh_after read_only restart scale "
        "shm_size stdin_open stop_grace_period stop_signal storage_opt sysctls tmpfs triggers tty "
        "ulimits umask user working_dir"
    ),
    judged=(
        "build cap_add cgroup configs container_name deploy env_file extends gpus image ipc label_file "
        "models network_mode pid post_start pre_start pre_stop runtime secrets security_opt uts "
        "userns_mode volumes volumes_from"
    ),
    gives="privileged use_api_socket provider devices device_cgroup_rules cgroup_parent",
)

#: ``build``. Inert: arguments, labels, platforms and what only changes how the image is made.
#: Judged: the context and every path it reads (a Dockerfile, a cache on disk, a secret, an SSH key,
#: another context), the entitlements, the network and the names the image is tagged with.
BUILD = _keys(
    inert=(
        "args dockerfile_inline extra_hosts isolation labels no_cache no_cache_filter platforms "
        "provenance pull sbom shm_size target ulimits"
    ),
    judged=(
        "additional_contexts cache_from cache_to context dockerfile entitlements network secrets ssh tags"
    ),
    gives="privileged",
)

#: A network of the document: whether it is made by Docker's own isolated driver, and where it is
#: attached on this server, is what decides.
NETWORK = _keys(
    inert="attachable enable_ipv4 enable_ipv6 internal ipam labels",
    judged="driver driver_opts external name",
)

#: A volume of the document.
VOLUME = _keys(inert="labels", judged="driver driver_opts external name")

#: A secret and a config: the file is a path of this server; the rest is data or swarm's.
SECRET = _keys(
    inert="driver driver_opts environment external labels name template_driver",
    judged="file",
)
CONFIG = _keys(inert="content environment external labels name template_driver", judged="file")

#: A model the document asks Docker's model runner to serve from this server.
MODEL = _keys(inert="context_size model name", judged="runtime_flags")

#: What ``post_start`` and ``pre_stop`` run in the container: ``privileged`` there is more than the
#: container has.
HOOK = _keys(inert="command environment user working_dir", judged="privileged")

#: ``include`` and the file an ``extends`` takes a service from.
INCLUDE = _keys(judged="path env_file project_directory")
EXTENDS = _keys(inert="service", judged="file")

#: ``deploy``: swarm's, ignored by Compose, except the devices a service reserves.
DEPLOY = _keys(
    inert="endpoint_mode labels mode placement replicas restart_policy rollback_config update_config",
    judged="resources",
)

#: The long form of a mount, and the kinds of mount there are.
MOUNT = _keys(
    inert="read_only consistency target bind volume tmpfs image",
    judged="type source",
)
MOUNT_TYPES = frozenset({"bind", "volume", "tmpfs", "npipe", "cluster", "image"})

#: A ``pre_start`` entry: a whole container, run to completion before the service starts, with what a
#: service has and the one key that says whether it runs for each replica.
PRE_START = {**SERVICE, "per_replica": INERT}

#: Every section by name, which is what the tests walk and the specification's keys are held to.
SECTIONS: dict[str, dict[str, str]] = {
    "top": TOP_LEVEL,
    "service": SERVICE,
    "pre_start": PRE_START,
    "build": BUILD,
    "network": NETWORK,
    "volume": VOLUME,
    "secret": SECRET,
    "config": CONFIG,
    "model": MODEL,
    "hook": HOOK,
    "include": INCLUDE,
    "extends": EXTENDS,
    "deploy": DEPLOY,
    "mount": MOUNT,
}


def is_extension(key: Any) -> bool:
    """
    Args:
        key: A key of a compose file.

    Returns:
        Whether it is an extension field (``x-...``), which Compose ignores.
    """
    return str(key).startswith("x-")


def unknown_keys(section: str, entry: Mapping[Any, Any]) -> list[str]:
    """
    List the keys of an entry that the guard has not decided about.

    Args:
        section: A key of :data:`SECTIONS`.
        entry: The entry as the file wrote it.

    Returns:
        Its keys that are neither in the section's table nor extensions, in the file's order.
    """
    known = SECTIONS[section]
    return [str(key) for key in entry if str(key) not in known and not is_extension(key)]


def judge_keys(
    findings: ComposeFindings,
    who: str,
    section: str,
    entry: Any,
    *,
    path: str = "",
) -> None:
    """
    Refuse the keys of an entry that the guard does not know.

    A key that is not in the closed list is something Noust cannot say what it gives a container, which
    is the one thing a guard must not guess: it is refused like what gives the machine (an exception
    for the application is the way out), and a stack that already had it is only warned about.

    Args:
        findings: Where the findings go.
        who: ``service web``, ``network lan``...
        section: A key of :data:`SECTIONS`.
        entry: The entry; anything that is not a mapping has no keys to judge.
        path: Where the entry is in its service (``build``, ``post_start``) for the sentence.
    """
    if not isinstance(entry, Mapping):
        return
    for key in unknown_keys(section, entry):
        named = f"{path}.{key}" if path else key
        findings.add(
            (who, "unknown_key", named),
            say("compose_guard.unknown_key", who=who, key=named),
            refused=True,
        )


def judge_mount(findings: ComposeFindings, who: str, entry: Any) -> None:
    """
    Refuse the keys of a mount written in the long form, and a kind of mount the guard does not know.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        entry: One entry of ``volumes``; a short one (``./data:/data``) has no keys.
    """
    if not isinstance(entry, Mapping):
        return
    judge_keys(findings, who, "mount", entry, path="volumes")
    kind = entry.get("type")
    if kind is not None and str(kind) not in MOUNT_TYPES:
        named = f"volumes.type={kind}"
        findings.add(
            (who, "unknown_key", named),
            say("compose_guard.unknown_key", who=who, key=named),
            refused=True,
        )
