# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the PHP-FPM deployer: detection, pools, the FastCGI probe and the pipeline.

The machine is faked the way the release pipeline's tests fake it - git,
nginx and the probe - plus a PHP-FPM installation laid out in a temporary
root. Releases, ``current`` and ``shared/`` are real directories and links.
"""

from __future__ import annotations

import os
import shutil
import socket
import stat
import struct
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_release_pipeline import FakeGit, FakeServices, FakeWeb, TreeRunner, write_tree
from tests.test_webserver_managers import FakeStore
from wasm.core.exceptions import DeploymentError, ValidationError
from wasm.core.logger import Logger
from wasm.core.runner import FakeRunner
from wasm.core.store import App, WASMStore
from wasm.deployers import lifecycle
from wasm.deployers import php_fpm as php_module
from wasm.deployers.helpers import php_fpm as fpm_module
from wasm.deployers.helpers.health_gate import HealthCheck
from wasm.deployers.helpers.layout import INPLACE, RELEASES
from wasm.deployers.helpers.php_fpm import (
    FastCgiResponse,
    FpmService,
    PoolSpec,
    fastcgi_params,
    fastcgi_probe,
    fastcgi_request,
    find_fpm,
    nginx_worker_group,
    pool_env_lines,
    render_pool,
)
from wasm.deployers.php_fpm import (
    PHP_SETTINGS_FILE,
    PhpFpmDeployer,
    PhpSettings,
    detect_webroot,
    load_php_settings,
)
from wasm.deployers.registry import detect_app_type
from wasm.managers.nginx_manager import NGINX_BACKEND, NginxManager

DOMAIN = "blog.example.com"
APP = "blog-example-com"
GIT_URL = "https://github.com/example/blog.git"
POOL_DIR = "etc/php/8.2/fpm/pool.d"

WORDPRESS = {
    "index.php": "<?php require __DIR__ . '/wp-blog-header.php';\n",
    "wp-config-sample.php": "<?php\n",
    "wp-content/plugins/akismet/akismet.php": "<?php\n",
    "wp-content/themes/twentyfour/style.css": "/* theme */\n",
}


# ---------------------------------------------------------------------------
# A PHP-FPM installation on disk
# ---------------------------------------------------------------------------


def debian_fpm(root: Path, *versions: str, binaries: tuple[str, ...] | None = None) -> Path:
    """Lay out Debian's per-version FPM directories under a fake root."""
    for version in versions:
        (root / "etc" / "php" / version / "fpm" / "pool.d").mkdir(parents=True, exist_ok=True)
    sbin = root / "usr" / "sbin"
    sbin.mkdir(parents=True, exist_ok=True)
    for version in versions if binaries is None else binaries:
        (sbin / f"php-fpm{version}").write_text("")
    return root


def test_debian_picks_the_newest_version_that_has_a_binary(tmp_path: Path) -> None:
    """An FPM directory left behind by an uninstalled version is not chosen."""
    root = debian_fpm(tmp_path, "7.4", "8.1", "8.3", binaries=("7.4", "8.1"))

    found = find_fpm(root)

    assert found.version == "8.1"
    assert found.pool_dir == root / "etc/php/8.1/fpm/pool.d"
    assert found.service == "php8.1-fpm"
    assert found.binary == "/usr/sbin/php-fpm8.1"
    assert found.socket(APP) == Path(f"/run/php/wasm-{APP}.sock")
    assert found.pool_file(APP).name == f"wasm-{APP}.conf"


def test_versions_compare_numerically(tmp_path: Path) -> None:
    """8.10 is newer than 8.9."""
    assert find_fpm(debian_fpm(tmp_path, "8.9", "8.10")).version == "8.10"


def test_fedora_has_one_unversioned_fpm(tmp_path: Path) -> None:
    """/etc/php-fpm.d, php-fpm, and sockets in /run/php-fpm."""
    (tmp_path / "etc/php-fpm.d").mkdir(parents=True)
    (tmp_path / "usr/sbin").mkdir(parents=True)
    (tmp_path / "usr/sbin/php-fpm").write_text("")

    found = find_fpm(tmp_path)

    assert (found.pool_dir, found.service, found.version) == (
        tmp_path / "etc/php-fpm.d",
        "php-fpm",
        None,
    )
    assert found.socket_dir == Path("/run/php-fpm")


