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

import yaml

from noust.core.compose_yaml import load_compose
from noust.core.runner import CommandRunner
from noust.core.server_text import ServerText, say
from noust.deployers.helpers import compose_keys, compose_owners
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

#: The files Compose reads and folds into what it prints, leaving no path in the model it prints.
_FOLDED_DOORS = ("env_file", "label_file")

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
        doors: For each service whose ``env_file`` or ``label_file`` is named through a variable, which
            of them: Compose folds what those files hold into the model it prints, so where the
            variable led is only seen in a path it keeps (``config --no-env-resolution``), and a
            service whose path it did not keep is refused (:func:`with_resolved`).
    """

    refused: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)
    keys: dict[str, FindingKey] = field(default_factory=dict, repr=False, compare=False)
    hard: set[str] = field(default_factory=set, repr=False, compare=False)
    said: dict[str, ServerText] = field(default_factory=dict, repr=False, compare=False)
    links: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False, compare=False)
    doors: dict[str, list[str]] = field(default_factory=dict, repr=False, compare=False)

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
        closed_keys: Refuse the keys the guard does not know
            (:mod:`~noust.deployers.helpers.compose_keys`). It is asked of the files as a person
            wrote them, and not of the model Compose prints, which has no key that those files do
            not and may spell its own.
    """

    app_path: Path
    project_dir: Path | None = None
    domain: str = ""
    project: str = ""
    env_project: str = ""
    owners: Owners | None = None
    pasted: bool = False
    closed_keys: bool = True

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


def _trees_of(directory: str | Path) -> list[PurePosixPath]:
    """
    Args:
        directory: A directory, as a store or a setting records it.

    Returns:
        Where it is under the name it has and where a link of an alias (a NAS's shared folder)
        really leads: the daemon takes a path for either.
    """
    forms = [PurePosixPath(posixpath.normpath(str(directory)))]
    forms.extend(form for form in [followed(directory)] if form is not None)
    return forms


def _other_trees(context: GuardContext) -> list[PurePosixPath]:
    """
    List the trees of the other applications, as the store registered them.

    An application does not have to live under the applications directory: an adopted stack runs
    where it already was (``/opt/billing``), and what the store says is another application's is
    another application's whatever its path looks like.

    Args:
        context: What the guard knows of the application, whose ``owners`` hold the store's list.

    Returns:
        The tree of every registered application but this one, each as :func:`_trees_of` gives it.
    """
    if context.owners is None:
        return []
    trees: list[PurePosixPath] = []
    for record in context.owners.index.registry.apps:
        if record.domain == context.owners.domain or not record.path:
            continue
        trees.extend(form for form in _trees_of(record.path) if form not in trees)
    return trees


