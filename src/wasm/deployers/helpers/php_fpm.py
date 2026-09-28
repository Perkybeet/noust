# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PHP-FPM on this machine: where a pool goes, which service runs it, and asking it a question.

A PHP application has no process of its own. It is a pool in the distribution's
PHP-FPM, one file per application in the pool directory, listening on its own
socket and running as the service user. Three things differ between the
distributions WASM supports, and :func:`find_fpm` is the one place that knows
them:

- Debian and Ubuntu install one FPM per PHP version: pools in
  ``/etc/php/<version>/fpm/pool.d``, the service ``php<version>-fpm``, the
  binary ``php-fpm<version>``, sockets in ``/run/php``.
- Fedora and RHEL install one unversioned FPM: pools in ``/etc/php-fpm.d``,
  the service and binary ``php-fpm``, sockets in ``/run/php-fpm``.
- openSUSE: pools in ``/etc/php<major>/fpm/php-fpm.d``, the service and binary
  ``php-fpm``, sockets in ``/run/php-fpm``.

A pool file is never left in place when FPM refuses it: FPM reads every pool
at once, so one bad file stops every PHP site on the server at the next
reload. :meth:`FpmService.install_pool` tests the whole configuration with
the new file and puts the previous one back when the test fails.

The health of a PHP application is asked of its pool directly, over FastCGI
on its socket (:func:`fastcgi_request`), with the same path, expectation and
timeout the health gate uses for a process that listens on a port. Going
through nginx instead would test nginx: with a certificate the port-80
server only redirects, so every release would pass.
"""

from __future__ import annotations

import os
import re
import socket
import struct
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined
from jinja2 import TemplateError as JinjaTemplateError

from wasm.core.exceptions import DeploymentError, TemplateError, ValidationError
from wasm.core.fs import FileSystem
from wasm.core.logger import Logger
from wasm.core.runner import CommandResult, CommandRunner

#: Every pool and socket WASM writes carries this prefix, so one never
#: collides with the distribution's own ``www`` pool or another tool's.
POOL_PREFIX = "wasm-"

#: Pool files hold the application's environment, secrets included, and only
#: the FPM master (root) reads them.
POOL_MODE = 0o640

#: ``php-fpm -t`` and a reload are quick; a stuck one must still end.
FPM_CONTROL_TIMEOUT = 60

#: Journal lines attached to a failed health gate.
JOURNAL_TIMEOUT = 15

#: Workers a pool may grow to, when the application sets no memory limit.
DEFAULT_MAX_CHILDREN = 5

#: ``memory_limit`` of a worker when the application sets no memory limit.
DEFAULT_MEMORY_LIMIT = "256M"

#: The smallest per-worker ``memory_limit`` derived from an application
#: limit: below this, WordPress and most frameworks cannot boot.
MIN_WORKER_MEMORY_MB = 64

#: A size as PHP and nginx both read it: a number, optionally k, m or g.
SIZE_PATTERN = re.compile(r"^[1-9][0-9]{0,5}[kKmMgG]?$")

#: How to install PHP-FPM, per package manager, for every message that says
#: it is missing.
FPM_INSTALL_HINT = (
    "Install it: apt install php-fpm php-mysql php-pgsql php-curl php-gd php-mbstring "
    "php-xml php-zip php-intl; dnf install php-fpm php-mysqlnd php-pgsql php-gd php-mbstring "
    "php-xml php-intl; zypper install php8-fpm php8-mysql php8-pgsql php8-gd php8-mbstring "
    "php8-intl php8-zip. On openSUSE, also copy /etc/php8/fpm/php-fpm.conf.default to "
    "php-fpm.conf."
)

_DEBIAN_VERSION = re.compile(r"^\d+\.\d+$")
_NGINX_USER = re.compile(r"^\s*user\s+([A-Za-z0-9_.-]+)(?:\s+([A-Za-z0-9_.-]+))?\s*;", re.M)


@dataclass(frozen=True)
class FpmInstallation:
    """
    The PHP-FPM this machine runs pools in.

    Attributes:
        pool_dir: Directory FPM includes every ``*.conf`` of.
        service: systemd unit of the FPM master.
        binary: Absolute path of the FPM binary, which tests a configuration.
        socket_dir: Directory the pools' sockets are created in; it exists
            while FPM runs.
        version: PHP version on Debian and Ubuntu, which install one FPM per
            version; None where there is one unversioned FPM.
    """

    pool_dir: Path
    service: str
    binary: str
    socket_dir: Path
    version: str | None = None

    def pool_file(self, app_name: str) -> Path:
        """
        Say where an application's pool is written.

        Args:
            app_name: The application name.

        Returns:
            ``<pool_dir>/wasm-<app_name>.conf``.
        """
        return self.pool_dir / f"{POOL_PREFIX}{app_name}.conf"

    def socket(self, app_name: str) -> Path:
        """
        Say where an application's pool listens.

        Args:
            app_name: The application name.

        Returns:
            ``<socket_dir>/wasm-<app_name>.sock``.
        """
        return self.socket_dir / f"{POOL_PREFIX}{app_name}.sock"


def _version_key(version: str) -> tuple[int, ...]:
    """
    Order PHP versions numerically, so 8.10 is newer than 8.9.

    Args:
        version: ``<major>.<minor>``.

    Returns:
        The version as a tuple of integers.
    """
    return tuple(int(part) for part in version.split("."))


def find_fpm(root: Path = Path("/")) -> FpmInstallation:
    """
    Find the PHP-FPM pools are written for.

    On Debian and Ubuntu the newest PHP version whose FPM binary is installed
    wins; an older one kept beside it serves nothing WASM writes.

    Args:
        root: The filesystem root, for tests.

    Returns:
        The installation.

    Raises:
        DeploymentError: When no PHP-FPM is installed.
    """
    etc_php = root / "etc" / "php"
    if etc_php.is_dir():
        versions = sorted(
            (
                entry.name
                for entry in etc_php.iterdir()
                if _DEBIAN_VERSION.match(entry.name) and (entry / "fpm" / "pool.d").is_dir()
            ),
            key=_version_key,
            reverse=True,
        )
        for version in versions:
            binary = root / "usr" / "sbin" / f"php-fpm{version}"
            if binary.exists():
                return FpmInstallation(
                    pool_dir=etc_php / version / "fpm" / "pool.d",
                    service=f"php{version}-fpm",
                    binary=f"/usr/sbin/php-fpm{version}",
                    socket_dir=Path("/run/php"),
                    version=version,
                )

    fedora = root / "etc" / "php-fpm.d"
    if fedora.is_dir() and (root / "usr" / "sbin" / "php-fpm").exists():
        return FpmInstallation(
            pool_dir=fedora,
            service="php-fpm",
            binary="/usr/sbin/php-fpm",
            socket_dir=Path("/run/php-fpm"),
        )

    suse = sorted((root / "etc").glob("php[0-9]*/fpm/php-fpm.d"), reverse=True)
    if suse and (root / "usr" / "sbin" / "php-fpm").exists():
        return FpmInstallation(
            pool_dir=suse[0],
            service="php-fpm",
            binary="/usr/sbin/php-fpm",
            socket_dir=Path("/run/php-fpm"),
        )

    raise DeploymentError("PHP-FPM is not installed", details=FPM_INSTALL_HINT)


def nginx_worker_group(default: str, root: Path = Path("/")) -> str:
    """
    Name the group nginx's workers run as, which must be able to open a pool's socket.

    Read from the ``user`` directive of ``nginx.conf``: ``www-data`` on
    Debian, ``nginx`` on Fedora. A directive that names a group as well
    (``user nginx www;``) gives the group.

    Args:
        default: What to answer when nginx.conf does not say.
        root: The filesystem root, for tests.

    Returns:
        The group name.
    """
    try:
        text = (root / "etc" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default
    match = _NGINX_USER.search(text)
    if match is None:
        return default
    return match.group(2) or match.group(1)


# Pool rendering -----------------------------------------------------------


@dataclass(frozen=True)
class PoolSpec:
    """
    Everything one application's pool file says.

    Attributes:
        app_name: The application name; names the pool and its socket.
        domain: The application's domain, for the file's header.
        user: Account the workers run as.
        group: Group the workers run as.
        listen_group: Group the socket belongs to: nginx's workers'.
        socket: The socket path.
        env: The application's environment, in order.
        max_upload: Largest request body, as PHP reads a size (``64m``).
        memory_max_mb: The application's memory limit, from which each
            worker's ``memory_limit`` is derived; None for the default.
        max_children: Most workers the pool grows to.
    """

    app_name: str
    domain: str
    user: str
    group: str
    listen_group: str
    socket: Path
    env: Mapping[str, str] = field(default_factory=dict)
    max_upload: str = "64m"
    memory_max_mb: int | None = None
    max_children: int = DEFAULT_MAX_CHILDREN

    def memory_limit(self) -> str:
        """
        Derive a worker's ``memory_limit``.

        The application's limit covers every worker together, so each gets
        its share, never less than :data:`MIN_WORKER_MEMORY_MB`.

        Returns:
            A PHP size such as ``256M``.
        """
        if self.memory_max_mb is None:
            return DEFAULT_MEMORY_LIMIT
        share = max(MIN_WORKER_MEMORY_MB, self.memory_max_mb // max(1, self.max_children))
        return f"{share}M"


def validate_size(value: str, *, field_name: str) -> str:
    """
    Check a size that ends up in both nginx and PHP configuration.

    Args:
        value: Candidate size, such as ``64m``.
        field_name: What the size is, for the message.

    Returns:
        The size, lower-cased.

    Raises:
        ValidationError: When it is not a plain number with an optional unit.
    """
    if not SIZE_PATTERN.match(value or ""):
        raise ValidationError(
            f"Invalid {field_name}: {value!r}",
            details="Use a number with an optional unit k, m or g, such as 64m.",
        )
    return value.lower()


def pool_env_lines(env: Mapping[str, str]) -> list[tuple[str, str]]:
    """
    Turn the application's environment into what a pool file can carry.

    Every value is written single-quoted, which FPM takes literally (no
    ``${...}`` expansion, no ``;`` comment): the one character it cannot hold
    is a single quote. An empty value is left out, since FPM rejects
    ``env[X] = ''`` and ``getenv()`` answers the same for unset and empty.

    Args:
        env: Variable name to value, already validated as an environment.

    Returns:
        ``(name, value)`` pairs, sorted by name.

    Raises:
        ValidationError: When a value holds a single quote or a line break.
    """
    lines: list[tuple[str, str]] = []
    for key in sorted(env):
        value = env[key]
        if value == "":
            continue
        if "'" in value or "\n" in value or "\r" in value or "\0" in value:
            raise ValidationError(
                f"{key} cannot be passed to PHP-FPM",
                details="A PHP-FPM pool carries each variable as a single-quoted value, "
                "which cannot contain a single quote or a line break. Change the value "
                f"with: wasm env set <domain> {key}=...",
            )
        lines.append((key, value))
    return lines


def render_pool(spec: PoolSpec) -> str:
    """
    Render an application's pool file.

    Args:
        spec: What the pool says.

    Returns:
        The file's content.

    Raises:
        ValidationError: When a value cannot be written into a pool file.
        TemplateError: When the template is missing or fails to render.
    """
    try:
        environment = Environment(
            loader=PackageLoader("wasm", "templates/php"),
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
            # An INI file, not markup; every value is validated above instead.
            autoescape=False,  # noqa: S701
        )
        return environment.get_template("pool.conf.j2").render(
            pool=f"{POOL_PREFIX}{spec.app_name}",
            domain=spec.domain,
            user=spec.user,
            group=spec.group,
            listen_group=spec.listen_group,
            socket=str(spec.socket),
            max_children=spec.max_children,
            memory_limit=spec.memory_limit(),
            max_upload=validate_size(spec.max_upload, field_name="upload size"),
            env=pool_env_lines(spec.env),
        )
    except (ValueError, ImportError, JinjaTemplateError) as exc:
        raise TemplateError(
            "Could not render the PHP-FPM pool",
            details=f"{exc}. Reinstall the wasm package if templates/php is missing.",
        ) from exc


# The FPM service ------------------------------------------------------------


class FpmService:
    """
    The distribution's PHP-FPM master: test, reload, read its journal.

    It also satisfies the health gate's ``UnitControl``, so a failed gate
    carries FPM's journal the way an application's gate carries its unit's.
    """

    def __init__(
        self,
        installation: FpmInstallation,
        *,
        runner: CommandRunner,
        fs: FileSystem,
        logger: Logger,
    ) -> None:
        """
        Initialize the controller.

        Args:
            installation: The FPM to control.
            runner: Where commands run.
            fs: Where pool files are written.
            logger: Where progress is reported.
        """
        self.installation = installation
        self._runner = runner
        self._fs = fs
        self._logger = logger

    def config_errors(self) -> str | None:
        """
        Test the whole FPM configuration, every pool included.

        Returns:
            None when FPM accepts it; otherwise FPM's own output, verbatim.
        """
        result = self._runner.run([self.installation.binary, "-t"], timeout=FPM_CONTROL_TIMEOUT)
        if result.success:
            return None
        return _output(result) or f"{self.installation.binary} -t exited with {result.exit_code}"

    def reload(self) -> None:
        """
        Reload FPM, starting it when it is not running.

        A reload restarts every worker gracefully, which is also what drops
        the opcache of the release ``current`` pointed at before.

        Raises:
            DeploymentError: When systemd could not reload or start it.
        """
        result = self._runner.run(
            ["systemctl", "reload-or-restart", self.installation.service],
            timeout=FPM_CONTROL_TIMEOUT,
        )
        if not result.success:
            raise DeploymentError(
                f"{self.installation.service} did not reload",
                details=_output(result)
                or f"See why with: systemctl status {self.installation.service}",
            )

    def restart(self, name: str) -> None:
        """
        Reload FPM: the health gate's restart of a PHP application.

        Args:
            name: The unit the gate names; always this FPM's.

        Raises:
            DeploymentError: When FPM did not reload.
        """
        self.reload()

    def logs(self, name: str, lines: int = 50) -> str:
        """
        Read the end of FPM's journal.

        Args:
            name: The unit whose journal to read.
            lines: How many lines.

        Returns:
            The journal's own output; empty when it could not be read.
        """
        result = self._runner.run(
            ["journalctl", "-u", name, "-n", str(lines), "--no-pager"],
            timeout=JOURNAL_TIMEOUT,
        )
        return result.stdout if result.success else ""

    def install_pool(self, path: Path, content: str) -> bool:
        """
        Put a pool file in place, keep it only if FPM accepts it, and reload.

        Args:
            path: The pool file.
            content: What it must say.

        Returns:
            Whether the file changed (and FPM was reloaded).

        Raises:
            ValidationError: When FPM refuses the configuration; ``details`` is
                its own output, and the previous file is back in place.
            DeploymentError: When FPM did not reload.
        """
        previous = _read(path)
        if previous == content:
            return False
        self._fs.write_text(path, content, mode=POOL_MODE)
        problem = self.config_errors()
        if problem is not None:
            if previous is None:
                self._fs.remove(path)
            else:
                self._fs.write_text(path, previous, mode=POOL_MODE)
            raise ValidationError(
                f"PHP-FPM rejected the pool {path.name}",
                details=problem,
            )
        self.reload()
        return True

    def remove_pool(self, path: Path) -> bool:
        """
        Remove a pool file and reload FPM, so its socket and workers go.

        Args:
            path: The pool file.

        Returns:
            Whether there was a file to remove.

        Raises:
            DeploymentError: When FPM did not reload.
        """
        if not os.path.lexists(path):
            return False
        self._fs.remove(path)
        self.reload()
        return True


def _read(path: Path) -> str | None:
    """
    Read a pool file that may not exist.

    Args:
        path: The file.

    Returns:
        Its content, or None when there is none.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _output(result: CommandResult) -> str:
    """
    Join what a command said on both streams.

    Args:
        result: The command outcome.

    Returns:
        stderr and stdout, stripped, one after the other.
    """
    return "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())