def test_opensuse_pools_live_under_the_versioned_php_directory(tmp_path: Path) -> None:
    """/etc/php8/fpm/php-fpm.d on openSUSE."""
    (tmp_path / "etc/php8/fpm/php-fpm.d").mkdir(parents=True)
    (tmp_path / "usr/sbin").mkdir(parents=True)
    (tmp_path / "usr/sbin/php-fpm").write_text("")

    assert find_fpm(tmp_path).pool_dir == tmp_path / "etc/php8/fpm/php-fpm.d"


def test_no_fpm_says_how_to_install_it(tmp_path: Path) -> None:
    """Every distribution's package names are in the message."""
    with pytest.raises(DeploymentError, match="PHP-FPM is not installed") as failure:
        find_fpm(tmp_path)

    assert "apt install php-fpm" in failure.value.details
    assert "dnf install php-fpm" in failure.value.details
    assert "zypper install php8-fpm" in failure.value.details


@pytest.mark.parametrize(
    ("directive", "group"),
    [
        ("user www-data;", "www-data"),
        ("user  nginx;", "nginx"),
        ("user nginx www;", "www"),
        ("# user nobody;", "fallback"),
    ],
)
def test_the_socket_group_is_the_one_nginx_workers_run_as(
    tmp_path: Path, directive: str, group: str
) -> None:
    """Read from nginx.conf, falling back to the service group."""
    conf = tmp_path / "etc/nginx/nginx.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(f"{directive}\nworker_processes auto;\n")

    assert nginx_worker_group("fallback", root=tmp_path) == group


# ---------------------------------------------------------------------------
# Pool files
# ---------------------------------------------------------------------------


def spec(**overrides: Any) -> PoolSpec:
    """A pool for the test application."""
    values: dict[str, Any] = {
        "app_name": APP,
        "domain": DOMAIN,
        "user": "www-data",
        "group": "www-data",
        "listen_group": "www-data",
        "socket": Path(f"/run/php/wasm-{APP}.sock"),
        "root": Path(f"/var/www/apps/{APP}"),
        "tmp_dir": Path(f"/var/www/apps/.wasm-php-tmp/{APP}"),
        "env": {"DB_NAME": "blog", "SALT": 'a$b;c"d'},
    }
    values.update(overrides)
    return PoolSpec(**values)


def test_a_pool_runs_as_the_service_user_on_its_own_socket() -> None:
    """The pool is named after the application and nginx's group may open the socket."""
    text = render_pool(spec(listen_group="nginx"))

    assert f"[wasm-{APP}]" in text
    assert "user = www-data" in text
    assert f"listen = /run/php/wasm-{APP}.sock" in text
    assert "listen.group = nginx" in text
    assert "listen.mode = 0660" in text
    assert "pm = dynamic" in text
    assert "clear_env = yes" in text


def test_the_environment_is_single_quoted_so_fpm_expands_nothing() -> None:
    """``$``, ``;`` and double quotes survive literally; the lines are sorted."""
    text = render_pool(spec())

    assert "env[DB_NAME] = 'blog'" in text
    assert "env[SALT] = 'a$b;c\"d'" in text
    assert text.index("env[DB_NAME]") < text.index("env[SALT]")


def test_empty_values_are_left_out_and_quotes_refused() -> None:
    """FPM rejects an empty env value; a single quote cannot be carried at all."""
    assert pool_env_lines({"A": "", "B": "x"}) == [("B", "x")]
    with pytest.raises(ValidationError, match="QUOTED cannot be passed to PHP-FPM"):
        pool_env_lines({"QUOTED": "it's"})


def test_the_worker_memory_limit_is_a_share_of_the_app_limit() -> None:
    """No limit: 256M. A limit: split over the workers, never under 64M."""
    assert spec().memory_limit() == "256M"
    assert spec(memory_max_mb=1000).memory_limit() == "200M"
    assert spec(memory_max_mb=100).memory_limit() == "64M"
    assert "php_admin_value[memory_limit] = 200M" in render_pool(spec(memory_max_mb=1000))


