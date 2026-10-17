# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a container application's settings may hold.

Every value here reaches ``docker`` (run as root) as an argument, a compose
file or a path under the application, and most of them come from a request or
from a repository nobody here wrote. So each is an allowlist, as in
:mod:`noust.validators.names`: an image reference that begins with ``-`` would
be an option of ``docker pull``, a build argument with a newline would end its
line in a compose file, a Dockerfile path with ``..`` would read a file from
anywhere on the host. The store calls these before it writes a setting, and
the deployer calls them again before it uses one, so a row edited by hand
cannot reach a command either.

The names are the field names of the API (``image``, ``dockerfile``,
``build_args``...), carried in the error so a client can put the refusal next
to its input.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from noust.core.exceptions import ValidationError
from noust.core.secret_detection import URL_CREDENTIALS, name_looks_secret

#: How a container application's image comes to exist.
METHOD_DOCKERFILE = "dockerfile"
METHOD_RAILPACK = "railpack"
METHOD_IMAGE = "image"
METHODS: tuple[str, ...] = (METHOD_DOCKERFILE, METHOD_RAILPACK, METHOD_IMAGE)

#: Docker's own restart policies. ``on-failure`` takes no count here: a policy
#: that gives up leaves the application down without the unit knowing.
RESTART_POLICIES: tuple[str, ...] = ("no", "always", "unless-stopped", "on-failure")

#: The registry a name without a host is pulled from.
DOCKER_HUB = "docker.io"

#: Longest reference Docker itself accepts.
_REFERENCE_MAX = 255

#: One component of an image's path: lower-case words joined by ``.``, ``_``,
#: ``__`` or runs of ``-`` (the reference grammar of the distribution spec).
_PATH_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"

#: A registry host, with an optional port.
_HOST = r"(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])(?:\.(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]))*(?::[0-9]+)?"

_DIGEST = r"sha256:[a-f0-9]{64}|sha512:[a-f0-9]{128}"

_REFERENCE = re.compile(
    rf"(?P<name>(?:(?P<host>{_HOST})/)?(?P<path>{_PATH_COMPONENT}(?:/{_PATH_COMPONENT})*))"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    rf"(?:@(?P<digest>{_DIGEST}))?"
)

#: One segment of a path inside the repository.
_PATH_SEGMENT = r"[A-Za-z0-9_@+][A-Za-z0-9._@+-]*"
_REPO_PATH = re.compile(rf"{_PATH_SEGMENT}(?:/{_PATH_SEGMENT})*")

#: Stage names, secret ids and registry accounts: what Docker takes in a name.
_STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SECRET_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_ARG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_REGISTRY_USER = re.compile(r"[A-Za-z0-9_@][A-Za-z0-9_.@$+=-]{0,255}")

#: Registry hosts as ``docker login`` takes them: no scheme, no path.
_REGISTRY_HOST = re.compile(rf"{_HOST}")

#: One segment of an absolute path inside the container.
_MOUNT_SEGMENT = re.compile(r"[A-Za-z0-9_@+][A-Za-z0-9._@+-]*")

#: Container directories a volume would break rather than persist.
_NOT_MOUNTABLE = ("proc", "sys", "dev")

#: Limits that keep a pasted blob from becoming a setting.
MAX_BUILD_ARGS = 64
MAX_BUILD_ARG_VALUE = 4096
MAX_BUILD_SECRETS = 32
MAX_VOLUMES = 16
MAX_COMMAND_WORDS = 64
MAX_COMMAND_WORD = 4096


def is_env_name(name: str) -> bool:
    """
    Tell whether a string can be the name of an environment variable.

    Args:
        name: The candidate.

    Returns:
        True for letters, numbers and ``_``, not starting with a number.
    """
    return _ARG_NAME.fullmatch(name) is not None


#: Names a Railpack secret cannot have: Railpack reads each secret from the
#: environment of a command Noust runs as root, so a secret named like one of
#: the variables that steer a process (where it loads code from, which daemon
#: it talks to, which proxy it goes through) would let whoever names it steer
#: that command instead of feeding a build.
_RESERVED_NAMES = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "IFS", "ENV", "TMPDIR", "LANG", "TZ", "PWD"}
)
_RESERVED_PREFIXES = (
    "LD_",
    "DOCKER_",
    "BUILDKIT_",
    "BASH_",
    "LC_",
    "GIT_",
    "SSL_",
    "XDG_",
    "HTTP",
    "NO_PROXY",
    "ALL_PROXY",
    "SUDO_",
    "SYSTEMD_",
    "NOUST_",
    "WASM_",
    "GO",
    "PYTHON",
)


