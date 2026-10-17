# Security model and guarantees

Noust runs as root and deploys code from repositories onto the machine it manages, so what it
promises about itself has to be something you can check. This page lists each promise, the failure
in other products that it answers (with a source anyone can read), how Noust enforces it, and the
tests that fail if it stops being true. It also says, as plainly, what Noust does not promise.

It is not prose that can go stale. The tables below are generated from the code, and
`tests/test_security_model.py` fails when this page, the code or the tests it names disagree:

```bash
python scripts/security_model.py            # check that this page is current
python scripts/security_model.py --write    # regenerate the generated parts
```

How Noust is built to be safe to operate (the threat model, every control, every file mode) is
[security.md](security.md). The contact for a vulnerability and the timelines are
[SECURITY.md](../SECURITY.md). The ENS mapping is [ENS.md](ENS.md).

## What is trusted, in one paragraph

Root on the machine, and whoever can run `noust` as root, are trusted: Noust does not defend a
machine against its own administrator. Accounts with the `admin` role and tokens that hold
`root_equivalent` can write units, cron commands and site configuration that run as root, which is
why those actions ask for sudo mode and, where approvals are on, for a second person. Everything
else is treated as hostile input: a repository, a form field, a webhook, a notification URL, a
central that has been compromised, an application that has been compromised.

## The guarantees

<!-- generated:guarantees -->
### 1. No process is started from a string of shell