def _exposure(reached: Reached, context: GuardContext) -> tuple[str, str] | None:
    """
    Say whether a path hands over Noust's state or another application.

    Every place the daemon may take the path for is judged, not the one it is spelled as: a link
    of the repository that leads to ``/etc/noust`` is ``/etc/noust``. Another application is what
    the store says is one (:func:`_other_trees`) and, for a tree the store does not know, whatever
    is under the applications directory.

    Args:
        reached: Where the path leads.
        context: What the guard knows of the application: its tree, and the store's list of the
            others.

    Returns:
        ``(what, directory)``: ``noust`` (it is Noust's own), ``contains_noust`` (it holds
        ``directory``), ``all_apps`` or ``other_app``; None when it is neither Noust's nor another
        application's.
    """
    from noust.core.config import Config

    roots = _noust_roots()
    own = _trees_of(context.app_path)
    apps = _trees_of(Config().apps_directory)
    others = _other_trees(context)
    for place in reached.places:
        for directory in roots:
            if within(place, directory):
                return "noust", str(directory)
            if place in directory.parents:
                return "contains_noust", str(directory)
        if any(within(place, app) for app in own):
            continue
        if any(within(directory, place) for directory in apps) or any(
            place in directory.parents for directory in others
        ):
            return "all_apps", ""
        if any(directory in place.parents for directory in apps) or any(
            within(place, tree) for tree in others
        ):
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
    exposure = _exposure(reached, context)
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
    exposure = _exposure(reached, context)
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
    return not reached.inside, _exposure(reached, context) is not None


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
    # The labels of a file are read by Compose as root and show in ``docker inspect``, which the
    # console shows to whoever may look at the container: a file read, like an env_file.
    for entry in _items(service.get("label_file")):
        if isinstance(entry, str) and entry:
            yield "label_file", PATH, entry
    build = service.get("build")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, Mapping):
        return
    context = build.get("context", ".")
    if isinstance(context, str) and context:
        yield "build.context", kind_of(context), context
        dockerfile = build.get("dockerfile")
        # The Dockerfile is read from the context (a relative name is the context's) and the client
        # may be told any path: one that leaves the application is a file of this server read into
        # the build. Of a context that is downloaded it is the download's.
        if isinstance(dockerfile, str) and dockerfile and kind_of(context) == PATH:
            yield "build.dockerfile", PATH, posixpath.join(context, dockerfile)
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
    *,
    section: str = "service",
    walk: _Walk | None = None,
) -> None:
    """
    Judge one service or job of a compose document.

    Args:
        findings: Where the findings go.
        document: The whole document, which holds the files its secrets and configs name.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        context: What the guard knows of the application.
        section: What the entry is in the table of keys: ``service``, or ``pre_start`` for a container
            a service runs to completion before it starts.
        walk: Where the guard is among the files the document brings in.
    """
    walk = walk or _Walk()
    judge_settings(findings, who, service, check_keys=context.closed_keys, section=section)
    for volume in _items(service.get("volumes")):
        if context.closed_keys:
            compose_keys.judge_mount(findings, who, volume)
        source, kind = _volume_source(volume)
        if source is not None and kind == "bind":
            _judge_source(findings, who, source, context)
    for file_section, word in _FILE_SECTIONS:
        defined = document.get(file_section)
        for entry in _secret_references(service, file_section):
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
            if key in _FOLDED_DOORS and "$" in value and key not in findings.doors.get(who, ()):
                findings.doors.setdefault(who, []).append(key)
    _judge_cache(findings, who, service, context)
    extends = service.get("extends")
    if context.closed_keys and isinstance(extends, Mapping):
        compose_keys.judge_keys(findings, who, "extends", extends, path="extends")
    extended = _extended_file(service)
    if extended is not None:
        _judge_extended(findings, who, extended, context)
        if isinstance(extends, Mapping):
            _judge_extended_service(findings, who, extends, context, walk)
    compose_owners.judge_service(findings, who, service, context)
    if section == "service":
        for number, container in enumerate(_items(service.get("pre_start")), start=1):
            if isinstance(container, Mapping):
                _judge_runnable(
                    findings,
                    document,
                    f"{who} pre_start {number}",
                    container,
                    context,
                    section="pre_start",
                    walk=walk,
                )