# FastCGI --------------------------------------------------------------------

_FCGI_VERSION = 1
_FCGI_BEGIN_REQUEST = 1
_FCGI_END_REQUEST = 3
_FCGI_PARAMS = 4
_FCGI_STDIN = 5
_FCGI_STDOUT = 6
_FCGI_STDERR = 7
_FCGI_RESPONDER = 1
_REQUEST_ID = 1
_HEADER = struct.Struct("!BBHHBx")
_MAX_CONTENT = 65535

#: Largest response the probe reads; a health check needs the headers.
_MAX_RESPONSE = 1024 * 1024


@dataclass(frozen=True)
class FastCgiResponse:
    """
    What a pool answered.

    Attributes:
        status: The HTTP status, from the ``Status`` header (200 without one).
        headers: Response headers, names lower-cased.
        stderr: What PHP wrote to FastCGI's error stream: its fatal errors.
    """

    status: int
    headers: dict[str, str]
    stderr: str


def _record(kind: int, content: bytes) -> bytes:
    """
    Frame one FastCGI record.

    Args:
        kind: Record type.
        content: At most 65535 bytes.

    Returns:
        The header, the content and its padding.
    """
    padding = -len(content) % 8
    return _HEADER.pack(_FCGI_VERSION, kind, _REQUEST_ID, len(content), padding) + (
        content + b"\0" * padding
    )


