# The MCP endpoint

Noust 4.0 speaks the **Model Context Protocol** (MCP) over Streamable HTTP, so an MCP client - an editor, a command-line tool, a script - is given tools to **inspect, deploy
and control** applications with the address of the console and an API token. It is a client of the API like any other: a tool call is a
request to the console's own routes, made as the token, through the same permissions, team scope,
approvals and audit trail. There is no second implementation of anything.

```
noust mcp enable                                         # off until a security officer turns it on
noust mcp token ci-bot --preset deploy --owner maria --team acme
```

Then give the client the address `https://noust.acme.test/api/mcp` and the header
`Authorization: Bearer <the token>`. Most clients take a block like this one:

```json
{
  "mcpServers": {
    "noust": {
      "type": "http",
      "url": "https://noust.acme.test/api/mcp",
      "headers": { "Authorization": "Bearer <the token>" }
    }
  }
}
```

## What a client is given

Twenty-seven tools, in three groups the operator switches on and off (`mcp.groups`,
`noust mcp groups`, or Settings > MCP):

| Group | Tools |
|---|---|
| `inspect` | `list_applications`, `get_application`, `application_logs`, `diagnose_application`, `list_environment_variables` (names only), `list_application_domains`, `list_releases`, `list_deployments`, `get_deployment`, `deployment_log`, `list_jobs`, `get_job`, `job_log`, `list_backups`, `list_certificates`, `list_teams`, `list_projects`, `server_health` |
| `deploy` | `deploy_application`, `rollback_application`*, `activate_release`*, `cancel_job`, `create_backup` |
| `control` | `restart_application`, `start_application`, `stop_application`*, `renew_certificate` |

\* needs `confirm: true` (see [Guardrails](#guardrails)). `noust mcp tools` lists every tool with the
permission its route needs.

**Nothing else exists.** No tool creates or deletes an application, edits configuration, runs a
command, opens a terminal, reaches a database, reads a secret or touches accounts. A test holds the
list to that: no tool's route may need `secrets.reveal`, `root_equivalent`, `apps.manage`,
`apps.terminal`, `server.*`, `settings.manage`, `security.manage`, `accounts.*`, `audit.*`,
`databases.write`, `databases.manage`, `backups.manage`, `teams.manage` or `fleet.manage`.

A tool takes what its route takes: its parameters are read from the route's OpenAPI schema, so they
cannot drift from it. `tools/list` offers only the tools the token's permissions allow (a viewer's
token is not offered `deploy_application`), and a tool that is not offered cannot be called by name.

**Prompts** hand the model what Noust knows, fenced as data: `troubleshoot_application` (the status
and the diagnosis: unit, port, probes, journal, web server, certificate, memory, disk, most likely
cause first) and `explain_failed_deploy` (the deployment, its error and the end of its build log).
**Resources** address the same material: `noust://apps`, `noust://apps/{domain}`,
`noust://apps/{domain}/diagnosis` and `noust://guardrails`.

## Setting it up

1. **Turn it on.** `noust mcp enable`, or Settings > MCP in the console (a security officer, in sudo
   mode). Until then `POST /api/mcp` answers `404 mcp_disabled`.
2. **Make the client a token.** `noust mcp token NAME --preset inspect|deploy|control --owner USER`.
   It holds only the permissions that kind of client's tools need, at most what the owner holds, has no
   sudo mode, and expires (30 days by default). Add `--team`, `--project` or `--env` to **bind** it:
   a token bound to a client's project sees that project and nothing else, even if its owner is an
   administrator ([teams.md](teams.md)). The token is printed once.
3. **Point the client at it.** The address is `https://<the console>/api/mcp`; the credential is
   `Authorization: Bearer <token>`. Protocol revisions `2025-06-18`, `2025-03-26` and `2024-11-05`
   are spoken. The server keeps no session and opens no stream (`GET` answers 405).

A browser session or the master token is **not** a client's credential and is refused
(`403 mcp_token_required`): cookies would let a web page act as the signed-in person, and the master
token is break-glass. A request that carries an `Origin` other than the host it was sent to is refused.

## Guardrails

These hold for every tool, whatever the token may do over HTTP.

* **A client never receives a secret.** The names of a variable are returned (`list_environment_variables`),
  never a value. Every answer is walked and the value of anything whose name says it is secret is
  replaced; every string is scrubbed of the application's own secret values, of passwords inside URLs
  and of `KEY=value` lines that look secret, of bearer tokens and of private keys. No tool reveals a
  secret in the first place. Scrubbing recognises what Noust knows is secret, not everything that might
  be: that is why the tools offer no way to read one.
* **Stopping or replacing what serves needs `confirm: true`.** `stop_application`,
  `rollback_application` and `activate_release` do nothing without it and say what they would have done,
  so the model has to say it twice. A rollback cannot pass a change of database schema whatever the model
  sends: that flag is not an argument.
* **What comes back is data.** Logs and diagnoses are written by the applications they describe; the tools'
  descriptions and the prompts tell the model so, and the prompts fence the text.
* **An answer is bounded** (60,000 characters); a log keeps its end.
* **It is accountable.** Every call is on the audit trail (`mcp.call`) as the token that made it, with
  `via: mcp`, the tool, the application it named and how it ended (`ok`, `denied`, `failure`); what the
  API then does is audited as ever. `mcp.settings` records turning it on and off.
* **It is the token's reach, no more.** The API refuses what the token may not do, in its own words,
  which the model reads (`The server refused (403, permission_denied): ...`). Another team's application
  reads as absent (404). Sudo mode is not available to a client: a route that demands it refuses.

Turning it off (`noust mcp disable`) stops it at once; the tokens are untouched and keep working over
HTTP as before.

## Reading these docs from a client

[`llms.txt`](llms.txt) is an index of this documentation in the [llmstxt.org](https://llmstxt.org)
format, generated from `docs/` (`python scripts/generate_llms.py`; CI fails when it is stale).
`python scripts/generate_llms.py --full` writes `llms-full.txt`, the documentation in one file, which a
release publishes.
