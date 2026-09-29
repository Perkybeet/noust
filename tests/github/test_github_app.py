# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the GitHub App's credentials, client, manifest flow and operations.

GitHub is a fake on loopback (``tests/github_fakes.py``) that speaks the few
routes WASM calls; ``openssl`` is a scripted answer, since real processes are
blocked, so the JWT's header and payload are checked byte for byte and its
signature is whatever openssl said, re-encoded.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest

from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.runner import FakeRunner, SubprocessRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, GitHubInstallationRecord, NoustStore
from noust.integrations.github import app as github_app
from noust.integrations.github import manifest, service
from noust.integrations.github.client import GitHubAPIError, GitHubClient
from tests.github.fakes import (
    APP_ID,
    FAKE_SIGNATURE_HEX,
    INSTALLATION_ID,
    FakeGitHub,
    token_route,
)


def b64decode(part: str) -> bytes:
    """
    Decode unpadded base64url.

    Args:
        part: The encoding.

    Returns:
        The bytes.
    """
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


# -- JWT ---------------------------------------------------------------------


def test_jwt_header_and_payload_are_encoded_as_github_expects() -> None:
    """RS256 header; iat backdated a minute, exp nine minutes ahead, iss the App id."""
    signing_input = github_app.jwt_signing_input(APP_ID, 1_000_000.9)
    header, payload = signing_input.split(".")
    assert "=" not in signing_input
    assert json.loads(b64decode(header)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(b64decode(payload)) == {
        "iat": 1_000_000 - 60,
        "exp": 1_000_000 + 540,
        "iss": APP_ID,
    }


def test_the_signature_comes_from_openssl_with_the_input_on_stdin(
    tmp_path: Path, openssl: FakeRunner
) -> None:
    """The key's path is the only thing argv names; the signing input goes on stdin."""
    key = tmp_path / "key.pem"
    signature = github_app.sign_rs256("head.payload", key)
    assert b64decode(signature) == bytes.fromhex(FAKE_SIGNATURE_HEX)
    assert openssl.calls == [("openssl", "dgst", "-sha256", "-sign", str(key), "-hex")]
    assert openssl.inputs == ["head.payload"]


def test_an_openssl_failure_is_an_integration_error_with_its_words(tmp_path: Path) -> None:
    """openssl's own message is carried verbatim."""
    runner = FakeRunner().script(["openssl"], exit_code=1, stderr="unable to load key")
    with pytest.raises(IntegrationError) as caught:
        github_app.sign_rs256("x.y", tmp_path / "key.pem", runner)
    assert caught.value.output == "unable to load key"


def test_the_jwt_never_puts_key_material_in_argv(
    github_configured: NoustStore, openssl: FakeRunner
) -> None:
    """The PEM stays in its 0600 file."""
    loaded = github_app.load_app()
    assert loaded is not None
    token = loaded.jwt()
    assert token.count(".") == 2
    argv = " ".join(" ".join(call) for call in openssl.calls)
    assert "BEGIN" not in argv and "fake" not in argv
    assert SecretStore().path(github_app.PRIVATE_KEY_SECRET).stat().st_mode & 0o777 == 0o600


@pytest.mark.allow_subprocess
def test_a_real_openssl_signature_verifies(tmp_path: Path) -> None:
    """When openssl is installed, the JWT it signs verifies with the public key."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    runner = SubprocessRunner()
    key = tmp_path / "key.pem"
    runner.run(["openssl", "genrsa", "-out", str(key), "2048"], timeout=60, check=True)
    runner.run(
        ["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(tmp_path / "pub.pem")],
        timeout=30,
        check=True,
    )
    signing_input = github_app.jwt_signing_input(APP_ID, 1_700_000_000)
    signature = b64decode(github_app.sign_rs256(signing_input, key, runner))
    (tmp_path / "sig").write_bytes(signature)
    verified = runner.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(tmp_path / "pub.pem"),
            "-signature",
            str(tmp_path / "sig"),
        ],
        input=signing_input,
        timeout=30,
    )
    assert verified.success, verified.stderr
    assert len(signature) == 256


# -- Installation tokens -----------------------------------------------------


def test_installation_tokens_are_cached_until_five_minutes_before_expiry(
    github_configured: NoustStore, openssl: FakeRunner, fake_github: FakeGitHub
) -> None:
    """One exchange serves every call until the refresh margin; then a new one."""
    token_route(fake_github, "ghs_first", "2030-01-01T01:00:00Z")
    expiry = 1_893_459_600.0  # 2030-01-01T01:00:00Z
    now = [expiry - 3600]
    loaded = github_app.load_app()
    assert loaded is not None
    loaded.clock = lambda: now[0]

    assert loaded.installation_token(INSTALLATION_ID) == "ghs_first"
    now[0] = expiry - 301
    assert loaded.installation_token(INSTALLATION_ID) == "ghs_first"
    assert (
        fake_github.paths("POST").count(f"/app/installations/{INSTALLATION_ID}/access_tokens") == 1
    )

    token_route(fake_github, "ghs_second", "2030-01-01T02:00:00Z")
    now[0] = expiry - 299
    assert loaded.installation_token(INSTALLATION_ID) == "ghs_second"
    exchange = [r for r in fake_github.requests if r.method == "POST"][-1]
    assert exchange.headers["authorization"].startswith("Bearer ")
    assert exchange.headers["x-github-api-version"] == "2022-11-28"
    assert exchange.headers["accept"] == "application/vnd.github+json"


def test_forgetting_tokens_forces_a_new_exchange(
    github_configured: NoustStore, openssl: FakeRunner, fake_github: FakeGitHub
) -> None:
    """A removed installation's token is not served from the cache."""
    token_route(fake_github)
    loaded = github_app.load_app()
    assert loaded is not None
    loaded.installation_token(INSTALLATION_ID)
    github_app.forget_tokens(INSTALLATION_ID)
    loaded.installation_token(INSTALLATION_ID)
    assert len(fake_github.paths("POST")) == 2


# -- Client ------------------------------------------------------------------


def test_githubs_message_is_kept_verbatim(fake_github: FakeGitHub) -> None:
    """An error status carries GitHub's own message and a hint."""
    fake_github.on("GET", "/boom", {"message": "Resource not accessible by integration"}, 403)
    with pytest.raises(GitHubAPIError) as caught:
        GitHubClient(fake_github.base_url).request("GET", "/boom")
    assert caught.value.status == 403
    assert caught.value.output == "Resource not accessible by integration"
    assert "permission" in caught.value.details


def test_the_client_refuses_anything_but_a_path() -> None:
    """An absolute URL is never followed to another host."""
    with pytest.raises(IntegrationError):
        GitHubClient("http://127.0.0.1:9").request("GET", "https://evil.example/x")
    with pytest.raises(IntegrationError):
        GitHubClient("http://127.0.0.1:9").request("GET", "//evil.example/x")


def test_an_unreachable_github_is_an_integration_error() -> None:
    """A refused connection is reported, not raised as OSError."""
    with pytest.raises(IntegrationError, match="could not be reached"):
        GitHubClient("http://127.0.0.1:9", timeout=2).request("GET", "/")


def test_paginate_reads_every_page(fake_github: FakeGitHub) -> None:
    """Full pages ask for the next one; a short page ends the listing."""
    fake_github.on("GET", r"/items\?per_page=100&page=1", list(range(100)))
    fake_github.on("GET", r"/items\?per_page=100&page=2", [100, 101])
    items = GitHubClient(fake_github.base_url).paginate("/items")
    assert items == list(range(102))


# -- Installation resolution and git credentials ------------------------------


def test_the_installation_is_the_apps_own_then_the_owners(github_configured: NoustStore) -> None:
    """An application's link wins; otherwise the owner's account decides."""
    assert github_app.installation_for("you/app") == INSTALLATION_ID
    assert github_app.installation_for("YOU/other") == INSTALLATION_ID
    assert github_app.installation_for("someone/else") is None
    github_configured.save_github_installation(
        GitHubInstallationRecord(installation_id=99, account="org")
    )
    github_configured.create_app(App(domain="a.example.com", source="github:someone/else"))
    github_configured.set_github_installation("a.example.com", 99)
    assert github_app.installation_for("someone/else") == 99
    assert github_app.installation_for("you/app", 5) == 5


def test_git_auth_environment_carries_the_token_as_an_extra_header(
    github_configured: NoustStore, openssl: FakeRunner, fake_github: FakeGitHub
) -> None:
    """GIT_CONFIG_* scoped to github.com, basic x-access-token."""
    token_route(fake_github, "ghs_tok")
    env = github_app.git_auth_environment("https://github.com/you/app.git", config_index=2)
    expected = base64.b64encode(b"x-access-token:ghs_tok").decode()
    assert env == {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_2": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_2": f"AUTHORIZATION: basic {expected}",
    }


def test_no_credential_for_other_hosts_or_uncovered_owners(github_configured: NoustStore) -> None:
    """Nothing is asked of GitHub when no installation covers the repository."""
    assert github_app.git_auth_environment("https://gitlab.com/you/app.git") == {}
    assert github_app.git_auth_environment("git@github.com:you/app.git") == {}
    assert github_app.git_auth_environment("https://github.com/stranger/app.git") == {}


def test_no_credential_without_an_app(store: NoustStore) -> None:
    """A server with no App adds nothing to git's environment."""
    assert github_app.git_auth_environment("https://github.com/you/app.git") == {}


# -- Manifest flow -----------------------------------------------------------


def test_the_app_name_is_sanitised_and_short() -> None:
    """noust-<host>, lower case, 34 characters at most."""
    assert manifest.app_name("Web_01.example.com") == "noust-web-01"
    assert len(manifest.app_name("x" * 80)) == 34


def test_manifest_without_a_public_hooks_url_has_no_webhook(store: NoustStore) -> None:
    """Permissions and events as specified; the callback on the console's origin."""
    started = manifest.start("http://localhost:8080", hooks_url=None)
    body = started.manifest
    assert body["redirect_url"] == "http://localhost:8080/integrations/github/callback"
    assert body["default_permissions"] == {
        "contents": "read",
        "metadata": "read",
        "deployments": "write",
        "statuses": "write",
        "pull_requests": "write",
    }
    # GitHub refuses events without a webhook URL ("Hook url cannot be blank").
    assert "default_events" not in body
    assert "hook_attributes" not in body
    assert started.post_url == f"https://github.com/settings/apps/new?state={started.state}"


def test_manifest_for_an_organisation_with_hooks(store: NoustStore) -> None:
    """The organisation's creation page; the webhook active at the public URL."""
    started = manifest.start(
        "https://console.example.com/",
        hooks_url="https://h.example.com/hooks/github",
        organization="acme",
    )
    assert started.post_url.startswith("https://github.com/organizations/acme/settings/apps/new?")
    assert started.manifest["hook_attributes"] == {
        "url": "https://h.example.com/hooks/github",
        "active": True,
    }
    assert started.manifest["default_events"] == ["push", "pull_request"]


@pytest.mark.parametrize(
    "origin", ["ftp://x", "http://x/path", "javascript:alert(1)", "http://u:p@x"]
)
def test_a_bad_origin_is_refused(store: NoustStore, origin: str) -> None:
    """The origin becomes a redirect target: only a bare http(s) origin passes."""
    with pytest.raises(ValidationError):
        manifest.start(origin, hooks_url=None)


def test_conversion_stores_the_credentials_as_secret_files(
    store: NoustStore, fake_github: FakeGitHub
) -> None:
    """The PEM, webhook and client secrets go to 0600 files; the record to the store."""
    started = manifest.start(
        "http://localhost:8080", hooks_url="https://h.example.com/hooks/github"
    )
    fake_github.on(
        "POST",
        "/app-manifests/abc123/conversions",
        {
            "id": APP_ID,
            "slug": "wasm-box",
            "name": "wasm-box",
            "owner": {"login": "acme", "type": "Organization"},
            "html_url": "https://github.com/apps/wasm-box",
            "client_id": "Iv1.x",
            "client_secret": "cs",
            "webhook_secret": "ws",
            "pem": "-----BEGIN RSA PRIVATE KEY-----\nk\n",
        },
        status=201,
    )
    record = manifest.convert("abc123", started.state)
    assert record.app_id == APP_ID and record.owner == "acme"
    secrets = SecretStore()
    assert secrets.read(github_app.PRIVATE_KEY_SECRET).startswith("-----BEGIN")
    assert secrets.read(github_app.WEBHOOK_SECRET) == "ws"
    assert secrets.read(github_app.CLIENT_SECRET) == "cs"
    status = service.status()
    assert status.configured
    assert status.settings_url == "https://github.com/organizations/acme/settings/apps/wasm-box"
    assert status.install_url == "https://github.com/apps/wasm-box/installations/new"


def test_a_state_is_redeemed_once(store: NoustStore, fake_github: FakeGitHub) -> None:
    """A replayed or foreign callback is refused before GitHub is asked."""
    with pytest.raises(ValidationError):
        manifest.convert("abc", "never-issued")
    started = manifest.start("http://localhost:8080", hooks_url=None)
    fake_github.on("POST", "/app-manifests/abc/conversions", {"message": "Not Found"}, 404)
    with pytest.raises(GitHubAPIError):
        manifest.convert("abc", started.state)
    with pytest.raises(ValidationError):
        manifest.convert("abc", started.state)
    assert fake_github.paths() == ["/app-manifests/abc/conversions"]


# -- Operations --------------------------------------------------------------


def test_add_installation_asks_github_first(
    github_configured: NoustStore, openssl: FakeRunner, fake_github: FakeGitHub
) -> None:
    """An installation id from a query string is stored only once GitHub confirms it."""
    fake_github.on(
        "GET",
        "/app/installations/555",
        {
            "id": 555,
            "account": {"login": "acme", "type": "Organization"},
            "repository_selection": "selected",
        },
    )
    info = service.add_installation(555)
    assert info.account == "acme"
    assert "organizations/acme/settings/installations/555" in (info.settings_url or "")
    with pytest.raises(GitHubAPIError):
        service.add_installation(556)
    assert {i.installation_id for i in github_configured.list_github_installations()} == {
        INSTALLATION_ID,
        555,
    }


def test_sync_makes_the_installations_githubs(
    github_configured: NoustStore, openssl: FakeRunner, fake_github: FakeGitHub
) -> None:
    """Listed ones are saved; the ones GitHub no longer lists are forgotten."""
    fake_github.on(
        "GET",
        r"/app/installations\?.*",
        [{"id": 9, "account": {"login": "acme", "type": "Organization"}}],
    )
    items = service.sync_installations()
    assert [i.installation_id for i in items] == [9]


def test_repositories_and_branches(
    github_configured: NoustStore, openssl: FakeRunner, fake_github: FakeGitHub
) -> None:
    """Every covered repository with its deploy source; branches with their heads."""
    token_route(fake_github)
    fake_github.on(
        "GET",
        r"/installation/repositories\?.*",
        {
            "total_count": 1,
            "repositories": [
                {
                    "full_name": "you/app",
                    "private": True,
                    "default_branch": "main",
                    "clone_url": "https://github.com/you/app.git",
                }
            ],
        },
    )
    fake_github.on(
        "GET",
        r"/repos/you/app/branches\?.*",
        [{"name": "main", "protected": True, "commit": {"sha": "abc"}}],
    )
    repos = service.list_repositories()
    assert repos == [
        {
            "full_name": "you/app",
            "private": True,
            "default_branch": "main",
            "clone_url": "https://github.com/you/app.git",
            "source": "github:you/app",
            "installation_id": INSTALLATION_ID,
        }
    ]
    assert service.list_branches("you", "app") == [
        {"name": "main", "protected": True, "commit": "abc"}
    ]
    listing = next(r for r in fake_github.requests if r.path.startswith("/installation/"))
    assert listing.headers["authorization"] == "token ghs_installation"
    with pytest.raises(IntegrationError, match="No installation"):
        service.list_branches("stranger", "app")
    with pytest.raises(ValidationError):
        service.list_branches("you", "../etc")


def test_remove_forgets_everything_and_names_the_page(github_configured: NoustStore) -> None:
    """Credentials, installations and links go; GitHub's page to delete it is returned."""
    github_configured.create_app(App(domain="a.example.com", source="github:you/app"))
    github_configured.set_github_installation("a.example.com", INSTALLATION_ID)
    outcome = service.remove()
    assert outcome == {
        "removed": True,
        "settings_url": "https://github.com/settings/apps/wasm-test",
    }
    assert github_configured.get_github_app() is None
    assert github_configured.list_github_installations() == []
    assert github_configured.get_app("a.example.com").github_installation_id is None
    assert SecretStore().read(github_app.PRIVATE_KEY_SECRET) is None


def test_configure_webhook_sets_url_and_secret(
    github_configured: NoustStore, openssl: FakeRunner, fake_github: FakeGitHub
) -> None:
    """The URL and this server's secret are sent; activity is still unknown."""
    fake_github.on("PATCH", "/app/hook/config", {"url": "x"})
    active = service.configure_webhook("https://h.example.com/hooks/github")
    assert active is False
    sent = next(r for r in fake_github.requests if r.method == "PATCH").body
    assert sent == {
        "url": "https://h.example.com/hooks/github",
        "content_type": "json",
        "secret": "hook-secret",
        "insecure_ssl": "0",
    }


@pytest.mark.allow_subprocess
def test_a_sealed_key_is_signed_with_from_a_private_copy_removed_after(
    github_configured: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a sealed store the key file is ciphertext; openssl gets a decrypted copy, briefly."""
    from noust.core import sealing

    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    sealed = SecretStore(runner=SubprocessRunner())
    sealing.seal_store(sealed.root, "correct horse battery staple", runner=SubprocessRunner())
    signer = FakeRunner().script(
        ["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {FAKE_SIGNATURE_HEX}\n"
    )
    try:
        record = github_configured.get_github_app()
        assert record is not None
        app = github_app.GitHubApp(record, secrets=sealed, runner=signer)

        assert app.jwt().count(".") == 2

        (call,) = signer.calls
        key = Path(call[call.index("-sign") + 1])
        assert key.is_relative_to(runtime)
        assert not key.exists()
    finally:
        sealing.lock(sealed.root)
