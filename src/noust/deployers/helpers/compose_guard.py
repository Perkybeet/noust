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

:func:`inspect_compose` reads a parsed compose file and sorts what it finds:
``privileged: true`` and a mount of ``docker.sock`` are refused for a new
deployment unless an operator recorded an exception for the application
(:func:`noust.deployers.helpers.sandbox.set_compose_exception`, with a reason);
a stack proven to run with them already (the compose file of the commit that
served, the file a redeploy replaces, containers an adoption found running
unchanged from the file) is warned about, never stopped, and anything an
update, a redeploy or an adoption adds is refused like a new stack's.

A stack is also kept to its own files. A bind mount of Noust's own state
(``/etc/noust``, ``/var/lib/noust``, the backups: the console's signing key, the
store, the audit key), of what contains it (``/``, ``/etc``, ``/var``), or of
another application's tree (or of the directory that holds them all) hands the
container the secrets of everything else on the server, which is what the
account an application runs as exists to prevent for the applications that
have one. Those are refused like ``privileged``, with the same recorded
exception as the way out. Paths are resolved before they are judged: ``..``
and a relative source that climbs out of the application are the other
application's directory, whatever they look like.

The host's namespaces (``pid``, ``network_mode``, ``ipc``, ``userns_mode: host``),
added capabilities, devices, disabled confinement and bind mounts of any other
host path outside the application are warned about: some stacks need them, and
the operator should know which do.

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
import os
import posixpath
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core.compose_yaml import load_compose
from noust.core.runner import CommandRunner

#: A mount whose source ends like this is the Docker daemon's API.
DOCKER_SOCKET = "docker.sock"

#: ``security_opt`` values that switch the container's confinement off.
_UNCONFINED = (
    "apparmor:unconfined",
    "apparmor=unconfined",
    "seccomp:unconfined",
    "seccomp=unconfined",
    "label:disable",
    "label=disable",
    "systempaths=unconfined",
)

#: Namespaces a service can share with the host.
_HOST_NAMESPACES = ("pid", "network_mode", "ipc", "userns_mode", "uts", "cgroup")

