# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Source validation for Noust.

Validates Git URLs, local paths, and other source formats.
"""

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from noust.core import paths
from noust.core.exceptions import DomainError, SourceError, ValidationError
from noust.validators.domain import validate_domain

#: A user name in an ssh remote. It starts with a letter, a digit or ``_``, never
#: ``-``: ssh would read ``-oProxyCommand=...@host`` as an option.
_SSH_USER = r"[A-Za-z0-9_][\w.+-]{0,63}"

#: A host name or address as the remotes below name it. It cannot start with
#: ``-`` for the same reason a user cannot.
_SSH_HOST = r"[A-Za-z0-9_][\w.-]{0,252}"

# Git URL patterns
#: scp-like ssh remote, ``user@host:path``. The user is required: without one,
#: ``host:path`` is also ``localhost:8080``, which names no repository.
GIT_SSH_PATTERN = re.compile(
    rf"^(?P<user>{_SSH_USER})@(?P<host>{_SSH_HOST}):(?!//)(?P<path>[\w./~+-]+?)(?:\.git)?$"
)
#: ``ssh://[user@]host[:port]/path``.
GIT_SSH_URL_PATTERN = re.compile(
    rf"^ssh://(?:(?P<user>{_SSH_USER})@)?(?P<host>{_SSH_HOST})(?::(?P<port>\d{{1,5}}))?"
    r"/(?P<path>[\w./~+-]*?[\w~+-][\w./~+-]*?)(?:\.git)?/?$"
)
#: A port as a URL spells it: ASCII digits, no sign and no leading zero, so one endpoint has one
#: spelling (``08443`` and ``8443`` would otherwise be two names for one credential's scope). The
#: range is judged by :func:`parse_url_remote`, which a pattern cannot say.
_URL_PORT = r"[1-9][0-9]{0,4}"

#: ``http[s]://host[:port]/path`` and ``git://host[:port]/path``. The host is a name or an IPv4
#: address: no user (a credential is taken off the address before it is judged, see
#: ``noust.managers.source_manager.split_url_credentials``) and no IPv6 literal.
GIT_HTTPS_PATTERN = re.compile(
    rf"^https?://(?P<host>[\w.-]+)(?::(?P<port>{_URL_PORT}))?/(?P<path>[\w./-]+?)(?:\.git)?$"
)
GIT_PROTOCOL_PATTERN = re.compile(
    rf"^git://(?P<host>[\w.-]+)(?::(?P<port>{_URL_PORT}))?/(?P<path>[\w./-]+?)(?:\.git)?$"
)

#: What follows the host of a URL that says a port, whatever it says; read only to name a port
#: that is not one (:func:`_bad_port`).
_PORT_TEXT = re.compile(r"(?:https?|git)://[\w.-]+:(?P<port>[^/@?#]*)/.*", re.DOTALL)

#: The host whose repositories this server's GitHub App can clone.
GITHUB_HOST = "github.com"

#: GitHub's own limits: an account is 1-39 alphanumerics or single hyphens,
#: a repository up to 100 of letters, digits, ``.``, ``-`` and ``_``.
_GITHUB_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_GITHUB_REPO = r"[A-Za-z0-9._-]{1,100}"

#: ``github:owner/repo``: the spelling the console's repository picker and
#: ``noust create --source`` share for a repository this server's GitHub App
#: can reach. No ``#branch``: no other source takes one here either, the
#: branch is its own field.
GITHUB_SHORTHAND_PATTERN = re.compile(
    rf"^github:(?P<owner>{_GITHUB_OWNER})/(?P<repo>{_GITHUB_REPO})$"
)

#: What no source contains: ASCII control characters, DEL and the Unicode line
#: and paragraph separators.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

#: The bare ``owner/repo`` shorthand :func:`validate_source` has long taken.
_BARE_SHORTHAND = re.compile(r"^[\w.-]+/[\w.-]+$")

_GITHUB_PATH = re.compile(rf"^/?(?P<owner>{_GITHUB_OWNER})/(?P<repo>{_GITHUB_REPO}?)(?:\.git)?/?$")


@dataclass(frozen=True)
class SshRemote:
    """
    A repository reached over ssh, whichever of the two spellings named it.

    Attributes:
        user: The account to sign in as, or None when the URL names none.
        host: The server's name or address.
        port: The port, or None for ssh's own default (22).
        path: The repository's path on the server, without ``.git``.
    """

    user: str | None
    host: str
    port: int | None
    path: str


@dataclass(frozen=True)
class UrlRemote:
    """
    A repository reached over https, http or the ``git://`` protocol.

    Attributes:
        scheme: ``https``, ``http`` or ``git``.
        host: The server's name or address, without its port.
        port: The port the address names, or None when it names none.
        path: The repository's path on the server, without ``.git`` and without a slash at
            either end.
    """

    scheme: str
    host: str
    port: int | None
    path: str

    @property
    def authority(self) -> str:
        """The host with its port when the address names one: what a client connects to."""
        return f"{self.host}:{self.port}" if self.port is not None else self.host


def parse_url_remote(url: str) -> UrlRemote | None:
    """
    Read an https, http or ``git://`` repository address.

    The sibling of :func:`parse_ssh_remote` for the other transports, so the validator, the
    allowed origins, the credentials and the forge's pages agree on which host and port an
    address means (rule 3). A ``#branch`` is not part of an address here: take it off first, as
    :func:`parse_git_url` does.

    Args:
        url: A source as given, without credentials or ``#branch``.

    Returns:
        The remote, or None when the text is not one: another scheme, a user or an IPv6 literal
        before the path, a port that is not 1-65535 or is written with a sign or a leading zero,
        or no repository path after the host.
    """
    text = url.strip()
    match = GIT_HTTPS_PATTERN.fullmatch(text) or GIT_PROTOCOL_PATTERN.fullmatch(text)
    if match is None:
        return None
    port = match.group("port")
    if port is not None and int(port) > 65535:
        return None
    return UrlRemote(
        scheme=text.split("://", 1)[0],
        host=match.group("host"),
        port=int(port) if port is not None else None,
        path=match.group("path").strip("/").removesuffix(".git"),
    )


def _bad_port(text: str) -> str | None:
    """
    Find the port an address names when it is not one.

    Args:
        text: A source as given.

    Returns:
        What stands after the host's colon, when the address is an http, https or ``git://`` one
        that says a port and :func:`parse_url_remote` does not accept it; None otherwise.
    """
    found = _PORT_TEXT.fullmatch(text.strip())
    if found is None or parse_url_remote(text) is not None:
        return None
    return found.group("port")


def _port_refusal(port: str) -> str:
    """
    Say why a port is not one.

    Args:
        port: What stands after the host's colon.

    Returns:
        The sentence, which says what a port is.
    """
    shown = port if len(port) <= 20 and port.isprintable() else "that"
    what = "there is no port after the colon" if not port else f"{shown!r} is not a port"
    return f"{what}: write a number from 1 to 65535, without a sign or a leading zero"


def url_authority_problem(url: str) -> str | None:
    """
    Say why the host and port of a URL cannot be handed to git, when they cannot.

    The last look before git, for a URL that did not go through :func:`validate_source`: the
    validator is stricter (no sign, no leading zero), but every reader downstream of this (the
    credential's scope, the forge's address) takes the port to be a number, and Python's own
    parser raises on a bracket that is no IPv6 literal.

    Args:
        url: A URL with a scheme, as ``urlsplit`` reads it.

    Returns:
        A sentence naming the problem (a port that is not a number from 1 to 65535, an empty
        one, a bracket that is no IPv6 literal); None when the authority is usable.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "The host or the port in it is not one: a port is a number from 1 to 65535."
    if port == 0 or parts.netloc.endswith(":"):
        return "A port is a number from 1 to 65535; leave the colon out for the default."
    return None