def _cache_places(build: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """
    List the directories of this server that a build's cache is read from and written to.

    Args:
        build: The ``build`` mapping of a service.

    Yields:
        ``(key, path)`` for each ``type=local`` entry of ``cache_from`` (its ``src``) and
        ``cache_to`` (its ``dest``). The other kinds (an image, a registry, a service of the cloud)
        are not a path of this server.
    """
    for key, option in (("cache_from", "src"), ("cache_to", "dest")):
        for entry in _items(build.get(key)):
            options = dict(
                part.partition("=")[::2] for part in str(entry).split(",") if "=" in part
            )
            path = options.get(option, "")
            if options.get("type") == "local" and path:
                yield f"build.{key}", path


def _judge_cache(
    findings: ComposeFindings, who: str, service: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the directories a build's cache lives in.

    BuildKit reads and writes a local cache as root, so a path that leaves the application is a place of
    this server read into a build or written by it. A relative one is the application's whichever
    directory the command runs from: it is judged from both.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        context: What the guard knows of the application.
    """
    build = service.get("build")
    if not isinstance(build, Mapping):
        return
    for key, path in _cache_places(build):
        _judge_door(findings, who, path, context, key=key)
        if not posixpath.isabs(path):
            _judge_door(
                findings, who, posixpath.join(str(context.app_path), path), context, key=key
            )


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


#: Docker's own drivers for a network the stack makes: the isolated bridge (and swarm's overlay). A
#: network of another driver (``host``, ``macvlan`` and ``ipvlan`` reach the machine's network cards,
#: a plugin is a program Noust has not heard of) is not isolated from this server.
_OWN_NETWORK_DRIVERS = frozenset({"", "bridge", "overlay", "none"})

#: The option that names the bridge of the machine a network is attached to instead of making one.
_BRIDGE_NAME = "com.docker.network.bridge.name"

#: The one driver of a volume Docker has: ``local``. Another is a plugin, and what its options name
#: (``mountpoint``, ``path``, ``source``) is whatever that program reads.
_OWN_VOLUME_DRIVERS = frozenset({"", "local"})


def _judge_volumes(
    findings: ComposeFindings, document: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the volumes of the file that are a host path by another name, and the ones a plugin makes.

    A volume of the ``local`` driver with ``driver_opts`` can be a bind mount of any directory
    (``o: bind``, ``device: /``) or a mount of a block device, and no service says so. A volume of
    another driver is made by a program Noust does not know, whose options can name any place.

    Args:
        findings: Where the findings go.
        document: The compose file.
        context: What the guard knows of the application.
    """
    volumes = document.get("volumes")
    if not isinstance(volumes, Mapping):
        return
    for key, entry in volumes.items():
        if not isinstance(entry, Mapping):
            continue
        who = f"volume {key}"
        if context.closed_keys:
            compose_keys.judge_keys(findings, who, "volume", entry)
        driver = str(entry.get("driver") or "").strip().lower()
        if driver not in _OWN_VOLUME_DRIVERS and not compose_owners.is_external(entry):
            findings.add(
                (who, "volume_driver", driver),
                say("compose_guard.volume_driver", who=who, driver=driver),
                refused=True,
            )
        options = entry.get("driver_opts")
        device = None
        for name, value in options.items() if isinstance(options, Mapping) else ():
            if str(name).lower() == "device":
                device = value
        if isinstance(device, str) and device.startswith(("/", "~", ".")):
            _judge_source(findings, who, device, context)


def _judge_networks(
    findings: ComposeFindings, document: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the networks of the file: which driver makes them and where they are attached.

    Args:
        findings: Where the findings go.
        document: The compose file.
        context: What the guard knows of the application.
    """
    networks = document.get("networks")
    if not isinstance(networks, Mapping):
        return
    for key, entry in networks.items():
        if not isinstance(entry, Mapping):
            continue
        who = f"network {key}"
        if context.closed_keys:
            compose_keys.judge_keys(findings, who, "network", entry)
        if compose_owners.is_external(entry):
            continue
        driver = str(entry.get("driver") or "").strip().lower()
        if driver not in _OWN_NETWORK_DRIVERS:
            findings.add(
                (who, "network_driver", driver),
                say("compose_guard.network_driver", who=who, driver=driver),
                refused=True,
            )
        options = entry.get("driver_opts")
        for name, value in options.items() if isinstance(options, Mapping) else ():
            if str(name).lower() == _BRIDGE_NAME and str(value or "").strip():
                findings.add(
                    (who, "network_bridge", str(value)),
                    say("compose_guard.network_bridge", who=who, bridge=value),
                    refused=True,
                )


def _judge_definitions(
    findings: ComposeFindings, document: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the other maps of the file: the keys of its secrets, configs and models.

    The files a secret or a config names are judged where a service uses them. What a model runner
    is given as flags is a program's options, on this server.

    Args:
        findings: Where the findings go.
        document: The compose file.
        context: What the guard knows of the application.
    """
    for section, word in (("secrets", "secret"), ("configs", "config"), ("models", "model")):
        entries = document.get(section)
        if not isinstance(entries, Mapping):
            continue
        for key, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            who = f"{word} {key}"
            if context.closed_keys:
                compose_keys.judge_keys(findings, who, word, entry)
            if word == "model" and entry.get("runtime_flags"):
                findings.add(
                    (who, "model_flags", ""),
                    say("compose_guard.model_flags", who=who),
                    refused=True,
                )


#: How far a file may bring in others: how many in all, how deep, and how large each may be.
#: Compose has no such limit, and a repository that nests files past any budget is one that
#: cannot be judged, which is refused.
MAX_COMPOSED_FILES = 32
MAX_COMPOSED_DEPTH = 8
MAX_COMPOSED_BYTES = 1024 * 1024

#: The sentence that says why a file a document brings in was refused, by the reason.
_NOT_READ = {
    "not_a_file": "compose_guard.composition_not_a_file",
    "unreadable": "compose_guard.composition_unreadable",
    "too_large": "compose_guard.composition_too_large",
    "too_deep": "compose_guard.composition_too_deep",
}


@dataclass
class _Walk:
    """
    Where the guard is among the files a document brings in.

    Attributes:
        budget: How many more files may be read, shared by the whole walk.
        depth: How many files it is inside of.
        reading: The real paths of the files it is inside of, so that a file that includes itself
            is read once.
    """

    budget: list[int] = field(default_factory=lambda: [MAX_COMPOSED_FILES])
    depth: int = 0
    reading: frozenset[str] = frozenset()

    def into(self, real: Path) -> _Walk:
        """The walk inside one more file."""
        return _Walk(self.budget, self.depth + 1, self.reading | {str(real)})


def _load_composed(real: Path) -> tuple[Mapping[str, Any] | None, str]:
    """
    Read a file a compose document brings in.

    Args:
        real: Where it is, with every link followed, inside the application.

    Returns:
        The document, or None and why not, as the end of the code of the sentence that says it
        (``not_a_file``, ``too_large``, ``unreadable``). The reason is never what the file says: a
        file that is not a compose file may be anything, a ``.env`` included, and it is shown.
    """
    try:
        if not real.is_file():
            return None, "not_a_file"
        if real.stat().st_size > MAX_COMPOSED_BYTES:
            return None, "too_large"
        document = load_compose(real.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError, ValueError):
        return None, "unreadable"
    if not isinstance(document, Mapping):
        return None, "unreadable"
    return document, ""


def _composed(
    findings: ComposeFindings, key: str, path: str, context: GuardContext, walk: _Walk
) -> tuple[Mapping[str, Any], Path] | None:
    """
    Read the file an ``include`` or an ``extends`` brings in, so that what it says is judged too.

    The model Compose prints has already folded some of what these files say into other keys (an
    ``env_file`` into ``environment``, where no path is left to judge), so the files are read as they
    are, with the directory they are in as the base of their relative paths. A file that leaves the
    application is not read: the finding of the line that names it refuses it, and reading is how it
    would leak. What cannot be read is refused.

    Args:
        findings: Where the findings go.
        key: ``include`` or ``extends``, for the sentence.
        path: The file as the document writes it.
        context: What the guard knows of the application, with the base of this document.
        walk: Where the guard is among the files.

    Returns:
        The file's document and where it really is; None when there is nothing to judge (a path made
        of variables, a download, one outside the application, one already being read) or when it
        was refused.
    """
    if kind_of(path) != PATH or "$" in path:
        return None
    reached = reach(path, context.base, context.app_path)
    if reached is None or reached.unresolved or not reached.inside or reached.real is None:
        return None
    real = Path(str(reached.real))
    if str(real) in walk.reading:
        return None
    problem = "too_deep"
    if walk.depth < MAX_COMPOSED_DEPTH and walk.budget[0] > 0:
        walk.budget[0] -= 1
        document, problem = _load_composed(real)
        if document is not None:
            return document, real
    findings.add(
        ("compose", "unreadable", f"{key}:{path}"),
        say(_NOT_READ[problem], path=path, key=key),
        refused=True,
    )
    return None


def _base_of(
    context: GuardContext, real: Path, project_directory: str | None = None
) -> GuardContext:
    """
    Args:
        context: The guard's knowledge of the document that brings a file in.
        real: The file it brings in, with every link followed.
        project_directory: The ``project_directory`` of the ``include`` that brings it in.

    Returns:
        The same, with the directory the file's relative paths are read from: its own, or the
        ``project_directory`` the include names when that stays in the application.
    """
    directory = real.parent
    if project_directory:
        reached = reach(project_directory, context.base, context.app_path)
        if (
            reached is not None
            and reached.real is not None
            and reached.inside
            and not reached.unresolved
        ):
            directory = Path(str(reached.real))
    return replace(context, project_dir=directory)


def _judge_extended_service(
    findings: ComposeFindings,
    who: str,
    extends: Mapping[str, Any],
    context: GuardContext,
    walk: _Walk,
) -> None:
    """
    Judge the service a service takes its definition from in another file.

    Only that service is judged, as the one the extending service is made of: what is in the same
    file beside it is not part of the stack.

    Args:
        findings: Where the findings go.
        who: The extending service, ``service web``.
        extends: Its ``extends`` mapping.
        context: What the guard knows of the application.
        walk: Where the guard is among the files.
    """
    composed = _composed(findings, "extends", str(extends.get("file")), context, walk)
    if composed is None:
        return
    document, real = composed
    for section, _word in _RUNNABLE:
        entries = document.get(section)
        entry = entries.get(str(extends.get("service"))) if isinstance(entries, Mapping) else None
        if isinstance(entry, Mapping):
            _judge_runnable(
                findings, document, who, entry, _base_of(context, real), walk=walk.into(real)
            )
            return


def _inspect_into(
    findings: ComposeFindings,
    document: Mapping[str, Any],
    context: GuardContext,
    walk: _Walk,
    *,
    main: bool,
) -> None:
    """
    Judge a document, and every file it brings in, into one set of findings.

    Args:
        findings: Where the findings go.
        document: The file, parsed.
        context: What the guard knows of the application, with this document's directory as the
            base of its relative paths.
        walk: Where the guard is among the files.
        main: Whether it is the stack's own file: the project it names is the stack's, and a file it
            includes has none of its own.
    """
    if context.closed_keys:
        compose_keys.judge_keys(findings, "the compose file", "top", document)
    for key, path in _included(document.get("include")):
        if key != "include":
            _judge_door(findings, "the compose file", path, context, key=key)
        elif kind_of(path) == REMOTE:
            _judge_download(findings, "the compose file", path, context, include=True)
        else:
            leaves, hard = _leaves(path, context)
            if leaves:
                findings.add(
                    ("compose", "include", path),
                    f"the compose file includes {path}, which is outside the application: "
                    "Compose would read a file of this server that is not part of the repository",
                    refused=True,
                    hard=hard,
                )
    for entry in _items(document.get("include")):
        directory = None
        if isinstance(entry, Mapping):
            if context.closed_keys:
                compose_keys.judge_keys(
                    findings, "the compose file", "include", entry, path="include"
                )
            paths = [str(path) for path in _items(entry.get("path"))]
            named = entry.get("project_directory")
            directory = named if isinstance(named, str) and named else None
        else:
            paths = [str(entry)]
        for path in paths:
            composed = _composed(findings, "include", path, context, walk)
            if composed is not None:
                included, real = composed
                _inspect_into(
                    findings,
                    included,
                    _base_of(context, real, directory),
                    walk.into(real),
                    main=False,
                )
    if main:
        compose_owners.judge_project(findings, document, context)
    compose_owners.judge_externals(findings, document, context)
    _judge_volumes(findings, document, context)
    _judge_networks(findings, document, context)
    _judge_definitions(findings, document, context)
    for section, word in _RUNNABLE:
        entries = document.get(section)
        if not isinstance(entries, Mapping):
            continue
        for name, service in entries.items():
            if isinstance(service, Mapping):
                _judge_runnable(findings, document, f"{word} {name}", service, context, walk=walk)


def inspect_compose(
    document: Any, app_path: Path, *, context: GuardContext | None = None
) -> ComposeFindings:
    """
    Sort what a parsed compose file, or the model Compose resolves it to, asks of the host.

    The same judgement serves both: the file as the repository wrote it, and what
    ``docker compose config --format json`` prints for it (:func:`resolve_model`), where an
    ``include`` or an ``extends`` has been merged, every service of every profile is present and
    a bind mount is a long entry with an absolute source. The files the document brings in are
    read and judged as well (what Compose folds into the model leaves no path to judge), each from
    its own directory.

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
    _inspect_into(findings, document, judged, _Walk(), main=True)
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
        {who: list(keys) for who, keys in file.doors.items()},
    )
    for who, keys in resolved.doors.items():
        merged.doors.setdefault(who, []).extend(k for k in keys if k not in merged.doors[who])
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
        # The model's own keys are Compose's: a key of it is a key of a file that was judged as written.
        printed = (
            replace(context, closed_keys=False)
            if context
            else GuardContext(app_path, closed_keys=False)
        )
        merged = merge_findings(findings, inspect_compose(model, app_path, context=printed))
        # Compose reads the env_file of a service and puts its lines in ``environment``, leaving no path
        # to judge; asked not to, a Compose that honours the flag keeps the files the stack names, as
        # absolute paths. One that does not know it, or keeps nothing, is judged by the files it was read
        # from, and a path made of a variable that no model shows is refused as unseen: that is what
        # leaves no door whichever Compose is installed.
        raw, _unasked = resolve_model(runner, _without_env_resolution(commands), app_path)
        if raw is not None:
            merged = merge_findings(merged, inspect_compose(raw, app_path, context=printed))
        _judge_unseen_doors(merged, raw)
        return merged, ""
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


def _door_kept(model: Mapping[str, Any] | None, who: str, key: str) -> bool:
    """
    Args:
        model: What Compose printed when asked to keep the paths of the files it folds, or None.
        who: ``service web`` or ``job migrate``.
        key: ``env_file`` or ``label_file``.

    Returns:
        Whether the model has a path under that key for that service.
    """
    word, _, name = who.partition(" ")
    section = {"service": "services", "job": "jobs"}.get(word, "")
    entries = model.get(section) if model is not None else None
    entry = entries.get(name) if isinstance(entries, Mapping) else None
    return isinstance(entry, Mapping) and bool(entry.get(key))


def _judge_unseen_doors(findings: ComposeFindings, model: Mapping[str, Any] | None) -> None:
    """
    Refuse the file a variable names that no model shows the path of.

    Where a variable led is a fact of the stack's ``.env`` and not of the file, and an ``env_file`` that
    Compose has read leaves nothing but its lines in the model. Asked to keep the paths
    (``--no-env-resolution``), a Compose that honours the flag prints them and they are judged like
    any other; one that does not know it (the 2.17 and 2.19 of Debian 12 and Synology), or knows it
    and keeps none (2.40.3), leaves the path unseen, and what cannot be seen cannot be judged.

    Args:
        findings: What was found; where the refusal goes.
        model: What Compose printed when asked to keep the paths of the files it folds, or None.
    """
    for who, keys in findings.doors.items():
        for key in keys:
            if not _door_kept(model, who, key):
                findings.add(
                    (who, "unseen_door", key),
                    say("compose_guard.door_unseen", who=who, key=key),
                    refused=True,
                )


def _without_env_resolution(commands: Sequence[Sequence[str]]) -> list[list[str]]:
    """
    Args:
        commands: ``docker compose ... config --format json`` argvs.

    Returns:
        The same commands asked not to resolve the ``env_file`` of the services
        (``config --no-env-resolution``), which keep their paths in the model that way.
    """
    asked: list[list[str]] = []
    for argv in commands:
        words = list(argv)
        if "config" in words:
            words.insert(words.index("config") + 1, "--no-env-resolution")
            asked.append(words)
    return asked


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
