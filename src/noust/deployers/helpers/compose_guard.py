# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What in a Docker Compose file is root on the host.

A compose file comes from the repository, and ``docker compose up`` runs it
through the Docker daemon, which is root. A service that is ``privileged`` or
mounts the Docker socket owns the server: a container with the socket starts
another with ``/`` mounted and writes wherever it likes. That is the class of
defect behind Coolify's CVE-2025-64419 and Dokploy's CVE-2026-72901, and until
3.1 nothing here looked.

:func:`inspect_compose` reads a parsed compose file and sorts what it finds, and
:mod:`noust.deployers.helpers.compose_verdict` decides what to do with it, one rule
for a deployment, an update, a redeploy, an adoption, an edit in the Docker zone and
a pasted document. What gives the machine to a container is **refused** for a new
stack unless an operator recorded an exception for the application
(:func:`noust.deployers.helpers.sandbox.set_compose_exception`, with a reason, which is
root-equivalent): ``privileged`` and the Docker socket, devices, the capabilities that
reconfigure the kernel, the host's namespaces, confinement switched off, another cgroup or
runtime, ``provider``, and every path of the server outside the application
(:mod:`noust.deployers.helpers.compose_host`). What a stack proven to run with it already
(the compose file of the commit that served, the file a redeploy replaces, containers an
adoption found running unchanged from the file) is **warned** about, never stopped, so an
application in production keeps updating. Anything an update, a redeploy or an adoption
adds is refused like a new stack's. Capabilities that do not give the machine are only
said.

**A path is judged by what Docker will mount, not by how it is spelled**
(:mod:`noust.deployers.helpers.compose_paths`). A repository can hold a link and the daemon
follows it: ``./host`` that points at ``/`` is ``/``. Every link on the way is followed; a link
inside the application that leads out of it is refused saying where it leads, and what cannot
be followed (a loop, a directory that cannot be read) is refused rather than guessed. The
same judgement is applied to every door that names a path of the server, not only to bind
mounts: ``env_file``, build contexts and what a build is given (additional contexts, secrets,
SSH keys), the file of a secret or a config, ``extends``, ``include`` with its ``env_file`` and
``project_directory``, and the ``device`` of a volume. Noust's own state (``/etc/noust``,
``/var/lib/noust``, the backups: the console's signing key, the store, the audit key), what
contains it (``/``, ``/etc``, ``/var``) and another application's tree hand a container the
secrets of everything else on the server; reached through a door with no reason to read them
they are refused with no exception that can allow it, and a bind mount of them is refused with
the exception as the way out. A download (a remote build context or include) is refused, and
for a pasted document, which has no repository to vouch for it, with no exception either.

**Whose each thing is** is the store's to say
(:mod:`noust.deployers.helpers.compose_owners`): the project a stack runs as, a volume or a
network that is ``external``, a ``container_name`` and an ``image``. What belongs to another
application or to Noust is refused with no exception that can allow it; what belongs to nobody
is something the stack is given from outside, refused for a new stack.

**What is judged is what Compose resolves, not only the ``services`` of the file.** A file can
bring in what it does not write: a service from an ``include``, a definition from an ``extends``,
the files of a ``secrets`` or ``configs`` entry, the mounts of another container through
``volumes_from`` or the namespace of one through ``pid: container:x``, a ``jobs`` entry (which
the Compose specification took in on 2026-08-16) and every service under a profile. These are the
ways Arcane's ``include`` (CVE-2026-47179) and Portainer's ``volumes_from``, configs and secrets
notices of 2026 reached the host. Three things follow. The same judgement is applied to the file
and to the model ``docker compose config --format json`` prints for it with every profile on
(:func:`resolve_model`), the second only adding what the first did not show
(:func:`merge_findings`). A file that leaves the application, an ``include`` or an ``extends``
whose target is outside it, is refused before Compose reads it: reading is how it leaks. And a file
that includes or extends and cannot be resolved is refused rather than guessed, because what it
brings in is exactly what cannot be judged.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core.runner import CommandRunner
from noust.core.server_text import ServerText, say
from noust.deployers.helpers import compose_owners
from noust.deployers.helpers.compose_host import items, judge_settings
from noust.deployers.helpers.compose_owners import Owners
from noust.deployers.helpers.compose_paths import (
    OTHER,
    PATH,
    REMOTE,
    RUNTIME_SOCKETS,
    Reached,
    followed,
    kind_of,
    reach,
    within,
)