def ssh_equivalent(url: str | None) -> str | None:
    """
    Spell an https repository URL the way ssh reaches it.

    Args:
        url: A repository URL, or None when it is not known.

    Returns:
        ``git@host:owner/repo.git``, or None when the URL is not http(s). A URL that names a
        port gets ``ssh://git@host:<ssh-port>/owner/repo.git``: the port is the web server's, and
        ssh answers on its own, which only the operator knows.
    """
    if not url:
        return None
    remote = parse_url_remote(url.split("#")[0])
    if remote is None or remote.scheme not in ("http", "https"):
        return None
    if remote.port is not None:
        return f"ssh://git@{remote.host}:<ssh-port>/{remote.path}.git"
    return f"git@{remote.host}:{remote.path}.git"


def parse_ssh_remote(url: str) -> SshRemote | None:
    """
    Read an ssh remote in either spelling.

    One reader for ``user@host:path`` and ``ssh://[user@]host[:port]/path``,
    so the validator, the deploy keys, the webhook matching and the connection
    test agree on which user, host and port a source means (rule 3).

    Args:
        url: A source as given, without credentials or ``#branch``.

    Returns:
        The remote, or None when the text is not an ssh remote: another
        scheme, a host or user ssh would read as an option, a port outside
        1-65535, or no repository path after the host.
    """
    text = url.strip().split("#", 1)[0]
    scheme = GIT_SSH_URL_PATTERN.fullmatch(text)
    if scheme:
        port = scheme.group("port")
        if port is not None and not 1 <= int(port) <= 65535:
            return None
        return SshRemote(
            user=scheme.group("user"),
            host=scheme.group("host"),
            port=int(port) if port is not None else None,
            path=scheme.group("path").strip("/"),
        )
    scp = GIT_SSH_PATTERN.fullmatch(text)
    if scp:
        return SshRemote(
            user=scp.group("user"),
            host=scp.group("host"),
            port=None,
            path=scp.group("path").strip("/"),
        )
    return None


