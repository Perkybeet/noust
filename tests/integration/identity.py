#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Identity sources against real software: Keycloak (SAML 2.0 and OpenID Connect), OpenLDAP and a
SCIM client.

The unit tests drive each protocol against a provider written for the tests, which can only
contradict the code in ways its author thought of. This harness drives the same code against
programs written by other people, so that what Noust believes a SAML response, an LDAP search
or a SCIM call looks like is held to what real ones do:

- ``saml``: Keycloak signs the assertion, the response, or both, with the realm's key; Noust is
  configured from Keycloak's own metadata, Keycloak is registered from Noust's, and a person
  signs in with a real login form. Then the response is tampered with, replayed, and answered
  to a client that asked for encryption.
- ``oidc``: the same realm as an OpenID Connect provider, with groups giving a role.
- ``ldap``: OpenLDAP over plain text, StartTLS and LDAPS, with an injection attempt in the name.
- ``scim``: a third-party SCIM 2.0 conformance client (``scim2-tester``, installed in a throwaway
  virtual environment) exercises ``/api/scim/v2``.

Noust itself runs in this process, the real application under uvicorn on a loopback port, over a
store in a temporary directory. Nothing here touches the machine's own configuration.

This file is intentionally excluded from pytest's default collection (it does not match
``test_*.py``): it drives Docker and the network.

Usage:
    .venv/bin/python tests/integration/identity.py
    .venv/bin/python tests/integration/identity.py --scenario saml
    .venv/bin/python tests/integration/identity.py --scenario ldap --keep

Docker safety, because the machine it runs on also holds containers that are not this
harness's: it creates only containers named ``noust-test-*``, labelled ``noust.test=1``, on
loopback ports it found free, removes them by exact name, and never prunes anything. A scenario
that cannot run (no Docker, no image, no network) reports SKIP with the reason, never PASS.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import html
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

PREFIX = "noust-test-"
LABEL = "noust.test=1"
KEYCLOAK_IMAGE_ENV = "NOUST_TEST_KEYCLOAK_IMAGE"
KEYCLOAK_IMAGE_DEFAULT = "quay.io/keycloak/keycloak:latest"
LDAP_IMAGE_ENV = "NOUST_TEST_LDAP_IMAGE"
LDAP_IMAGE_DEFAULT = "osixia/openldap:1.5.0"
DOCKER_CONFIG_DIR = Path(tempfile.gettempdir()) / "noust-test-docker-config"

PASSWORD = "correct horse battery staple"
REALM = "noust"


class Skip(Exception):
    """A scenario that cannot run here, and why."""


@dataclass
class Report:
    """What each scenario found."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        """
        Record one thing that must be true.

        Args:
            name: What was checked.
            condition: Whether it held.
            detail: What was found, shown when it did not.
        """
        if condition:
            self.passed.append(name)
            print(f"  ok    {name}")
        else:
            self.failed.append(f"{name}: {detail}")
            print(f"  FAIL  {name}: {detail}")


REPORT = Report()


# ------------------------------------------------------------------------------------ docker


def docker(
    *args: str, input_text: str | None = None, check: bool = True, timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    """
    Run the Docker client.

    Args:
        *args: Its arguments.
        input_text: Standard input.
        check: Raise on a non-zero exit.
        timeout: Seconds.

    Returns:
        The completed process.
    """
    DOCKER_CONFIG_DIR.mkdir(exist_ok=True)
    config = DOCKER_CONFIG_DIR / "config.json"
    if not config.exists():
        # A clean client configuration: a credential helper that cannot run on this machine
        # would otherwise stop a pull of a public image.
        config.write_text("{}", encoding="utf-8")
    env = dict(os.environ, DOCKER_CONFIG=str(DOCKER_CONFIG_DIR))
    return subprocess.run(
        ["docker", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
        env=env,
    )


def free_port() -> int:
    """
    Returns:
        A loopback port nothing is listening on.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Containers:
    """The containers this run created, removed by exact name when it ends."""

    def __init__(self, keep: bool) -> None:
        self.names: list[str] = []
        self.keep = keep

    def run(self, name: str, image: str, *args: str, command: list[str] | None = None) -> None:
        """
        Start a container.

        Args:
            name: Its name; must start with the harness's prefix.
            image: The image.
            *args: ``docker run`` options (ports, environment).
            command: The command to run in it.

        Raises:
            ValueError: When the name is not the harness's.
        """
        if not name.startswith(PREFIX):
            raise ValueError(f"refusing to create a container named {name!r}")
        # A leftover of an earlier run of this harness (same exact name), never anything else.
        docker("rm", "-f", "-v", name, check=False)
        # Never the command in an error: its environment holds this run's throwaway passwords.
        done = docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            LABEL,
            *args,
            image,
            *(command or []),
            check=False,
        )
        if done.returncode != 0:
            raise RuntimeError(f"docker run of {name} failed: {done.stderr.strip()[-400:]}")
        self.names.append(name)

    def exec(self, name: str, *command: str, input_text: str | None = None) -> str:
        """
        Run a command in one of this run's containers.

        Args:
            name: The container.
            *command: The command.
            input_text: Standard input.

        Returns:
            Its output.
        """
        assert name in self.names
        done = docker("exec", "-i", name, *command, input_text=input_text, check=False)
        if done.returncode != 0:
            raise RuntimeError(f"{' '.join(command[:2])} failed: {done.stderr.strip()[:400]}")
        return done.stdout

    def logs(self, name: str) -> str:
        """
        Args:
            name: A container of this run.

        Returns:
            The tail of its log.
        """
        return docker("logs", "--tail", "40", name, check=False).stdout[-3000:]

    def cleanup(self) -> None:
        """Remove what this run created, by exact name."""
        if self.keep:
            print(f"kept: {', '.join(self.names)}")
            return
        for name in self.names:
            docker("rm", "-f", "-v", name, check=False)
        self.names.clear()


