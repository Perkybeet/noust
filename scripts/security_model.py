#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Write and check docs/SECURITY-MODEL.md, the public page of what Noust guarantees.

A security page that is only prose goes stale the day after the code changes.
This one is built from the code: the table of roles and permissions is read
from ``roles.py``, the headers from the middleware that sends them, the routes
from the application's own OpenAPI document, the sshd block from the function
that writes it. What cannot be read from the code, the guarantees themselves
and the failure each answers, is data in this file, and every test it names is
checked to exist. ``--check`` (CI runs it, and so does
``tests/test_security_model.py``) fails when the page is out of date; ``--write``
regenerates it.

The page has two kinds of text. Between ``<!-- generated:NAME -->`` and
``<!-- /generated:NAME -->`` it is generated and must not be edited; everything
else is written by hand in the template below the markers' first appearance.

Usage:
    scripts/security_model.py            Check that docs/SECURITY-MODEL.md is current.
    scripts/security_model.py --write    Regenerate it.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import importlib
import importlib.util
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "SECURITY-MODEL.md"

REPOSITORY = "https://github.com/Perkybeet/noust"
COOLIFY_ADVISORIES = "https://github.com/coollabsio/coolify/security/advisories"
DOKPLOY_ADVISORIES = "https://github.com/Dokploy/dokploy/security/advisories"


# ---------------------------------------------------------------------------
# The guarantees (data: the claim, the failure it answers, the proof)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Guarantee:
    """
    One thing Noust promises, and everything needed to check it.

    Attributes:
        number: Its position on the page.
        title: The promise, in a sentence.
        failure: The class of failure in other products it answers, with a
            source anyone can read.
        promise: What holds, stated so that it can be false.
        mechanism: How it is enforced, naming the code.
        evidence: Tests that fail when it stops holding, as ``path::name``
            (``path::Class::name`` for a method; a fixture or a script check
            is named the same way).
        limit: What it does not cover.
    """

    number: int
    title: str
    failure: str
    promise: str
    mechanism: str
    evidence: tuple[str, ...]
    limit: str