def check_railpack_secret_id(secret_id: str) -> str:
    """
    Accept the id of a secret a Railpack build reads from its environment.

    Args:
        secret_id: The id, which is also the name of the variable.

    Returns:
        The id.

    Raises:
        ValidationError: It is not a variable name, or is one that steers the
            commands Noust runs.
    """
    if not is_env_name(secret_id):
        raise ValidationError(
            f"{secret_id!r} cannot be a build secret of a Railpack build",
            details="Railpack reads secrets as environment variables: use letters, numbers "
            "and _, not starting with a number.",
            field="build_secrets",
        )
    if secret_id in _RESERVED_NAMES or secret_id.startswith(_RESERVED_PREFIXES):
        raise ValidationError(
            f"{secret_id} is a name Noust keeps for itself",
            details="Railpack reads secrets as environment variables of the commands that "
            "build, so a few names are reserved. Choose another, such as NPM_TOKEN.",
            field="build_secrets",
        )
    return secret_id


def check_method(method: str) -> str:
    """
    Accept one of the ways a container application's image comes to exist.

    Args:
        method: ``dockerfile``, ``railpack`` or ``image``.

    Returns:
        The method.

    Raises:
        ValidationError: It is none of them.
    """
    if method not in METHODS:
        raise ValidationError(
            f"{method!r} is not a way to build a container application",
            details="Use 'dockerfile' (a Dockerfile in the repository), 'railpack' (Railpack "
            "builds it, no Dockerfile needed) or 'image' (an image from a registry).",
            field="method",
        )
    return method


def check_image_reference(reference: str) -> str:
    """
    Accept an image reference, and nothing Docker could read as an option.

    Args:
        reference: ``name[:tag][@sha256:digest]``, where the name may start
            with a registry host (``ghcr.io/owner/app``).

    Returns:
        The reference, unchanged.

    Raises:
        ValidationError: It is not a reference. The leading character is the
            point: a reference that begins with ``-`` would be an option of
            the ``docker`` command it is handed to.
    """
    how = (
        "Give the image as name[:tag][@sha256:digest], such as ghcr.io/owner/app:1.4 or "
        "nginx:1.27-alpine: lower-case, no spaces, no URL scheme."
    )
    if (
        not isinstance(reference, str)
        or not reference
        or len(reference) > _REFERENCE_MAX
        or _REFERENCE.fullmatch(reference) is None
    ):
        shown = reference if isinstance(reference, str) and len(reference) <= 80 else "that value"
        raise ValidationError(f"{shown!r} is not an image reference", details=how, field="image")
    return reference


def with_tag(reference: str, tag: str) -> str:
    """
    Name the same image at another tag.

    A CI step that pushed ``ghcr.io/acme/app:1.4.2`` asks for exactly that tag;
    a digest the reference pinned is dropped, because it would win over the tag.

    Args:
        reference: A valid image reference.
        tag: The tag to deploy.

    Returns:
        ``name:tag``.

    Raises:
        ValidationError: The reference or the tag is not valid.
    """
    check_image_reference(reference)
    match = _REFERENCE.fullmatch(reference)
    if match is None or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag or "") is None:
        raise ValidationError(
            f"{tag!r} is not an image tag",
            details="A tag is letters, digits, '_', '.' and '-', at most 128, and does not "
            "start with '.' or '-'.",
            field="image_tag",
        )
    return check_image_reference(f"{match['name']}:{tag}")


def registry_of(reference: str) -> str:
    """
    Name the registry an image is pulled from.

    Docker reads the first component as a host only when it looks like one (it
    has a ``.`` or a ``:``, or is ``localhost``); otherwise the image is on
    Docker Hub.

    Args:
        reference: A valid image reference.

    Returns:
        The registry host, ``docker.io`` for Docker Hub.
    """
    match = _REFERENCE.fullmatch(reference)
    host = match["host"] if match is not None else None
    if host is not None and ("." in host or ":" in host or host == "localhost"):
        return host
    return DOCKER_HUB