def expand_github_shorthand(source: str) -> str:
    """
    Spell ``github:owner/repo`` as the https URL git clones.

    Args:
        source: A source as given.

    Returns:
        ``https://github.com/owner/repo.git``; any other source unchanged
        but stripped.
    """
    stripped = source.strip()
    match = GITHUB_SHORTHAND_PATTERN.fullmatch(stripped)
    if not match:
        return stripped
    repo = match.group("repo").removesuffix(".git")
    return f"https://{GITHUB_HOST}/{match.group('owner')}/{repo}.git"


def github_repository_parts(full_name: str) -> tuple[str, str]:
    """
    Split ``owner/repo`` into the two names GitHub's API takes as path segments.

    The one place a repository named by a person is judged before it becomes part
    of a request to GitHub: the whole text must be a repository (a trailing newline
    is not one) and neither name may be ``.`` or ``..``, which would move the
    request to another endpoint of the same host.

    Args:
        full_name: ``owner/repo``.

    Returns:
        The owner and the repository.

    Raises:
        ValidationError: It is not a GitHub repository.
    """
    match = GITHUB_SHORTHAND_PATTERN.fullmatch(f"github:{full_name}")
    if match is None or not match.group("repo").strip("."):
        raise ValidationError(
            f"Not a GitHub repository: {full_name!r}",
            field="repository",
            details="Name it owner/repo, as GitHub does.",
        )
    return match.group("owner"), match.group("repo")


#: The port each web scheme answers on when an address names none.
_WEB_DEFAULT_PORTS = {"http": 80, "https": 443}


def _names_another_server(scheme: str, port: int | None) -> bool:
    """
    Tell whether an address's port makes it a different server from the host's own.

    Args:
        scheme: The address's scheme.
        port: The port it names, or None.

    Returns:
        True for an http(s) address on a port other than its scheme's default. An ssh or
        ``git://`` address names the port of another service, and the default port written out
        is the same server as none.
    """
    default = _WEB_DEFAULT_PORTS.get(scheme.lower())
    return default is not None and port is not None and port != default