GUARANTEES: tuple[Guarantee, ...] = (
    Guarantee(
        1,
        "No process is started from a string of shell",
        f"Command injection is a class of advisory this kind of product has had: Coolify's "
        f"[GHSA-234r-xrrg-m8f3]({COOLIFY_ADVISORIES}/GHSA-234r-xrrg-m8f3) (CVE-2025-64419, "
        f"critical) and Dokploy's "
        f"[GHSA-w223-vw9m-4f9c]({DOKPLOY_ADVISORIES}/GHSA-w223-vw9m-4f9c) (CVE-2026-72901, "
        "critical, remote code execution) are two. In the Coolify 4.4.0 tree read on "
        "2026-10-13 a remote action is a shell string sent over SSH (`instant_remote_process` "
        "is called 281 times in `app/` and `bootstrap/`) and `escapeshellarg` appears 451 "
        "times in the same files.",
        "Every process Noust starts is an argument list. A string where a list is expected, an "
        "empty list and a NUL byte are refused before anything runs; a metacharacter is data. "
        "Every call has a timeout. Secrets reach a process through its environment or its "
        "standard input, never its command line.",
        "`src/noust/core/runner.py` is the only module that imports `subprocess` (or starts a "
        "process any other way); `--dry-run` is implemented in the same place. Text a person or "
        "a repository supplies is judged by the validators listed under *Where text reaches the "
        "machine* before it becomes an argument.",
        (
            "tests/test_architecture.py::TestExecutionSeam::test_nothing_else_imports_subprocess",
            "tests/test_architecture.py::TestExecutionSeam::test_nothing_spawns_a_process_through_asyncio",
            "tests/test_architecture.py::TestExecutionSeam::test_no_shell_execution_anywhere",
            "tests/test_runner.py::TestArgvOnly::test_rejects_a_string_command",
            "tests/test_runner.py::TestArgvOnly::test_rejects_nul_bytes",
            "tests/test_runner.py::TestArgvOnly::test_shell_metacharacters_are_literal_data",
            "tests/test_runner.py::TestSecrets::test_the_secret_never_appears_in_argv",
            "tests/test_adversarial_inputs.py::TestTheRunnerRefusesWhatCannotBeAnArgumentList",
            "tests/conftest.py::forbid_real_subprocess",
        ),
        "It removes a class; it is not immunity. A flaw in a validator, in a program Noust "
        "calls, or in how an argument is interpreted by that program (a value that starts with "
        "`-` is an option to `git`) is still possible, which is why each validator is attacked "
        "by `tests/test_adversarial_inputs.py` and why values that could be options are refused "
        "or placed after `--`.",
    ),
    Guarantee(
        2,
        "The code of a repository does not run as root without a decision on record",
        "A helper container that mounts the Docker socket makes the permission to deploy "
        "equal to root on the host. In the Coolify 4.4.0 tree read on 2026-10-13, the "
        "deployment job starts its helper with `-v /var/run/docker.sock:/var/run/docker.sock` "
        "(`app/Jobs/ApplicationDeploymentJob.php`), and the build commands are the "
        "repository's.",
        "Installing dependencies, building and running a deploy hook happen as `noust-build` "
        "(or the application's own account) in a transient systemd unit with a read-only "
        "system, no access to Noust's configuration or store, and no new privileges. Before the "
        "first sandboxed build a canary proves the sandbox holds, and the build stops, instead "
        "of running as root, when it does not (failing closed). Building as root is a "
        "per-application decision recorded with a name and a reason. A Compose stack is refused "
        "`privileged`, the Docker socket and a bind mount of Noust's state or of another "
        "application's files unless an exception is recorded.",
        "`noust.core.runner.SandboxSpec` and `deployers/helpers/sandbox.py`; "
        "`deployers/helpers/compose_guard.py` for Compose; `docs/security.md#builds`.",
        (
            "tests/test_build_sandbox.py::test_the_sandbox_failing_its_self_test_stops_the_build_instead_of_running_as_root",
            "tests/test_build_sandbox.py::test_building_as_root_needs_a_reason_where_the_server_asks_for_one",
            "tests/test_build_sandbox.py::test_another_applications_env_outside_the_apps_directory_is_inaccessible",
            "tests/test_runner_sandbox.py::TestSandboxArgv::test_every_level_one_property_is_there",
            "tests/test_sandbox_default.py::test_a_passing_trial_turns_the_sandbox_on_and_the_update_builds_in_it",
            "tests/test_sandbox_default.py::test_an_operators_decision_to_build_as_root_is_respected",
            "tests/test_compose_guard.py::TestInspect::test_privileged_is_refused",
            "tests/test_compose_guard.py::TestInspect::test_the_docker_socket_is_refused_in_either_syntax",
            "tests/test_compose_guard.py::TestInspect::test_noust_own_state_is_refused_named_or_contained",
            "tests/test_compose_guard.py::TestInspect::test_another_applications_files_are_refused",
            "tests/test_monorepo_identity.py::TestANewMonorepo::test_it_is_given_an_account_of_its_own",
            "tests/test_repository_symlinks.py::test_the_seam_refuses_to_chmod_through_a_link",
        ),
        "Docker Compose builds run in the Docker daemon, outside any sandbox Noust controls, and "
        "applications deployed before 3.1 are tried in the sandbox on their next update, not "
        "at once. Applications run as the account of their unit, not inside a container: Noust "
        "is **not a multi-tenant isolation boundary** (see *What Noust does not guarantee*).",
    ),
    Guarantee(
        3,
        "A compromised central does not get a shell on the servers it manages",
        "A panel that holds the keys of its servers and does not check theirs is a shell on "
        "every server for whoever takes the panel or sits between it and a server. In the "
        "Coolify 4.4.0 tree read on 2026-10-13, the private keys of the servers it manages "
        "are written as plain files on its own host (`PrivateKey::storeInFileSystem`, "
        "`app/Models/PrivateKey.php:205`) and its SSH calls use "
        "`-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null` "
        "(`app/Helpers/SshMultiplexingHelper.php:426`).",
        "A central reaches a node through a tunnel, to its console port and nowhere else: the "
        "account is `noust-tunnel` (no home, no shell, no password), forced to run "
        "`/usr/bin/false`, with no terminal, no agent or X11 forwarding, no reverse or "
        "Unix-socket forwarding, and one permitted destination. The node, not the central, sets "
        "the ceiling (`read`, `deploy` or `admin`, and host access off by default) and enforces "
        "it whatever the central claims. Sudo mode and approvals are vouched for by the central "
        "and checked again by the node. A node's host key is pinned at enrolment and a change "
        "closes the tunnel.",
        "`fleet/authorize.py` (the sshd block and the key options printed below), "
        "`fleet/policy.py`, `fleet/tunnels.py`, `web/api/node_proxy.py`; `docs/CENTRAL.md`.",
        (
            "tests/test_fleet_guarantees.py::TestTheSshdBlock",
            "tests/test_fleet_guarantees.py::TestTheKeyLine",
            "tests/test_fleet_authorize.py::TestAuthorizedKeys::test_remote_forwarding_is_pinned_to_a_port_nothing_uses",
            "tests/test_fleet_authorize.py::TestTunnelAccount::test_a_missing_account_is_created_without_a_home_a_shell_or_a_password",
            "tests/test_fleet_access.py::TestAdmission::test_a_read_ceiling_withholds_writes_from_any_central",
            "tests/test_fleet_access.py::TestPermits::test_no_ceiling_reaches_the_node_s_own_accounts_security_or_audit",
            "tests/test_fleet_role_admission.py::test_the_nodes_ceiling_narrows_the_role",
            "tests/test_fleet_tunnels.py::TestTunnels::test_a_changed_host_key_is_refused_with_ssh_s_words",
            "tests/test_node_proxy.py::TestElevation::test_an_elevated_operation_needs_the_centrals_sudo_mode",
            "tests/test_fleet_token_scope.py::test_create_api_token_refuses_fleet_with_a_pointer",
        ),
        "The node's API runs as root: a remote code execution in it is a root shell on that "
        "node. The claim is that a compromised *central* gets no shell and no more than the "
        "node's own ceiling, not that nodes cannot be attacked. An external review of the "
        "tunnel has not been done (see *Independent review*).",
    ),
    Guarantee(
        4,
        "Every route declares its permission, and ownership is checked where the data is",
        "Authorization by convention fails in the route that forgets it. The advisory "
        f"[GHSA-725v-f5gh-22q9]({COOLIFY_ADVISORIES}/GHSA-725v-f5gh-22q9) (CVE-2026-57498, "
        "critical) against Coolify is a cross-team IDOR: Livewire components accepted a "
        "`server_id` without scoping it to the team while the API did.",
        "Every API route names the permission it needs in `web/permissions/routes_<area>.py`; "
        "one global dependency enforces it and publishes it as `x-noust-permission`; a route "
        "without one fails a test and is refused when the server runs. Every WebSocket does "
        "the same, at the handshake. What a team may see is decided in the data layer "
        "(`ScopedStore`), and a resource of another team answers 404 with the body of one that "
        "does not exist. A unit Noust did not create cannot be touched through the service "
        "manager, whatever the caller.",
        "`web/permissions/` (the maps, `enforce.py`, `scopes.py`), `core/teams/scoped_store.py`, "
        "`ServiceManager`. The matrix of roles and permissions is generated below.",
        (
            "tests/test_permission_routes.py::TestTheMapsAreComplete::test_every_route_declares_a_permission",
            "tests/test_permission_routes.py::TestTheMapsAreComplete::test_every_other_route_authenticates",
            "tests/test_permission_routes.py::test_every_route_refuses_a_role_without_its_permission",
            "tests/test_permission_routes.py::TestWebSocketsDeclareAPermission::test_every_websocket_route_declares_a_permission",
            "tests/test_scope_architecture.py::TestEveryRouteIsHeldToTheScope::test_every_route_is_classified",
            "tests/test_scope_architecture.py::TestNoRouterReadsAScopedResourceFromTheStore::test_the_api_asks_the_scoped_store",
            "tests/test_scoped_store.py::TestApplications::test_a_member_lists_and_reads_its_teams_applications_only",
            "tests/test_service_manager.py::test_update_config_refuses_a_foreign_unit",
            "tests/test_service_manager.py::test_follow_logs_refuses_a_foreign_unit",
            "tests/test_get_routes_read_only.py::test_no_get_route_is_named_for_an_action",
            "tests/test_web_auth.py::test_every_lockout_path_is_a_real_route",
        ),
        "The CLI is not held to roles: it is the emergency channel, requires root, and is "
        "audited with the operating-system identity that ran it. A team is a boundary of "
        "permissions and organisation, not of execution.",
    ),
    Guarantee(
        5,
        "Root-equivalent actions need a fresh proof, and sometimes a second person",
        "A confirmation that stays valid for hours is not a fresh proof. Coolify's password "
        "confirmation lasts three hours (`password_timeout` is 10800 in `config/auth.php:113`; "
        "4.4.0, read on 2026-10-13); Noust's sudo mode closes fifteen minutes after the last "
        "elevated action, and a window it opened never outlives its ceiling.",
        "Five roles with least privilege. Actions that are destructive or root-equivalent need "
        "sudo mode, a fresh proof of who is asking with a password or a passkey, for every kind "
        "of identity (a single-sign-on user's provider never stands in for the second factor). "
        "Where approvals are on, a root-equivalent change waits for a second person, and the "
        "approver cannot be the requester. The master token is break-glass, not the normal way "
        "in, and is reduced to account recovery under the ENS profile.",
        "`web/api/deps.py` (`require_elevated`), `core/accounts/approvals.py`, "
        "`web/permissions/roles.py`.",
        (
            "tests/test_permission_routes.py::TestTheRoles::test_operators_restart_renew_and_deploy_but_do_not_change",
            "tests/test_permission_routes.py::TestTheRoles::test_admin_does_not_govern_security_accounts_or_the_audit_under_the_ens_matrix",
            "tests/test_web_sudo.py::test_revoking_a_certificate_requires_elevation",
            "tests/test_web_sudo.py::test_an_idle_window_closes_fifteen_minutes_after_the_last_elevated_action",
            "tests/test_web_oidc.py::TestTheWholeSignIn::test_the_providers_claims_about_its_own_mfa_do_not_skip_ours",
            "tests/test_web_oidc.py::TestTheWholeSignIn::test_the_provider_alone_is_never_the_whole_sign_in",
            "tests/test_approvals.py::TestExecution::test_only_the_requester_executes",
            "tests/test_approvals.py::TestDeciding::test_security_approves_and_the_requester_executes_once",
        ),
        "Four-eyes approval is off by default and a single-person installation cannot use it.",
    ),
    Guarantee(
        6,
        "The sign-in surface is limited by a client that cannot choose its own identity",
        "A limit per address means nothing when the client chooses the address it appears to "
        "come from. In the Coolify 4.4.0 tree read on 2026-10-13, `TrustProxies` is `*`, so "
        "`X-Forwarded-*` is believed from anyone. Its published advisories include a "
        f"rate-limit bypass ([GHSA-688j-rm43-5r8x]({COOLIFY_ADVISORIES}/GHSA-688j-rm43-5r8x)), "
        f"reset-link poisoning ([GHSA-cgj8-7m5q-x5gv]({COOLIFY_ADVISORIES}/GHSA-cgj8-7m5q-x5gv)) "
        f"and a `GET` that reset a password "
        f"([GHSA-389w-cc6x-wr2m]({COOLIFY_ADVISORIES}/GHSA-389w-cc6x-wr2m)).",
        "Forwarding headers are believed only from a proxy the operator declared, "
        "and by default from nobody. A request is counted before its endpoint runs: per "
        "address when it carries no valid credential (an IPv6 client by its /64), per "
        "credential when it does; a wrong credential is counted as anonymous and toward the "
        "lockout. Failed sign-ins lock the address and the account, separately. A body is "
        "capped before authentication. A credential holds a bounded number of WebSockets and "
        "event streams, and each is closed after twelve hours. No `GET` changes state, so the "
        "CSRF protection of the other methods is enough. Every account must have a second factor, an authenticator "
        "app or a passkey.",
        "`web/auth.py` (`get_client_ip`, `RateLimiter`, `BruteForceProtection`, `limiter_key`), "
        "`web/server.py` (`SecurityMiddleware`); the numbers are generated below.",
        (
            "tests/test_web_auth.py::test_rotating_x_forwarded_for_does_not_bypass_lockout",
            "tests/test_web_auth.py::test_x_real_ip_does_not_bypass_lockout",
            "tests/test_web_auth.py::test_forwarded_header_is_honoured_for_configured_proxies",
            "tests/test_web_auth.py::test_mutation_with_cookie_and_no_csrf_token_is_rejected",
            "tests/test_web_rate_limit.py::test_a_wrong_credential_is_counted_as_anonymous",
            "tests/test_web_rate_limit.py::test_the_lockout_still_stops_credential_guessing",
            "tests/test_web_rate_limit_ipv6.py::TestThroughTheConsole",
            "tests/test_web_rate_limit_ipv6.py::TestTheLockout",
            "tests/test_web_event_stream_limit.py::test_a_credential_holds_a_few_streams_and_the_next_is_refused",
            "tests/test_get_routes_read_only.py::test_no_get_route_asks_for_sudo_mode_or_approval_unless_it_is_a_sensitive_read",
        ),
        "A deployment behind a proxy it did not declare sees the proxy's address for everyone, "
        "and the console says so. Rate limiting is per process.",
    ),
    Guarantee(
        7,
        "The browser is held to a strict policy that a real browser tests",
        "A page with no policy depends on every line of its markup being right. In the "
        "Coolify 4.4.0 tree searched on 2026-10-13, no Content-Security-Policy, frame, "
        "content-type or transport-security header appears in `app/`, `config/`, "
        "`bootstrap/`, `routes/` or `docker/`, and its session cookie is `same_site => lax` "
        "(`config/session.php:199`). Dokploy has a published clickjacking advisory "
        f"([GHSA-c94j-8wgf-2q9q]({DOKPLOY_ADVISORIES}/GHSA-c94j-8wgf-2q9q)).",
        "The console loads nothing from any other origin and runs under a Content Security Policy "
        "with no inline script or style and Trusted Types. Every response, including those "
        "refused before routing, carries the same hardening headers (printed below). Session "
        "cookies are `HttpOnly`, `SameSite=Strict`, and `Secure` over TLS; every mutation made "
        "with a session carries a CSRF token. The end-to-end suite fails on any CSP violation "
        "and runs axe in both themes.",
        "`web/server.py` (`HARDENING_HEADERS`, `CONTENT_SECURITY_POLICY`), "
        "`panel/e2e/` (the gate), `docs/security.md#the-browser`.",
        (
            "tests/test_console_headers.py::TestEveryKindOfAnswer",
            "tests/test_console_headers.py::TestTheTransport",
            "tests/test_console_headers.py::TestThePolicyItself",
            "tests/test_web_auth.py::test_security_headers_are_present",
            "tests/test_web_auth.py::test_hsts_is_sent_when_https_is_required",
            "tests/test_web_auth.py::test_login_sets_httponly_samesite_cookie",
            "tests/test_web_auth.py::test_cookie_is_secure_over_https",
            "tests/test_architecture.py::TestSelfContained::test_no_external_assets",
            "tests/test_architecture.py::TestSelfContained::test_the_built_console_references_only_same_origin_assets",
            "panel/e2e/fixtures.ts::test",
        ),
        "A policy is enforced by the browser, so a user of a browser that ignores Trusted Types "
        "or CSP is protected by less. The header snapshot below is of what the code sends, not "
        "of what a proxy in front of it may rewrite.",
    ),
    Guarantee(
        8,
        "What was done is recorded, tamper-evident, and shipped off the machine",
        "An audit trail that is deleted, rewritable or lost with the machine cannot answer who "
        "did something after an incident. In the Coolify 4.4.0 tree (commit e793472 of "
        "2026-10-06, read on 2026-10-21) the `audit_events` table has no hash or signature "
        "column (`database/migrations/2026_08_20_000000_create_audit_events_table.php`) and "
        "`AuditEvent::pruneExpired` deletes the rows older than ninety days "
        "(`app/Models/AuditEvent.php:231-236`).",
        "Every change made through the API and every command that changes something is an event "
        "from a closed catalogue (a test scans the code for undeclared ones). Each line carries "
        "the MAC of the previous one; `noust audit verify` finds the first broken link; "
        "lines are shipped to journald, syslog or standard output as they are written. **When "
        "the log cannot be written the action still happens** (an action must not fail for "
        "being recorded), the failure is kept, and `noust health` and the console report it as "
        "an *issue*, not a warning: under the ENS profile an action that leaves no record is a "
        "failure of its own.",
        "`core/audit/` (`record`, `chain`, `catalog`, `sinks`, `status`); `docs/security.md#audit-log`.",
        (
            "tests/test_audit_catalog.py::test_every_event_the_code_records_is_in_the_catalog",
            "tests/test_audit_chain.py::TestWriting::test_two_writers_on_one_file_extend_one_chain",
            "tests/test_audit_chain.py::TestVerify::test_an_unchained_line_after_the_chain_is_found",
            "tests/test_audit_record.py::TestRecord::test_secrets_never_reach_the_log",
            "tests/test_audit_shipping.py::TestJournald::test_every_recorded_event_reaches_journald",
            "tests/test_audit_flood.py::test_identified_actors_are_never_held_back",
            "tests/test_health_builds_and_audit.py::TestTheAuditTrail::test_a_failing_trail_is_an_issue",
        ),
        "Root holds the chain's key and could rewrite a consistent log: a verified chain "
        "proves consistency, not originality, and comparing its head with the copy shipped off "
        "the machine is what proves the log original.",
    ),
    Guarantee(
        9,
        "Secrets are not left in the open, and backups leave encrypted",
        "A secret on a command line is readable by every local user in `ps`. In the 4.4.0 tree "
        "read on 2026-10-13, Coolify passes a PostgreSQL password to `docker exec` as "
        "`-e PGPASSWORD=...` and S3 keys to `mc alias set` "
        "(`app/Jobs/DatabaseBackupJob.php:687` and `:892`).",
        "A secret is never in a command line or a log: it travels in the environment or on "
        "standard input, and a value Noust knows is scrubbed from build output, job logs and "
        "the audit trail. Files that hold one are `0600`. A central can seal its keys and "
        "tokens under a passphrase that is never stored (scrypt, then AES-256-CBC with an "
        "HMAC). A remote destination made with `noust backup destination add` is encrypted "
        "unless `--no-encrypt` says otherwise; with `backup.encryption: required` or the ENS "
        "profile nothing leaves for an unencrypted destination, and the refusal comes before "
        "`rclone` runs. A backup's metadata is signed and a scheduled backup is deep-verified "
        "before it is sent. `noust health` names the destinations that send in clear.",
        "`core/redact.py`, `core/sealing.py`, `managers/backup_destinations.py`, "
        "`managers/security_posture.py`.",
        (
            "tests/test_central_sealing.py::TestLocked::test_a_locked_process_cannot_read",
            "tests/test_central_sealing.py::TestTampering::test_a_file_in_clear_in_a_sealed_store_is_refused",
            "tests/test_redact.py::test_a_build_that_echoes_a_secret_leaves_no_secret_in_the_history",
            "tests/test_redact.py::TestScrubber::test_leaves_short_values_alone",
            "tests/test_ens_backups.py::TestTheSidecarMac::test_archive_and_checksum_replaced_together_are_caught",
            "tests/test_ens_checks.py::TestFailures::test_an_unencrypted_destination_fails",
            "tests/test_cli_backup_destinations.py::test_a_remote_destination_is_encrypted_unless_told_otherwise",
            "tests/test_security_posture.py::TestBackupsLeaveEncrypted",
            "tests/test_security_posture.py::TestTheCentralsKeys",
            "tests/test_backup_destination_hosts.py::TestConfiguringADestination",
            "tests/test_runner.py::TestSecrets::test_the_secret_never_appears_in_argv",
        ),
        "The scrubber works by value: it does not recognise a secret it was not told, and it "
        "leaves values shorter than six characters alone. `config.yaml` holds SMTP and "
        "destination secrets in clear, `0600`; a key beside the ciphertext does not protect "
        "from a compromised host, which is why the central can be sealed. Losing a backup's "
        "encryption key loses the backup.",
    ),
    Guarantee(
        10,
        "A release can be checked, and a fix is announced with its release",
        "A release nobody can check is trusted on the word of the channel it came from, and a "
        "fix announced after its release leaves everyone who updates on time unwarned. In the "
        "4.4.0 tree read on 2026-10-13, Coolify updates itself by downloading a script and "
        "running it with `bash` (`app/Actions/Server/UpdateCoolify.php:122-131`), and its "
        "`SECURITY.md` has a contact address and no timeline. On 2026-10-13 "
        f"[Coolify]({COOLIFY_ADVISORIES}) had published 70 advisories (21 critical) and "
        f"[Dokploy]({DOKPLOY_ADVISORIES}) 60 (34 critical), counted from the published "
        "advisories of each repository.",
        "Every release is built by GitHub Actions from its tag, signed from 4.0.0 on with the "
        "maintainer's SSH key. Its files have a signed "
        "checksum file and a build-provenance attestation; the wheel has its SBOM attested; "
        "the container image is signed and attested by digest, and a fixable critical "
        "vulnerability in it stops the release; PyPI publishes with attestations; the "
        "installer is published with its own signed checksum. The update the product performs is one fixed "
        "command per installation method; nothing of the request reaches the command line. A "
        "release that fixes a vulnerability is titled *(security release)*, which the update "
        "check, `noust health` and the console report. `SECURITY.md` commits to acknowledging "
        "in 3 working days, confirming in 10 and fixing in 90.",
        "`.github/workflows/release.yml`, `.github/workflows/security.yml` (CodeQL, gitleaks, "
        "Trivy, dependency review), `scripts/release.py`, `SECURITY.md`, "
        "`.well-known/security.txt`, `managers/self_update.py`.",
        (
            "tests/test_security_workflows.py::TestTheReleaseIsSignedAndAttested",
            "tests/test_security_workflows.py::TestTheSecurityWorkflow",
            "tests/test_security_workflows.py::TestPinning",
            "tests/test_security_workflows.py::TestASecurityReleaseSaysSo",
            "tests/test_security_policy.py::TestSecurityTxt",
            "tests/test_release_script.py::test_the_real_security_policy_lists_the_current_line",
            "tests/test_sbom_script.py::TestTheSbom::test_the_real_lockfile_reads",
            "tests/test_security_release.py::TestReadingTheReleases",
            "tests/test_security_release.py::TestTheUpdateCheckAsksOnlyWhenThereIsSomethingNewer",
            "tests/test_self_update.py::TestStart::test_each_method_has_its_one_command",
        ),
        "The pip and pipx update path trusts TLS and PyPI (and the attestations PyPI shows) "
        "without pinning hashes; the OBS repositories are signed by their own key. No "
        "independent review has been done.",
    ),
    Guarantee(
        11,
        "A compliance profile is one module that every area reads",
        "A hardening profile that each area applies for itself drifts: the profile is whatever "
        "the last area decided. A profile that is one module every area reads can be checked "
        "in one place.",
        "`security.profile: ens-medium` fixes, in one module, the values Spain's Esquema Nacional "
        "de Seguridad (RD 311/2022) expects of a category MEDIUM system: sessions, lockout, "
        "passwords, token lifetimes, audit retention, approvals, reasons for changes, backup "
        "encryption, the console's certificate. `noust ens check` compares the server with "
        "it and `noust ens report` writes the evidence.",
        "`core/ens/profile.py`; the mapping control by control is `docs/ENS.md`.",
        (
            "tests/test_ens_profile.py::TestEveryAreaReadsTheOneModule",
            "tests/test_ens_profile.py::TestTheProfilesNumbers::test_outside_the_profile_nothing_is_forced",
            "tests/test_ens_checks.py::TestFailures::test_an_old_or_unverified_backup_warns_and_a_missing_one_fails",
            "tests/test_ens_incident.py::TestThePackage::test_every_file_is_in_the_manifest_with_its_sha256",
        ),
        "It is a mapping of controls to settings, not a certification, and the organisational "
        "measures stay the organisation's.",
    ),
)