#: Namespaces a service can join of another container, by ``container:<name>``.
_JOINABLE_NAMESPACES = ("pid", "network_mode", "ipc", "userns_mode")

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
        refused: Root on the host: refused for a new stack without an
            exception.
        warned: Worth knowing: said in the log and allowed.
        keys: For each sentence above, what it is a finding of, so that the file and the model
            Compose resolves it to are not both reported (:func:`merge_findings`).
    """

    refused: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)
    keys: dict[str, FindingKey] = field(default_factory=dict, repr=False, compare=False)

    def __bool__(self) -> bool:
        """True when anything was found."""
        return bool(self.refused or self.warned)

    def add(self, key: FindingKey, sentence: str, *, refused: bool) -> None:
        """
        Record a finding once.

        Args:
            key: What it is a finding of.
            sentence: What is said of it.
            refused: Whether it is root on the host (else it is only worth knowing).
        """
        if key in self.keys.values():
            return
        self.keys[sentence] = key
        (self.refused if refused else self.warned).append(sentence)


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


def _resolved(source: str, app_path: Path) -> PurePosixPath | None:
    """
    Turn a bind mount's source into the absolute path Docker will mount.

    ``..`` and ``.`` are collapsed first, the way the daemon sees the path: a
    source of ``/var/www/apps/shop/../other`` is ``other``'s directory, and
    ``../other`` is too when the application is ``shop``. A relative source is
    relative to the application, as ``docker compose`` reads it.

    Args:
        source: The source as written.
        app_path: The application directory.

    Returns:
        The path, or None for a source that starts with ``~`` or ``$``, which
        the shell or the compose file's variables decide and no text shows.
    """
    if source.startswith(("~", "$")):
        return None
    joined = source if source.startswith("/") else posixpath.join(str(app_path), source)
    return PurePosixPath(posixpath.normpath(joined))


def _outside(source: str, app_path: Path) -> bool:
    """
    Tell whether a bind mount's source is a host path outside the application.

    Args:
        source: The source as written.
        app_path: The application directory.

    Returns:
        True for a path (or one from a home directory, or a variable) that is
        not the application directory nor inside it, once ``..`` is resolved.
    """
    path = _resolved(source, app_path)
    if path is None:
        return True
    root = PurePosixPath(posixpath.normpath(str(app_path)))
    return not (path == root or root in path.parents)


def _noust_roots() -> tuple[PurePosixPath, ...]:
    """
    List the directories where Noust keeps what must not leave the host.

    Returns:
        The configuration, the store and the backups, under their names and under the
        old ones a link may still stand at.
    """
    from noust.core import paths, retired_names

    return tuple(
        PurePosixPath(str(directory))
        for directory in (
            paths.config_dir(),
            paths.state_dir(),
            paths.backup_dir(),
            *retired_names.old_data_roots(),
        )
    )


def _exposes(source: str, app_path: Path) -> str | None:
    """
    Say whether a bind mount hands over Noust's state or another application.

    Args:
        source: The source as written.
        app_path: The application directory.

    Returns:
        The reason the mount is root-equivalent for everything else on the
        server, or None when it is neither Noust's nor another application's.
    """
    from noust.core.config import Config

    path = _resolved(source, app_path)
    if path is None:
        return None
    for directory in _noust_roots():
        if path == directory or directory in path.parents:
            return "that is Noust's own configuration and secrets"
        if path in directory.parents:
            return f"that contains {directory}, where Noust keeps its configuration and secrets"
    apps = PurePosixPath(posixpath.normpath(str(Config().apps_directory)))
    app = PurePosixPath(posixpath.normpath(str(app_path)))
    if path == app or app in path.parents:
        return None
    if path == apps or path in apps.parents:
        return "that contains every application's files, the other applications' included"
    if apps in path.parents:
        return "that is another application's files, which it must not read"
    return None


def _items(value: Any) -> list[Any]:
    """
    Read a compose list that a hostile file may have written as anything.

    Args:
        value: What the key held: a list, a scalar or nothing.

    Returns:
        Its entries; a scalar is the one entry, and a mapping (whose entries are not the list a
        compose file means) or nothing is none.
    """
    if value is None or isinstance(value, Mapping):
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _leaves(path: str, app_path: Path) -> bool:
    """
    Tell whether a file a compose file names for Compose to read is outside the application.

    The path is judged as the file system sees it: a link inside the application that leads
    out is a way out, which is what a lexical check lets through.

    Args:
        path: The file, as written.
        app_path: The application directory.

    Returns:
        True when it is not inside the application, and for text that cannot be a path at all
        (a NUL byte): such a thing is not a file of the application, whatever Compose makes of
        it. A path made of variables is False here: the model Compose resolves says where it
        went.
    """
    if "$" in path or not path:
        return False
    if "\x00" in path:
        return True
    joined = path if os.path.isabs(path) else os.path.join(app_path, path)
    try:
        root = os.path.realpath(app_path)
        real = os.path.realpath(joined)
    except (OSError, ValueError):
        return True
    return not (real == root or real.startswith(root + os.sep))


def _included(include: Any) -> Iterator[str]:
    """
    List the files a document's ``include`` makes Compose read.

    Args:
        include: The value of the top-level ``include``: paths, or mappings with a ``path`` that
            is one path or several.

    Yields:
        Each path as written.
    """
    for entry in _items(include):
        if isinstance(entry, Mapping):
            for path in _items(entry.get("path")):
                yield str(path)
        else:
            yield str(entry)


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


def _judge_source(
    findings: ComposeFindings, who: str, source: str, app_path: Path, *, as_a: str = ""
) -> None:
    """
    Judge one host path a service is given: a bind mount, or the file of a secret or a config.

    Args:
        findings: Where the finding goes.
        who: ``service web`` or ``job migrate``.
        source: The host path as written.
        app_path: The application directory.
        as_a: ``" as a secret"`` or ``" as a config"`` for a file given that way, else empty.
    """
    path = _resolved(source, app_path)
    where = str(path) if path is not None else source
    if source.rstrip("/").endswith(DOCKER_SOCKET):
        findings.add(
            (who, "socket", where),
            f"{who} mounts the Docker socket ({source}): it can start a container with this "
            "server's root filesystem",
            refused=True,
        )
    elif (reason := _exposes(source, app_path)) is not None:
        findings.add(
            (who, "exposes", where),
            f"{who} mounts {source}{as_a}: {reason}. The files of other applications and "
            "Noust's own are not for a container to read",
            refused=True,
        )
    elif _outside(source, app_path):
        findings.add(
            (who, "outside", where),
            f"{who} mounts {source} from this server{as_a}, outside the application",
            refused=False,
        )


def _judge_runnable(
    findings: ComposeFindings,
    document: Mapping[str, Any],
    who: str,
    service: Mapping[str, Any],
    app_path: Path,
) -> None:
    """
    Judge one service or job of a compose document.

    Args:
        findings: Where the findings go.
        document: The whole document, which holds the files its secrets and configs name.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        app_path: The application directory.
    """
    if service.get("privileged") in (True, "true", "True", "yes"):
        findings.add(
            (who, "privileged", ""),
            f"{who} is privileged: it has every capability and every device of this server",
            refused=True,
        )
    for volume in _items(service.get("volumes")):
        source, kind = _volume_source(volume)
        if source is not None and kind == "bind":
            _judge_source(findings, who, source, app_path)
    for section, word in _FILE_SECTIONS:
        defined = document.get(section)
        for entry in _items(service.get(section)):
            reference = entry.get("source") if isinstance(entry, Mapping) else entry
            definition = defined.get(str(reference)) if isinstance(defined, Mapping) else None
            file = definition.get("file") if isinstance(definition, Mapping) else None
            if isinstance(file, str) and file:
                _judge_source(findings, who, file, app_path, as_a=f" as a {word}")
    for borrowed in _items(service.get("volumes_from")):
        kind, _, rest = str(borrowed).partition(":")
        if kind == "container":
            target = rest.split(":", 1)[0]
            findings.add(
                (who, "borrows", target),
                f"{who} takes the volumes of the container {target}, which is not part of this "
                "stack: whatever that container has mounted is then in this one",
                refused=True,
            )
    for namespace in _JOINABLE_NAMESPACES:
        value = str(service.get(namespace) or "")
        if value.startswith("container:"):
            target = value.partition(":")[2]
            findings.add(
                (who, "joins", f"{namespace}:{target}"),
                f"{who} joins the {namespace} of the container {target}, which is not part of "
                "this stack",
                refused=True,
            )
    extended = _extended_file(service)
    if extended is not None and _leaves(extended, app_path):
        findings.add(
            (who, "extends", extended),
            f"{who} extends a file outside the application ({extended}): its definitions, and "
            "the files they name, are not this application's to take",
            refused=True,
        )
    for namespace in _HOST_NAMESPACES:
        if str(service.get(namespace) or "") == "host":
            findings.add(
                (who, "host", namespace), f"{who} shares the host's {namespace}", refused=False
            )
    if service.get("cap_add"):
        capabilities = ", ".join(str(cap) for cap in _items(service.get("cap_add")))
        findings.add(
            (who, "cap_add", ""), f"{who} adds capabilities: {capabilities}", refused=False
        )
    if service.get("devices"):
        findings.add((who, "devices", ""), f"{who} is given host devices", refused=False)
    for option in _items(service.get("security_opt")):
        if str(option).replace(" ", "") in _UNCONFINED:
            findings.add(
                (who, "unconfined", str(option)),
                f"{who} turns its confinement off ({option})",
                refused=False,
            )


def inspect_compose(document: Any, app_path: Path) -> ComposeFindings:
    """
    Sort what a parsed compose file, or the model Compose resolves it to, asks of the host.

    The same judgement serves both: the file as the repository wrote it, and what
    ``docker compose config --format json`` prints for it (:func:`resolve_model`), where an
    ``include`` or an ``extends`` has been merged, every service of every profile is present and
    a bind mount is a long entry with an absolute source.

    Args:
        document: The file, as ``yaml.safe_load`` returned it, or the resolved model.
        app_path: The application directory, the one place a bind mount may
            come from without a warning.

    Returns:
        The findings, each a sentence naming the service, or the job.
    """
    findings = ComposeFindings()
    if not isinstance(document, Mapping):
        return findings
    for path in _included(document.get("include")):
        if _leaves(path, app_path):
            findings.add(
                ("compose", "include", path),
                f"the compose file includes {path}, which is outside the application: Compose "
                "would read a file of this server that is not part of the repository",
                refused=True,
            )
    for section, word in _RUNNABLE:
        entries = document.get(section)
        if not isinstance(entries, Mapping):
            continue
        for name, service in entries.items():
            if isinstance(service, Mapping):
                _judge_runnable(findings, document, f"{word} {name}", service, app_path)
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
    merged = ComposeFindings(list(file.refused), list(file.warned), dict(file.keys))
    for sentences, refused in ((resolved.refused, True), (resolved.warned, False)):
        for sentence in sentences:
            key = resolved.keys.get(sentence)
            if key is None:
                (merged.refused if refused else merged.warned).append(sentence)
            else:
                merged.add(key, sentence, refused=refused)
    return merged


def with_resolved(
    findings: ComposeFindings,
    document: Any,
    runner: CommandRunner,
    commands: Sequence[Sequence[str]],
    app_path: Path,
) -> tuple[ComposeFindings, str]:
    """
    Add to the findings of a file what only the model Compose resolves it to shows.

    Args:
        findings: What the file as written asks of the host.
        document: The file, parsed.
        runner: Runs ``docker compose``.
        commands: The commands :func:`resolve_model` tries.
        app_path: The application directory, where they run.

    Returns:
        The findings, and a sentence for the log when Compose could not resolve a stack that
        brings nothing in (it is judged as written). A file that includes or extends another
        and cannot be resolved is refused: what it brings in is what cannot be judged.
    """
    model, problem = resolve_model(runner, commands, app_path)
    if model is not None:
        return merge_findings(findings, inspect_compose(model, app_path)), ""
    if uses_composition(document):
        findings.add(
            ("compose", "unresolved", ""),
            "the compose file includes or extends another file and Docker Compose could not "
            f"resolve the stack ({problem}), so what it brings in cannot be judged",
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


def stack_findings(app_path: Path) -> ComposeFindings:
    """
    Inspect the compose file a deployed stack runs from.

    Args:
        app_path: The stack's directory.

    Returns:
        The findings; none when no compose file can be read.
    """
    import yaml

    from noust.deployers.docker_compose import COMPOSE_FILE_PRIORITY

    for name in COMPOSE_FILE_PRIORITY:
        candidate = app_path / name
        if candidate.is_file() and not candidate.is_symlink():
            try:
                document = load_compose(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                return ComposeFindings()
            return inspect_compose(document, app_path)
    return ComposeFindings()