def github_repository(source: str | None, hosts: Sequence[str] | None = None) -> str | None:
    """
    Name the GitHub repository a source clones, whichever way it is spelled.

    One answer for every place that has to match an application to a
    repository - a push delivered to ``/hooks/github``, a deployment status,
    the installation that clones it - so ``github:you/app``,
    ``https://github.com/you/app.git``, ``git@github.com:you/app`` and a
    stored ``https://token@github.com/you/app`` all name the same one.

    Args:
        source: A source as stored or given; None or empty for none.
        hosts: The hosts whose repositories count: ``github.com`` unless the
            caller serves a GitHub Enterprise host
            (:func:`noust.core.github_host.integration_repository`). ``github:`` shorthand
            always names github.com, so it only counts when github.com is one of them. An
            https address on a port other than 443 is another server and counts only when
            the host is listed with that port (``ghe.example.com:8443``); an address with
            no port, or an ssh one, counts by the host's name.

    Returns:
        ``owner/repo`` as written in the source, or None when the source is
        not a repository of one of the hosts.
    """
    wanted = {host.lower() for host in (hosts or (GITHUB_HOST,))}
    if not source:
        return None
    stripped = source.strip().split("#", 1)[0]
    if _BARE_SHORTHAND.fullmatch(stripped) and not os.path.exists(stripped):
        # validate_source reads a bare ``owner/repo`` that is not a local
        # directory as a GitHub repository, so this does too.
        stripped = f"github:{stripped}"
    shorthand = GITHUB_SHORTHAND_PATTERN.fullmatch(stripped)
    if shorthand:
        if GITHUB_HOST not in wanted:
            return None
        return f"{shorthand.group('owner')}/{shorthand.group('repo').removesuffix('.git')}"

    ssh = GIT_SSH_PATTERN.fullmatch(stripped)
    if ssh:
        if ssh.group("host").lower() not in wanted:
            return None
        path = ssh.group("path")
    else:
        try:
            parts = urlsplit(stripped)
            port = parts.port
        except ValueError:
            # A port that is not one, or a bracket that is not an IPv6 literal, names no
            # service, and a payload can say anything.
            return None
        if parts.scheme.lower() not in ("https", "http", "ssh", "git"):
            return None
        hostname = (parts.hostname or "").lower()
        # The port of an ssh or git:// address is another service's (ssh answers on its own),
        # and the default of http(s) is the same as none; only another http(s) port is a
        # different server on the same host, and it must be the one the host was given with.
        if _names_another_server(parts.scheme, port):
            if f"{hostname}:{port}" not in wanted:
                return None
        elif hostname not in wanted:
            return None
        path = parts.path
    # The lazy repository group leaves a ``.git`` suffix to the optional
    # group after it, so ``you/app.git`` and ``you/app`` name one repository.
    match = _GITHUB_PATH.fullmatch(path)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


#: What the source of an application deployed with ``git push`` looks like: the domain whose
#: repository (``noust push enable``) its code arrives in. Never typed by an operator and never
#: a place anything is fetched from over the network: only that application's own repository
#: cache may be filled from it (:mod:`noust.pushdeploy.source`).
PUSH_SOURCE_PREFIX = "push:"


def push_source(domain: str) -> str:
    """
    Write the source of an application deployed with ``git push``.

    Args:
        domain: The application's domain.

    Returns:
        ``push:<domain>``.
    """
    return f"{PUSH_SOURCE_PREFIX}{domain}"


def is_push_source(source: str | None) -> bool:
    """
    Tell whether a source is a push repository's.

    Args:
        source: A source as stored or typed.

    Returns:
        True for ``push:`` followed by something; the domain behind it is checked by
        :func:`push_source_domain`.
    """
    return source is not None and source.strip().startswith(PUSH_SOURCE_PREFIX)


#: What the source of an application deployed from an archive somebody uploaded looks like:
#: ``upload:`` and the identifier of the upload (``noust upload add``, the console's file
#: picker). It stands for a directory Noust itself extracted the archive into
#: (:mod:`noust.managers.source_uploads`), so it is a local source that is not a path anybody
#: typed.
UPLOAD_SOURCE_PREFIX = "upload:"

#: An upload's identifier: 128 random bits, which is what makes the id the capability.
_UPLOAD_ID = re.compile(r"[0-9a-f]{32}")


