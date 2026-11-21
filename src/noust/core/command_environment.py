# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What environment a command is started with, when it reads a file somebody else wrote.

``docker compose`` fills ``${NAME}`` in a Compose document, and reads ``secrets: {x: {environment:
NAME}}``, from the environment of the process that runs it, and the runner hands a command the
whole environment of Noust's own process. A repository's file could therefore name any variable of
that process (a token, the sealing key, the data directory, a cloud key somebody exported in the
shell that started the service) and have its value put into a container or an image. Every command
that reads such a file, ``docker compose`` and the ways of building an image, is started with a
**closed list** of what Docker and Compose themselves read (:data:`STACK_ENV_ALLOW`), decided in one
place that the runner goes through, so no caller can forget. What the caller passes on purpose
(``env=``) stays, and the variables of a stack are the ones of its ``.env``, which Compose reads
beside the file.

A list that names what to drop (everything that starts with ``NOUST_``) loses to the next secret that
does not: this one names what is needed, and a variable nobody thought of does not arrive.

Kept beside :mod:`noust.core.runner`, which does not grow.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Mapping, Sequence

#: The ``docker`` subcommands whose input is a file somebody else wrote and which fill ``${NAME}``
#: from their environment: Compose, and the ways of building an image.
_FILE_READING_SUBCOMMANDS = frozenset({"compose", "build", "buildx", "builder"})

#: What a command that reads a file of a stack is started with, from the environment of this process,
#: and why each is there (names are case sensitive, ``*`` globs allowed):
#:
#: - Where the programs are and who runs them: ``PATH`` (``docker`` finds its plugins and credential
#:   helpers through it), ``HOME`` (``~/.docker/config.json`` holds the logins to private registries),
#:   ``USER``, ``LOGNAME`` and ``TMPDIR``.
#: - The language and the clock, which change what a message says and when a certificate is valid:
#:   ``LANG``, ``LANGUAGE``, ``LC_*`` and ``TZ``.
#: - The proxy a network needs to reach a registry and the certificates it is trusted with, in both
#:   spellings (Go reads the lower case first): ``*_proxy``, ``SSL_CERT_FILE``, ``SSL_CERT_DIR``.
#: - Where the credential helpers keep what they open (``docker-credential-pass``, the secret service):
#:   ``XDG_*``, ``GNUPGHOME``, ``PASSWORD_STORE_DIR``, ``DBUS_SESSION_BUS_ADDRESS``. They are places, not
#:   secrets.
#: - What Docker's client, Compose, buildx and BuildKit read to know which engine to talk to and how:
#:   the documented ``DOCKER_*`` names (not a prefix: ``DOCKER_PASSWORD`` and ``DOCKERHUB_TOKEN`` are a
#:   person's, and are nobody else's to read) and the ``COMPOSE_*``, ``BUILDX_*`` and ``BUILDKIT_*``
#:   families, which name no secret.
#:
#: Not here, on purpose: ``SSH_AUTH_SOCK`` (a build asking for ``ssh: default`` would be given the
#: keys that whoever started Noust has loaded), ``SHELL``, ``NOUST_*`` and whatever a shell exported.
STACK_ENV_ALLOW: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "LANG",
    "LANGUAGE",
    "LC_*",
    "TZ",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    "GNUPGHOME",
    "PASSWORD_STORE_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "DOCKER_API_VERSION",
    "DOCKER_BUILDKIT",
    "DOCKER_CERT_PATH",
    "DOCKER_CLI_*",
    "DOCKER_CONFIG",
    "DOCKER_CONTENT_TRUST",
    "DOCKER_CONTENT_TRUST_SERVER",
    "DOCKER_CONTEXT",
    "DOCKER_CUSTOM_HEADERS",
    "DOCKER_DEFAULT_PLATFORM",
    "DOCKER_HIDE_LEGACY_COMMANDS",
    "DOCKER_HOST",
    "DOCKER_TLS",
    "DOCKER_TLS_VERIFY",
    "COMPOSE_*",
    "BUILDX_*",
    "BUILDKIT_*",
)

#: The options of ``docker`` itself that take a value, so what follows one is not the subcommand.
_DOCKER_VALUE_OPTIONS = frozenset(
    {
        "-H",
        "--host",
        "-c",
        "--context",
        "--config",
        "-l",
        "--log-level",
        "--tlscacert",
        "--tlscert",
        "--tlskey",
    }
)


def reads_a_file_of_a_stack(argv: Sequence[str]) -> bool:
    """
    Tell whether a command reads a file somebody else wrote and fills it in from its environment.

    Args:
        argv: The command.

    Returns:
        True for ``docker compose ...``, ``docker-compose``, and ``docker build``, ``buildx``,
        ``builder`` and ``image build``, whatever options of ``docker`` come before the subcommand.
    """
    if not argv:
        return False
    program = os.path.basename(argv[0])
    if program == "docker-compose":
        return True
    if program != "docker":
        return False
    words = iter(argv[1:])
    for word in words:
        if word in _DOCKER_VALUE_OPTIONS:
            next(words, None)
            continue
        if word.startswith("-"):
            continue
        if word in _FILE_READING_SUBCOMMANDS:
            return True
        if word == "image":
            return next(words, "") == "build"
        return False
    return False


def command_environment(
    argv: Sequence[str], base: Mapping[str, str], extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """
    Compose the environment a command is started with.

    Args:
        argv: The command.
        base: What it would inherit: this process's environment, or the allowed part of it.
        extra: What the caller adds on purpose; it wins over everything, even a name like Noust's.

    Returns:
        The environment: only :data:`STACK_ENV_ALLOW` of ``base`` when the command reads a file of
        a stack (:func:`reads_a_file_of_a_stack`), all of it for any other; plus ``extra``.
    """
    composed = dict(base)
    if reads_a_file_of_a_stack(argv):
        composed = {
            name: value
            for name, value in composed.items()
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in STACK_ENV_ALLOW)
        }
    if extra:
        composed.update(extra)
    return composed