def test_an_upload_size_that_is_not_a_size_is_refused() -> None:
    """It ends up in two configuration files."""
    assert "upload_max_filesize] = 128m" in render_pool(spec(max_upload="128M"))
    with pytest.raises(ValidationError, match="upload size"):
        render_pool(spec(max_upload="64m; evil"))


@pytest.fixture
def fpm(tmp_path: Path) -> SimpleNamespace:
    """An FPM controller over a fake Debian root and a fake runner."""
    root = debian_fpm(tmp_path / "fpm-root", "8.2")
    runner = FakeRunner()
    service = FpmService(
        find_fpm(root),
        runner=runner,
        fs=php_module.PhpFpmDeployer().fs,
        logger=Logger(verbose=False),
    )
    return SimpleNamespace(root=root, runner=runner, service=service)


def test_a_pool_fpm_accepts_is_kept_and_fpm_reloaded(fpm: SimpleNamespace) -> None:
    """The file is 0640 and FPM is reloaded (or started) through systemd."""
    path = fpm.service.installation.pool_file(APP)

    assert fpm.service.install_pool(path, "[pool]\n") is True

    assert path.read_text() == "[pool]\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert fpm.runner.ran("/usr/sbin/php-fpm8.2", "-t")
    assert fpm.runner.ran("systemctl", "reload-or-restart", "php8.2-fpm")


def test_an_unchanged_pool_does_not_reload_fpm(fpm: SimpleNamespace) -> None:
    """Nothing to do is nothing done."""
    path = fpm.service.installation.pool_file(APP)
    fpm.service.install_pool(path, "[pool]\n")
    fpm.runner.calls.clear()

    assert fpm.service.install_pool(path, "[pool]\n") is False
    assert fpm.runner.calls == []


def test_a_pool_fpm_refuses_puts_the_previous_one_back(fpm: SimpleNamespace) -> None:
    """One bad pool would stop every PHP site at the next reload."""
    path = fpm.service.installation.pool_file(APP)
    fpm.service.install_pool(path, "[good]\n")
    fpm.runner.script(
        ["/usr/sbin/php-fpm8.2", "-t"],
        stderr="ERROR: [pool bad] unknown entry 'nope'",
        exit_code=78,
    )

    with pytest.raises(ValidationError, match="rejected the pool") as failure:
        fpm.service.install_pool(path, "[bad]\nnope = 1\n")

    assert "unknown entry 'nope'" in failure.value.details
    assert path.read_text() == "[good]\n"
    assert fpm.runner.calls_to("systemctl") == [("systemctl", "reload-or-restart", "php8.2-fpm")]


def test_a_new_pool_fpm_refuses_is_removed(fpm: SimpleNamespace) -> None:
    """Nothing is left in the pool directory."""
    path = fpm.service.installation.pool_file(APP)
    fpm.runner.script(["/usr/sbin/php-fpm8.2", "-t"], stderr="bad", exit_code=78)

    with pytest.raises(ValidationError):
        fpm.service.install_pool(path, "[bad]\n")

    assert not path.exists()


def test_a_reload_that_fails_says_why(fpm: SimpleNamespace) -> None:
    """systemd's own words."""
    fpm.runner.script(["systemctl", "reload-or-restart"], stderr="Job failed", exit_code=1)

    with pytest.raises(DeploymentError, match=r"php8\.2-fpm did not reload") as failure:
        fpm.service.reload()

    assert "Job failed" in failure.value.details


# ---------------------------------------------------------------------------
# FastCGI
# ---------------------------------------------------------------------------