# ---------------------------------------------------------------------------
# Where text reaches the machine (data: the classes of input and their guards)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputClass:
    """
    A kind of text a person or a repository supplies, and the one place it is judged.

    Attributes:
        name: The class.
        fields: Where it comes in (API fields, CLI arguments, repository files).
        guard: ``module:function`` that judges it; importable and callable.
        sink: Where an accepted value goes.
        accepts: What an accepted value is.
        tests: The tests that attack the guard.
    """

    name: str
    fields: str
    guard: str
    sink: str
    accepts: str
    tests: tuple[str, ...]
    refusals: tuple[str, ...] = field(default=())


INPUT_CLASSES: tuple[InputClass, ...] = (
    InputClass(
        "Application, service, database and file names",
        "`name` of an application, service, database, database user, upload or backup file",
        "noust.validators.names:validate_app_name",
        "a directory under the apps directory, a unit file name, a SQL identifier",
        "one path segment of letters, digits and `._@-`, starting with an alphanumeric, at most "
        "64 characters (63 for SQL); never `.`, `..` or a reserved device name; a trailing "
        "newline is refused",
        ("tests/test_adversarial_inputs.py::TestNamesThatBecomePathsUnitsAndSql",),
    ),
    InputClass(
        "Paths inside a directory",
        "persistent paths, release and backup member names, a file inside an application",
        "noust.validators.names:resolve_within",
        "a file the process opens or writes as root",
        "a path that, resolved (symlinks included), is strictly inside the base directory",
        ("tests/test_adversarial_inputs.py::TestPathContainment",),
    ),
    InputClass(
        "Domains and host names",
        "`domain` of an application, site, preview or node",
        "noust.validators.domain:validate_domain",
        "web server `server_name`, `certbot -d`, a directory and a unit name",
        "lowercase letters, digits, hyphens and dots; labels of at most 63 characters and "
        "253 in all; scheme, path and port are cut before the check",
        ("tests/test_adversarial_inputs.py::TestDomains",),
    ),
    InputClass(
        "Environment variable names and values, and unit values",
        "`env_vars`, `.env` entries, a unit's description, command and working directory",
        "noust.validators.environment:validate_unit_value",
        "`EnvironmentFile=`, `Environment=` and every directive of a systemd unit",
        "no control character, so a value cannot end its line; names are POSIX identifiers; a "
        "value is escaped for the quote it sits in",
        (
            "tests/test_adversarial_inputs.py::TestEnvironmentAndUnitValues",
            "tests/test_adversarial_inputs.py::TestTheUnitTemplateKeepsEveryValueOnItsOwnLine",
        ),
    ),
    InputClass(
        "Sources: Git remotes, refs, tags, commits and archives",
        "`source`, `branch`, `tag`, `commit` of an application; an archive URL",
        "noust.validators.source:validate_source",
        "`git` and `git clone` arguments, a directory name, an outbound HTTP request",
        "an http(s), ssh or git URL, an absolute local path or a download URL; never a remote "
        "helper (`ext::`, `fd::`), a value starting with `-`, a control character or a host "
        "inside the machine's own network",
        (
            "tests/test_adversarial_inputs.py::TestSources",
            "tests/test_source_archive_hosts.py::TestTheDownload",
        ),
    ),
    InputClass(
        "Hosts a request is sent to",
        "a notification URL, a backup destination host, a source archive URL, a repository index",
        "noust.core.outbound:require_public_host",
        "an outbound connection made as root",
        "a host whose every address is outside loopback, private, link-local and the other "
        "networks of the machine, unless the operator named it",
        (
            "tests/test_outbound.py::TestRequirePublicHost",
            "tests/test_backup_destination_hosts.py::TestConfiguringADestination",
            "tests/test_source_archive_hosts.py::TestTheRedirects",
        ),
    ),
    InputClass(
        "Health check path and ports",
        "`health_path`, `port`, a container's published port",
        "noust.validators.health:check_health_path",
        "the request line of the deploy's health gate; a socket",
        "a path on the application (not a host), printable ASCII only; a port from 1 to 65535",
        (
            "tests/test_adversarial_inputs.py::TestHealthCheckPath",
            "tests/test_adversarial_inputs.py::TestPorts",
        ),
    ),
    InputClass(
        "Container images and options",
        "`image`, a digest, build arguments, volumes, a restart policy, a registry",
        "noust.validators.container:check_image_reference",
        "arguments of `docker` and a generated compose file",
        "a reference that cannot be read as an option, with no whitespace or URL scheme",
        ("tests/test_adversarial_inputs.py::TestContainerReferences",),
    ),
    InputClass(
        "Identifiers Noust made",
        "a release id, a backup id, a destination name in a path parameter",
        "noust.deployers.releases:is_release_id",
        "a directory or an archive read or deleted as root",
        "exactly the shape Noust generates, matched against the whole string",
        ("tests/test_adversarial_inputs.py::TestIdentifiersOfThingsNoustWrote",),
    ),
    InputClass(
        "Command lines of any process",
        "every argument list Noust builds, including the repository's hooks and build commands",
        "noust.core.runner:_validate",
        "`execve`",
        "a sequence of strings, never one string; not empty; no NUL byte",
        ("tests/test_adversarial_inputs.py::TestTheRunnerRefusesWhatCannotBeAnArgumentList",),
    ),
)