#: A mount whose source ends like this is the Docker daemon's API.
DOCKER_SOCKET = "docker.sock"

#: The sections of a compose document that hold things which run: its services and, since the
#: Compose specification took them in (2026-08-16), its jobs. The word is the one the
#: sentences use.
_RUNNABLE = (("services", "service"), ("jobs", "job"))

#: The ``file:`` sections: the file of a secret or a config is mounted into the container.
_FILE_SECTIONS = (("secrets", "secret"), ("configs", "config"))

#: How long Compose gets to resolve a stack.
RESOLVE_TIMEOUT = 60

#: What names a finding without its words: where, what, which. Two findings with one key are the
#: same thing found twice, in the file and in the model Compose resolves it to.
FindingKey = tuple[str, str, str]


@dataclass
class ComposeFindings:
    """
    What a compose file asks of the host.

    Attributes:
        refused: Root on the host, or what gives it: refused for a new stack without an
            exception.
        warned: Worth knowing: said in the log and allowed.
        keys: For each sentence above, what it is a finding of, so that the file and the model
            Compose resolves it to are not both reported (:func:`merge_findings`).
        hard: The refused sentences that no exception allows: another application's files and
            Noust's own, reached by a door that has no reason to (an ``env_file``, a build
            context), and what belongs to another application or to Noust (a project, a volume,
            an image). A stack that already had one is warned, as with the rest, and never
            stopped.
        said: The sentences that carry a code for the console to translate.
        links: For a sentence about a path that goes through a link of the tree, that link: what
            an update that changed the link did not leave as it was (:func:`~noust.deployers.
            helpers.compose_verdict.unchanged`).
    """

    refused: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)
    keys: dict[str, FindingKey] = field(default_factory=dict, repr=False, compare=False)
    hard: set[str] = field(default_factory=set, repr=False, compare=False)
    said: dict[str, ServerText] = field(default_factory=dict, repr=False, compare=False)
    links: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False, compare=False)

    def __bool__(self) -> bool:
        """True when anything was found."""
        return bool(self.refused or self.warned)

    def add(
        self,
        key: FindingKey,
        sentence: str | ServerText,
        *,
        refused: bool,
        hard: bool = False,
        link: str = "",
    ) -> None:
        """
        Record a finding once.

        Args:
            key: What it is a finding of.
            sentence: What is said of it: text, or a sentence of the catalog.
            refused: Whether it is root on the host (else it is only worth knowing).
            hard: Whether no exception allows it (only meaningful when refused).
            link: The link of the tree the path goes through, when it does.
        """
        if key in self.keys.values():
            return
        text = sentence.text if isinstance(sentence, ServerText) else sentence
        if isinstance(sentence, ServerText):
            self.said[text] = sentence
        self.keys[text] = key
        if link:
            self.links[text] = (link,)
        if hard and refused:
            self.hard.add(text)
        (self.refused if refused else self.warned).append(text)


@dataclass(frozen=True)
class GuardContext:
    """
    What the guard knows of the application whose file it judges.

    Attributes:
        app_path: The application directory, the one tree a path may stay in.
        project_dir: Where a relative path is read from (the directory of the compose file);
            the application directory when None.
        domain: The application's domain, empty for a document that belongs to none yet.
        project: The Compose project the application runs under: the store's or the name
            Compose derives. Empty when not known (a pasted document), and then the file's own
            project name is not judged.
        env_project: ``COMPOSE_PROJECT_NAME`` as the project's ``.env`` sets it.
        owners: Whose Docker objects are, when the store was asked; None judges nothing of the
            names a file claims.
        pasted: The document was typed or pasted: it has no repository, so a download is refused
            for it with no exception.
    """

    app_path: Path
    project_dir: Path | None = None
    domain: str = ""
    project: str = ""
    env_project: str = ""
    owners: Owners | None = None
    pasted: bool = False

    @property
    def base(self) -> Path:
        """Where a relative path of the file is read from."""
        return self.project_dir or self.app_path