def _length(size: int) -> bytes:
    """
    Encode a name or value length as FastCGI does.

    Args:
        size: The length.

    Returns:
        One byte below 128, four with the high bit set otherwise.
    """
    if size < 128:
        return bytes([size])
    return struct.pack("!I", size | 0x80000000)


def _params(params: Mapping[str, str]) -> bytes:
    """
    Encode request parameters as FastCGI name-value pairs.

    Args:
        params: CGI variables.

    Returns:
        The encoded stream, before framing.
    """
    out = bytearray()
    for name, value in params.items():
        key, val = name.encode(), value.encode()
        out += _length(len(key)) + _length(len(val)) + key + val
    return bytes(out)


def _receive(sock: socket.socket, size: int) -> bytes:
    """
    Read exactly ``size`` bytes.

    Args:
        sock: The connected socket.
        size: How many bytes.

    Returns:
        The bytes.

    Raises:
        OSError: When the pool closed the connection early.
    """
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("PHP-FPM closed the connection mid-response")
        data += chunk
    return bytes(data)


def fastcgi_request(
    socket_path: Path, params: Mapping[str, str], *, timeout: float
) -> FastCgiResponse:
    """
    Send one GET request to a pool and read its answer.

    Args:
        socket_path: The pool's socket.
        params: CGI variables: SCRIPT_FILENAME, REQUEST_URI and the rest.
        timeout: Seconds for the whole exchange.

    Returns:
        The status, headers and PHP's error stream.

    Raises:
        OSError: When the pool cannot be reached or answers garbage.
    """
    encoded = _params(params)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        payload = bytearray(_record(_FCGI_BEGIN_REQUEST, struct.pack("!HB5x", _FCGI_RESPONDER, 0)))
        for start in range(0, len(encoded), _MAX_CONTENT):
            payload += _record(_FCGI_PARAMS, encoded[start : start + _MAX_CONTENT])
        payload += _record(_FCGI_PARAMS, b"") + _record(_FCGI_STDIN, b"")
        sock.sendall(bytes(payload))

        stdout = bytearray()
        stderr = bytearray()
        while True:
            _version, kind, _request, length, padding = _HEADER.unpack(_receive(sock, _HEADER.size))
            content = _receive(sock, length + padding)[:length]
            if kind == _FCGI_STDOUT:
                stdout += content
            elif kind == _FCGI_STDERR:
                stderr += content
            elif kind == _FCGI_END_REQUEST:
                break
            if len(stdout) + len(stderr) > _MAX_RESPONSE:
                break
    return _parse_response(bytes(stdout), bytes(stderr))