# ---------------------------------------------------------------------------
# Verifying that the evidence exists
# ---------------------------------------------------------------------------


def evidence_exists(reference: str) -> bool:
    """
    Check that a test reference names something that exists.

    Args:
        reference: ``path``, ``path::name`` or ``path::Class::name``; the name
            may be a test function, a class, a method, or a function or
            fixture defined at module level.

    Returns:
        True when the file exists and the dotted names are defined in it.
    """
    path_text, _, rest = reference.partition("::")
    path = ROOT / path_text
    if not path.is_file():
        return False
    if not rest:
        return True
    if path.suffix != ".py":
        # A TypeScript spec or helper: the name must appear in the file.
        return rest.split("::")[-1] in path.read_text(encoding="utf-8")
    scope: list[ast.stmt] = ast.parse(path.read_text(encoding="utf-8")).body
    for name in rest.split("::"):
        match = next(
            (
                node
                for node in scope
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ),
            None,
        )
        if match is None:
            return False
        scope = match.body if isinstance(match, ast.ClassDef) else []
    return True


def missing_evidence() -> list[str]:
    """
    List every test reference of the page that does not exist.

    Returns:
        The references, empty when all of them do.
    """
    references = [ref for guarantee in GUARANTEES for ref in guarantee.evidence]
    references += [ref for item in INPUT_CLASSES for ref in item.tests]
    return sorted({ref for ref in references if not evidence_exists(ref)})