def is_upload_source(source: str | None) -> bool:
    """
    Tell whether a source names an uploaded archive.

    Args:
        source: A source as stored or typed.

    Returns:
        True for ``upload:`` followed by something; the identifier behind it is checked by
        :func:`upload_source_id`.
    """
    return source is not None and source.strip().startswith(UPLOAD_SOURCE_PREFIX)


def upload_source(upload_id: str) -> str:
    """
    Write the source of an application deployed from an uploaded archive.

    Args:
        upload_id: The identifier of the upload.

    Returns:
        ``upload:<id>``.
    """
    return f"{UPLOAD_SOURCE_PREFIX}{upload_id}"


def check_upload_id(text: str) -> str:
    """
    Check that a text is an upload identifier, which is what keeps a path from being named.

    Args:
        text: What a client or an operator gave as the identifier.

    Returns:
        The identifier.

    Raises:
        SourceError: It is not 32 lower-case hex digits.
    """
    if not _UPLOAD_ID.fullmatch(text):
        raise SourceError(
            f"Not an upload: {text!r}",
            details="An upload is named by the identifier 'noust upload add' or the console "
            "printed when the archive was uploaded.",
        )
    return text


def upload_source_id(source: str) -> str:
    """
    Read the identifier an upload source names.

    Args:
        source: A source for which :func:`is_upload_source` is true.

    Returns:
        The identifier.

    Raises:
        SourceError: What follows ``upload:`` is not an identifier Noust issues, which also
            keeps a path from being named through it.
    """
    return check_upload_id(source.strip()[len(UPLOAD_SOURCE_PREFIX) :])


def upload_tree(upload_id: str) -> Path:
    """
    Name the directory an upload was extracted into.

    Args:
        upload_id: The identifier, as :func:`upload_source_id` returns it.

    Returns:
        The directory, which may not exist.

    Raises:
        SourceError: It is not an identifier Noust issues; the path is built from it, so it is
            judged here and not only by the callers that remember to.
    """
    return paths.uploads_dir() / check_upload_id(upload_id)


def is_upload_tree(path: str | os.PathLike[str]) -> bool:
    """
    Tell whether a path is the extracted tree of an upload.

    Args:
        path: A path as a source records it, already resolved.

    Returns:
        True when it is a direct child of the uploads directory named like an identifier.
    """
    candidate = Path(path)
    return candidate.parent == paths.uploads_dir() and bool(_UPLOAD_ID.fullmatch(candidate.name))


def push_source_domain(source: str) -> str:
    """
    Read the domain a push source names.

    Args:
        source: A source for which :func:`is_push_source` is true.

    Returns:
        The domain, validated.

    Raises:
        SourceError: The text after ``push:`` is not a domain.
    """
    text = source.strip()[len(PUSH_SOURCE_PREFIX) :]
    refusal = SourceError(
        f"Not a push repository: {source.strip()!r}",
        details="A push source is push: and the domain of the application whose repository "
        "receives the pushes (noust push enable DOMAIN).",
    )
    try:
        domain = validate_domain(text)
    except DomainError as exc:
        raise refusal from exc
    # validate_domain reads a URL or a path as the domain in it; this has to be one as typed,
    # because it names a directory.
    if domain != text.lower():
        raise refusal
    return domain


def is_git_url(source: str) -> bool:
    """
    Check if source is a Git URL.

    Args:
        source: Source string to check.

    Returns:
        True if source is a Git URL.
    """
    source = source.strip()

    if GITHUB_SHORTHAND_PATTERN.fullmatch(source):
        return True

    # Check SSH formats (user@host:path, ssh://[user@]host[:port]/path)
    if parse_ssh_remote(source) is not None:
        return True

    # Check https, http and git:// (with or without a port)
    if parse_url_remote(source) is not None:
        return True

    # Check for .git suffix with valid URL
    if source.endswith(".git"):
        return True

    return False


def is_local_path(source: str) -> bool:
    """
    Check if source is a local path.

    Args:
        source: Source string to check.

    Returns:
        True if source is a local path.
    """
    source = source.strip()

    # Expand user home
    if source.startswith("~"):
        source = os.path.expanduser(source)

    # Check if it's an absolute or relative path that exists
    path = Path(source)
    return path.exists() or source.startswith("/") or source.startswith("./")