def _parse_response(stdout: bytes, stderr: bytes) -> FastCgiResponse:
    """
    Read the CGI headers off a response.

    Args:
        stdout: What the pool wrote to FastCGI's output stream.
        stderr: What it wrote to the error stream.

    Returns:
        The response.

    Raises:
        OSError: When the output carries no header block.
    """
    head, separator, _body = stdout.partition(b"\r\n\r\n")
    if not separator:
        head, separator, _body = stdout.partition(b"\n\n")
    if not separator and stdout:
        raise ConnectionError("PHP-FPM answered without headers")
    headers: dict[str, str] = {}
    for line in head.decode("latin-1").splitlines():
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip().lower()] = value.strip()
    status = 200
    raw = headers.get("status", "")
    if raw[:3].isdigit():
        status = int(raw[:3])
    elif "location" in headers:
        # A CGI script that sends Location without Status is a redirect.
        status = 302
    return FastCgiResponse(
        status=status, headers=headers, stderr=stderr.decode("utf-8", errors="replace").strip()
    )


def fastcgi_params(
    *,
    document_root: Path,
    path: str,
    domain: str,
    https: bool,
) -> dict[str, str]:
    """
    Build the CGI variables nginx would send for a GET of ``path``.

    The script is chosen as the site's front controller does: a path that
    names a ``.php`` file runs that file, anything else runs ``index.php``.
    The document root is resolved here, as ``$realpath_root`` is in the
    site, so the probe runs the release ``current`` points at now.

    Args:
        document_root: The directory the site serves.
        path: The health path, with its query string if any.
        domain: The primary domain, as the Host header.
        https: Whether the site is served over TLS.

    Returns:
        The variables.
    """
    root = Path(os.path.realpath(document_root))
    uri, _, query = path.partition("?")
    script = uri if uri.endswith(".php") else "/index.php"
    return {
        "GATEWAY_INTERFACE": "CGI/1.1",
        "SERVER_SOFTWARE": "wasm-health-check",
        "REQUEST_METHOD": "GET",
        "REQUEST_URI": path,
        "DOCUMENT_URI": script,
        "SCRIPT_NAME": script,
        "SCRIPT_FILENAME": f"{root}{script}",
        "DOCUMENT_ROOT": str(root),
        "QUERY_STRING": query,
        "SERVER_PROTOCOL": "HTTP/1.1",
        "SERVER_NAME": domain,
        "SERVER_PORT": "443" if https else "80",
        "HTTP_HOST": domain,
        "REMOTE_ADDR": "127.0.0.1",
        "REMOTE_PORT": "0",
        "SERVER_ADDR": "127.0.0.1",
        "REQUEST_SCHEME": "https" if https else "http",
        **({"HTTPS": "on"} if https else {}),
    }