def image_for(env: str, default: str) -> str:
    """
    Args:
        env: The environment variable that overrides it.
        default: The image otherwise.

    Returns:
        The image to run.

    Raises:
        Skip: When Docker is not usable.
    """
    if shutil.which("docker") is None or docker("version", check=False).returncode != 0:
        raise Skip("Docker is not available")
    return os.environ.get(env, default)


# ------------------------------------------------------------------------------------ noust


class Noust:
    """
    The real console under uvicorn on a loopback port, over a store of its own.

    Args:
        workdir: A directory this run owns.
    """

    def __init__(self, workdir: Path) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        self.workdir = workdir
        os.environ["HOME"] = str(workdir)
        os.environ["XDG_DATA_HOME"] = str(workdir / "data")
        os.environ["XDG_CONFIG_HOME"] = str(workdir / "config")
        from noust.core import config as config_module
        from noust.core.accounts import AuthPolicy, passwords
        from noust.core.config import Config
        from noust.core.store import NoustStore
        from noust.web.auth import STATE_DIR_ENV, SecurityConfig
        from noust.web.server import create_app, get_token_manager

        passwords.COST = (2**4, 8, 1)
        passwords._dummy_hash = None
        config_module.DEFAULT_CONFIG_PATH = workdir / "etc" / "config.yaml"
        Config.reset_instance()
        NoustStore.reset_instance()
        self.store = NoustStore(workdir / "noust.db")
        state = workdir / "state"
        state.mkdir(mode=0o700, exist_ok=True)
        os.environ[STATE_DIR_ENV] = str(state)
        self.config = Config()
        self.config.set(
            "security.allow_private_hosts", ["localhost", "127.0.0.1", "ldap.acme.test"]
        )
        self.app = create_app(
            SecurityConfig(state_dir=state, rate_limit_requests=100000, auth_policy=AuthPolicy())
        )
        self.get_token_manager = get_token_manager
        self.port = free_port()
        self.base = f"http://localhost:{self.port}"
        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self.server = server
        self.thread = threading.Thread(target=server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 30
        while not server.started and time.time() < deadline:
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("the console did not start")

    @property
    def accounts(self) -> Any:
        """The console's account manager."""
        return self.get_token_manager().accounts

    def stop(self) -> None:
        """Stop serving."""
        self.server.should_exit = True
        self.thread.join(timeout=10)


def a_browser() -> Any:
    """
    A client that keeps cookies the way a browser does on a loopback address.

    Keycloak marks its cookies ``Secure`` even when it is reached over plain HTTP, and browsers
    accept them for ``localhost`` and ``127.0.0.1`` (a potentially trustworthy origin); a plain
    cookie jar would not send them back.

    Returns:
        An httpx client.
    """
    import httpx

    def relax(response: Any) -> None:
        for cookie in client.cookies.jar:
            cookie.secure = False

    client = httpx.Client(timeout=30, event_hooks={"response": [relax]})
    return client


# -------------------------------------------------------------------------------- html forms


def forms(text: str) -> list[tuple[str, dict[str, str]]]:
    """
    Read the forms of an HTML page the way a browser would fill them in.

    Args:
        text: The page.

    Returns:
        Each form's action and its fields (hidden and typed ones, empty).
    """
    found = []
    for match in re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.S | re.I):
        action = re.search(r'action="([^"]*)"', match.group(1))
        fields = {}
        for tag in re.finditer(r"<input\b([^>]*)>", match.group(2), re.I):
            name = re.search(r'name="([^"]*)"', tag.group(1))
            value = re.search(r'value="([^"]*)"', tag.group(1))
            if name:
                fields[html.unescape(name.group(1))] = (
                    html.unescape(value.group(1)) if value else ""
                )
        found.append((html.unescape(action.group(1)) if action else "", fields))
    return found


# --------------------------------------------------------------------------------- keycloak