def _volume_source(volume: Any) -> tuple[str | None, str]:
    """
    Read where a service's volume comes from.

    Args:
        volume: One entry of ``volumes``, short (``src:dst[:mode]``) or long
            (a mapping with ``type`` and ``source``).

    Returns:
        ``(source, kind)``: the host path or volume name (None for an
        anonymous volume) and ``bind`` or ``volume``.
    """
    if isinstance(volume, Mapping):
        source = volume.get("source")
        kind = str(
            volume.get("type")
            or ("bind" if str(source or "").startswith(("/", ".", "~")) else "volume")
        )
        return (str(source) if source else None), kind
    text = str(volume)
    if ":" not in text:
        return None, "volume"
    source = text.split(":", 1)[0]
    kind = "bind" if source.startswith(("/", ".", "~", "$")) else "volume"
    return source, kind


def _noust_roots() -> tuple[PurePosixPath, ...]:
    """
    List the directories where Noust keeps what must not leave the host.

    Returns:
        The configuration, the store and the backups, under their names and under the
        old ones a link may still stand at, and where a link of an alias (a NAS's shared
        folder) really leads.
    """
    from noust.core import paths, retired_names

    roots: list[PurePosixPath] = []
    for directory in (
        paths.config_dir(),
        paths.state_dir(),
        paths.backup_dir(),
        *retired_names.old_data_roots(),
    ):
        for form in (PurePosixPath(str(directory)), followed(str(directory))):
            if form is not None and form not in roots:
                roots.append(form)
    return tuple(roots)


def _exposure(reached: Reached, app_path: Path) -> tuple[str, str] | None:
    """
    Say whether a path hands over Noust's state or another application.

    Every place the daemon may take the path for is judged, not the one it is spelled as: a link
    of the repository that leads to ``/etc/noust`` is ``/etc/noust``.

    Args:
        reached: Where the path leads.
        app_path: The application directory.

    Returns:
        ``(what, directory)``: ``noust`` (it is Noust's own), ``contains_noust`` (it holds
        ``directory``), ``all_apps`` or ``other_app``; None when it is neither Noust's nor another
        application's.
    """
    from noust.core.config import Config

    roots = _noust_roots()
    own = [PurePosixPath(posixpath.normpath(str(app_path)))]
    own.extend(form for form in [followed(app_path)] if form is not None)
    apps_directory = str(Config().apps_directory)
    apps = [PurePosixPath(posixpath.normpath(apps_directory))]
    apps.extend(form for form in [followed(apps_directory)] if form is not None)
    for place in reached.places:
        for directory in roots:
            if within(place, directory):
                return "noust", str(directory)
            if place in directory.parents:
                return "contains_noust", str(directory)
        if any(within(place, app) for app in own):
            continue
        if any(place == directory or place in directory.parents for directory in apps):
            return "all_apps", ""
        if any(directory in place.parents for directory in apps):
            return "other_app", ""
    return None


#: What a mount says of what a path hands over, in the words the guard has always used.
_EXPOSURE_REASON = {
    "noust": "that is Noust's own configuration and secrets",
    "all_apps": "that contains every application's files, the other applications' included",
    "other_app": "that is another application's files, which it must not read",
}