def missing_guards() -> list[str]:
    """
    List every guard of an input class that cannot be imported and called.

    Returns:
        The ``module:function`` strings that failed.
    """
    broken = []
    for item in INPUT_CLASSES:
        module_name, _, function = item.guard.partition(":")
        try:
            target = getattr(importlib.import_module(module_name), function)
        except (ImportError, AttributeError):
            broken.append(item.guard)
            continue
        if not callable(target):
            broken.append(item.guard)
    return broken


# ---------------------------------------------------------------------------
# The generated blocks
# ---------------------------------------------------------------------------


def _openapi_schema() -> dict[str, Any]:
    """
    Build the application's OpenAPI document the way ``export_openapi.py`` does.

    Returns:
        The document.
    """
    spec = importlib.util.spec_from_file_location(
        "export_openapi_for_model", ROOT / "scripts" / "export_openapi.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("scripts/export_openapi.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("export_openapi_for_model", module)
    spec.loader.exec_module(module)
    return module._build_schema()  # type: ignore[no-any-return]


def block_guarantees() -> str:
    """The eleven guarantees, each with its evidence."""
    parts = []
    for item in GUARANTEES:
        evidence = "\n".join(f"  - `{ref}`" for ref in item.evidence)
        parts.append(
            f"### {item.number}. {item.title}\n\n"
            f"**The failure it answers.** {item.failure}\n\n"
            f"**The guarantee.** {item.promise}\n\n"
            f"**How it is enforced.** {item.mechanism}\n\n"
            f"**What fails if it stops holding.**\n\n{evidence}\n\n"
            f"**What it does not cover.** {item.limit}\n"
        )
    return "\n".join(parts)


def block_input_classes() -> str:
    """The classes of input, the guard of each, and where an accepted value goes."""
    rows = [
        "| Class | Comes in as | Judged by | Goes to | Accepted means | Attacked by |",
        "|---|---|---|---|---|---|",
    ]
    for item in INPUT_CLASSES:
        tests = "<br>".join(f"`{ref}`" for ref in item.tests)
        rows.append(
            f"| {item.name} | {item.fields} | `{item.guard}` | {item.sink} | {item.accepts} | {tests} |"
        )
    return "\n".join(rows)


def block_headers() -> str:
    """The headers every response carries, as the middleware sends them."""
    from noust.web.server import (
        _DISABLED_FEATURES,
        CONTENT_SECURITY_POLICY,
        HARDENING_HEADERS,
        HSTS_VALUE,
    )

    rows = ["| Header | Value |", "|---|---|"]
    for name, value in HARDENING_HEADERS.items():
        if name == "Content-Security-Policy":
            continue
        rows.append(f"| `{name}` | `{value}` |")
    rows.append(
        f"| `Strict-Transport-Security` (over TLS, direct or through a declared proxy) | `{HSTS_VALUE}` |"
    )
    rows.append(
        "| `Cache-Control` | `no-store`, except hashed files under `/assets/`: `public, max-age=31536000, immutable` |"
    )
    directives = "\n".join(
        f"  {part};" if not part.endswith(";") else f"  {part}"
        for part in CONTENT_SECURITY_POLICY.split("; ")
    )
    return (
        "\n".join(rows)
        + "\n\nThe policy:\n\n```\nContent-Security-Policy:\n"
        + directives.rstrip(";")
        + "\n```\n\nBrowser features switched off for the console: "
        + ", ".join(f"`{feature}`" for feature in _DISABLED_FEATURES)
        + "."
    )


def block_roles() -> str:
    """The matrix of roles and permissions, from ``roles.py``."""
    from noust.web.permissions import DESCRIPTIONS
    from noust.web.permissions.roles import ROLE_PERMISSIONS, STANDARD_ROLE_PERMISSIONS

    # The matrix the ENS review decided (the roles split), and beside it the one column that
    # differs by default: one person running their own servers needs one account (item 83).
    columns = {
        **{role: ROLE_PERMISSIONS[role] for role in ROLE_PERMISSIONS},
        "admin (default)": STANDARD_ROLE_PERMISSIONS["admin"],
    }
    headers = {"admin": "admin (roles split)"}
    names = [headers.get(name, name) for name in columns]
    rows = [
        "| Permission | What it allows | " + " | ".join(names) + " |",
        "|---|---|" + "|".join("---" for _ in names) + "|",
    ]
    for permission in sorted(DESCRIPTIONS):
        held = ["yes" if permission in columns[name] else "" for name in columns]
        rows.append(f"| `{permission}` | {DESCRIPTIONS[permission]} | " + " | ".join(held) + " |")
    return "\n".join(rows)


def block_routes() -> str:
    """How many operations need which permission, from the application's own OpenAPI document."""
    from noust.web.permissions.websockets import WEBSOCKETS

    schema = _openapi_schema()
    methods = ("get", "put", "post", "delete", "patch")
    operations = [
        operation
        for item in schema["paths"].values()
        for method, operation in item.items()
        if method in methods
    ]
    per_permission: dict[str, int] = {}
    for operation in operations:
        per_permission[operation["x-noust-permission"]] = (
            per_permission.get(operation["x-noust-permission"], 0) + 1
        )
    elevated = sum(1 for operation in operations if operation.get("x-noust-requires-elevation"))
    approved = sum(1 for operation in operations if operation.get("x-noust-requires-approval"))
    public = per_permission.get("public", 0)
    lines = [
        f"- **{len(operations)}** API operations. **{len(operations) - public}** need a permission "
        f"(`x-noust-permission`) and **{public}** are public on purpose (the sign-in, the health "
        "probe, the forge webhooks, which check their own credential); a route that is neither "
        "fails a test and is refused when the server runs.",
        f"- **{elevated}** operations need sudo mode and **{approved}** can need a second person.",
        f"- **{len(WEBSOCKETS)}** WebSocket routes, each with its permission, checked at the handshake.",
        "",
        "| Permission | Operations |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | {count} |" for name, count in sorted(per_permission.items())]
    return "\n".join(lines)


def block_limits() -> str:
    """The limits of the sign-in surface and the console, from the constants that enforce them."""
    from noust.web import auth

    return "\n".join(
        [
            "| Limit | Default |",
            "|---|---|",
            f"| Requests without a valid credential, per client (an IPv6 client by its /{auth.IPV6_COUNTED_PREFIX}) | {auth.RATE_LIMIT_MAX_REQUESTS} per {auth.RATE_LIMIT_WINDOW} seconds |",
            f"| Requests with a valid credential, per credential | {auth.RATE_LIMIT_AUTHENTICATED_MAX_REQUESTS} per {auth.RATE_LIMIT_WINDOW} seconds |",
            f"| Failed sign-ins before an address, and separately an account, is locked | {auth.MAX_FAILED_ATTEMPTS}, for {auth.LOCKOUT_DURATION // 60} minutes |",
            f"| A request body | {auth.MAX_BODY_BYTES // (1024 * 1024)} MiB ({auth.MAX_HOOK_BODY_BYTES // (1024 * 1024)} MiB for a forge webhook) |",
            f"| Open WebSockets per credential | {auth.WS_MAX_PER_CREDENTIAL} |",
            f"| Open event streams per credential | {auth.EVENT_STREAMS_MAX_PER_CREDENTIAL}, each closed after 12 hours |",
            f"| Bad webhook signatures before an application's hook is locked | per application, for {auth.WEBHOOK_LOCKOUT_DURATION // 60} minutes |",
        ]
    )


def block_fleet() -> str:
    """The restrictions a node places on the central's key, as the code writes them."""
    from noust.fleet.authorize import authorized_key_line, tunnel_policy_block
    from noust.fleet.models import PublicKey

    key = PublicKey(
        key_type="ssh-ed25519",
        blob="AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderPlaceholderPlaceholderPlaceho",
    )
    return (
        "In `sshd_config` (a drop-in that `noust fleet authorize` writes, validates with `sshd -t` "
        "and puts back if sshd refuses it):\n\n```\n"
        + tunnel_policy_block("noust-tunnel", 8443)
        + "```\n\nAnd on the central's key, the same limits repeated where they cannot be overridden by "
        "another part of the configuration:\n\n```\n"
        + authorized_key_line("central-1", key, 8443)
        + "\n```\n"
    )


def block_audit() -> str:
    """The size of the audit catalogue."""
    from noust.core.audit import catalog

    categories: dict[str, int] = {}
    for spec in catalog.EVENTS.values():
        categories[spec.category] = categories.get(spec.category, 0) + 1
    rows = ["| Category | Events |", "|---|---|"]
    rows += [
        f"| {name} | {categories[name]} |" for name in catalog.CATEGORIES if name in categories
    ]
    return f"The catalogue holds **{len(catalog.EVENTS)}** events.\n\n" + "\n".join(rows)


BLOCKS: dict[str, Callable[[], str]] = {
    "guarantees": block_guarantees,
    "input-classes": block_input_classes,
    "headers": block_headers,
    "roles": block_roles,
    "routes": block_routes,
    "limits": block_limits,
    "fleet": block_fleet,
    "audit": block_audit,
}

_REGION = re.compile(
    r"(?P<open><!-- generated:(?P<name>[a-z-]+) -->)(?P<body>.*?)(?P<close><!-- /generated:(?P=name) -->)",
    re.DOTALL,
)


def render(text: str) -> str:
    """
    Replace every generated region of the page with its current content.

    Args:
        text: The page as it is.

    Returns:
        The page with each ``<!-- generated:NAME -->`` region regenerated.

    Raises:
        KeyError: A region names a block this script does not know.
    """

    def fill(match: re.Match[str]) -> str:
        return f"{match.group('open')}\n{BLOCKS[match.group('name')]()}\n{match.group('close')}"

    return _REGION.sub(fill, text)


def check() -> int:
    """
    Compare the page with what the code says.

    Returns:
        0 when it is current, 1 with a diff otherwise.
    """
    if not DOC.exists():
        print(f"{DOC.relative_to(ROOT)}: missing; run scripts/security_model.py --write")
        return 1
    current = DOC.read_text(encoding="utf-8")
    problems = []
    absent = [name for name in BLOCKS if f"<!-- generated:{name} -->" not in current]
    if absent:
        problems.append(f"the page has no region for: {', '.join(absent)}")
    for reference in missing_evidence():
        problems.append(f"evidence that does not exist: {reference}")
    for guard in missing_guards():
        problems.append(f"a guard that cannot be called: {guard}")
    expected = render(current)
    if expected != current:
        diff = difflib.unified_diff(
            current.splitlines(),
            expected.splitlines(),
            "docs/SECURITY-MODEL.md",
            "generated",
            lineterm="",
            n=1,
        )
        problems.append("the page is out of date:\n" + "\n".join(list(diff)[:60]))
    if problems:
        print("docs/SECURITY-MODEL.md:")
        for problem in problems:
            print(f"  - {problem}")
        print("Regenerate with: python scripts/security_model.py --write")
        return 1
    print("docs/SECURITY-MODEL.md is current")
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    Command line entry point.

    Args:
        argv: Arguments, defaulting to ``sys.argv``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate the page")
    args = parser.parse_args(argv)
    if args.write:
        DOC.write_text(render(DOC.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"wrote {DOC.relative_to(ROOT)}")
        return check()
    return check()


if __name__ == "__main__":
    sys.exit(main())