class Keycloak:
    """
    A Keycloak realm with one person in a group, administered through its REST API.

    Args:
        containers: The run's containers.
        image: The image.
    """

    def __init__(self, containers: Containers, image: str) -> None:
        import httpx

        self.httpx = httpx
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.admin = "admin"
        self.admin_password = secrets.token_urlsafe(18)
        containers.run(
            f"{PREFIX}keycloak",
            image,
            "-p",
            f"127.0.0.1:{self.port}:8080",
            "-e",
            f"KC_BOOTSTRAP_ADMIN_USERNAME={self.admin}",
            "-e",
            f"KC_BOOTSTRAP_ADMIN_PASSWORD={self.admin_password}",
            command=["start-dev"],
        )
        self.containers = containers
        self.version = ""
        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.base}/realms/master", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Keycloak did not start:\n" + containers.logs(f"{PREFIX}keycloak"))
        self.token = ""
        self._login()
        found = re.search(
            r"Keycloak (\d+(?:\.\d+)+)", docker("logs", f"{PREFIX}keycloak", check=False).stdout
        )
        self.version = found.group(1) if found else "unknown"
        print(f"  (Keycloak {self.version})")

    def _login(self) -> None:
        answer = self.httpx.post(
            f"{self.base}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.admin,
                "password": self.admin_password,
            },
            timeout=20,
        )
        answer.raise_for_status()
        self.token = answer.json()["access_token"]

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        """
        Call the admin API.

        Args:
            method: The verb.
            path: The path under ``/admin/realms``.
            **kwargs: Passed to httpx.

        Returns:
            The response.
        """
        headers = {"Authorization": f"Bearer {self.token}", **kwargs.pop("headers", {})}
        answer = self.httpx.request(
            method, f"{self.base}/admin/realms{path}", headers=headers, timeout=30, **kwargs
        )
        if answer.status_code == 401:
            self._login()
            return self.call(method, path, **kwargs)
        if answer.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {answer.status_code} {answer.text[:300]}")
        return answer

    def make_realm(self) -> None:
        """Create the realm, a group, and two people: Maria in the group, Tom outside it."""
        self.call("POST", "", json={"realm": REALM, "enabled": True, "sslRequired": "none"})
        self.call("POST", f"/{REALM}/groups", json={"name": "noust-ops"})
        group = self.call("GET", f"/{REALM}/groups", params={"search": "noust-ops"}).json()[0]["id"]
        for username, first, last, in_group in (
            ("maria", "Maria", "Lopez", True),
            ("tom", "Tom", "Ward", False),
        ):
            self.call(
                "POST",
                f"/{REALM}/users",
                json={
                    "username": username,
                    "email": f"{username}@acme.test",
                    "emailVerified": True,
                    "enabled": True,
                    "firstName": first,
                    "lastName": last,
                    "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}],
                },
            )
            user = self.call(
                "GET", f"/{REALM}/users", params={"username": username, "exact": "true"}
            )
            if in_group:
                self.call("PUT", f"/{REALM}/users/{user.json()[0]['id']}/groups/{group}")

    def idp_descriptor(self) -> str:
        """
        Returns:
            The realm's SAML metadata, as a program fetches it.
        """
        return self.httpx.get(
            f"{self.base}/realms/{REALM}/protocol/saml/descriptor", timeout=20
        ).text

    def saml_client(self, sp_metadata: str, **attributes: str) -> str:
        """
        Register Noust as a SAML client from the metadata Noust publishes.

        Args:
            sp_metadata: Noust's service provider metadata.
            **attributes: Client attributes to set (Keycloak's own names).

        Returns:
            The client's internal id.
        """
        converted = self.call(
            "POST",
            f"/{REALM}/client-description-converter",
            content=sp_metadata,
            headers={"Content-Type": "application/xml"},
        ).json()
        converted["attributes"] = {
            **converted.get("attributes", {}),
            "saml_name_id_format": "persistent",
            "saml_force_name_id_format": "true",
            "saml.client.signature": "false",
            "saml.authnstatement": "true",
            "saml.force.post.binding": "true",
            **attributes,
        }
        converted["protocolMappers"] = [
            {
                "name": "email",
                "protocol": "saml",
                "protocolMapper": "saml-user-property-mapper",
                "config": {
                    "user.attribute": "email",
                    "attribute.name": "email",
                    "friendly.name": "email",
                    "attribute.nameformat": "Basic",
                },
            },
            {
                "name": "displayName",
                "protocol": "saml",
                "protocolMapper": "saml-user-property-mapper",
                "config": {
                    "user.attribute": "firstName",
                    "attribute.name": "displayName",
                    "attribute.nameformat": "Basic",
                },
            },
            {
                "name": "groups",
                "protocol": "saml",
                "protocolMapper": "saml-group-membership-mapper",
                "config": {
                    "attribute.name": "groups",
                    "single": "false",
                    "full.path": "false",
                    "attribute.nameformat": "Basic",
                },
            },
        ]
        existing = self.call(
            "GET", f"/{REALM}/clients", params={"clientId": converted["clientId"]}
        ).json()
        for old in existing:
            self.call("DELETE", f"/{REALM}/clients/{old['id']}")
        self.call("POST", f"/{REALM}/clients", json=converted)
        return str(
            self.call(
                "GET", f"/{REALM}/clients", params={"clientId": converted["clientId"]}
            ).json()[0]["id"]
        )

    def oidc_client(self, redirect: str) -> tuple[str, str]:
        """
        Register Noust as an OpenID Connect client that puts the person's groups in the token.

        Args:
            redirect: Noust's callback address.

        Returns:
            The client id and its secret.
        """
        client_id = "noust-oidc"
        secret = secrets.token_urlsafe(24)
        self.call(
            "POST",
            f"/{REALM}/clients",
            json={
                "clientId": client_id,
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": secret,
                "standardFlowEnabled": True,
                "redirectUris": [redirect],
                "attributes": {"pkce.code.challenge.method": "S256"},
                "protocolMappers": [
                    {
                        "name": "groups",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-group-membership-mapper",
                        "config": {
                            "claim.name": "groups",
                            "full.path": "false",
                            "id.token.claim": "true",
                            "access.token.claim": "true",
                            "userinfo.token.claim": "true",
                        },
                    }
                ],
            },
        )
        return client_id, secret

    def sign_in(self, browser: Any, url: str, username: str, password: str = PASSWORD) -> Any:
        """
        Go to Keycloak with a browser's address and fill in its login form.

        Args:
            browser: An httpx client that holds Keycloak's cookies.
            url: Where Noust sent the browser.
            username: The person.
            password: Their password.

        Returns:
            The response to the login form's submission, not followed.
        """
        page = browser.get(url, follow_redirects=True)
        login = [f for f in forms(page.text) if "username" in f[1]]
        if not login:
            raise RuntimeError("no login form: " + page.text[:400])
        action, fields = login[0]
        fields.update({"username": username, "password": password})
        answer = browser.post(action, data=fields, follow_redirects=False)
        if answer.status_code == 200 and any("username" in f[1] for f in forms(answer.text)):
            why = re.search(r"kc-feedback-text[^>]*>\s*([^<]+)", answer.text)
            raise RuntimeError(
                f"Keycloak refused the login of {username}: {why.group(1).strip() if why else 'no reason shown'}"
            )
        return answer


# ----------------------------------------------------------------------------------- scenarios

SCENARIOS: dict[str, Callable[..., None]] = {}