def _judge_source(
    findings: ComposeFindings,
    who: str,
    source: str,
    context: GuardContext,
    *,
    as_a: str = "",
    door: bool = False,
) -> None:
    """
    Judge one host path a service is given: a bind mount, or the file of a secret or a config.

    A path is judged by what Docker will mount
    (:func:`~noust.deployers.helpers.compose_paths.reach`): where every link on it leads, never
    how it is spelled.

    Args:
        findings: Where the finding goes.
        who: ``service web`` or ``job migrate``.
        source: The host path as written.
        context: What the guard knows of the application.
        as_a: ``" as a secret"`` or ``" as a config"`` for a file given that way, else empty.
        door: The path is not a mount but a file the stack is given (a secret or a config): what
            reaches Noust's state or another application through it is refused with no exception.
    """
    reached = reach(source, context.base, context.app_path)
    if reached is None:
        return
    where = str(reached.places[0])
    exposure = _exposure(reached, context.app_path)
    if any(place.name == DOCKER_SOCKET for place in reached.places) or (
        source.rstrip("/").endswith(DOCKER_SOCKET)
    ):
        findings.add(
            (who, "socket", where),
            f"{who} mounts the Docker socket ({source}): it can start a container with this "
            "server's root filesystem",
            refused=True,
        )
    elif any(place.name in RUNTIME_SOCKETS for place in reached.places):
        findings.add(
            (who, "socket", where),
            say("compose_guard.runtime_socket", who=who, source=source),
            refused=True,
        )
    elif exposure is not None:
        what, directory = exposure
        reason = (
            f"that contains {directory}, where Noust keeps its configuration and secrets"
            if what == "contains_noust"
            else _EXPOSURE_REASON[what]
        )
        via = f" (a link that leads to {reached.real})" if reached.link else ""
        findings.add(
            (who, "exposes", where),
            f"{who} mounts {source}{via}{as_a}: {reason}. The files of other applications and "
            "Noust's own are not for a container to read",
            refused=True,
            hard=door,
            link=reached.link_at,
        )
    elif reached.unresolved and reached.inside:
        findings.add(
            (who, "unresolved", source),
            say("compose_guard.path_unresolved", who=who, source=source),
            refused=True,
        )
    elif not reached.inside and reached.link:
        link, _, target = reached.link.partition(" leads to ")
        findings.add(
            (who, "outside", where),
            say("compose_guard.mount_link", who=who, source=source, link=link, target=target),
            refused=True,
            link=reached.link_at,
        )
    elif not reached.inside:
        findings.add(
            (who, "outside", where),
            f"{who} mounts {source} from this server{as_a}, outside the application",
            refused=True,
        )


def _judge_door(
    findings: ComposeFindings, who: str, source: str, context: GuardContext, *, key: str
) -> None:
    """
    Judge a path that is not mounted but read: an ``env_file``, a build context, an SSH key.

    Whatever the path leads to is read as root, by Compose or by the daemon, and for a build it
    is copied into an image the stack owns. Another application's files and Noust's own are
    refused with no exception that can allow it; anything else outside the application is
    refused for a new stack.

    Args:
        findings: Where the finding goes.
        who: ``service web`` or ``job migrate``.
        source: The path as written.
        context: What the guard knows of the application.
        key: The compose key that names it (``env_file``, ``build.context``).
    """
    reached = reach(source, context.base, context.app_path)
    if reached is None:
        return
    where = str(reached.places[0])
    exposure = _exposure(reached, context.app_path)
    if exposure is None and reached.unresolved and reached.inside:
        findings.add(
            (who, "unresolved", source),
            say("compose_guard.path_unresolved", who=who, source=source),
            refused=True,
        )
    elif exposure is not None:
        what, directory = exposure
        if what == "noust":
            sentence = say("compose_guard.door_noust", who=who, source=source, key=key)
        elif what == "contains_noust":
            sentence = say(
                "compose_guard.door_contains_noust",
                who=who,
                source=source,
                key=key,
                directory=directory,
            )
        elif what == "all_apps":
            sentence = say("compose_guard.door_all_apps", who=who, source=source, key=key)
        else:
            sentence = say("compose_guard.door_other_app", who=who, source=source, key=key)
        findings.add(
            (who, f"door:{key}", where),
            sentence,
            refused=True,
            hard=True,
            link=reached.link_at,
        )
    elif not reached.inside and reached.link:
        link, _, target = reached.link.partition(" leads to ")
        findings.add(
            (who, f"door:{key}", where),
            say(
                "compose_guard.door_link",
                who=who,
                source=source,
                key=key,
                link=link,
                target=target,
            ),
            refused=True,
            link=reached.link_at,
        )
    elif not reached.inside:
        findings.add(
            (who, f"door:{key}", where),
            say("compose_guard.door_outside", who=who, source=source, key=key),
            refused=True,
        )