#: Names Docker Hub is reached by, all the one registry.
_HUB_HOSTS = frozenset(
    {"docker.io", "index.docker.io", "registry-1.docker.io", "registry.hub.docker.com"}
)


def split_reference(reference: str) -> tuple[str, str, str | None, str | None]:
    """
    Take an image reference apart the way Docker reads it, so two spellings of one image compare.

    ``nginx``, ``library/nginx:latest`` and ``docker.io/library/nginx`` are one image; so are
    ``ghcr.io/Owner/App`` and the lower-case name a registry reports.

    Args:
        reference: A valid image reference.

    Returns:
        ``(registry, repository, tag, digest)``: the registry host (``docker.io`` for Docker
        Hub), the path within it (``library/nginx`` for an official image), the tag (``latest``
        when neither a tag nor a digest is given, None for a digest alone) and the digest, or
        None.

    Raises:
        ValidationError: It is not a reference.
    """
    check_image_reference(reference)
    match = _REFERENCE.fullmatch(reference)
    if match is None:  # pragma: no cover - check_image_reference passed above
        raise ValidationError(f"{reference!r} is not an image reference", field="image")
    host = match["host"]
    real_host = host is not None and ("." in host or ":" in host or host == "localhost")
    registry = host.lower() if real_host and host is not None else DOCKER_HUB
    # With no host in front, the first component is part of the path: ``owner/app``.
    repository = match["path"] if real_host else match["name"]
    if registry in _HUB_HOSTS:
        registry = DOCKER_HUB
        if "/" not in repository:
            repository = f"library/{repository}"
    digest = match["digest"]
    tag = match["tag"] or (None if digest else "latest")
    return registry, repository, tag, digest


def check_digest(digest: str) -> str:
    """
    Accept the content digest of an image.

    Args:
        digest: ``sha256:`` and 64 hex digits (or ``sha512:`` and 128).

    Returns:
        The digest.

    Raises:
        ValidationError: It is not a digest.
    """
    if not isinstance(digest, str) or re.fullmatch(_DIGEST, digest) is None:
        raise ValidationError(
            f"{digest!r} is not an image digest",
            details="A digest is sha256: followed by 64 hexadecimal digits.",
            field="digest",
        )
    return digest


def check_repo_path(path: str, *, field: str) -> str:
    """
    Accept a path inside the repository: a Dockerfile, or the build context.

    Allowlisted rather than normalised: ``a/./b``, ``a//b`` and anything with
    ``..`` are refused instead of being quietly rewritten, so what is stored
    is what is read. Whether a link inside the checkout leads out of it can
    only be known once the checkout exists; the deployer checks that.

    Args:
        path: Relative to the repository root. ``.`` is the root itself.
        field: The API field, for the error.

    Returns:
        The path, without a trailing slash.

    Raises:
        ValidationError: It is empty, absolute, climbs with ``..`` or holds
            anything but letters, digits and ``._@+-``.
    """
    if isinstance(path, str) and path != "/":
        path = path.rstrip("/")
    if not isinstance(path, str) or not (path == "." or _REPO_PATH.fullmatch(path)):
        raise ValidationError(
            f"{path!r} is not a path inside the repository",
            details="Name it relative to the root of the repository, such as 'docker/Dockerfile' "
            "or 'services/api': letters, digits and . _ @ + - only, no '..', no leading slash.",
            field=field,
        )
    if ".." in path.split("/"):
        raise ValidationError(
            f"{path!r} climbs out of the repository",
            details="A path may not contain '..'.",
            field=field,
        )
    return path


def check_target(target: str) -> str:
    """
    Accept the name of a stage of a multi-stage Dockerfile.

    Args:
        target: The stage, as ``FROM ... AS <name>`` names it.

    Returns:
        The target.

    Raises:
        ValidationError: It is not a stage name.
    """
    if not isinstance(target, str) or _STAGE.fullmatch(target) is None:
        raise ValidationError(
            f"{target!r} is not a build stage name",
            details="Use the name after AS in the Dockerfile: letters, digits and . _ -",
            field="target",
        )
    return target