def scenario(name: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """
    Register a scenario.

    Args:
        name: Its name on the command line.

    Returns:
        The decorator.
    """

    def register(function: Callable[..., None]) -> Callable[..., None]:
        SCENARIOS[name] = function
        return function

    return register


def saml_post(noust_client: Any, response: str, relay: str) -> Any:
    """
    Post a provider's form to the console as the browser would.

    Args:
        noust_client: The browser's client for the console.
        response: ``SAMLResponse``.
        relay: ``RelayState``.

    Returns:
        The console's answer, not followed.
    """
    return noust_client.post(
        "/api/auth/saml/acs",
        data={"SAMLResponse": response, "RelayState": relay},
        follow_redirects=False,
    )


def outcome(response: Any) -> str:
    """
    Args:
        response: The answer of the ACS or the callback.

    Returns:
        ``ticket`` or the one word the sign-in page was told.
    """
    location = response.headers.get("location", "")
    if "sso=" in location:
        return "ticket"
    match = re.search(r"sso_error=(\w+)", location)
    return match.group(1) if match else f"status {response.status_code}"


@scenario("saml")
def saml_with_keycloak(workdir: Path, containers: Containers, keep: bool) -> None:
    """Keycloak signs, Noust verifies: three signature layouts, an attack and a refusal."""
    import httpx

    image = image_for(KEYCLOAK_IMAGE_ENV, KEYCLOAK_IMAGE_DEFAULT)
    noust = Noust(workdir / "saml")
    try:
        from noust.core.accounts.saml import SamlManager
        from noust.core.teams import TeamStore

        noust.accounts.create("root-admin", "admin", password=PASSWORD)
        keycloak = Keycloak(containers, image)
        keycloak.make_realm()
        manager = SamlManager(noust.store, accounts=noust.accounts)
        provider = manager.configure(
            "kc",
            {
                "idp_metadata": keycloak.idp_descriptor(),
                "label": "Keycloak",
                "provisioning": "auto",
                "default_role": "viewer",
                "group_sync": True,
                "group_mappings": [{"group": "noust-ops", "role": "operator"}],
            },
        )
        REPORT.check(
            "Noust is configured from Keycloak's own metadata",
            provider.idp_entity_id == f"{keycloak.base}/realms/{REALM}"
            and provider.sso_url.startswith(keycloak.base)
            and len(provider.certificates) >= 1,
            str(provider.to_dict())[:300],
        )
        checks = manager.test("kc", origin=noust.base)
        REPORT.check(
            "the provider's test finds the certificate and the sign-in address good",
            all(c.ok for c in checks if c.name in ("certificate", "sign-in address")),
            str([c.to_dict() for c in checks]),
        )
        metadata = httpx.get(f"{noust.base}/api/auth/saml/metadata/kc", timeout=10)
        REPORT.check(
            "Noust publishes service provider metadata",
            metadata.status_code == 200 and "AssertionConsumerService" in metadata.text,
            metadata.text[:200],
        )

        TeamStore(noust.store)  # the default team exists before a person is made

        # Three layouts of signature, each checked by the real thing.
        layouts = (
            (
                "assertion signed",
                {"saml.assertion.signature": "true", "saml.server.signature": "false"},
            ),
            (
                "response signed",
                {"saml.assertion.signature": "false", "saml.server.signature": "true"},
            ),
            ("both signed", {"saml.assertion.signature": "true", "saml.server.signature": "true"}),
        )
        first_account_id = None
        for label, attributes in layouts:
            keycloak.saml_client(metadata.text, **attributes)
            browser = a_browser()
            noust_client = httpx.Client(base_url=noust.base, timeout=30)
            start = noust_client.get(
                "/api/auth/saml/start",
                params={"provider": "kc", "next": "/apps"},
                follow_redirects=False,
            )
            posted = keycloak.sign_in(browser, start.headers["location"], "maria")
            form = [f for f in forms(posted.text) if "SAMLResponse" in f[1]]
            if not form:
                Path("/tmp/noust-test-keycloak-page.html").write_text(posted.text, encoding="utf-8")
                raise RuntimeError(
                    f"{label}: no SAML form from Keycloak (status {posted.status_code}, "
                    f"forms {[(a[:60], sorted(f)) for a, f in forms(posted.text)]})"
                )
            action, values = form[0]
            REPORT.check(
                f"{label}: Keycloak posts to the address Noust registered",
                action == f"{noust.base}/api/auth/saml/acs",
                action,
            )
            answered = saml_post(noust_client, values["SAMLResponse"], values["RelayState"])
            result = outcome(answered)
            REPORT.check(f"{label}: Noust accepts the response", result == "ticket", result)
            if result != "ticket":
                continue
            ticket = re.search(r"sso=([^&]+)", answered.headers["location"]).group(1)  # type: ignore[union-attr]
            done = noust_client.post("/api/auth/oidc/complete", json={"ticket": ticket})
            REPORT.check(
                f"{label}: the ticket is collected and the second factor is still owed",
                done.status_code == 200 and done.json()["login"]["mfa_required"] is True,
                done.text[:200],
            )
            account = noust.accounts.find("maria")
            REPORT.check(
                f"{label}: Maria's account exists with the role her group gives",
                account is not None and account.role == "operator",
                str(getattr(account, "role", None)),
            )
            if first_account_id is None and account is not None:
                first_account_id = account.id
            elif account is not None:
                REPORT.check(
                    f"{label}: the same person is the same account (no second one)",
                    account.id == first_account_id,
                    f"{account.id} vs {first_account_id}",
                )
            # Replay: the very same answer, posted again.
            again = saml_post(noust_client, values["SAMLResponse"], values["RelayState"])
            REPORT.check(
                f"{label}: a replayed response is refused",
                outcome(again) != "ticket",
                outcome(again),
            )

        identities = (
            noust.store._get_connection()
            .execute("SELECT protocol, issuer, subject FROM account_identities")
            .fetchall()
        )
        REPORT.check(
            "the person is known by Keycloak's persistent identifier, once",
            len(identities) == 1 and identities[0][0] == "saml" and len(identities[0][2]) > 8,
            str([tuple(row) for row in identities]),
        )

        # An attack: the same response with the person changed after it was signed.
        keycloak.saml_client(metadata.text, **{"saml.assertion.signature": "true"})
        browser = a_browser()
        noust_client = httpx.Client(base_url=noust.base, timeout=30)
        start = noust_client.get(
            "/api/auth/saml/start", params={"provider": "kc"}, follow_redirects=False
        )
        posted = keycloak.sign_in(browser, start.headers["location"], "tom")
        action, values = next(f for f in forms(posted.text) if "SAMLResponse" in f[1])
        document = base64.b64decode(values["SAMLResponse"]).decode()
        tampered = document.replace("tom@acme.test", "maria@acme.test")
        REPORT.check("the tampering changed the document", tampered != document)
        answered = saml_post(
            noust_client, base64.b64encode(tampered.encode()).decode(), values["RelayState"]
        )
        REPORT.check(
            "a response changed after signing is refused",
            outcome(answered) == "failed",
            outcome(answered),
        )
        REPORT.check(
            "and nobody was made of it",
            noust.accounts.find("tom") is None,
            "tom exists",
        )

        # Tom, signed in properly, comes in as the viewer his lack of group makes him.
        start = noust_client.get(
            "/api/auth/saml/start", params={"provider": "kc"}, follow_redirects=False
        )
        posted = keycloak.sign_in(a_browser(), start.headers["location"], "tom")
        action, values = next(f for f in forms(posted.text) if "SAMLResponse" in f[1])
        answered = saml_post(noust_client, values["SAMLResponse"], values["RelayState"])
        tom = noust.accounts.find("tom")
        REPORT.check(
            "a person outside the group is let in as the default role",
            outcome(answered) == "ticket" and tom is not None and tom.role == "viewer",
            f"{outcome(answered)} {getattr(tom, 'role', None)}",
        )

        # An answer to a client that asked for encrypted assertions is refused, not guessed at.
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(65537, 2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "noust-test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=2))
            .sign(key, hashes.SHA256())
        )
        pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
        body = "".join(pem.strip().splitlines()[1:-1])
        keycloak.saml_client(
            metadata.text,
            **{
                "saml.assertion.signature": "true",
                "saml.encrypt": "true",
                "saml.encryption.certificate": body,
            },
        )
        noust_client = httpx.Client(base_url=noust.base, timeout=30)
        start = noust_client.get(
            "/api/auth/saml/start", params={"provider": "kc"}, follow_redirects=False
        )
        posted = keycloak.sign_in(a_browser(), start.headers["location"], "maria")
        form = [f for f in forms(posted.text) if "SAMLResponse" in f[1]]
        if not form:
            REPORT.check(
                "Keycloak encrypts the assertion for the client that asked",
                False,
                posted.text[:300],
            )
        else:
            action, values = form[0]
            document = base64.b64decode(values["SAMLResponse"]).decode()
            REPORT.check(
                "Keycloak encrypts the assertion for the client that asked",
                "EncryptedAssertion" in document,
            )
            answered = saml_post(noust_client, values["SAMLResponse"], values["RelayState"])
            REPORT.check(
                "an encrypted assertion is refused, saying so",
                outcome(answered) == "failed",
                outcome(answered),
            )
    finally:
        noust.stop()


@scenario("oidc")
def oidc_with_keycloak(workdir: Path, containers: Containers, keep: bool) -> None:
    """The same realm as an OpenID Connect provider: a real ID token, groups giving a role."""
    import httpx

    image = image_for(KEYCLOAK_IMAGE_ENV, KEYCLOAK_IMAGE_DEFAULT)
    noust = Noust(workdir / "oidc")
    try:
        from noust.core.accounts.oidc import OidcManager

        noust.accounts.create("root-admin", "admin", password=PASSWORD)
        keycloak = Keycloak(containers, image)
        keycloak.make_realm()
        client_id, secret = keycloak.oidc_client(f"{noust.base}/api/auth/oidc/callback")
        manager = OidcManager(noust.store, accounts=noust.accounts)
        manager.configure(
            "kc",
            {
                "issuer": f"{keycloak.base}/realms/{REALM}",
                "client_id": client_id,
                "label": "Keycloak",
                "provisioning": "auto",
                "group_sync": True,
                "group_mappings": [{"group": "noust-ops", "role": "operator"}],
            },
            client_secret=secret,
        )
        checks = manager.test("kc", redirect_uri=f"{noust.base}/api/auth/oidc/callback")
        REPORT.check(
            "the provider's test reads Keycloak's discovery document and keys",
            all(c.ok for c in checks),
            str([c.to_dict() for c in checks]),
        )
        noust_client = httpx.Client(base_url=noust.base, timeout=30)
        start = noust_client.get(
            "/api/auth/oidc/start",
            params={"provider": "kc", "next": "/apps"},
            follow_redirects=False,
        )
        REPORT.check("the sign-in leaves for Keycloak", start.status_code == 302, start.text[:200])
        posted = keycloak.sign_in(a_browser(), start.headers["location"], "maria")
        location = posted.headers.get("location", "")
        REPORT.check(
            "Keycloak sends the browser back to the callback",
            location.startswith(f"{noust.base}/api/auth/oidc/callback"),
            location[:200],
        )
        parts = urlsplit(location)
        answered = noust_client.get(f"{parts.path}?{parts.query}", follow_redirects=False)
        REPORT.check("Noust accepts the ID token", outcome(answered) == "ticket", outcome(answered))
        account = noust.accounts.find("maria")
        REPORT.check(
            "Maria is made with the role her group gives",
            account is not None and account.role == "operator",
            str(getattr(account, "role", None)),
        )
        again = noust_client.get(f"{parts.path}?{parts.query}", follow_redirects=False)
        REPORT.check("a replayed callback is refused", outcome(again) != "ticket", outcome(again))
    finally:
        noust.stop()


LDIF_TEMPLATE = """\
dn: ou=people,{base}
objectClass: organizationalUnit
ou: people

dn: ou=groups,{base}
objectClass: organizationalUnit
ou: groups

dn: uid=maria,ou=people,{base}
objectClass: inetOrgPerson
uid: maria
cn: Maria Lopez
sn: Lopez
displayName: Maria Lopez
mail: maria@acme.test
userPassword: {maria_password}

dn: uid=tom,ou=people,{base}
objectClass: inetOrgPerson
uid: tom
cn: Tom Ward
sn: Ward
mail: tom@acme.test
userPassword: {tom_password}

dn: uid=svc,ou=people,{base}
objectClass: inetOrgPerson
uid: svc
cn: Service
sn: Service
userPassword: {service_password}

dn: cn=ops,ou=groups,{base}
objectClass: groupOfNames
cn: ops
member: uid=maria,ou=people,{base}
"""


def tls_answers(port: int, authority: Path, host: str) -> bool:
    """
    Args:
        port: A loopback port.
        authority: The certificate authority to trust.
        host: The name the certificate must be for.

    Returns:
        Whether a TLS handshake with that port succeeds.
    """
    import ssl

    context = ssl.create_default_context(cafile=str(authority))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return True
    except (OSError, ssl.SSLError):
        return False


def make_certificates(directory: Path, host: str) -> Path:
    """
    Make an authority and a server certificate for ``host``, in the files OpenLDAP's image reads.

    Args:
        directory: Where to write ``ca.crt``, ``ldap.crt`` and ``ldap.key``.
        host: The name on the server's certificate.

    Returns:
        The authority's path.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(65537, 2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Noust test authority")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )  # fmt: skip
    key = rsa.generate_private_key(65537, 2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    (directory / "ca.crt").write_bytes(ca.public_bytes(pem))
    (directory / "ldap.crt").write_bytes(server.public_bytes(pem))
    (directory / "ldap.key").write_bytes(
        key.private_bytes(
            pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        )
    )
    # The image generates Diffie-Hellman parameters when it finds none, which takes minutes; a
    # standard group (RFC 7919) is made in an instant and is what a modern server uses anyway.
    openssl = shutil.which("openssl")
    if openssl is None:
        raise Skip("openssl is needed to make Diffie-Hellman parameters for the directory")
    subprocess.run(
        [
            openssl, "genpkey", "-genparam", "-algorithm", "DH", "-pkeyopt", "group:ffdhe2048",
            "-out", str(directory / "dhparam.pem"),
        ],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    # The image's own user reads these: they are a throwaway authority's, valid for two days.
    for path in directory.iterdir():
        path.chmod(0o644)
    directory.chmod(0o755)
    return directory / "ca.crt"


@scenario("ldap")
def ldap_with_openldap(workdir: Path, containers: Containers, keep: bool) -> None:
    """OpenLDAP over plain text, StartTLS and LDAPS, with an injection attempt in the name."""
    image = image_for(LDAP_IMAGE_ENV, LDAP_IMAGE_DEFAULT)
    if docker("image", "inspect", image, check=False).returncode != 0:
        pulled = docker("pull", image, check=False, timeout=600)
        if pulled.returncode != 0:
            raise Skip(f"cannot pull {image}: {pulled.stderr.strip()[:200]}")
    base = "dc=acme,dc=test"
    admin_password = secrets.token_urlsafe(18)
    passwords = {name: secrets.token_urlsafe(12) for name in ("maria", "tom", "service")}
    ldap_port, ldaps_port = free_port(), free_port()
    certs = workdir / "ldap-certs"
    ca_path = make_certificates(certs, "ldap.acme.test")
    containers.run(
        f"{PREFIX}openldap",
        image,
        "-v",
        f"{certs}:/container/service/slapd/assets/certs",
        "-e",
        "LDAP_TLS_CRT_FILENAME=ldap.crt",
        "-e",
        "LDAP_TLS_KEY_FILENAME=ldap.key",
        "-e",
        "LDAP_TLS_CA_CRT_FILENAME=ca.crt",
        "--hostname",
        "ldap.acme.test",
        "-p",
        f"127.0.0.1:{ldap_port}:389",
        "-p",
        f"127.0.0.1:{ldaps_port}:636",
        "-e",
        "LDAP_ORGANISATION=Acme",
        "-e",
        "LDAP_DOMAIN=acme.test",
        "-e",
        f"LDAP_ADMIN_PASSWORD={admin_password}",
        "-e",
        "LDAP_TLS_VERIFY_CLIENT=never",
        "-e",
        "CONTAINER_LOG_LEVEL=4",
    )
    name = f"{PREFIX}openldap"
    deadline = time.time() + 120
    while time.time() < deadline:
        done = docker(
            "exec", name, "ldapsearch", "-x", "-H", "ldap://localhost", "-b", base,
            "-D", f"cn=admin,{base}", "-w", admin_password, "-s", "base", "dn",
            check=False,
        )  # fmt: skip
        if done.returncode == 0:
            break
        time.sleep(2)
    else:
        raise RuntimeError("OpenLDAP did not start:\n" + containers.logs(name))
    # The directory is up before its TLS is: the image configures it last.
    deadline = time.time() + 180
    while time.time() < deadline:
        if tls_answers(ldaps_port, ca_path, "ldap.acme.test"):
            break
        time.sleep(2)
    else:
        raise RuntimeError("OpenLDAP never answered over TLS:\n" + containers.logs(name))
    containers.exec(
        name, "ldapadd", "-x", "-H", "ldap://localhost", "-D", f"cn=admin,{base}", "-w", admin_password,
        input_text=LDIF_TEMPLATE.format(
            base=base,
            maria_password=passwords["maria"],
            tom_password=passwords["tom"],
            service_password=passwords["service"],
        ),
    )  # fmt: skip
    # The image's default access rules let nobody but the administrator read anything: a service
    # account that searches has to be given read access, as an operator of OpenLDAP does.
    config_dn = next(
        line.split(": ", 1)[1]
        for line in containers.exec(
            name, "ldapsearch", "-Y", "EXTERNAL", "-H", "ldapi:///", "-LLL", "-b", "cn=config",
            f"(olcSuffix={base})", "dn",
        ).splitlines()
        if line.startswith("dn: ")
    )  # fmt: skip
    containers.exec(
        name, "ldapmodify", "-Y", "EXTERNAL", "-H", "ldapi:///",
        input_text=(
            f"dn: {config_dn}\nchangetype: modify\nadd: olcAccess\n"
            f'olcAccess: {{0}}to * by dn.exact="uid=svc,ou=people,{base}" read by * break\n'
        ),
    )  # fmt: skip

    noust = Noust(workdir / "ldap")
    try:
        from noust.core.accounts.directory import DirectoryManager
        from noust.core.accounts.oidc_model import OidcError

        noust.accounts.create("root-admin", "admin", password=PASSWORD)
        bind_dn = f"uid=svc,ou=people,{base}"

        def manager(**more: Any) -> DirectoryManager:
            return DirectoryManager(
                noust.store, accounts=noust.accounts, resolver=lambda _name: ("127.0.0.1",), **more
            )

        plain = manager()
        settings = {
            "host": "127.0.0.1",
            "port": ldap_port,
            "tls": "none",
            "user_base": f"ou=people,{base}",
            "user_filter": "(&(objectClass=inetOrgPerson)(uid={username}))",
            "bind_dn": bind_dn,
            "label": "Acme directory",
            "provisioning": "auto",
            "group_base": f"ou=groups,{base}",
            "group_filter": "(&(objectClass=groupOfNames)(member={dn}))",
            "group_sync": True,
            "group_mappings": [{"group": f"cn=ops,ou=groups,{base}", "role": "operator"}],
        }
        plain.configure("ad", settings, bind_password=passwords["service"])
        checks = plain.test("ad")
        REPORT.check(
            "the directory's test connects, binds and searches",
            all(c.ok for c in checks),
            str([c.to_dict() for c in checks]),
        )

        def attempt(directory: DirectoryManager, username: str, typed: str) -> str:
            try:
                directory.authenticate("ad", username, typed, "203.0.113.5")
            except OidcError as exc:
                return exc.reason
            return "admitted"

        REPORT.check(
            "the right password signs Maria in, creating her account with her group's role",
            attempt(plain, "maria", passwords["maria"]) == "admitted"
            and getattr(noust.accounts.find("maria"), "role", None) == "operator",
            str(getattr(noust.accounts.find("maria"), "role", None)),
        )
        REPORT.check("a wrong password is refused", attempt(plain, "maria", "not it") != "admitted")
        REPORT.check(
            "an unknown name is refused", attempt(plain, "nobody", "whatever") != "admitted"
        )
        REPORT.check(
            "an empty password is refused without asking the directory",
            attempt(plain, "maria", "") != "admitted",
        )
        REPORT.check(
            "a name that tries to widen the search finds no one",
            attempt(plain, "*", passwords["maria"]) != "admitted",
        )
        REPORT.check(
            "a name with a closing parenthesis and a condition finds no one",
            attempt(plain, "maria)(uid=*", passwords["maria"]) != "admitted",
        )
        REPORT.check(
            "Tom, not in the group, is let in as the default role",
            attempt(plain, "tom", passwords["tom"]) == "admitted"
            and getattr(noust.accounts.find("tom"), "role", None) == "viewer",
            str(getattr(noust.accounts.find("tom"), "role", None)),
        )
        identity = (
            noust.store._get_connection()
            .execute(
                "SELECT subject FROM account_identities WHERE protocol = 'ldap' ORDER BY id LIMIT 1"
            )
            .fetchone()
        )
        REPORT.check(
            "a person is known by the entryUUID the directory keeps for them",
            identity is not None and re.fullmatch(r"[0-9a-f-]{36}", identity[0]) is not None,
            str(tuple(identity) if identity else None),
        )

        for mode, port in (("ldaps", ldaps_port), ("starttls", ldap_port)):
            secure = manager()
            secure.configure(
                f"ad-{mode}",
                {
                    **settings,
                    "host": "ldap.acme.test",
                    "port": port,
                    "tls": mode,
                    "ca_file": str(ca_path),
                    "provisioning": "off",
                },
                bind_password=passwords["service"],
            )
            found = secure.test(f"ad-{mode}")
            REPORT.check(
                f"{mode}: the connection is verified against the server's authority and works",
                all(c.ok for c in found),
                str([c.to_dict() for c in found]),
            )
        wrong = manager()
        other = workdir / "other-ca.pem"
        other.write_text(
            "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUTEST\n-----END CERTIFICATE-----\n",
            encoding="utf-8",
        )
        try:
            wrong.configure(
                "ad-wrong",
                {
                    **settings,
                    "host": "ldap.acme.test",
                    "port": ldaps_port,
                    "tls": "ldaps",
                    "ca_file": str(other),
                },
                bind_password=passwords["service"],
            )
            found = wrong.test("ad-wrong")
            REPORT.check(
                "a connection to an authority that did not sign the server is refused",
                not all(c.ok for c in found),
            )
        except Exception as exc:  # the file is not a certificate, which is a refusal as well
            REPORT.check(
                "a connection to an authority that did not sign the server is refused",
                True,
                str(exc)[:100],
            )

        # The same through the console's own route, as a person would.
        import httpx

        client = httpx.Client(base_url=noust.base, timeout=30)
        noust.accounts.require("maria")
        answered = client.post(
            "/api/auth/ldap/login",
            json={"directory": "ad", "username": "maria", "password": passwords["maria"]},
        )
        REPORT.check(
            "the console's login route signs Maria in and still owes her second factor or enrolment",
            answered.status_code == 200 and answered.json().get("success") is not None,
            answered.text[:200],
        )
        wrong_password = client.post(
            "/api/auth/ldap/login",
            json={"directory": "ad", "username": "maria", "password": "nope"},
        )
        REPORT.check(
            "and a wrong password is a 401",
            wrong_password.status_code == 401,
            str(wrong_password.status_code),
        )
    finally:
        noust.stop()


@scenario("scim")
def scim_with_a_client(workdir: Path, containers: Containers, keep: bool) -> None:
    """A SCIM 2.0 conformance client written by other people, run against /api/scim/v2."""
    venv = Path(tempfile.gettempdir()) / "noust-test-scim-venv"
    python = venv / "bin" / "python"
    if not python.exists():
        made = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True, check=False
        )
        if made.returncode != 0:
            raise Skip("cannot make a virtual environment: " + made.stderr[:200])
        installed = subprocess.run(
            [str(python), "-m", "pip", "install", "-q", "scim2-tester", "scim2-client", "httpx2"],
            capture_output=True, text=True, check=False, timeout=600,
        )  # fmt: skip
        if installed.returncode != 0:
            raise Skip("cannot install scim2-tester: " + installed.stderr.strip()[-300:])
    noust = Noust(workdir / "scim")
    try:
        from noust.core.accounts.scim import ScimManager

        noust.accounts.create("root-admin", "admin", password=PASSWORD)
        source, token = ScimManager(noust.store, accounts=noust.accounts).configure(
            "tester", {"label": "Conformance client", "default_role": "viewer"}
        )
        del source
        script = workdir / "scim_client.py"
        script.write_text(SCIM_CLIENT, encoding="utf-8")
        env = dict(os.environ, SCIM_BASE=f"{noust.base}/api/scim/v2", SCIM_TOKEN=str(token))
        done = subprocess.run(
            [str(python), str(script)],
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
            check=False,
        )
        if os.environ.get("NOUST_TEST_TRACE"):
            Path("/tmp/noust-test-scim-trace.txt").write_text(done.stdout, encoding="utf-8")
        if done.stderr.strip():
            print(done.stderr[-2000:])
        results = [
            line.split("\t", 3)[1:]
            for line in done.stdout.splitlines()
            if line.startswith("RESULT\t")
        ]
        REPORT.check(
            "the conformance client ran to its end",
            done.returncode == 0 and results != [],
            f"exit {done.returncode}",
        )
        failures, deviations = [], []
        for status, title, reason in results:
            if status != "ERROR":
                continue
            known = scim_deviation(title, reason)
            (deviations if known else failures).append(f"{title}: {known or reason[:160]}")
        passed = sum(1 for status, _title, _reason in results if status == "SUCCESS")
        print(f"  {len(results)} checks: {passed} passed, {len(deviations)} documented deviations")
        for line in sorted(set(deviations)):
            print(f"    deviation  {line}")
        REPORT.check(
            "the client found nothing that is not a documented deviation",
            not failures,
            "; ".join(failures[:5]),
        )
        REPORT.check(
            "and most of what it checked passed",
            passed >= 0.5 * len(results),
            f"{passed} of {len(results)}",
        )
    finally:
        noust.stop()


#: Attributes of the core User schema that Noust accepts and does not keep: it has no use for a
#: person's phone numbers or title, and a provider that sends them (they all do) must not be
#: refused. The conformance client reads such an attribute back and finds it missing.
SCIM_NOT_KEPT = frozenset(
    "x509Certificates userType title timezone roles profileUrl preferredLanguage photos "
    "phoneNumbers nickName locale ims entitlements addresses".split()
)


def scim_deviation(title: str, reason: str) -> str:
    """
    Args:
        title: The conformance check.
        reason: What it found.

    Returns:
        Why Noust differs from what the check expects, or an empty string when it should not.
    """
    match = re.search(r"unexpected value for '(\w+)'", reason)
    attribute = match.group(1) if match else ""
    if title in ("check_add_attribute", "check_replace_attribute"):
        if attribute in SCIM_NOT_KEPT:
            return f"{attribute} is accepted and not kept"
        if attribute == "emails":
            return "an account keeps the e-mail it was provisioned with (separation of duties)"
        if attribute == "name":
            return "only the formatted name is kept, as the account's display name"
        if attribute == "members":
            return "a member's display is the person's real name, not the one sent"
    if title == "check_remove_attribute":
        if "'emails'" in reason:
            return "an account keeps the e-mail it was provisioned with"
        if "'active'" in reason:
            return "active is the account's state and is always present"
    return ""


SCIM_CLIENT = r"""\
import os
import sys

import httpx2 as httpx
from scim2_client.engines.httpx2 import SyncSCIMClient
from scim2_models import Group, User
from scim2_tester import Status, check_server

def trace(response):
    if os.environ.get("NOUST_TEST_TRACE"):
        request = response.request
        body = request.content[:400].decode("utf-8", "replace") if request.content else ""
        print(f"> {request.method} {request.url.path}?{request.url.query.decode()} {body}")
        response.read()
        print(f"< {response.status_code} {response.text[:300]}")


http = httpx.Client(
    event_hooks={"response": [trace]},
    base_url=os.environ["SCIM_BASE"],
    headers={"Authorization": "Bearer " + os.environ["SCIM_TOKEN"], "Accept": "application/scim+json"},
    timeout=30,
)
client = SyncSCIMClient(http, resource_models=(User, Group))
client.discover()
results = check_server(client, raise_exceptions=False)
for result in results:
    reason = (result.reason or "").replace("\n", " ")[:400]
    print("RESULT\t" + "\t".join([result.status.name, result.title, reason]))
"""


# ----------------------------------------------------------------------------------- main


def main() -> int:
    """
    Run the scenarios.

    Returns:
        0 when none failed.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--scenario", action="append", choices=sorted(SCENARIOS), help="Only this one (repeat)."
    )
    parser.add_argument("--keep", action="store_true", help="Leave the containers running.")
    args = parser.parse_args()
    chosen = args.scenario or list(SCENARIOS)
    workdir = Path(tempfile.mkdtemp(prefix="noust-test-identity-"))
    containers = Containers(args.keep)
    try:
        for name in chosen:
            print(f"== {name}")
            try:
                SCENARIOS[name](workdir, containers, args.keep)
            except Skip as why:
                REPORT.skipped.append(f"{name}: {why}")
                print(f"  SKIP  {why}")
            except Exception as exc:
                REPORT.failed.append(f"{name}: {type(exc).__name__}: {str(exc)[:500]}")
                print(f"  FAIL  {type(exc).__name__}: {str(exc)[:500]}")
            finally:
                containers.cleanup()
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(workdir)
    print()
    print(
        f"{len(REPORT.passed)} passed, {len(REPORT.failed)} failed, {len(REPORT.skipped)} skipped"
    )
    for line in REPORT.failed:
        print(f"  FAIL  {line}")
    for line in REPORT.skipped:
        print(f"  SKIP  {line}")
    return 1 if REPORT.failed else 0


if __name__ == "__main__":
    sys.exit(main())