def _leaves(path: str, context: GuardContext) -> tuple[bool, bool]:
    """
    Tell whether a file a compose file names for Compose to read leaves the application.

    The path is judged as the file system sees it: a link inside the application that leads
    out is a way out, which is what a lexical check lets through.

    Args:
        path: The file, as written.
        context: What the guard knows of the application.

    Returns:
        ``(leaves, hard)``: whether it is not inside the application (a path that cannot be
        followed, a NUL byte, is not a file of the application whatever Compose makes of it), and
        whether where it leads is Noust's state or another application's. A path made of
        variables is not judged here: the model Compose resolves says where it went.
    """
    reached = reach(path, context.base, context.app_path)
    if reached is None:
        return False, False
    if reached.unresolved:
        return True, False
    return not reached.inside, _exposure(reached, context.app_path) is not None


def _judge_download(
    findings: ComposeFindings,
    who: str,
    source: str,
    context: GuardContext,
    *,
    include: bool,
) -> None:
    """
    Judge a build context or an include that Docker or Compose would fetch from the network.

    Nothing on this server checks where such a request goes (``core/outbound.py`` is where one
    that Noust makes is checked): it is refused for a new stack, and for a pasted document, which
    has no repository to vouch for it, no exception allows it.

    Args:
        findings: Where the finding goes.
        who: ``service web``, or ``the compose file`` for an include.
        source: The context or the include as written.
        context: What the guard knows of the application.
        include: Whether it is an ``include`` (else a build context).
    """
    if include:
        sentence = say("compose_guard.remote_include", source=source)
    else:
        sentence = say("compose_guard.remote_context", who=who, source=source)
    findings.add((who, "remote", source), sentence, refused=True, hard=context.pasted)


def _items(value: Any) -> list[Any]:
    """Read a compose list that a hostile file may have written as anything (see :func:`items`)."""
    return items(value)


def _included(include: Any) -> Iterator[tuple[str, str]]:
    """
    List what a document's ``include`` makes Compose read.

    Args:
        include: The value of the top-level ``include``: paths, or mappings with a ``path`` that
            is one path or several, and an ``env_file`` and a ``project_directory`` of their own.

    Yields:
        ``(key, path)`` for each, the key being ``include`` for a file Compose reads and
        ``include.env_file`` or ``include.project_directory`` for what it reads beside it.
    """
    for entry in _items(include):
        if isinstance(entry, Mapping):
            for path in _items(entry.get("path")):
                yield "include", str(path)
            for env_file in _items(entry.get("env_file")):
                yield "include.env_file", str(env_file)
            directory = entry.get("project_directory")
            if isinstance(directory, str) and directory:
                yield "include.project_directory", directory
        else:
            yield "include", str(entry)


def _extended_file(service: Mapping[str, Any]) -> str | None:
    """
    Args:
        service: One entry of ``services`` or ``jobs``.

    Returns:
        The other file it ``extends`` from, or None when it extends nothing or a service of
        its own file.
    """
    extends = service.get("extends")
    if isinstance(extends, Mapping) and extends.get("file"):
        return str(extends["file"])
    return None


def uses_composition(document: Any) -> bool:
    """
    Tell whether a compose file brings in the text of another file.

    That is ``include`` and an ``extends`` of another file: what they bring is not in this
    document, so a failure to resolve it leaves it unjudged. A service of its own file is in
    the document and is judged as it stands.

    Args:
        document: The file, parsed.

    Returns:
        True when it includes or extends a file.
    """
    if not isinstance(document, Mapping):
        return False
    if _items(document.get("include")):
        return True
    for section, _word in _RUNNABLE:
        entries = document.get(section)
        if isinstance(entries, Mapping) and any(
            isinstance(entry, Mapping) and _extended_file(entry) for entry in entries.values()
        ):
            return True
    return False


