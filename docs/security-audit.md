# The audit log, and an incident

Part of [Security](security.md).

## Audit log

Every event goes through one function and one closed catalogue (`noust audit events` lists it),
into `/etc/noust/web-audit.log`: one JSON line per event with its time, a sequence number and
id, the event and its category and severity, the actor (kind, name, role, and how it came:
session, token, fleet, CLI with the operating-system user), the client address, the target,
the outcome, details and a correlation id shared by everything one request, command or job did.
A 3.0 line is still a valid line, and the 3.0 fields are still there. Credentials are never
written to it.

Recorded:

- every change made through the API and every command that changes something, with its
  `--reason` when given (required under the ENS profile);
- sign-ins, sign-outs, failures, lockouts, refusals for a permission, CSRF or sudo mode;
- accounts, roles, invitations, tokens, sessions, second factors and passkeys;
- sensitive reads: revealing an `.env` or a secret, exporting with secrets, reading the audit
  log itself;
- approvals, from request to use; accepted risks; the sandbox turned off for an application;
- with `audit.host_activity` (`mutations` by default), every process Noust runs and every file
  it writes that changes the system, linked to the event that caused it; read-only probes are
  not recorded.

**Exporting.** `noust audit export` and the console's Audit page (**Export**) write the same file with
the same code, for a period and with the filters the page has (event, outcome, actor, category,
target, request and the search box). NDJSON keeps every field and the chain, for an auditor or a
SIEM; CSV is for a spreadsheet, with what each event recorded in its last column, credentials of a
recognisable shape masked and no cell that a spreadsheet would run as a formula. The console's export
needs `audit.read`, and every export is an `audit.export` event with who asked, the period, the
format and the filter.

**Integrity.** Each line carries the MAC of the previous one (HMAC-SHA256 under the chain's own
key), and a signed checkpoint is written every `audit.checkpoint_minutes`. `noust audit verify`
reports the first broken link. Root holds the key and could rewrite a consistent chain, so a
pass proves consistency, not originality: compare the head with a copy shipped off the machine.

**Shipping.** To journald wherever its socket exists, to RFC 5424 syslog receivers
(`audit.syslog`: Unix socket, UDP, TCP with octet counting, or TLS with an optional client
certificate and a pinned key), and to standard output in a central's container. Each destination
has an on-disk queue and is retried; one that falls more than `audit.sink_lag_minutes` behind is
reported. `noust audit status` says whether the trail works and where it goes; a failing trail
shows in the console and in `noust health`.

**When the trail cannot be written** (the disk is full, the directory went read-only, the file
cannot be opened) an action is never lost for it by default: `audit.on_failure: warn` makes the
change and is loud, since the failure is kept as state, shown in the console and in `noust
health`, and followed by an `audit.recovered` event when a write works again. Set
`audit.on_failure: refuse` and, under `security.profile: ens-medium` always, no change goes
unrecorded instead: while the trail cannot take an event, every change through the API (and its
webhooks, `POST /hooks/...`) answers `503 audit_unavailable` with the reason and the way out, and
every command that changes something exits 1 before doing anything. Reading, signing in and out,
`noust audit`, `health`, `ens`, `config`, `setup`, `incident` and `web`, which are how the trail
is put right, stay open, and so do the units and timers (a scheduled backup that did not run is
worse than one whose line was not written; their failure keeps shouting). The server looks at
the log's directory *before* the change (it exists, can be written to, has room) so the first
change after the disk filled is the first one refused, not the last one made blind; a look is
never a write. A refusal is itself an `audit.refused` event, and the write that records it
working is also what shows the trail has recovered.

**Retention and flooding.** The file is closed every day and when it reaches
`audit.rotate_mb`. Closed files are deleted once older than `audit.retention_days` (365, at
least 90) **and** received by every destination, and the deletion is itself an event that
anchors the chain's new start; `audit.max_total_mb` caps the disk it takes. A burst of identical
events is collapsed into one with a count. Jobs, deployment records and ended sessions have their
own retention (`retention.*`).

Read it from Settings > Audit log, `noust audit list|show|export`, or `GET /api/audit` (all need
`audit.read`: a `security` or `auditor` account). `noust audit review` records a periodic review.

## Incidents

`noust incident freeze --reason "<incident reference>"` takes an owner-only evidence package
(the audit log and its verification, journal excerpts, the configuration without secrets, a store
snapshot with the secret values taken out of it (`store/scrubbed.json` says how many rows), running
units, listening sockets, the console's sessions and tokens without hashes),
writes a `MANIFEST.sha256` of it (`sha256sum -c` checks it) and records that manifest's hash in
the audit log, which is shipped off the machine. It then locks the console down: every new
session of an account is refused where sessions are made, and only the master token - the
break-glass way in - still signs in. `--revoke-sessions` also ends the sessions open now;
`noust incident unfreeze --reason ...` lifts the lockdown.