def is_archive_url(source: str) -> bool:
    """
    Check if source is a downloadable archive URL.

    An archive URL may carry its expected checksum in the URL fragment (see
    :func:`noust.managers.source_manager.split_archive_checksum`), so the
    extension check is made against the URL with any ``#...`` fragment
    removed.

    Args:
        source: Source string to check.

    Returns:
        True if source is an archive URL.
    """
    archive_extensions = [".tar.gz", ".tgz", ".tar.bz2", ".zip", ".tar.xz"]
    source_lower = source.lower().split("#", 1)[0]

    if not source_lower.startswith(("http://", "https://")):
        return False

    return any(source_lower.endswith(ext) for ext in archive_extensions)


def parse_git_url(url: str) -> dict:
    """
    Parse a Git URL into components.

    Args:
        url: Git URL to parse.

    Returns:
        Dictionary with host, owner, repo, branch, and, for what the URL says,
        scheme (``ssh``, ``https``, ``http`` or ``git``), user and port. The
        repository is the last part of the path and the owner what comes
        before it, so a GitLab subgroup (``group/sub/app``) is owner
        ``group/sub``, repository ``app``.
    """
    original = url.strip()
    url = expand_github_shorthand(url)
    result: dict = {
        "host": "",
        "owner": "",
        "repo": "",
        "branch": None,
        "original": original,
        "scheme": "",
        "user": None,
        "port": None,
    }

    # Handle branch specification (url#branch)
    if "#" in url:
        url, result["branch"] = url.rsplit("#", 1)

    def fill(path: str) -> None:
        parts = [part for part in path.removesuffix(".git").split("/") if part]
        if parts:
            result["repo"] = parts[-1]
            result["owner"] = "/".join(parts[:-1])

    remote = parse_ssh_remote(url)
    if remote is not None:
        result.update(scheme="ssh", host=remote.host, user=remote.user, port=remote.port)
        fill(remote.path)
        return result

    # HTTPS and git:// share a shape. git:// used to fall through here with
    # no host, so a transport is_git_url and the remote URL check accept was
    # refused as "Could not determine Git host".
    http_remote = parse_url_remote(url)
    if http_remote is not None:
        result.update(scheme=http_remote.scheme, host=http_remote.host, port=http_remote.port)
        fill(http_remote.path)
        return result

    return result


def validate_git_url(url: str) -> tuple[bool, str]:
    """
    Validate a Git URL format.

    Args:
        url: Git URL to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not url:
        return False, "Git URL cannot be empty"

    url = url.strip()

    # A port that is not one would otherwise be reported as a host that could not be found.
    bad_port = _bad_port(url)
    if bad_port is not None:
        return False, _port_refusal(bad_port)

    # Check for valid patterns
    if not is_git_url(url):
        return False, "Invalid Git URL format"

    # Parse and check components
    parsed = parse_git_url(url)
    if not parsed["host"]:
        return False, "Could not determine Git host"

    if not parsed["repo"]:
        return False, "Could not determine repository name"

    return True, ""


def validate_local_path(path: str) -> tuple[bool, str]:
    """
    Validate a local path.

    Args:
        path: Local path to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not path:
        return False, "Path cannot be empty"

    # Expand user home
    if path.startswith("~"):
        path = os.path.expanduser(path)

    path_obj = Path(path)

    if not path_obj.exists():
        return False, f"Path does not exist: {path}"

    if not path_obj.is_dir():
        return False, f"Path is not a directory: {path}"

    # Check for common project files
    project_indicators = [
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "index.html",
        "Cargo.toml",
        "go.mod",
    ]

    has_project_file = any((path_obj / f).exists() for f in project_indicators)
    if not has_project_file:
        return False, "Directory doesn't appear to contain a valid project"

    return True, ""