def _door_sources(service: Mapping[str, Any]) -> Iterator[tuple[str, str, str]]:
    """
    List the paths of a service that Compose or the daemon reads without mounting them.

    Args:
        service: One entry of ``services`` or ``jobs``.

    Yields:
        ``(key, kind, value)``: the compose key that names it, what it is
        (:func:`~noust.deployers.helpers.compose_paths.kind_of`) and the value as written.
    """
    for entry in _items(service.get("env_file")):
        path = entry.get("path") if isinstance(entry, Mapping) else entry
        if isinstance(path, str) and path:
            yield "env_file", PATH, path
    build = service.get("build")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, Mapping):
        return
    context = build.get("context", ".")
    if isinstance(context, str) and context:
        yield "build.context", kind_of(context), context
    extra = build.get("additional_contexts")
    contexts = (
        list(extra.values())
        if isinstance(extra, Mapping)
        else [str(entry).partition("=")[2] for entry in _items(extra)]
    )
    for value in contexts:
        if isinstance(value, str) and value:
            yield "build.additional_contexts", kind_of(value), value
    ssh = build.get("ssh")
    keys = (
        list(ssh.values())
        if isinstance(ssh, Mapping)
        else [str(entry).partition("=")[2] for entry in _items(ssh)]
    )
    for value in keys:
        if isinstance(value, str) and value:
            yield "build.ssh", PATH, value


def _secret_references(service: Mapping[str, Any], section: str) -> list[Any]:
    """
    Args:
        service: One entry of ``services`` or ``jobs``.
        section: ``secrets`` or ``configs``.

    Returns:
        What the service names of that section, and for secrets what its build is given too: a
        build secret is a file of this server copied into the build.
    """
    found = _items(service.get(section))
    build = service.get("build")
    if section == "secrets" and isinstance(build, Mapping):
        found.extend(_items(build.get("secrets")))
    return found


def _judge_runnable(
    findings: ComposeFindings,
    document: Mapping[str, Any],
    who: str,
    service: Mapping[str, Any],
    context: GuardContext,
) -> None:
    """
    Judge one service or job of a compose document.

    Args:
        findings: Where the findings go.
        document: The whole document, which holds the files its secrets and configs name.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        context: What the guard knows of the application.
    """
    judge_settings(findings, who, service)
    for volume in _items(service.get("volumes")):
        source, kind = _volume_source(volume)
        if source is not None and kind == "bind":
            _judge_source(findings, who, source, context)
    for section, word in _FILE_SECTIONS:
        defined = document.get(section)
        for entry in _secret_references(service, section):
            reference = entry.get("source") if isinstance(entry, Mapping) else entry
            definition = defined.get(str(reference)) if isinstance(defined, Mapping) else None
            file = definition.get("file") if isinstance(definition, Mapping) else None
            if isinstance(file, str) and file:
                _judge_source(findings, who, file, context, as_a=f" as a {word}", door=True)
    for key, kind, value in _door_sources(service):
        if kind == REMOTE:
            _judge_download(findings, who, value, context, include=False)
        elif kind != OTHER:
            _judge_door(findings, who, value, context, key=key)
    extended = _extended_file(service)
    if extended is not None:
        _judge_extended(findings, who, extended, context)
    compose_owners.judge_service(findings, who, service, context)


def _judge_extended(
    findings: ComposeFindings, who: str, extended: str, context: GuardContext
) -> None:
    """
    Judge the other file a service extends.

    Args:
        findings: Where the finding goes.
        who: ``service web`` or ``job migrate``.
        extended: The file as written.
        context: What the guard knows of the application.
    """
    if kind_of(extended) == REMOTE:
        _judge_download(findings, who, extended, context, include=False)
        return
    leaves, hard = _leaves(extended, context)
    if leaves:
        findings.add(
            (who, "extends", extended),
            f"{who} extends a file outside the application ({extended}): its definitions, and "
            "the files they name, are not this application's to take",
            refused=True,
            hard=hard,
        )