def check_build_args(args: Mapping[str, str]) -> dict[str, str]:
    """
    Accept the build arguments of a Dockerfile build.

    Build arguments are written into the image's history and shown on the
    command line, so a secret does not belong in one: a name or a value that
    looks like one is refused, with the build secret to use instead.

    Args:
        args: Name to value.

    Returns:
        A copy, in the order given.

    Raises:
        ValidationError: Too many, a name that is not an identifier or looks
            like a secret, or a value that is multi-line, too long or holds
            credentials in a URL. The value is never echoed.
    """
    if not isinstance(args, Mapping) or len(args) > MAX_BUILD_ARGS:
        raise ValidationError(
            f"A build takes at most {MAX_BUILD_ARGS} arguments",
            details="Move what is not a small switch into the Dockerfile.",
            field="build_args",
        )
    checked: dict[str, str] = {}
    for name, value in args.items():
        if not isinstance(name, str) or _ARG_NAME.fullmatch(name) is None:
            raise ValidationError(
                f"{name!r} is not a build argument name",
                details="Use letters, digits and _, not starting with a digit.",
                field="build_args",
            )
        if name_looks_secret(name):
            raise ValidationError(
                f"{name} looks like a secret, and a build argument is written into the image",
                details="Give it as a build secret instead (--build-secret): it is mounted for "
                "the build step that asks for it and is never part of the image.",
                field="build_args",
            )
        if (
            not isinstance(value, str)
            or len(value) > MAX_BUILD_ARG_VALUE
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValidationError(
                f"The value of {name} cannot be a build argument",
                details=f"A value is one line of at most {MAX_BUILD_ARG_VALUE} characters, "
                "without control characters.",
                field="build_args",
            )
        if URL_CREDENTIALS.search(value):
            raise ValidationError(
                f"The value of {name} carries credentials inside a URL",
                details="A build argument is written into the image. Give the credentials as a "
                "build secret instead (--build-secret).",
                field="build_args",
            )
        checked[name] = value
    return checked


def check_secret_ids(ids: Iterable[str]) -> tuple[str, ...]:
    """
    Accept the ids of the build secrets an application mounts while it builds.

    Args:
        ids: The ids, as the Dockerfile names them (``RUN --mount=type=secret,id=npmrc``).

    Returns:
        The ids, in order.

    Raises:
        ValidationError: Too many, repeated, or not a name Docker takes as an id.
    """
    listed = list(ids)
    if len(listed) > MAX_BUILD_SECRETS or len(set(listed)) != len(listed):
        raise ValidationError(
            "Build secrets must be distinct, and at most " + str(MAX_BUILD_SECRETS),
            details="Name each secret once.",
            field="build_secrets",
        )
    for secret_id in listed:
        if not isinstance(secret_id, str) or _SECRET_ID.fullmatch(secret_id) is None:
            raise ValidationError(
                f"{secret_id!r} is not a build secret id",
                details="Use letters, digits and . _ - , not starting with a dash.",
                field="build_secrets",
            )
    return tuple(listed)


def check_container_port(port: object) -> int:
    """
    Accept the port the application listens on inside its container.

    Args:
        port: The port.

    Returns:
        The port.

    Raises:
        ValidationError: It is not an integer from 1 to 65535.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValidationError(
            f"{port!r} is not a port",
            details="Give the port the process listens on inside the container, from 1 to 65535.",
            field="container_port",
        )
    return port


def check_volumes(paths: Iterable[str]) -> tuple[str, ...]:
    """
    Accept the directories of the container that keep their data across deploys.

    Each becomes a named volume, mounted at that path in every release, so what
    the application writes there survives the container being replaced.

    Args:
        paths: Absolute paths inside the container, such as ``/data``.

    Returns:
        The paths, without trailing slash, in order.

    Raises:
        ValidationError: A path is not absolute, is the root, climbs with
            ``..``, is a system directory of the container, or repeats.
    """
    cleaned: list[str] = []
    for raw in paths:
        path = raw.rstrip("/") if isinstance(raw, str) else raw
        parts = path.split("/")[1:] if isinstance(path, str) and path.startswith("/") else []
        if (
            not parts
            or len(cleaned) >= MAX_VOLUMES
            or not all(_MOUNT_SEGMENT.fullmatch(part) for part in parts)
            or parts[0] in _NOT_MOUNTABLE
            or ".." in parts
        ):
            raise ValidationError(
                f"{raw!r} cannot be a volume",
                details="Give an absolute path inside the container, such as /data: letters, "
                f"digits and . _ @ + - only, not /, /proc, /sys or /dev, at most {MAX_VOLUMES}.",
                field="volumes",
            )
        if path in cleaned:
            raise ValidationError(
                f"{path} is listed twice", details="Name each volume once.", field="volumes"
            )
        cleaned.append(path)
    return tuple(cleaned)


def split_command(text: str, *, field: str) -> tuple[str, ...]:
    """
    Split what a person typed as a command into its words, the way a POSIX shell would.

    Nothing is run by a shell: the words are the container's argument vector, so quotes only
    group a word and ``$`` and ``;`` mean nothing.

    Args:
        text: The command as typed, such as ``node dist/server.js --port 8080``.
        field: The setting it is for (``command`` or ``entrypoint``), carried in the error.

    Returns:
        The words; empty for an empty text.

    Raises:
        ValidationError: A quote is left open.
    """
    import shlex

    try:
        return tuple(shlex.split(text))
    except ValueError as exc:
        raise ValidationError(
            f"{text!r} cannot be split into words: {exc}",
            details="Close the quote, or give the words as a list.",
            field=field,
        ) from exc


def check_command(words: object, *, field: str) -> tuple[str, ...]:
    """
    Accept the command or the entrypoint of a container as an argument vector.

    It is never a shell line: the words go to the container as they are, so a pipe or a ``&&``
    is an argument of the program. A program that needs a shell is started with one
    (``["sh", "-c", "migrate && serve"]``).

    Args:
        words: The program and its arguments, one string each. An empty list is the
            decision to run nothing by default (an image's own entrypoint cleared).
        field: The setting it is for, carried in the error.

    Returns:
        The words.

    Raises:
        ValidationError: It is not a list of strings, a word holds a NUL byte or is longer than
            :data:`MAX_COMMAND_WORD`, or there are more than :data:`MAX_COMMAND_WORDS`.
    """
    if isinstance(words, str) or not isinstance(words, list | tuple):
        raise ValidationError(
            f"The {field} must be a list of words",
            details="Give the program and each argument as a separate word, or split a line "
            "with noust app container set --"
            f"{field} 'node server.js'.",
            field=field,
        )
    if len(words) > MAX_COMMAND_WORDS:
        raise ValidationError(
            f"The {field} has {len(words)} words; at most {MAX_COMMAND_WORDS} are accepted",
            details="Put a long list of arguments in a file of the image or a mounted file.",
            field=field,
        )
    for word in words:
        if not isinstance(word, str) or "\x00" in word or len(word) > MAX_COMMAND_WORD:
            raise ValidationError(
                f"A word of the {field} is not text a process can be given",
                details=f"Each word is a string of at most {MAX_COMMAND_WORD} characters with no "
                "NUL byte.",
                field=field,
            )
    return tuple(words)


def check_restart_policy(policy: str) -> str:
    """
    Accept a Docker restart policy.

    Args:
        policy: One of :data:`RESTART_POLICIES`.

    Returns:
        The policy.

    Raises:
        ValidationError: It is none of them.
    """
    if policy not in RESTART_POLICIES:
        raise ValidationError(
            f"{policy!r} is not a restart policy",
            details="Use one of: " + ", ".join(RESTART_POLICIES) + ".",
            field="restart_policy",
        )
    return policy


def check_registry_server(server: str) -> str:
    """
    Accept the registry host credentials are kept for.

    Args:
        server: ``ghcr.io``, ``registry.example.com:5000``: a host and maybe a port.

    Returns:
        The host.

    Raises:
        ValidationError: It carries a scheme or a path, or is not a host.
    """
    if not isinstance(server, str) or _REGISTRY_HOST.fullmatch(server) is None:
        raise ValidationError(
            f"{server!r} is not a registry host",
            details="Give the host as 'docker login' takes it: ghcr.io or "
            "registry.example.com:5000, without https:// and without a path.",
            field="registry_server",
        )
    return server


def check_registry_username(user: str) -> str:
    """
    Accept the account a registry knows the application's image by.

    Args:
        user: The username.

    Returns:
        The username.

    Raises:
        ValidationError: It is empty, long, or holds whitespace or control
            characters, which would split ``docker login``'s arguments.
    """
    if not isinstance(user, str) or _REGISTRY_USER.fullmatch(user) is None:
        raise ValidationError(
            f"{user!r} is not a registry username",
            details="Use the account name the registry gave you: letters, digits and . _ - @ $ "
            "+ =, at most 256 characters, without spaces.",
            field="registry_username",
        )
    return user