def validate_source(
    source: str,
    must_exist: bool = False,
) -> tuple[str, str]:
    """
    Validate and identify a source.

    Args:
        source: Source to validate (Git URL or local path).
        must_exist: For local paths, require the path to exist.

    Returns:
        Tuple of (source_type, normalized_source).
        source_type is one of: "git", "local", "archive".

    Raises:
        SourceError: If source is invalid.
    """
    if not isinstance(source, str) or not source.strip():
        raise SourceError("Source is required")

    # Before anything else looks at it: a line break, a NUL byte or another control
    # character is never part of a repository, a URL or a path somebody means, and
    # each of them has been the way a value got a second line or lost its tail.
    if _CONTROL_CHARACTER.search(source.strip()):
        raise SourceError(
            "The source contains a control character",
            details="Remove line breaks, NUL bytes and other control characters from the source.",
        )

    if is_push_source(source):
        # A git source for everything that judges one (commits, tags, branches); only the
        # code that fills the repository cache knows where it is (noust.pushdeploy.source).
        return "git", push_source(push_source_domain(source))

    if is_upload_source(source):
        # A local source for everything that copies one; only an upload Noust extracted exists
        # at that name, so no text can reach another directory through it.
        tree = upload_tree(upload_source_id(source))
        if not tree.is_dir():
            raise SourceError(
                f"There is no upload {source.strip()[len(UPLOAD_SOURCE_PREFIX) :]}",
                details="It was never finished, or it was removed because nothing used it. "
                "Upload the archive again.",
            )
        return "local", str(tree)

    source = expand_github_shorthand(source)

    # Check if it's a Git URL
    if is_git_url(source):
        is_valid, error = validate_git_url(source)
        if not is_valid:
            raise SourceError(f"Invalid Git URL: {error}")
        return "git", source

    # Check if it's an archive URL
    if is_archive_url(source):
        return "archive", source

    # Check if it's a local path. The file system has its own refusals (a name too
    # long for it, a loop of links): they are this validator's refusals too, not an
    # OSError the caller has to know to catch.
    try:
        is_local = is_local_path(source)
        if is_local:
            if source.startswith("~"):
                source = os.path.expanduser(source)
            source = str(Path(source).resolve())
    except (OSError, ValueError) as exc:
        raise SourceError("The source is not a path this server can use", details=str(exc)) from exc

    if is_local:
        if must_exist:
            is_valid, error = validate_local_path(source)
            if not is_valid:
                raise SourceError(error)

        return "local", source

    # Could be a GitHub shorthand (user/repo)
    if re.fullmatch(r"[\w.-]+/[\w.-]+", source):
        # Convert to GitHub URL
        github_url = f"https://github.com/{source}.git"
        return "git", github_url

    bad_port = _bad_port(source)
    if bad_port is not None:
        raise SourceError(f"Invalid Git URL: {_port_refusal(bad_port)}")

    raise SourceError(
        f"Invalid source: '{source}'",
        details="Source must be a Git URL (SSH or HTTPS), local path, or GitHub shorthand (user/repo)",
    )


def get_repo_name(source: str) -> str:
    """
    Extract repository/project name from source.

    Args:
        source: Source URL or path.

    Returns:
        Repository name.
    """
    source = source.strip()

    # Git URL
    if is_git_url(source):
        parsed = parse_git_url(source)
        return parsed["repo"] or "app"

    # Archive URL: a trailing #sha256=... or #checksum=... fragment names a
    # checksum, not part of the file name.
    if is_archive_url(source):
        return Path(source.split("#", 1)[0]).name or "app"

    # Local path
    if source.startswith("~"):
        source = os.path.expanduser(source)

    return Path(source).name or "app"


def is_valid_source(source: str) -> bool:
    """
    Check if source is valid (Git URL or local path).

    Args:
        source: Source string to validate.

    Returns:
        True if source is valid, False otherwise.
    """
    if not source or not source.strip():
        return False

    source = source.strip()

    # Check if it's a Git URL
    if is_git_url(source):
        is_valid, _ = validate_git_url(source)
        return is_valid

    # Check if it's an archive URL
    if is_archive_url(source):
        return True

    # Check if it's a local path
    if is_local_path(source):
        return True

    return False