def serve_fastcgi(path: Path, answer: bytes, stderr: bytes = b"") -> tuple[threading.Thread, list]:
    """
    Answer one FastCGI request on a Unix socket, recording its parameters.

    Returns:
        The serving thread and the list the received parameters land in.
    """
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    received: list[dict[str, str]] = []

    def read_exact(conn: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            data += conn.recv(size - len(data))
        return data

    def record(kind: int, content: bytes) -> bytes:
        return struct.pack("!BBHHBx", 1, kind, 1, len(content), 0) + content

    def run() -> None:
        conn, _ = server.accept()
        params = b""
        with conn:
            while True:
                _v, kind, _id, length, padding = struct.unpack("!BBHHBx", read_exact(conn, 8))
                content = read_exact(conn, length + padding)[:length]
                if kind == 4:
                    params += content
                if kind == 5 and not content:
                    break
            decoded: dict[str, str] = {}
            index = 0
            while index < len(params):
                lengths = []
                for _ in range(2):
                    first = params[index]
                    if first < 128:
                        lengths.append(first)
                        index += 1
                    else:
                        lengths.append(
                            struct.unpack("!I", params[index : index + 4])[0] & 0x7FFFFFFF
                        )
                        index += 4
                name = params[index : index + lengths[0]].decode()
                index += lengths[0]
                decoded[name] = params[index : index + lengths[1]].decode()
                index += lengths[1]
            received.append(decoded)
            if stderr:
                conn.sendall(record(7, stderr))
            conn.sendall(record(6, answer))
            conn.sendall(record(3, b"\0" * 8))
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, received


@pytest.fixture
def sock_path() -> Iterator[Path]:
    """A short socket path: AF_UNIX paths are limited to about 100 bytes."""
    directory = Path(f"/tmp/wasm-fcgi-{os.getpid()}-{threading.get_ident()}")
    directory.mkdir(exist_ok=True)
    yield directory / "p.sock"
    for child in directory.iterdir():
        child.unlink()
    directory.rmdir()


def test_a_redirect_from_php_is_read_off_its_status_header(sock_path: Path) -> None:
    """WordPress before its install answers 302, with PHP's stderr kept apart."""
    thread, received = serve_fastcgi(
        sock_path,
        b"Status: 302 Found\r\nLocation: /wp-admin/install.php\r\n\r\n",
        stderr=b"PHP Notice: something",
    )
    long_value = "x" * 300

    response = fastcgi_request(sock_path, {"REQUEST_URI": "/", "LONG": long_value}, timeout=5)
    thread.join(5)

    assert response.status == 302
    assert response.headers["location"] == "/wp-admin/install.php"
    assert response.stderr == "PHP Notice: something"
    assert received == [{"REQUEST_URI": "/", "LONG": long_value}]


def test_a_response_without_status_is_a_200(sock_path: Path) -> None:
    """CGI's default."""
    thread, _ = serve_fastcgi(sock_path, b"Content-Type: text/html\r\n\r\n<p>hi</p>")

    assert fastcgi_request(sock_path, {}, timeout=5).status == 200
    thread.join(5)


def test_the_front_controller_runs_for_any_path_and_the_root_is_resolved(
    tmp_path: Path,
) -> None:
    """$realpath_root: the release current points at, not the link."""
    release = tmp_path / "releases" / "r1"
    release.mkdir(parents=True)
    (tmp_path / "current").symlink_to(Path("releases/r1"))

    params = fastcgi_params(
        document_root=tmp_path / "current", path="/healthz?x=1", domain=DOMAIN, https=True
    )

    assert params["SCRIPT_FILENAME"] == f"{release}/index.php"
    assert params["DOCUMENT_ROOT"] == str(release)
    assert params["REQUEST_URI"] == "/healthz?x=1"
    assert params["QUERY_STRING"] == "x=1"
    assert params["HTTP_HOST"] == DOMAIN
    assert params["HTTPS"] == "on"
    assert (
        fastcgi_params(document_root=release, path="/wp-login.php", domain=DOMAIN, https=False)[
            "SCRIPT_FILENAME"
        ]
        == f"{release}/wp-login.php"
    )


def test_the_probe_honours_the_expectation_and_reports_php_errors() -> None:
    """A 500 with a PHP fatal error is a failed attempt that says why."""
    answers = [
        OSError("[Errno 2] No such file or directory"),
        FastCgiResponse(status=500, headers={}, stderr="PHP Fatal error: db"),
        FastCgiResponse(status=302, headers={}, stderr=""),
    ]

    def requester(_path: Path, _params: Any, *, timeout: float) -> FastCgiResponse:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    attempts: list[str] = []
    probe = fastcgi_probe(Path("/run/php/x.sock"), dict, requester=requester, sleep=lambda _s: None)

    assert probe("", retries=3, delay=0.1, on_attempt=attempts.append, accept=lambda s: s < 400)
    assert "No such file" in attempts[0]
    assert "HTTP 500" in attempts[1] and "PHP Fatal error: db" in attempts[1]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (WORDPRESS, "php-fpm"),
        (
            {
                "composer.json": "{}",
                "artisan": "",
                "public/index.php": "<?php",
                "package.json": '{"devDependencies": {"vite": "5"}, "scripts": {"build": "vite build"}}',
                "vite.config.js": "",
            },
            "php-fpm",
        ),
        ({"composer.json": "{}", "src/App.php": "<?php"}, "php-fpm"),
        ({"index.php": "<?php", "index.html": "<p>"}, "php-fpm"),
        (
            {
                "composer.json": "{}",
                "package.json": '{"scripts": {"start": "node server.js"}}',
            },
            "nodejs",
        ),
        (
            {"index.php": "<?php", "package.json": '{"scripts": {"start": "node s.js"}}'},
            "nodejs",
        ),
        ({"index.html": "<p>hi</p>"}, "static"),
        ({"index.php": "<?php", "requirements.txt": "flask"}, "python"),
    ],
    ids=[
        "wordpress",
        "laravel-with-vite",
        "composer-only",
        "php-and-html",
        "composer-without-front-controller-and-node",
        "index-php-with-node",
        "static",
        "python",
    ],
)
def test_detection(tmp_path: Path, files: dict[str, str], expected: str) -> None:
    """PHP when the tree is clearly PHP; everything else keeps its owner."""
    assert detect_app_type(write_tree(tmp_path / "src", files)) == expected