#: A single request to a pool; injectable for tests.
Requester = Callable[..., FastCgiResponse]


def fastcgi_probe(
    socket_path: Path,
    params: Callable[[], Mapping[str, str]],
    *,
    requester: Requester = fastcgi_request,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[..., bool]:
    """
    Build a health probe that asks a pool over FastCGI.

    The probe has the signature of
    :func:`~wasm.deployers.helpers.health.wait_until_healthy`, so the health
    gate drives it exactly as it drives an HTTP probe: the same attempts, the
    same expectation, the same wall-clock limit.

    Args:
        socket_path: The pool's socket.
        params: Builds the request's CGI variables at each attempt, so the
            document root is resolved after ``current`` moved.
        requester: Sends one request.
        clock: Monotonic time source.
        sleep: How to pause.

    Returns:
        The probe.
    """

    def probe(
        _url: str,
        *,
        retries: int = 5,
        delay: float = 2.0,
        on_attempt: Callable[[str], None] | None = None,
        accept: Callable[[int], bool] | None = None,
        within: float | None = None,
    ) -> bool:
        deadline = clock() + within if within is not None else None
        for attempt in range(retries):
            timeout = 5.0
            if deadline is not None:
                left = deadline - clock()
                if left <= 0:
                    break
                timeout = min(timeout, left)
            try:
                response = requester(socket_path, params(), timeout=timeout)
            # Not answering yet is what a probe exists to report, never a crash.
            except OSError as exc:
                reason = f"{socket_path}: {exc}"
            else:
                ok = accept(response.status) if accept is not None else response.status == 200
                if ok:
                    return True
                reason = f"HTTP {response.status} is not an expected status"
                if response.stderr:
                    reason += f"\n{response.stderr}"
            if on_attempt is not None:
                on_attempt(f"Health check attempt {attempt + 1} failed: {reason}")
            if attempt == retries - 1:
                break
            pause = delay if deadline is None else min(delay, deadline - clock())
            if pause <= 0:
                break
            sleep(pause)
        return False

    return probe


def as_sequence(value: object, *, field_name: str) -> Sequence[str]:
    """
    Read a list of strings from settings that came off disk.

    Args:
        value: The value.
        field_name: What it is, for the message.

    Returns:
        The strings.

    Raises:
        ValidationError: When it is not a list of strings.
    """
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError(f"{field_name} must be a list of strings", details=f"Got {value!r}.")
    return value
