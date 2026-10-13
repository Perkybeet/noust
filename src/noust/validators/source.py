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

from noust.core.exceptions import SourceError

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
GIT_HTTPS_PATTERN = re.compile(r"^https?://(?P<host>[\w.-]+)/(?P<path>[\w./-]+?)(?:\.git)?$")
GIT_PROTOCOL_PATTERN = re.compile(r"^git://(?P<host>[\w.-]+)/(?P<path>[\w./-]+?)(?:\.git)?$")

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
            always names github.com, so it only counts when github.com is one of them.

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
        parts = urlsplit(stripped)
        if parts.scheme.lower() not in ("https", "http", "ssh", "git"):
            return None
        hostname = (parts.hostname or "").lower()
        authority = f"{hostname}:{parts.port}" if parts.port else hostname
        if hostname not in wanted and authority not in wanted:
            return None
        path = parts.path
    # The lazy repository group leaves a ``.git`` suffix to the optional
    # group after it, so ``you/app.git`` and ``you/app`` name one repository.
    match = _GITHUB_PATH.fullmatch(path)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


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

    # Check HTTPS format
    if GIT_HTTPS_PATTERN.fullmatch(source):
        return True

    # Check git:// protocol
    if GIT_PROTOCOL_PATTERN.fullmatch(source):
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
    match = GIT_HTTPS_PATTERN.fullmatch(url) or GIT_PROTOCOL_PATTERN.fullmatch(url)
    if match:
        result["scheme"] = url.split("://", 1)[0].lower()
        result["host"] = match.group("host")
        fill(match.group("path"))
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