def test_the_web_root_is_public_when_it_has_the_front_controller(tmp_path: Path) -> None:
    """Laravel and Symfony serve public/."""
    assert detect_webroot(write_tree(tmp_path / "a", {"public/index.php": ""})) == "public"
    assert detect_webroot(write_tree(tmp_path / "b", {"index.php": ""})) == "."


@pytest.mark.parametrize(
    "settings",
    [
        {"webroot": "../etc"},
        {"webroot": "/var/www"},
        {"deny": ["wp-config.php; return 200"]},
        {"deny": ["a/../b"]},
        {"max_upload": "1g;"},
        {"shared_from_release": ["../x"]},
        {"deny": "wp-config.php"},
    ],
)
def test_settings_that_would_escape_or_inject_are_refused(settings: dict[str, Any]) -> None:
    """Each of them ends up in nginx's configuration or moves files."""
    with pytest.raises((ValidationError, DeploymentError)):
        PhpSettings.from_mapping(settings)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WASMStore]:
    """A store in the test's directory."""
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    WASMStore.reset_instance()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    Git, nginx, systemd, PHP-FPM and the FastCGI probe, faked.

    The probe answers 302 unless the active release's index.php says ``fatal``.
    """
    root = tmp_path / "apps" / APP
    fpm_root = debian_fpm(tmp_path / "fpm-root", "8.2")
    monkeypatch.setattr(php_module, "FPM_ROOT", fpm_root)
    probes: list[dict[str, str]] = []

    def fake_probe(socket_path: Path, params: Any, **_kwargs: Any) -> Any:
        def probe(_url: str, **kwargs: Any) -> bool:
            sent = dict(params())
            probes.append({"socket": str(socket_path), **sent})
            script = Path(sent["SCRIPT_FILENAME"])
            if script.is_file() and "fatal" in script.read_text():
                if kwargs.get("on_attempt"):
                    kwargs["on_attempt"](
                        "Health check attempt 1 failed: HTTP 500 is not an expected status\n"
                        "PHP Fatal error: Uncaught Error"
                    )
                return False
            return True

        return probe

    monkeypatch.setattr(php_module, "fastcgi_probe", fake_probe)
    runner = TreeRunner()
    return SimpleNamespace(
        root=root,
        fpm_root=fpm_root,
        pool=fpm_root / POOL_DIR / f"wasm-{APP}.conf",
        runner=runner,
        git=FakeGit(),
        web=FakeWeb(),
        services=FakeServices(root),
        probes=probes,
    )


def wire(deployer: PhpFpmDeployer, machine: SimpleNamespace) -> PhpFpmDeployer:
    """Point a deployer at the fakes."""
    deployer.source_manager = machine.git  # type: ignore[assignment]
    deployer.service_manager = machine.services  # type: ignore[assignment]
    deployer.cert_manager = SimpleNamespace(obtain=lambda *a, **k: True)  # type: ignore[assignment]
    deployer._webserver_manager = lambda: machine.web  # type: ignore[method-assign]
    deployer.pre_flight_check = lambda: True  # type: ignore[method-assign]
    return deployer


def deploy(machine: SimpleNamespace, layout: str = RELEASES, **options: Any) -> PhpFpmDeployer:
    """Deploy the application with the WordPress recipe's PHP settings."""
    deployer = wire(PhpFpmDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(
        DOMAIN,
        GIT_URL,
        port=3100,
        ssl=False,
        app_path=machine.root,
        layout=layout,
        env_vars={"WORDPRESS_DB_NAME": "blog", "WORDPRESS_AUTH_KEY": "k" + "ey"},
        **options,
    )
    deployer.deploy()
    return deployer


WORDPRESS_OPTIONS: dict[str, Any] = {
    "php_webroot": ".",
    "php_deny": ["wp-config.php", "readme.html"],
    "php_max_upload": "128m",
    "php_shared_from_release": ["wp-content"],
    "php_files": {"wp-config.php": "<?php /* reads getenv() */\n"},
    "persistent_paths": ["wp-content", "wp-config.php"],
    "initial_health": ("/", "200-399", None),
}


def test_a_wordpress_release_gets_a_pool_a_fastcgi_site_and_shared_content(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """Everything a first deploy writes, and where."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))

    deploy(machine, **WORDPRESS_OPTIONS)

    root = machine.root
    # The pool: the application's .env, root-only, and FPM tested and reloaded.
    pool = machine.pool.read_text()
    assert "env[WORDPRESS_DB_NAME] = 'blog'" in pool
    assert "php_admin_value[upload_max_filesize] = 128m" in pool
    assert stat.S_IMODE(machine.pool.stat().st_mode) == 0o640
    assert machine.runner.ran("/usr/sbin/php-fpm8.2", "-t")
    assert machine.runner.ran("systemctl", "reload-or-restart", "php8.2-fpm")
    # The site: FastCGI, rooted through current, on the pool's socket.
    site = machine.web.sites[DOMAIN]
    assert site["template"] == "fastcgi"
    assert site["context"]["document_root"] == str(root / "current")
    assert site["context"]["fastcgi_socket"] == f"/run/php/wasm-{APP}.sock"
    assert site["context"]["deny_paths"] == ["wp-config.php", "readme.html"]
    assert site["context"]["max_upload"] == "128m"
    # wp-content moved to shared/ and linked; wp-config.php seeded and linked.
    assert (root / "shared/wp-content/plugins/akismet/akismet.php").is_file()
    assert (root / "current/wp-content").is_symlink()
    assert (root / "current/wp-config.php").read_text() == "<?php /* reads getenv() */\n"
    assert stat.S_IMODE((root / "shared/wp-config.php").stat().st_mode) == 0o640
    # The gate asked the pool, through current, as the domain.
    assert machine.probes[-1]["socket"] == f"/run/php/wasm-{APP}.sock"
    assert machine.probes[-1]["HTTP_HOST"] == DOMAIN
    assert machine.probes[-1]["SCRIPT_FILENAME"].startswith(str(root / "releases"))
    # The row: static (no unit), with the recipe's health check.
    app = store.get_app(DOMAIN)
    assert app is not None
    assert (app.app_type, app.is_static, app.layout) == ("php-fpm", True, RELEASES)
    assert app.health_expect == "200-399"
    assert machine.services.units == {}
    # The settings, for every later deploy.
    assert load_php_settings(root).shared_from_release == ("wp-content",)
    assert (root / PHP_SETTINGS_FILE).is_file()


def test_a_later_release_keeps_what_was_installed_through_the_admin(
    tmp_path: Path,
    machine: SimpleNamespace,
    store: WASMStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin installed in production survives an update; the stock copy does not come back."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
    deploy(machine, **WORDPRESS_OPTIONS)
    (machine.root / "current/wp-content/plugins/woo").mkdir()
    (machine.root / "current/wp-content/plugins/woo/woo.php").write_text("<?php\n")
    (machine.root / "shared/.env").write_text("WORDPRESS_DB_NAME=renamed\n")

    machine.git.publish(write_tree(tmp_path / "v2", {**WORDPRESS, "index.php": "<?php // v2\n"}))
    monkeypatch.setattr(
        lifecycle,
        "get_deployer",
        lambda app_type, verbose=False: wire(
            PhpFpmDeployer(verbose=False, runner=machine.runner), machine
        ),
    )
    outcome = lifecycle.update_app(DOMAIN)

    current = machine.root / "current"
    assert (current / "index.php").read_text() == "<?php // v2\n"
    assert (current / "wp-content").is_symlink()
    assert (current / "wp-content/plugins/woo/woo.php").is_file()
    # The pool follows the .env as it is now.
    assert "env[WORDPRESS_DB_NAME] = 'renamed'" in machine.pool.read_text()
    assert outcome.is_static is True


def test_a_first_release_that_does_not_answer_leaves_no_pool_behind(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """The pool's undo runs with the rest, and the failure carries PHP's error."""
    machine.git.publish(write_tree(tmp_path / "v1", {**WORDPRESS, "index.php": "<?php fatal"}))

    with pytest.raises(DeploymentError, match="did not pass its health check") as failure:
        deploy(machine, **WORDPRESS_OPTIONS)

    assert "PHP Fatal error" in failure.value.details
    assert not machine.pool.exists()
    assert machine.web.sites == {}
    assert store.get_app(DOMAIN) is None
    assert not machine.root.exists()


def test_a_release_that_does_not_answer_goes_back_to_the_previous_one(
    tmp_path: Path,
    machine: SimpleNamespace,
    store: WASMStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same gate as any release: current goes back, and the pool stays."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
    deploy(machine, **WORDPRESS_OPTIONS)
    first = os.readlink(machine.root / "current")

    machine.git.publish(write_tree(tmp_path / "v2", {**WORDPRESS, "index.php": "<?php fatal"}))
    monkeypatch.setattr(
        lifecycle,
        "get_deployer",
        lambda app_type, verbose=False: wire(
            PhpFpmDeployer(verbose=False, runner=machine.runner), machine
        ),
    )
    with pytest.raises(DeploymentError, match="is active again"):
        lifecycle.update_app(DOMAIN)

    assert os.readlink(machine.root / "current") == first
    assert machine.pool.is_file()


def test_in_place_serves_the_tree_itself_and_detects_public(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """A Laravel-shaped tree in place: the web root is public/, composer runs as root."""
    tree = write_tree(
        tmp_path / "v1",
        {"composer.json": "{}", "artisan": "", "public/index.php": "<?php\n"},
    )

    class CopyFetch:
        """In place, the source is copied into the application directory."""

        def fetch(self, source: str, destination: Path, **_kwargs: Any) -> bool:
            shutil.copytree(tree, destination, dirs_exist_ok=True)
            return True

    machine.git = CopyFetch()
    deploy(machine, layout=INPLACE)

    site = machine.web.sites[DOMAIN]["context"]
    assert site["document_root"] == str(machine.root / "public")
    install = [call for call in machine.runner.calls if call[:2] == ("composer", "install")]
    assert install == [
        (
            "composer",
            "install",
            "--no-dev",
            "--no-interaction",
            "--prefer-dist",
            "--optimize-autoloader",
        )
    ]


def test_composer_missing_is_an_error_with_the_install_hint(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """Only when the tree has a composer.json."""
    machine.runner.only_knows("git", "systemctl")
    machine.git.publish(write_tree(tmp_path / "v1", {"composer.json": "{}", "index.php": ""}))

    with pytest.raises(DeploymentError, match="Composer, which is not installed") as failure:
        deploy(machine)

    assert "apt install composer" in failure.value.details


def test_apache_is_refused_before_anything_changes(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """No Apache template exists for PHP-FPM."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
    deployer = wire(PhpFpmDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, ssl=False, app_path=machine.root, webserver="apache")

    with pytest.raises(DeploymentError, match="served by nginx only"):
        deployer.build_pipeline()


def test_a_rollback_uses_the_php_gate(
    tmp_path: Path, machine: SimpleNamespace, store: WASMStore
) -> None:
    """lifecycle's gate for a PHP row reloads FPM and asks the pool, not a port."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
    deploy(machine, **WORDPRESS_OPTIONS)
    app = store.get_app(DOMAIN)
    assert app is not None

    gate = lifecycle.health_gate_for(app, store, Logger(verbose=False))

    assert gate.unit == "php8.2-fpm"
    assert gate.url is not None and gate.url.startswith(f"unix:/run/php/wasm-{APP}.sock")
    assert gate.check is not None and gate.check.expect == "200-399"


def test_deleting_the_app_removes_its_pool(
    tmp_path: Path, machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale pool would keep workers for a deleted application, and its tmp its sessions."""
    monkeypatch.setattr(fpm_module, "FPM_CONTROL_TIMEOUT", 1)
    machine.pool.write_text("[pool]\n")
    tmp = fpm_module.pool_tmp_dir(machine.root)
    tmp.mkdir(parents=True)
    (tmp / "sess_x").write_text("s")
    runner = FakeRunner()
    monkeypatch.setattr(php_module, "get_runner", lambda: runner)

    assert php_module.remove_pool_of(machine.root, Logger(verbose=False)) is True
    assert not machine.pool.exists()
    assert not tmp.exists()
    assert runner.ran("systemctl", "reload-or-restart", "php8.2-fpm")
    assert php_module.remove_pool_of(machine.root, Logger(verbose=False)) is False


def test_health_gate_for_app_reads_the_settings_file(
    tmp_path: Path, machine: SimpleNamespace
) -> None:
    """The web root the deploy used is the one the gate probes."""
    root = machine.root
    (root / "releases/r1/web").mkdir(parents=True)
    (root / "current").symlink_to(Path("releases/r1"))
    (root / PHP_SETTINGS_FILE).write_text('{"webroot": "web"}')
    app = App(domain=DOMAIN, app_type="php-fpm", app_path=str(root), layout=RELEASES)

    gate = php_module.health_gate_for_app(app, Logger(verbose=False))

    assert gate.check == HealthCheck.for_app(app)
    assert gate.url == f"unix:/run/php/wasm-{APP}.sock /"


# ---------------------------------------------------------------------------
# The nginx site
# ---------------------------------------------------------------------------


@pytest.fixture
def nginx(tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> NginxManager:
    """An nginx manager over a temporary tree and an in-memory store."""
    fake = FakeStore()
    monkeypatch.setattr("wasm.managers.webserver.get_store", lambda: fake)
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


SITE_CASES = {
    "plain": {"ssl": False},
    "ssl-redirect": {"ssl": True, "redirect_domains": ["www.example.com"]},
}


@pytest.mark.parametrize("case", sorted(SITE_CASES))
def test_the_fastcgi_site_matches_the_snapshot(
    nginx: NginxManager, case: str, snapshot: Any
) -> None:
    """A change to what nginx is told must be visible in review."""
    context = {
        "document_root": "/var/www/apps/example-com/current",
        "fastcgi_socket": "/run/php/wasm-example-com.sock",
        "max_upload": "64m",
        "deny_paths": ["wp-config.php", "readme.html"],
        **SITE_CASES[case],
    }

    assert nginx.render_config("example.com", "fastcgi", context) == snapshot(name=case)


def test_every_refusal_comes_before_the_php_handler(nginx: NginxManager) -> None:
    """nginx tries regex locations in order: a refusal after the handler never applies."""
    text = nginx.render_config(
        "example.com",
        "fastcgi",
        {
            "document_root": "/srv/x",
            "fastcgi_socket": "/run/php/x.sock",
            "max_upload": "64m",
            "deny_paths": ["wp-config.php"],
        },
    )
    handler = text.index("location ~ \\.php$")
    for refusal in (
        "location = /wp-config.php",
        "location ~ /\\.",
        "location ~* ^/(?:composer",
        "location ~* /(?:uploads|files)/.*\\.php$",
    ):
        assert text.index(refusal) < handler, refusal
    assert "fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;" in text
    assert "client_max_body_size 64m;" in text
    assert "index index.php index.html;" in text