def _judge_volumes(
    findings: ComposeFindings, document: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the volumes of the file that are a host path by another name.

    A volume of the ``local`` driver with ``driver_opts`` can be a bind mount of any directory
    (``o: bind``, ``device: /``) or a mount of a block device, and no service says so.

    Args:
        findings: Where the findings go.
        document: The compose file.
        context: What the guard knows of the application.
    """
    volumes = document.get("volumes")
    if not isinstance(volumes, Mapping):
        return
    for key, entry in volumes.items():
        options = entry.get("driver_opts") if isinstance(entry, Mapping) else None
        device = options.get("device") if isinstance(options, Mapping) else None
        if isinstance(device, str) and device.startswith(("/", "~", ".")):
            _judge_source(findings, f"volume {key}", device, context)


def inspect_compose(
    document: Any, app_path: Path, *, context: GuardContext | None = None
) -> ComposeFindings:
    """
    Sort what a parsed compose file, or the model Compose resolves it to, asks of the host.

    The same judgement serves both: the file as the repository wrote it, and what
    ``docker compose config --format json`` prints for it (:func:`resolve_model`), where an
    ``include`` or an ``extends`` has been merged, every service of every profile is present and
    a bind mount is a long entry with an absolute source.

    Args:
        document: The file, as ``yaml.safe_load`` returned it, or the resolved model.
        app_path: The application directory, the one tree a path may stay in.
        context: What else is known of the application: its project, who owns what, where a
            relative path is read from. Without it the file is judged on its own.

    Returns:
        The findings, each a sentence naming the service, or the job.
    """
    findings = ComposeFindings()
    if not isinstance(document, Mapping):
        return findings
    judged = replace(context, app_path=app_path) if context else GuardContext(app_path)
    for key, path in _included(document.get("include")):
        if key != "include":
            _judge_door(findings, "the compose file", path, judged, key=key)
        elif kind_of(path) == REMOTE:
            _judge_download(findings, "the compose file", path, judged, include=True)
        else:
            leaves, hard = _leaves(path, judged)
            if leaves:
                findings.add(
                    ("compose", "include", path),
                    f"the compose file includes {path}, which is outside the application: "
                    "Compose would read a file of this server that is not part of the repository",
                    refused=True,
                    hard=hard,
                )
    compose_owners.judge_project(findings, document, judged)
    compose_owners.judge_externals(findings, document, judged)
    _judge_volumes(findings, document, judged)
    for section, word in _RUNNABLE:
        entries = document.get(section)
        if not isinstance(entries, Mapping):
            continue
        for name, service in entries.items():
            if isinstance(service, Mapping):
                _judge_runnable(findings, document, f"{word} {name}", service, judged)
    return findings


def merge_findings(file: ComposeFindings, resolved: ComposeFindings) -> ComposeFindings:
    """
    Add to what a file shows what only the model Compose resolves it to shows.

    Args:
        file: The findings of the file as written.
        resolved: The findings of the resolved model.

    Returns:
        The file's findings, in their own words, and after them what the model adds: a thing
        found in both is said once, with the file's sentence, which is the one an operator can
        find in the repository.
    """
    merged = ComposeFindings(
        list(file.refused),
        list(file.warned),
        dict(file.keys),
        set(file.hard),
        dict(file.said),
        dict(file.links),
    )
    for sentences, refused in ((resolved.refused, True), (resolved.warned, False)):
        for sentence in sentences:
            key = resolved.keys.get(sentence)
            said = resolved.said.get(sentence)
            if key is None:
                (merged.refused if refused else merged.warned).append(sentence)
                if sentence in resolved.hard:
                    merged.hard.add(sentence)
            else:
                merged.add(
                    key,
                    said if said is not None else sentence,
                    refused=refused,
                    hard=sentence in resolved.hard,
                    link=resolved.links.get(sentence, ("",))[0],
                )
    return merged


def _variable_paths(document: Any) -> bool:
    """
    Tell whether a compose file names a path of this server through a variable.

    Args:
        document: The file, parsed.

    Returns:
        True when a mount, a file a secret or a config reads, a path Compose or the daemon reads
        (an ``env_file``, a build context), an ``include`` or a ``device`` of a volume has a ``$``
        in it: where it leads is what the variable's value says, which only the model Compose
        resolves with the stack's own ``.env`` can tell.
    """
    if not isinstance(document, Mapping):
        return False
    if any("$" in path for _key, path in _included(document.get("include"))):
        return True
    volumes = document.get("volumes")
    if isinstance(volumes, Mapping):
        for entry in volumes.values():
            options = entry.get("driver_opts") if isinstance(entry, Mapping) else None
            device = options.get("device") if isinstance(options, Mapping) else None
            if isinstance(device, str) and device.startswith("$"):
                return True
    for section, _word in _RUNNABLE:
        entries = document.get(section)
        for service in entries.values() if isinstance(entries, Mapping) else ():
            if not isinstance(service, Mapping):
                continue
            for volume in _items(service.get("volumes")):
                source, kind = _volume_source(volume)
                if source is not None and kind == "bind" and "$" in source:
                    return True
            if any("$" in value for _key, kind, value in _door_sources(service) if kind == PATH):
                return True
            extended = _extended_file(service)
            if extended is not None and "$" in extended:
                return True
            for file_section, _kind in _FILE_SECTIONS:
                defined = document.get(file_section)
                for entry in _secret_references(service, file_section):
                    reference = entry.get("source") if isinstance(entry, Mapping) else entry
                    definition = (
                        defined.get(str(reference)) if isinstance(defined, Mapping) else None
                    )
                    file = definition.get("file") if isinstance(definition, Mapping) else None
                    if isinstance(file, str) and "$" in file:
                        return True
    return False


def with_resolved(
    findings: ComposeFindings,
    document: Any,
    runner: CommandRunner,
    commands: Sequence[Sequence[str]],
    app_path: Path,
    *,
    context: GuardContext | None = None,
    final: bool = False,
) -> tuple[ComposeFindings, str]:
    """
    Add to the findings of a file what only the model Compose resolves it to shows.

    Args:
        findings: What the file as written asks of the host.
        document: The file, parsed.
        runner: Runs ``docker compose``.
        commands: The commands :func:`resolve_model` tries.
        app_path: The application directory, where they run.
        context: What the guard knows of the application, for judging the model as the file.
        final: The ``.env`` the stack runs with is written: a path made of variables that Compose
            still cannot resolve is then refused rather than guessed.

    Returns:
        The findings, and a sentence for the log when Compose could not resolve a stack that
        brings nothing in (it is judged as written). A file that includes or extends another
        and cannot be resolved is refused: what it brings in is what cannot be judged.
    """
    model, problem = resolve_model(runner, commands, app_path)
    if model is not None:
        return merge_findings(findings, inspect_compose(model, app_path, context=context)), ""
    if uses_composition(document):
        findings.add(
            ("compose", "unresolved", ""),
            "the compose file includes or extends another file and Docker Compose could not "
            f"resolve the stack ({problem}), so what it brings in cannot be judged",
            refused=True,
        )
        return findings, ""
    if final and _variable_paths(document):
        findings.add(
            ("compose", "unresolved_paths", ""),
            say("compose_guard.paths_unresolved", problem=problem),
            refused=True,
        )
        return findings, ""
    return findings, (
        f"the stack could not be resolved by 'docker compose config' ({problem}); it is judged "
        "as written"
    )


def resolve_model(
    runner: CommandRunner, commands: Sequence[Sequence[str]], cwd: Path
) -> tuple[dict[str, Any] | None, str]:
    """
    Ask Compose for the stack as it resolves it, trying each command until one answers.

    The output holds the stack's secrets: it is parsed and dropped, never logged, and nothing
    of it is in what this says when it fails.

    Args:
        runner: Runs the commands.
        commands: ``docker compose ... config --format json`` argvs, the one that asks for the
            most (every profile) first: a Compose that does not know the flag falls to the next.
        cwd: The directory they run in.

    Returns:
        The model and an empty sentence, or None and one line saying why there is none.
    """
    problem = "docker compose was not asked"
    for argv in commands:
        result = runner.run(list(argv), cwd=cwd, timeout=RESOLVE_TIMEOUT)
        if not result.success:
            first = next((line.strip() for line in result.stderr.splitlines() if line.strip()), "")
            problem = f"exit code {result.exit_code}" + (f": {first[:200]}" if first else "")
            continue
        try:
            model = json.loads(result.stdout)
        except ValueError:
            problem = "it printed something that is not a configuration"
            continue
        if isinstance(model, dict):
            return model, ""
        problem = "it printed something that is not a configuration"
    return None, problem