**The failure it answers.** Command injection is the largest class of advisories in the products Noust is compared with: Coolify's [GHSA-234r-xrrg-m8f3](https://github.com/coollabsio/coolify/security/advisories/GHSA-234r-xrrg-m8f3) (CVE-2025-64419, critical) and Dokploy's [GHSA-w223-vw9m-4f9c](https://github.com/Dokploy/dokploy/security/advisories/GHSA-w223-vw9m-4f9c) (CVE-2026-72901, critical, remote code execution) are two. Coolify builds each remote action as a shell string sent over SSH (`instant_remote_process` is called 281 times in `app/` and `bootstrap/` of the 4.4.0 tree read on 2026-10-13) and escapes by hand (`escapeshellarg` appears 451 times in the same files).

**The guarantee.** Every process Noust starts is an argument list. A string where a list is expected, an empty list and a NUL byte are refused before anything runs; a metacharacter is data. Every call has a timeout. Secrets reach a process through its environment or its standard input, never its command line.

**How it is enforced.** `src/noust/core/runner.py` is the only module that imports `subprocess` (or starts a process any other way); `--dry-run` is implemented in the same place. Text a person or a repository supplies is judged by the validators listed under *Where text reaches the machine* before it becomes an argument.

**What fails if it stops holding.**

  - `tests/test_architecture.py::TestExecutionSeam::test_nothing_else_imports_subprocess`
  - `tests/test_architecture.py::TestExecutionSeam::test_nothing_spawns_a_process_through_asyncio`
  - `tests/test_architecture.py::TestExecutionSeam::test_no_shell_execution_anywhere`
  - `tests/test_runner.py::TestArgvOnly::test_rejects_a_string_command`
  - `tests/test_runner.py::TestArgvOnly::test_rejects_nul_bytes`
  - `tests/test_runner.py::TestArgvOnly::test_shell_metacharacters_are_literal_data`
  - `tests/test_runner.py::TestSecrets::test_the_secret_never_appears_in_argv`
  - `tests/test_adversarial_inputs.py::TestTheRunnerRefusesWhatCannotBeAnArgumentList`
  - `tests/conftest.py::forbid_real_subprocess`

**What it does not cover.** It removes a class; it is not immunity. A flaw in a validator, in a program Noust calls, or in how an argument is interpreted by that program (a value that starts with `-` is an option to `git`) is still possible, which is why each validator is attacked by `tests/test_adversarial_inputs.py` and why values that could be options are refused or placed after `--`.

### 2. The code of a repository does not run as root without a decision on record

**The failure it answers.** A helper container that mounts the Docker socket makes the permission to deploy equal to root on the host: Coolify's deployment job starts its helper with `-v /var/run/docker.sock:/var/run/docker.sock` (`app/Jobs/ApplicationDeploymentJob.php`, 4.4.0, read on 2026-10-13), and the build commands are the repository's.

**The guarantee.** Installing dependencies, building and running a deploy hook happen as `noust-build` (or the application's own account) in a transient systemd unit with a read-only system, no access to Noust's configuration or store, and no new privileges. Before the first sandboxed build a canary proves the sandbox holds, and the build stops, instead of running as root, when it does not (failing closed). Building as root is a per-application decision recorded with a name and a reason. A Compose stack is refused `privileged`, the Docker socket and a bind mount of Noust's state or of another application's files unless an exception is recorded.

**How it is enforced.** `noust.core.runner.SandboxSpec` and `deployers/helpers/sandbox.py`; `deployers/helpers/compose_guard.py` for Compose; `docs/security.md#builds`.

**What fails if it stops holding.**

  - `tests/test_build_sandbox.py::test_the_sandbox_failing_its_self_test_stops_the_build_instead_of_running_as_root`
  - `tests/test_build_sandbox.py::test_building_as_root_needs_a_reason`
  - `tests/test_build_sandbox.py::test_another_applications_env_outside_the_apps_directory_is_inaccessible`
  - `tests/test_runner_sandbox.py::TestSandboxArgv::test_every_level_one_property_is_there`
  - `tests/test_sandbox_default.py::test_a_passing_trial_turns_the_sandbox_on_and_the_update_builds_in_it`
  - `tests/test_sandbox_default.py::test_an_operators_decision_to_build_as_root_is_respected`
  - `tests/test_compose_guard.py::TestInspect::test_privileged_is_refused`
  - `tests/test_compose_guard.py::TestInspect::test_the_docker_socket_is_refused_in_either_syntax`
  - `tests/test_compose_guard.py::TestInspect::test_noust_own_state_is_refused_named_or_contained`
  - `tests/test_compose_guard.py::TestInspect::test_another_applications_files_are_refused`
  - `tests/test_monorepo_identity.py::TestANewMonorepo::test_it_is_given_an_account_of_its_own`
  - `tests/test_repository_symlinks.py::test_the_seam_refuses_to_chmod_through_a_link`

**What it does not cover.** Docker Compose builds run in the Docker daemon, outside any sandbox Noust controls, and applications deployed before 3.1 are tried in the sandbox on their next update, not at once. Applications run as the account of their unit, not inside a container: Noust is **not a multi-tenant isolation boundary** (see *What Noust does not guarantee*).

### 3. A compromised central does not get a shell on the servers it manages

**The failure it answers.** Coolify's panel writes the private keys of the servers it manages as plain files on its own host (`PrivateKey::storeInFileSystem`, `app/Models/PrivateKey.php:205`) and makes its SSH calls with `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null` (`app/Helpers/SshMultiplexingHelper.php:426`; 4.4.0, read on 2026-10-13), so whoever takes the panel, or sits between it and a server, has what it needs to act as root there.

**The guarantee.** A central reaches a node through a tunnel, to its console port and nowhere else: the account is `noust-tunnel` (no home, no shell, no password), forced to run `/usr/bin/false`, with no terminal, no agent or X11 forwarding, no reverse or Unix-socket forwarding, and one permitted destination. The node, not the central, sets the ceiling (`read`, `deploy` or `admin`, and host access off by default) and enforces it whatever the central claims. Sudo mode and approvals are vouched for by the central and checked again by the node. A node's host key is pinned at enrolment and a change closes the tunnel.

**How it is enforced.** `fleet/authorize.py` (the sshd block and the key options printed below), `fleet/policy.py`, `fleet/tunnels.py`, `web/api/node_proxy.py`; `docs/CENTRAL.md`.

**What fails if it stops holding.**

  - `tests/test_fleet_guarantees.py::TestTheSshdBlock`
  - `tests/test_fleet_guarantees.py::TestTheKeyLine`
  - `tests/test_fleet_authorize.py::TestAuthorizedKeys::test_remote_forwarding_is_pinned_to_a_port_nothing_uses`
  - `tests/test_fleet_authorize.py::TestTunnelAccount::test_a_missing_account_is_created_without_a_home_a_shell_or_a_password`
  - `tests/test_fleet_access.py::TestAdmission::test_a_read_ceiling_withholds_writes_from_any_central`
  - `tests/test_fleet_access.py::TestPermits::test_no_ceiling_reaches_the_node_s_own_accounts_security_or_audit`
  - `tests/test_fleet_role_admission.py::test_the_nodes_ceiling_narrows_the_role`
  - `tests/test_fleet_tunnels.py::TestTunnels::test_a_changed_host_key_is_refused_with_ssh_s_words`
  - `tests/test_node_proxy.py::TestElevation::test_an_elevated_operation_needs_the_centrals_sudo_mode`
  - `tests/test_fleet_token_scope.py::test_create_api_token_refuses_fleet_with_a_pointer`

**What it does not cover.** The node's API runs as root: a remote code execution in it is a root shell on that node. The claim is that a compromised *central* gets no shell and no more than the node's own ceiling, not that nodes cannot be attacked. An external review of the tunnel has not been done (see *Independent review*).

### 4. Every route declares its permission, and ownership is checked where the data is

**The failure it answers.** Authorization by convention: Coolify's Livewire components accepted a `server_id` without scoping it to the team while its API did (cross-team IDOR, [GHSA-725v-f5gh-22q9](https://github.com/coollabsio/coolify/security/advisories/GHSA-725v-f5gh-22q9), CVE-2026-57498, critical).

**The guarantee.** Every API route names the permission it needs in `web/permissions/routes_<area>.py`; one global dependency enforces it and publishes it as `x-noust-permission`; a route without one fails a test and is refused when the server runs. Every WebSocket does the same, at the handshake. What a team may see is decided in the data layer (`ScopedStore`), and a resource of another team answers 404 with the body of one that does not exist. A unit Noust did not create cannot be touched through the service manager, whatever the caller.

**How it is enforced.** `web/permissions/` (the maps, `enforce.py`, `scopes.py`), `core/teams/scoped_store.py`, `ServiceManager`. The matrix of roles and permissions is generated below.

**What fails if it stops holding.**

  - `tests/test_permission_routes.py::TestTheMapsAreComplete::test_every_route_declares_a_permission`
  - `tests/test_permission_routes.py::TestTheMapsAreComplete::test_every_other_route_authenticates`
  - `tests/test_permission_routes.py::test_every_route_refuses_a_role_without_its_permission`
  - `tests/test_permission_routes.py::TestWebSocketsDeclareAPermission::test_every_websocket_route_declares_a_permission`
  - `tests/test_scope_architecture.py::TestEveryRouteIsHeldToTheScope::test_every_route_is_classified`
  - `tests/test_scope_architecture.py::TestNoRouterReadsAScopedResourceFromTheStore::test_the_api_asks_the_scoped_store`
  - `tests/test_scoped_store.py::TestApplications::test_a_member_lists_and_reads_its_teams_applications_only`
  - `tests/test_service_manager.py::test_update_config_refuses_a_foreign_unit`
  - `tests/test_service_manager.py::test_follow_logs_refuses_a_foreign_unit`
  - `tests/test_get_routes_read_only.py::test_no_get_route_is_named_for_an_action`
  - `tests/test_web_auth.py::test_every_lockout_path_is_a_real_route`

**What it does not cover.** The CLI is not held to roles: it is the emergency channel, requires root, and is audited with the operating-system identity that ran it. A team is a boundary of permissions and organisation, not of execution.

### 5. Root-equivalent actions need a fresh proof, and sometimes a second person

**The failure it answers.** Coolify's password confirmation lasts three hours (`password_timeout` is 10800 in `config/auth.php:113`; 4.4.0, read on 2026-10-13); Noust's sudo mode closes fifteen minutes after the last elevated action, and a window it opened never outlives its ceiling.

**The guarantee.** Five roles with least privilege. Actions that are destructive or root-equivalent need sudo mode, a fresh proof of who is asking with a password or a passkey, for every kind of identity (a single-sign-on user's provider never stands in for the second factor). Where approvals are on, a root-equivalent change waits for a second person, and the approver cannot be the requester. The master token is break-glass, not the normal way in, and is reduced to account recovery under the ENS profile.

**How it is enforced.** `web/api/deps.py` (`require_elevated`), `core/accounts/approvals.py`, `web/permissions/roles.py`.

**What fails if it stops holding.**

  - `tests/test_permission_routes.py::TestTheRoles::test_operators_restart_renew_and_deploy_but_do_not_change`
  - `tests/test_permission_routes.py::TestTheRoles::test_admin_does_not_govern_security_accounts_or_the_audit`
  - `tests/test_web_sudo.py::test_revoking_a_certificate_requires_elevation`
  - `tests/test_web_sudo.py::test_an_idle_window_closes_fifteen_minutes_after_the_last_elevated_action`
  - `tests/test_web_oidc.py::TestTheWholeSignIn::test_the_providers_claims_about_its_own_mfa_do_not_skip_ours`
  - `tests/test_web_oidc.py::TestTheWholeSignIn::test_the_provider_alone_is_never_the_whole_sign_in`
  - `tests/test_approvals.py::TestExecution::test_only_the_requester_executes`
  - `tests/test_approvals.py::TestDeciding::test_security_approves_and_the_requester_executes_once`

**What it does not cover.** Four-eyes approval is off by default and a single-person installation cannot use it.

### 6. The sign-in surface is limited by a client that cannot choose its own identity

**The failure it answers.** Coolify trusts `X-Forwarded-*` from anyone (`TrustProxies` is `*`): a rate-limit bypass ([GHSA-688j-rm43-5r8x](https://github.com/coollabsio/coolify/security/advisories/GHSA-688j-rm43-5r8x)) and reset-link poisoning ([GHSA-cgj8-7m5q-x5gv](https://github.com/coollabsio/coolify/security/advisories/GHSA-cgj8-7m5q-x5gv)) follow, and a `GET` that reset a password ([GHSA-389w-cc6x-wr2m](https://github.com/coollabsio/coolify/security/advisories/GHSA-389w-cc6x-wr2m)).

**The guarantee.** Forwarding headers are believed only from a proxy the operator declared, and by default from nobody. A request is counted before its endpoint runs: per address when it carries no valid credential (an IPv6 client by its /64), per credential when it does; a wrong credential is counted as anonymous and toward the lockout. Failed sign-ins lock the address and the account, separately. A body is capped before authentication. A credential holds a bounded number of WebSockets and event streams, and each is closed after twelve hours. No `GET` changes state, so the CSRF protection of the other methods is enough. Every account must have a second factor, an authenticator app or a passkey.

**How it is enforced.** `web/auth.py` (`get_client_ip`, `RateLimiter`, `BruteForceProtection`, `limiter_key`), `web/server.py` (`SecurityMiddleware`); the numbers are generated below.

**What fails if it stops holding.**

  - `tests/test_web_auth.py::test_rotating_x_forwarded_for_does_not_bypass_lockout`
  - `tests/test_web_auth.py::test_x_real_ip_does_not_bypass_lockout`
  - `tests/test_web_auth.py::test_forwarded_header_is_honoured_for_configured_proxies`
  - `tests/test_web_auth.py::test_mutation_with_cookie_and_no_csrf_token_is_rejected`
  - `tests/test_web_rate_limit.py::test_a_wrong_credential_is_counted_as_anonymous`
  - `tests/test_web_rate_limit.py::test_the_lockout_still_stops_credential_guessing`
  - `tests/test_web_rate_limit_ipv6.py::TestThroughTheConsole`
  - `tests/test_web_rate_limit_ipv6.py::TestTheLockout`
  - `tests/test_web_event_stream_limit.py::test_a_credential_holds_a_few_streams_and_the_next_is_refused`
  - `tests/test_get_routes_read_only.py::test_no_get_route_asks_for_sudo_mode_or_approval_unless_it_is_a_sensitive_read`

**What it does not cover.** A deployment behind a proxy it did not declare sees the proxy's address for everyone, and the console says so. Rate limiting is per process.

### 7. The browser is held to a strict policy that a real browser tests

**The failure it answers.** No Content-Security-Policy, frame, content-type or transport-security headers appear in Coolify's `app/`, `config/`, `bootstrap/`, `routes/` or `docker/` (searched on 2026-10-13), and its session cookie is `same_site => lax` (`config/session.php:199`); Dokploy had a clickjacking advisory ([GHSA-c94j-8wgf-2q9q](https://github.com/Dokploy/dokploy/security/advisories/GHSA-c94j-8wgf-2q9q)).

**The guarantee.** The console loads nothing from any other origin and runs under a Content Security Policy with no inline script or style and Trusted Types. Every response, including those refused before routing, carries the same hardening headers (printed below). Session cookies are `HttpOnly`, `SameSite=Strict`, and `Secure` over TLS; every mutation made with a session carries a CSRF token. The end-to-end suite fails on any CSP violation and runs axe in both themes.

**How it is enforced.** `web/server.py` (`HARDENING_HEADERS`, `CONTENT_SECURITY_POLICY`), `panel/e2e/` (the gate), `docs/security.md#the-browser`.

**What fails if it stops holding.**

  - `tests/test_console_headers.py::TestEveryKindOfAnswer`
  - `tests/test_console_headers.py::TestTheTransport`
  - `tests/test_console_headers.py::TestThePolicyItself`
  - `tests/test_web_auth.py::test_security_headers_are_present`
  - `tests/test_web_auth.py::test_hsts_is_sent_when_https_is_required`
  - `tests/test_web_auth.py::test_login_sets_httponly_samesite_cookie`
  - `tests/test_web_auth.py::test_cookie_is_secure_over_https`
  - `tests/test_architecture.py::TestSelfContained::test_no_external_assets`
  - `tests/test_architecture.py::TestSelfContained::test_the_built_console_references_only_same_origin_assets`
  - `panel/e2e/fixtures.ts::test`

**What it does not cover.** A policy is enforced by the browser, so a user of a browser that ignores Trusted Types or CSP is protected by less. The header snapshot below is of what the code sends, not of what a proxy in front of it may rewrite.

### 8. What was done is recorded, tamper-evident, and shipped off the machine

**The failure it answers.** Coolify's audit log is a ninety-day table written on a best-effort basis, with no tamper evidence.

**The guarantee.** Every change made through the API and every command that changes something is an event from a closed catalogue (a test scans the code for undeclared ones). Each line carries the MAC of the previous one; `noust audit verify` finds the first broken link; lines are shipped to journald, syslog or standard output as they are written. **When the log cannot be written the action still happens** (an action must not fail for being recorded), the failure is kept, and `noust health` and the console report it as an *issue*, not a warning: under the ENS profile an action that leaves no record is a failure of its own.

**How it is enforced.** `core/audit/` (`record`, `chain`, `catalog`, `sinks`, `status`); `docs/security.md#audit-log`.

**What fails if it stops holding.**

  - `tests/test_audit_catalog.py::test_every_event_the_code_records_is_in_the_catalog`
  - `tests/test_audit_chain.py::TestWriting::test_two_writers_on_one_file_extend_one_chain`
  - `tests/test_audit_chain.py::TestVerify::test_an_unchained_line_after_the_chain_is_found`
  - `tests/test_audit_record.py::TestRecord::test_secrets_never_reach_the_log`
  - `tests/test_audit_shipping.py::TestJournald::test_every_recorded_event_reaches_journald`
  - `tests/test_audit_flood.py::test_identified_actors_are_never_held_back`
  - `tests/test_health_builds_and_audit.py::TestTheAuditTrail::test_a_failing_trail_is_an_issue`

**What it does not cover.** Root holds the chain's key and could rewrite a consistent log: a verified chain proves consistency, not originality, and comparing its head with the copy shipped off the machine is what proves the log original.

### 9. Secrets are not left in the open, and backups leave encrypted

**The failure it answers.** Coolify passes a PostgreSQL password to `docker exec` as `-e PGPASSWORD=...` and S3 keys to `mc alias set` on a command line (`app/Jobs/DatabaseBackupJob.php:687` and `:892`; 4.4.0, read on 2026-10-13), where every local user can read them in `ps`.

**The guarantee.** A secret is never in a command line or a log: it travels in the environment or on standard input, and a value Noust knows is scrubbed from build output, job logs and the audit trail. Files that hold one are `0600`. A central can seal its keys and tokens under a passphrase that is never stored (scrypt, then AES-256-CBC with an HMAC). A remote destination made with `noust backup destination add` is encrypted unless `--no-encrypt` says otherwise; with `backup.encryption: required` or the ENS profile nothing leaves for an unencrypted destination, and the refusal comes before `rclone` runs. A backup's metadata is signed and a scheduled backup is deep-verified before it is sent. `noust health` names the destinations that send in clear.

**How it is enforced.** `core/redact.py`, `core/sealing.py`, `managers/backup_destinations.py`, `managers/security_posture.py`.

**What fails if it stops holding.**

  - `tests/test_central_sealing.py::TestLocked::test_a_locked_process_cannot_read`
  - `tests/test_central_sealing.py::TestTampering::test_a_file_in_clear_in_a_sealed_store_is_refused`
  - `tests/test_redact.py::test_a_build_that_echoes_a_secret_leaves_no_secret_in_the_history`
  - `tests/test_redact.py::TestScrubber::test_leaves_short_values_alone`
  - `tests/test_ens_backups.py::TestTheSidecarMac::test_archive_and_checksum_replaced_together_are_caught`
  - `tests/test_ens_checks.py::TestFailures::test_an_unencrypted_destination_fails`
  - `tests/test_cli_backup_destinations.py::test_a_remote_destination_is_encrypted_unless_told_otherwise`
  - `tests/test_security_posture.py::TestBackupsLeaveEncrypted`
  - `tests/test_security_posture.py::TestTheCentralsKeys`
  - `tests/test_backup_destination_hosts.py::TestConfiguringADestination`
  - `tests/test_runner.py::TestSecrets::test_the_secret_never_appears_in_argv`

**What it does not cover.** The scrubber works by value: it does not recognise a secret it was not told, and it leaves values shorter than six characters alone. `config.yaml` holds SMTP and destination secrets in clear, `0600`; a key beside the ciphertext does not protect from a compromised host, which is why the central can be sealed. Losing a backup's encryption key loses the backup.

### 10. A release can be checked, and a fix is announced with its release

**The failure it answers.** Coolify updates itself by downloading a script and running it with `bash` (`app/Actions/Server/UpdateCoolify.php:122-131`; 4.4.0, read on 2026-10-13), and its `SECURITY.md` is twenty-five lines with a contact address and no timeline. On 2026-10-13 [Coolify](https://github.com/coollabsio/coolify/security/advisories) had published 70 advisories (21 critical) and [Dokploy](https://github.com/Dokploy/dokploy/security/advisories) 60 (34 critical), counted from the published advisories of each repository; a fix whose advisory is published long after the release leaves everyone who updates on time unwarned.

**The guarantee.** Every release is built by GitHub Actions from a signed tag. Its files have a signed checksum file and a build-provenance attestation; the wheel has its SBOM attested; the container image is signed and attested by digest, and a fixable critical vulnerability in it stops the release; PyPI publishes with attestations; the installer is published with its own signed checksum. The update the product performs is one fixed command per installation method; nothing of the request reaches the command line. A release that fixes a vulnerability is titled *(security release)*, which the update check, `noust health` and the console report. `SECURITY.md` commits to acknowledging in 3 working days, confirming in 10 and fixing in 90.

**How it is enforced.** `.github/workflows/release.yml`, `.github/workflows/security.yml` (CodeQL, gitleaks, Trivy, dependency review), `scripts/release.py`, `SECURITY.md`, `.well-known/security.txt`, `managers/self_update.py`.

**What fails if it stops holding.**

  - `tests/test_security_workflows.py::TestTheReleaseIsSignedAndAttested`
  - `tests/test_security_workflows.py::TestTheSecurityWorkflow`
  - `tests/test_security_workflows.py::TestPinning`
  - `tests/test_security_workflows.py::TestASecurityReleaseSaysSo`
  - `tests/test_security_policy.py::TestSecurityTxt`
  - `tests/test_release_script.py::test_the_real_security_policy_lists_the_current_line`
  - `tests/test_sbom_script.py::TestTheSbom::test_the_real_lockfile_reads`
  - `tests/test_security_release.py::TestReadingTheReleases`
  - `tests/test_security_release.py::TestTheUpdateCheckAsksOnlyWhenThereIsSomethingNewer`
  - `tests/test_self_update.py::TestStart::test_each_method_has_its_one_command`

**What it does not cover.** The pip and pipx update path trusts TLS and PyPI (and the attestations PyPI shows) without pinning hashes; the OBS repositories are signed by their own key. No independent review has been done.

### 11. A compliance profile is one module that every area reads

**The failure it answers.** Coolify has no hardening profile for self-hosted installations.

**The guarantee.** `security.profile: ens-medium` fixes, in one module, the values Spain's Esquema Nacional de Seguridad (RD 311/2022) expects of a category MEDIUM system: sessions, lockout, passwords, token lifetimes, audit retention, approvals, reasons for changes, backup encryption, the console's certificate. `noust ens check` compares the server with it and `noust ens report` writes the evidence.

**How it is enforced.** `core/ens/profile.py`; the mapping control by control is `docs/ENS.md`.

**What fails if it stops holding.**

  - `tests/test_ens_profile.py::TestEveryAreaReadsTheOneModule`
  - `tests/test_ens_profile.py::TestTheProfilesNumbers::test_outside_the_profile_nothing_is_forced`
  - `tests/test_ens_checks.py::TestFailures::test_an_old_or_unverified_backup_warns_and_a_missing_one_fails`
  - `tests/test_ens_incident.py::TestThePackage::test_every_file_is_in_the_manifest_with_its_sha256`

**What it does not cover.** It is a mapping of controls to settings, not a certification, and the organisational measures stay the organisation's.

<!-- /generated:guarantees -->

## Where text from a person reaches the machine

Noust turns text it was given into paths, argument lists, unit files, web server configuration and
SQL. There is no list of bad strings: each class of text is judged by an allowlist at one place,
and each place is attacked by `tests/test_adversarial_inputs.py` with generated text and with the
payloads that have beaten validators before. Two properties are held for every one: it answers or
refuses with its own error (never a crash on hostile input), and what it accepts is inert where it
goes. Two defects were found this way when the suite was written, both in code that claimed to
refuse what it accepted: a pattern ending in `$` matches before a trailing newline, so
`validate_app_name("shop\n")` was accepted (a ratchet,
`tests/test_regex_anchors.py`, now stops a new one), and a domain was checked after being stripped
and returned before.

The database server, not Noust, enforces what a SQL statement may do: on PostgreSQL, MySQL and
MariaDB the console's read mode runs as an account that can only `SELECT`, in a read-only
transaction, and a keyword list is not asked to do that job. MongoDB and Redis have no read-only
mode, so every statement there is a write that needs `databases.write` and sudo mode.

<!-- generated:input-classes -->
| Class | Comes in as | Judged by | Goes to | Accepted means | Attacked by |
|---|---|---|---|---|---|
| Application, service, database and file names | `name` of an application, service, database, database user, upload or backup file | `noust.validators.names:validate_app_name` | a directory under the apps directory, a unit file name, a SQL identifier | one path segment of letters, digits and `._@-`, starting with an alphanumeric, at most 64 characters (63 for SQL); never `.`, `..` or a reserved device name; a trailing newline is refused | `tests/test_adversarial_inputs.py::TestNamesThatBecomePathsUnitsAndSql` |
| Paths inside a directory | persistent paths, release and backup member names, a file inside an application | `noust.validators.names:resolve_within` | a file the process opens or writes as root | a path that, resolved (symlinks included), is strictly inside the base directory | `tests/test_adversarial_inputs.py::TestPathContainment` |
| Domains and host names | `domain` of an application, site, preview or node | `noust.validators.domain:validate_domain` | web server `server_name`, `certbot -d`, a directory and a unit name | lowercase letters, digits, hyphens and dots; labels of at most 63 characters and 253 in all; scheme, path and port are cut before the check | `tests/test_adversarial_inputs.py::TestDomains` |
| Environment variable names and values, and unit values | `env_vars`, `.env` entries, a unit's description, command and working directory | `noust.validators.environment:validate_unit_value` | `EnvironmentFile=`, `Environment=` and every directive of a systemd unit | no control character, so a value cannot end its line; names are POSIX identifiers; a value is escaped for the quote it sits in | `tests/test_adversarial_inputs.py::TestEnvironmentAndUnitValues`<br>`tests/test_adversarial_inputs.py::TestTheUnitTemplateKeepsEveryValueOnItsOwnLine` |
| Sources: Git remotes, refs, tags, commits and archives | `source`, `branch`, `tag`, `commit` of an application; an archive URL | `noust.validators.source:validate_source` | `git` and `git clone` arguments, a directory name, an outbound HTTP request | an http(s), ssh or git URL, an absolute local path or a download URL; never a remote helper (`ext::`, `fd::`), a value starting with `-`, a control character or a host inside the machine's own network | `tests/test_adversarial_inputs.py::TestSources`<br>`tests/test_source_archive_hosts.py::TestTheDownload` |
| Hosts a request is sent to | a notification URL, a backup destination host, a source archive URL, a repository index | `noust.core.outbound:require_public_host` | an outbound connection made as root | a host whose every address is outside loopback, private, link-local and the other networks of the machine, unless the operator named it | `tests/test_outbound.py::TestRequirePublicHost`<br>`tests/test_backup_destination_hosts.py::TestConfiguringADestination`<br>`tests/test_source_archive_hosts.py::TestTheRedirects` |
| Health check path and ports | `health_path`, `port`, a container's published port | `noust.validators.health:check_health_path` | the request line of the deploy's health gate; a socket | a path on the application (not a host), printable ASCII only; a port from 1 to 65535 | `tests/test_adversarial_inputs.py::TestHealthCheckPath`<br>`tests/test_adversarial_inputs.py::TestPorts` |
| Container images and options | `image`, a digest, build arguments, volumes, a restart policy, a registry | `noust.validators.container:check_image_reference` | arguments of `docker` and a generated compose file | a reference that cannot be read as an option, with no whitespace or URL scheme | `tests/test_adversarial_inputs.py::TestContainerReferences` |
| Identifiers Noust made | a release id, a backup id, a destination name in a path parameter | `noust.deployers.releases:is_release_id` | a directory or an archive read or deleted as root | exactly the shape Noust generates, matched against the whole string | `tests/test_adversarial_inputs.py::TestIdentifiersOfThingsNoustWrote` |
| Command lines of any process | every argument list Noust builds, including the repository's hooks and build commands | `noust.core.runner:_validate` | `execve` | a sequence of strings, never one string; not empty; no NUL byte | `tests/test_adversarial_inputs.py::TestTheRunnerRefusesWhatCannotBeAnArgumentList` |
<!-- /generated:input-classes -->

The one place text deliberately reaches a configuration as written is `custom_directives` in a
repository's `noust.nginx.yaml`, which goes into the site's `server` block behind only the web
server's own test: read it as you would read a site before deploying a repository you do not control.

## The numbers behind the guarantees

### Who may do what

Five roles, and the permissions each holds. The master token is not a role: while no account exists it
holds every permission, afterwards (it is break-glass) all but `audit.manage`, and under the ENS profile
only what recovering an account takes.

<!-- generated:roles -->
| Permission | What it allows | viewer | operator | admin | security | auditor |
|---|---|---|---|---|---|---|
| `accounts.manage` | Create, change, lock and remove accounts; others' sessions |  |  |  | yes |  |
| `accounts.read` | See accounts and separation-of-duties exceptions |  |  |  | yes | yes |
| `apps.deploy` | Update, roll back, activate a release, rebuild |  | yes | yes |  |  |
| `apps.manage` | Create, configure and delete applications, sites and domains |  |  | yes |  |  |
| `apps.operate` | Start, stop and restart; renew certificates; run cron jobs |  | yes | yes |  |  |
| `apps.read` | See applications, sites, certificates, jobs, logs and deployments | yes | yes | yes | yes | yes |
| `apps.terminal` | A terminal and one-off commands as an application's own account, with its secrets |  |  | yes |  |  |
| `audit.manage` | Manage the audit log and activity history |  |  |  | yes |  |
| `audit.read` | Read the whole audit log |  |  |  | yes | yes |
| `backups.manage` | Restore, delete, and configure destinations |  |  | yes |  |  |
| `backups.read` | See backups, schedules and destinations | yes | yes | yes | yes | yes |
| `backups.run` | Create and verify backups |  | yes | yes |  |  |
| `compliance.read` | Read the ENS compliance check and its evidence report |  |  | yes | yes | yes |
| `databases.manage` | Install engines, drop databases, restore dumps |  |  | yes |  |  |
| `databases.read` | See database engines, databases and users | yes | yes | yes | yes | yes |
| `databases.write` | Create databases and users, run queries |  |  | yes |  |  |
| `fleet.manage` | Add and remove servers |  |  | yes |  |  |
| `fleet.read` | See the servers this central manages | yes | yes | yes | yes | yes |
| `root_equivalent` | Raw units, cron commands, backup hooks, raw site configuration |  |  | yes |  |  |
| `secrets.reveal` | Read secrets in clear: .env values, exports, keys |  |  | yes |  |  |
| `security.manage` | Change security settings: console access, sign-in policy |  |  |  | yes |  |
| `self` | Own session, second factor, password, tokens and usage notice | yes | yes | yes | yes | yes |
| `server.host_access` | Change the host itself: SSH, firewall, packages |  |  | yes |  |  |
| `server.manage` | Manage the server: services, the monitor |  |  | yes |  |  |
| `server.read` | See the server: system, services, metrics, monitor | yes | yes | yes | yes | yes |
| `server.terminal` | A root shell on the server itself, in the browser |  |  | yes |  |  |
| `settings.manage` | Change the configuration outside security settings |  |  | yes |  |  |
| `settings.read` | Read the configuration, secrets redacted | yes | yes | yes | yes | yes |
| `teams.manage` | Create and delete teams, projects and environments, and their members |  |  | yes |  |  |
| `teams.read` | See the teams, projects and environments one belongs to | yes | yes | yes | yes | yes |
<!-- /generated:roles -->

### The routes

<!-- generated:routes -->
- **826** API operations. **812** need a permission (`x-noust-permission`) and **14** are public on purpose (the sign-in, the health probe, the forge webhooks, which check their own credential); a route that is neither fails a test and is refused when the server runs.
- **281** operations need sudo mode and **35** can need a second person.
- **6** WebSocket routes, each with its permission, checked at the handshake.

| Permission | Operations |
|---|---|
| `accounts.manage` | 22 |
| `accounts.read` | 8 |
| `apps.deploy` | 24 |
| `apps.manage` | 113 |
| `apps.operate` | 28 |
| `apps.read` | 109 |
| `apps.terminal` | 12 |
| `audit.manage` | 1 |
| `audit.read` | 9 |
| `backups.manage` | 21 |
| `backups.read` | 19 |
| `backups.run` | 14 |
| `compliance.read` | 3 |
| `databases.manage` | 33 |
| `databases.read` | 55 |
| `databases.write` | 12 |
| `fleet.manage` | 38 |
| `fleet.read` | 18 |
| `public` | 14 |
| `root_equivalent` | 15 |
| `secrets.reveal` | 13 |
| `security.manage` | 6 |
| `self` | 40 |
| `server.host_access` | 17 |
| `server.manage` | 44 |
| `server.read` | 68 |
| `server.terminal` | 1 |
| `settings.manage` | 25 |
| `settings.read` | 20 |
| `teams.manage` | 14 |
| `teams.read` | 10 |
<!-- /generated:routes -->

### The browser

<!-- generated:headers -->
| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `no-referrer` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Permissions-Policy` | `accelerometer=(), bluetooth=(), browsing-topics=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), hid=(), interest-cohort=(), magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=()` |
| `X-Permitted-Cross-Domain-Policies` | `none` |
| `Strict-Transport-Security` (over TLS, direct or through a declared proxy) | `max-age=31536000; includeSubDomains` |
| `Cache-Control` | `no-store`, except hashed files under `/assets/`: `public, max-age=31536000, immutable` |

The policy:

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self';
  img-src 'self' data:;
  font-src 'self';
  connect-src 'self';
  frame-ancestors 'none';
  base-uri 'none';
  form-action 'self' https://github.com;
  object-src 'none';
  require-trusted-types-for 'script'
```

Browser features switched off for the console: `accelerometer`, `bluetooth`, `browsing-topics`, `camera`, `display-capture`, `geolocation`, `gyroscope`, `hid`, `interest-cohort`, `magnetometer`, `microphone`, `midi`, `payment`, `serial`, `usb`.
<!-- /generated:headers -->

### Limits

<!-- generated:limits -->
| Limit | Default |
|---|---|
| Requests without a valid credential, per client (an IPv6 client by its /64) | 120 per 60 seconds |
| Requests with a valid credential, per credential | 1200 per 60 seconds |
| Failed sign-ins before an address, and separately an account, is locked | 5, for 15 minutes |
| A request body | 1 MiB (5 MiB for a forge webhook) |
| Open WebSockets per credential | 8 |
| Open event streams per credential | 16, each closed after 12 hours |
| Bad webhook signatures before an application's hook is locked | per application, for 15 minutes |
<!-- /generated:limits -->

### The fleet's tunnel

What `noust fleet authorize` writes on a node so that the central it enrols can reach the node's
console and nothing else:

<!-- generated:fleet -->
In `sshd_config` (a drop-in that `noust fleet authorize` writes, validates with `sshd -t` and puts back if sshd refuses it):

```
Match User noust-tunnel
    AuthorizedKeysFile /etc/ssh/noust/%u.keys
    PubkeyAuthentication yes
    AuthenticationMethods publickey
    DisableForwarding no
    AllowTcpForwarding local
    AllowStreamLocalForwarding no
    PermitOpen 127.0.0.1:8443
    PermitListen none
    GatewayPorts no
    PermitTunnel no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PermitUserRC no
    ForceCommand /usr/bin/false
```

And on the central's key, the same limits repeated where they cannot be overridden by another part of the configuration:

```
restrict,port-forwarding,permitopen="127.0.0.1:8443",permitlisten="127.0.0.1:1",command="/usr/bin/false" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderPlaceholderPlaceholderPlaceho noust-central:central-1
```

<!-- /generated:fleet -->

### The audit trail

<!-- generated:audit -->
The catalogue holds **427** events.

| Category | Events |
|---|---|
| access | 35 |
| account | 36 |
| config | 17 |
| change | 280 |
| read | 20 |
| denial | 15 |
| system | 21 |
| host | 3 |
<!-- /generated:audit -->

## Compared with Coolify and Dokploy

Dated 2026-10-13, against Coolify's `main` (its own version string reads 4.4.0) and Dokploy's
published advisories. Every cell of the Coolify column was read in that source tree, at the path
given; Dokploy's source was not read, so nothing is claimed of it beyond what its advisories say.
It compares designs; it is not a ranking, and a project with more users and more researchers has
more advisories whatever its code.

| | Coolify | Dokploy | Noust |
|---|---|---|---|
| Published security advisories ([Coolify](https://github.com/coollabsio/coolify/security/advisories), [Dokploy](https://github.com/Dokploy/dokploy/security/advisories)) | 70: 21 critical, 26 high, 15 medium, 8 low | 60: 34 critical, 17 high, 8 medium, 1 low | none published, and see *What Noust does not guarantee* |
| A remote action | a shell string sent over SSH (`instant_remote_process`, 281 calls; `escapeshellarg`, 451) | not read | an argument list through one module; no shell anywhere |
| SSH host keys | not checked: `StrictHostKeyChecking=no`, `UserKnownHostsFile=/dev/null` (`app/Helpers/SshMultiplexingHelper.php:426`) | not read | pinned at enrolment; a change closes the tunnel |
| What a central or panel holds | the servers' private keys as plain files on its host (`app/Models/PrivateKey.php:205`) | not read | a key that can only open a tunnel to the console port; the central's secrets can be sealed |
| Build helper | mounts the Docker socket (`app/Jobs/ApplicationDeploymentJob.php:2752`) | not read | a transient unit as an unprivileged account, read-only system, no socket |
| Forwarding headers | trusted from anyone (`TrustProxies::$proxies = '*'`, `app/Http/Middleware/TrustProxies.php`) | not read | from a declared proxy only, nobody by default |
| Session cookie | `same_site` is `lax` (`config/session.php:199`) | not read | `SameSite=Strict`, `HttpOnly`, `Secure` over TLS |
| Security headers in the repository (CSP, frame, content type, HSTS) | none found in `app/`, `config/`, `bootstrap/`, `routes/`, `docker/` | a clickjacking advisory ([GHSA-c94j-8wgf-2q9q](https://github.com/Dokploy/dokploy/security/advisories/GHSA-c94j-8wgf-2q9q)) | on every response, printed above |
| Database password and S3 keys | in a `docker exec` command line (`app/Jobs/DatabaseBackupJob.php:687`, `:892`) | not read | environment or standard input, never argv |
| How it updates itself | downloads `upgrade.sh` and runs it with `bash` (`app/Actions/Server/UpdateCoolify.php:122-131`) | not read | one fixed command per installation method; nothing of the request in it |
| Disclosure policy | `SECURITY.md`: a contact address, no timeline | not read | 3 working days to acknowledge, 10 to confirm, 90 to fix, safe harbour |
| Signed releases | no signing, attestation or SBOM step found in `.github/` | not read | signed checksums, build provenance, SBOM and image attestations |

## What Noust does not guarantee

These are the limits, stated before you find them.

- **Noust requires root, and its API runs as root.** A remote code execution in the console is a root
  shell on that machine. The claims above are that a *compromised central* gets no shell and no more than
  a node's own ceiling, and that code from a *repository* does not run as root, not that the console
  cannot be attacked. Noust never offers a "non-root mode" that only looks like one.
- **It is not a multi-tenant isolation boundary.** An application created since 3.2 (and a
  monorepo since 3.4) runs as its own account and cannot read another's files; one from before keeps
  the shared account until `noust app identity migrate` moves it. Applications still share the
  kernel, the network and the machine, and a team is a boundary of permissions, not of execution. A
  Compose stack is kept to its own files by the compose check, but its processes are the
  containers' and run as the users their images say.
- **The absence of published advisories is not evidence of safety.** Noust has none; the products it
  is compared with have many because many people look at them. Until independent people have
  looked at Noust, "fewer advisories" would be a statement about attention, not about security.
- **Whole classes are removed by construction; the product is not immune.** Shell injection, a route
  without a permission and an unscoped query are made hard to write and caught by tests. A logic flaw in
  an authorization rule, in a validator or in a program Noust calls is still possible.
- **The audit log proves consistency, not originality, against root**: root holds the chain's key. Ship
  it off the machine (`audit.syslog`) and compare the head.
- **A redactor works by value.** It scrubs what Noust knows is a secret and nothing it does not, and it
  leaves values shorter than six characters alone.
- **A key beside the ciphertext does not protect from a compromised host.** `config.yaml` holds SMTP and
  destination secrets in clear, `0600`, and the central can be sealed with `noust central seal`; neither
  helps if the attacker is root on the same machine.
- **`security.profile: ens-medium` is a mapping, not a certification.**
- **Dated.** The counts of other products' advisories are those of 2026-10-13, read from GitHub's
  published advisories of each repository. Nothing here is a ranking: advisories count attention as
  much as flaws, and a fix in a release's notes without an advisory is invisible to a count.

## Independent review

No external penetration test has been done, so none is claimed. If you are going to look, these are
the places the guarantees depend on most, in the order that matters:

1. **The fleet tunnel** (guarantee 3): no shell, no reverse or Unix-socket forwarding, the ceiling a
   compromised central cannot exceed, and the host-key pin.
2. **Authorization of every route** (guarantee 4), including teams and the WebSockets.
3. **The build sandbox and the compose check** (guarantee 2).
4. **Sign-in:** the passkey and single-sign-on paths, sudo mode and approvals (guarantee 5).
5. **The update path and the installer** (guarantee 10).

Anything found on your own installation, reported as `SECURITY.md` describes, is welcome and
credited. A summary of any review that is done will be published here with its date and scope.

## Verifying a release

A release is built by GitHub Actions from a signed tag, and what it publishes can be checked without
trusting whoever uploaded it. `VERSION` below is the release, for example `3.4.0`.

The files of a release (wheel, source distribution, Debian packages, SBOM, installer) have one
checksum file, signed by the release workflow with a keyless Sigstore certificate that names the
workflow:

```bash
base=https://github.com/Perkybeet/noust/releases/download/vVERSION
curl -fsSLO "$base/noust-VERSION.SHA256SUMS"
curl -fsSLO "$base/noust-VERSION.SHA256SUMS.sig"
curl -fsSLO "$base/noust-VERSION.SHA256SUMS.pem"

cosign verify-blob \
  --certificate noust-VERSION.SHA256SUMS.pem \
  --signature noust-VERSION.SHA256SUMS.sig \
  --certificate-identity-regexp '^https://github.com/Perkybeet/noust/\.github/workflows/release\.yml@refs/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  noust-VERSION.SHA256SUMS

sha256sum -c --ignore-missing noust-VERSION.SHA256SUMS      # after downloading the files you want
```

Each file also has a build-provenance attestation in GitHub, and the wheel has its SBOM attested:

```bash
gh attestation verify noust-VERSION-py3-none-any.whl --repo Perkybeet/noust
gh attestation verify noust-VERSION-py3-none-any.whl --repo Perkybeet/noust \
  --predicate-type https://cyclonedx.org/bom
```

The central's container image is signed and attested by digest:

```bash
cosign verify \
  --certificate-identity-regexp '^https://github.com/Perkybeet/noust/\.github/workflows/release\.yml@refs/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/perkybeet/noust:VERSION

gh attestation verify oci://ghcr.io/perkybeet/noust:VERSION --repo Perkybeet/noust
```

The installer is verified with its own `SHA256SUMS`, signed the same way: see
[PLATFORMS.md](PLATFORMS.md#verifying-the-installer). The Debian, Ubuntu, Fedora and openSUSE
repositories are signed by their repository key, and `noust` on PyPI is published by trusted
publishing with attestations, which PyPI shows on the release's page.

A release that fixes a vulnerability is titled *(security release)* on GitHub, and an installation
that is behind one says so in `noust health`, in its CLI banner and in Settings > About.

## How this page is kept true

- The parts between `<!-- generated:NAME -->` markers are written by `scripts/security_model.py` from the
  code: the roles from `web/permissions/roles.py`, the headers from the middleware that sends them, the
  routes from the application's own OpenAPI document, the sshd block from the function that writes it.
- Every test named under a guarantee must exist; the script checks it.
- The check is a test, `tests/test_security_model.py`, so CI and the release gate run it with the rest of
  the suite: a release does not go out with a page that disagrees with the code.
- When you change a guarantee, change it in `scripts/security_model.py`, regenerate, and change the test
  that proves it in the same commit.
