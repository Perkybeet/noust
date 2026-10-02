# Configuration reference

Noust keeps its settings in one file, `/etc/noust/config.yaml` (mode `0600`, in a `0700`
directory: it holds the database, SMTP and notification credentials). Nothing needs to be in it:
every setting has a default, `noust config show` prints what is in effect, and the file only has
to hold what you changed.

```bash
noust config show                       # the settings in effect, secrets shown as ***
noust config get monitor.smtp.host      # one setting
noust config set web.port 8443          # one setting, checked before it is written
noust config set monitor.smtp.password  # a secret: asked hidden at a terminal
printf '%s' "$PASS" | noust config set monitor.smtp.password --stdin
noust config set monitor.email_recipients a@example.com,b@example.com   # a list
noust config upgrade                    # append the sections a newer Noust introduced
noust config clean                      # remove the settings no version reads any more
noust config path                       # where the file is
```

A secret typed as `VALUE` lands in the shell history and in `ps` for every local user, so secrets
go through the prompt or `--stdin`. `noust config show` and `GET /api/config` print `***` for
every secret that is set and an empty value for one that is not.

The environment overrides a few settings for one process: `NOUST_APPS_DIR` (`apps_directory`),
`NOUST_WEBSERVER` (`webserver`), `NOUST_SERVICE_USER` (`service_user`), `NOUST_SSL_EMAIL`
(`ssl.email`) and `NOUST_CENTRAL_ROLE` (`central.role`). The `WASM_*` spelling of each still works.

A value that fails its check (a port out of range, a relative path, a host that is not a name) is
refused in the same words by `noust config set`, the console and the API.

## Obsolete settings

Settings of features that no longer exist stay in a file an older version wrote. They are ignored
on load, they are not part of `noust config show` (which lists them apart, by name and never with
a value), `noust config set` refuses them, and a full `PUT /api/config` cannot bring them back.

`noust config clean` removes them from `config.yaml` as text: every comment and the rest of the
file stay as they are. It first writes a copy next to the file with the date in its name
(`config.yaml.bak-20260929-101500`, mode `0600`) - except that the credential of a removed feature
is not kept in it: the OpenAI API key the old AI monitor stored is deleted from the file and is
not in the copy either. A file it cannot edit safely as text (a multi-line flow mapping holding
one of them) is left untouched and the command says which lines to remove by hand. The package
upgrade runs `noust config upgrade` and then `noust config clean`.

| Setting | Why it is obsolete |
|---|---|
| `monitor.use_ai` | The AI analysis of processes was removed in 1.0. |
| `monitor.ai_interval` | The AI analysis of processes was removed in 1.0. |
| `monitor.openai` | Nothing sends data to OpenAI. The API key stored here is deleted by `noust config clean`. |
| `monitor.auto_terminate` | The monitor reports; it never kills a process. Read as `false` whatever the file says. |
| `monitor.terminate_malicious_only` | Same. Read as `false`. |
| `monitor.dry_run` | Same. Read as `true`. |
| `databases.backup_dir` | Backups go where `backup.directory` says. |
| `databases.default_encoding` | A database is created with the character set of its own command or request (utf8mb4 for MySQL, UTF8 for PostgreSQL). |
| `databases.auto_start` | `noust db install` always starts the engine it installs. |
| `databases.auto_enable` | `noust db install` always enables the engine it installs at boot. |
| `logging` | Noust writes its logs to journald and to files it names itself. |
| `nodejs` | Noust uses the Node.js installed on the machine; these were never consulted. |
| `python` | Noust uses the Python installed on the machine; these were never consulted. |

## Applications and web server

| Setting | Default | Meaning |
|---|---|---|
| `apps_directory` | `/var/www/apps` | Where applications are deployed, one directory per domain. Must be an absolute path. A Docker Compose stack adopted with `noust app adopt` stays where it already ran (`/opt/proggest`, say), outside this directory; deleting it keeps that directory unless `noust delete --remove-adopted-directory` names it (see [compose.md](compose.md#adopting-a-running-stack)). |
| `webserver` | `nginx` | The web server new sites are written for: `nginx` or `apache`. |
| `service_user` | `www-data` | The Unix user the units of applications without an account of their own run as, and their files belong to. From 3.2 an application Noust creates runs as its own account, `noust-app-<name>`, and one from before moves to its own with `noust app identity migrate`; Docker Compose stacks, monorepos and static sites keep this account (see [releases.md](releases.md#the-account-an-application-runs-as)). |
| `service_group` | `www-data` | Its group. |
| `deploy.layout` | `releases` | Layout of applications created from now on: `releases` builds each deploy in its own directory behind a health gate; `inplace` is the 1.x layout. An application keeps its layout until it is migrated explicitly. |
| `ssl.email` | empty | The address certbot registers certificates with. Empty registers without one. |
| `ssl.enabled` | `true` | Shown in the console's general settings. It switches nothing: every site gets a certificate unless it is created with `--no-ssl`. |
| `ssl.provider` | `certbot` | Shown in the console's general settings. certbot is the only provider. |
| `ssl.hsts` | `false` | Whether the nginx and Apache sites Noust writes send `Strict-Transport-Security: max-age=31536000` on their HTTPS side. Off until asked for: browsers remember it for a year, so it is not something to turn on for a domain that may still need plain HTTP. Sites already written change the next time they are written (a deploy, a domain change, `noust site`). |
| `backup.directory` | `/var/backups/noust` | Where backups are written. Empty means the default; a relative path is refused. Not in the defaults: it is read with its default in place. |
| `backup.max_per_app` | `10` | Backups kept per application by rotation, 1 to 100. A schedule with its own retention ignores it. |
| `backup.max_bytes` | 256 GiB | Largest total size a backup archive may extract to; one past it is refused instead of filling the disk during a restore. Not in the defaults. |
| `backup.max_entries` | `2000000` | Most files a backup archive may hold when it is extracted; one past it is refused. Not in the defaults. |
| `updates.check` | `true` | Whether Noust asks GitHub for the latest release (the CLI's background check and `GET /api/system/version`). Off means the request is never made. |

`backup.*` and the other settings listed below as "not in the defaults" are read where they are
used, with their default in the code, so a file that lacks them behaves as documented.

## Server

| Setting | Default | Meaning |
|---|---|---|
| `server.name` | empty | The name this server goes by in every notification and, on a central, in the fleet. Empty means the machine's short hostname. At most 64 characters, one line. |
| `central.role` | `server` | What this Noust is for: `server` deploys and serves applications (and may also manage other servers), `hub` only manages other servers (a central on a NAS or in a container) and refuses applications, sites and certificates. `NOUST_CENTRAL_ROLE` overrides it. |
| `central.name` | the host name | The name a central goes by to the servers it manages. Not in the defaults. |
| `metrics.retention_days` | `400` | Days the hourly tier of the charts' history is kept (35 to 3650). The finer tiers are fixed: 5 s for 2 hours, 1 minute for 26 hours, 10 minutes for 8 days. The per-minute process samples behind the charts are kept as long as the minute tier (26 hours); the unit failures and recoveries the timeline reads, as long as the hourly tier (see [MONITOR.md](MONITOR.md#process-samples)). |

## Console

| Setting | Default | Meaning |
|---|---|---|
| `web.enabled` | `false` | Set to `true` when the console runs on this server: the CLI then prints links into it (`--open`). |
| `web.host` | `127.0.0.1` | The address the CLI builds console links from and the firewall checks read. What the console actually binds is decided by `noust web start --host`, never by this file. |
| `web.port` | `8080` | The same for the port. Must be 1 to 65535. |
| `web.public_url` | empty | Where the console is reachable from a browser (an absolute `https://` URL, no trailing slash). Deploy notifications link to it, and its host is always allowed by `web.allowed_hosts`. |
| `web.hooks_url` | empty | The public base of `/hooks/` that code hosts deliver to. Written by `noust web expose-hooks`; unset while webhooks can only reach this server through an address of your own. |
| `web.allowed_hosts` | empty | The Host header values the console answers to, checked on every request: names, or `*.example.com` for every subdomain. A request for any other host gets a 400 saying so. Loopback and the hosts of `web.public_url` and `web.hooks_url` are always allowed. Empty allows any host, as every earlier version did. Names only: no scheme, port or path. |
| `web.ip_whitelist` | empty | Client addresses or networks allowed to reach the console; empty allows any. `noust web start --allow-ip` overrides it, and a central's `NOUST_ALLOW_IP` too. |
| `web.rate_limit_enabled` | `true` | Whether requests are rate limited. |
| `web.rate_limit_requests` | `120` | Requests without a credential allowed per window and client address. |
| `web.rate_limit_authenticated_requests` | `1200` | Requests with a valid credential allowed per window and credential. |
| `web.rate_limit_window` | `60` | Length of that window, in seconds. |
| `web.max_failed_attempts` | `5` | Failed sign-ins before a client address is locked out. |
| `web.lockout_duration` | `900` | How long that lockout lasts, in seconds. |
| `web.token_expiration_hours` | `12` | Console session lifetime in hours, renewed by activity. |
| `web.session_timeout` | `3600` | Shown in the console's general settings, 300 to 86400. The session lifetime that is enforced is `web.token_expiration_hours` and `auth.session.*`. Not in the defaults. |

## Accounts and audit

| Setting | Default | Meaning |
|---|---|---|
| `security.profile` | `standard` | `standard`, or `ens-medium` for Spain's ENS category MEDIUM: it caps the `auth.*` settings at the profile's values and keeps the master token to account recovery. |
| `auth.session.idle_minutes` | `30` | A session unused for this long is over. |
| `auth.session.absolute_hours` | `12` | A session older than this is over, however active. |
| `auth.lockout.threshold` | `5` | Consecutive failed sign-ins that lock an account. |
| `auth.lockout.minutes` | `15` | How long the account stays locked. |
| `auth.password.min_length` | `12` | The shortest password accepted. |
| `auth.tokens.max_days` | `90` | Longest life of an API token under the ENS profile, and its default. |
| `auth.notice.text` | empty | Rights and obligations shown after sign-in and accepted on record. Editing it asks everybody to accept it again. |
| `auth.login_label` | empty | Shown on the sign-in page instead of the host name, which is not shown before sign-in. |
| `audit.retention_days` | `365` | Days audit events are kept; 90 is the least accepted. Nothing is deleted before every destination received it. |
| `audit.max_total_mb` | `2048` | Disk the audit trail may take. |
| `audit.rotate_mb` | `64` | Size at which its file is rotated. |
| `audit.host_activity` | `mutations` | What the host action ledger records: `off`, `mutations` or `all`. |
| `audit.flood_window_seconds` | `60` | Window of the anti-flood rule that collapses a burst of identical events. |
| `audit.flood_burst` | `10` | Identical events allowed in that window before they are collapsed. |
| `audit.journald` | `auto` | Send events to journald: `auto` (when its socket exists), `on` or `off`. |
| `audit.stdout` | `auto` | Write events to standard output: `auto` (inside a central's container), `on` or `off`. |
| `audit.syslog` | none | RFC 5424 receivers, each `{transport: unix\|udp\|tcp\|tls, address, facility, ca, client_cert, client_key, pin_sha256, server_name, backfill}`. |
| `audit.enterprise_id` | `32473` | IANA Private Enterprise Number of the structured data ID `noust@<id>`. 32473 is the documentation number: set your organisation's own. |
| `audit.checkpoint_minutes` | `5` | How often the chain writes a signed checkpoint. |
| `audit.sink_lag_minutes` | `15` | How far a destination may fall behind before it is reported. |
| `retention.jobs_days` | `90` | Days finished background jobs and their logs are kept. |
| `retention.deployments_days` | `365` | Days deployment records are kept. |
| `retention.sessions_days` | `30` | Days ended sessions are kept. |

## Monitor and notifications

| Setting | Default | Meaning |
|---|---|---|
| `monitor.enabled` | `false` | Shown by `noust monitor config`. Whether the monitor runs is decided by `noust-monitor.service` (`noust monitor enable`/`disable`). |
| `monitor.scan_interval` | `60` | Seconds between scans, and so the longest a failed unit can go unannounced. |
| `monitor.cpu_threshold` | `80.0` | CPU percentage above which a process is noted. |
| `monitor.memory_threshold` | `80.0` | Memory percentage above which a process is noted. |
| `monitor.log_file` | `/var/log/noust/monitor.log` | Where the monitor writes its own log. |
| `monitor.smtp.host` | empty | The SMTP server email notifications go through. |
| `monitor.smtp.port` | `465` | Its port, 1 to 65535. |
| `monitor.smtp.username` | empty | The account. |
| `monitor.smtp.password` | empty | Its password (a secret). |
| `monitor.smtp.use_ssl` | `true` | Implicit TLS, usually port 465. Not together with `use_tls`. |
| `monitor.smtp.use_tls` | `false` | STARTTLS, usually port 587. Not together with `use_ssl`. |
| `monitor.smtp.from_address` | empty | The sender; empty uses the account name. |
| `monitor.smtp.timeout` | `30` | Seconds before a connection to the SMTP server is given up. Not in the defaults. |
| `monitor.email_recipients` | empty list | Who receives email notifications: a list of addresses, or `a@x.com,b@y.com` from the command line. |
| `monitor.notify` | `false` | Also mail the monitor's raw observations. Not in the defaults. |
| `monitor.watch_units` | none | Extra systemd units the monitor watches besides the applications'. Not in the defaults. |
| `monitor.retention_days` | `30` | Days the monitor keeps its observations. Not in the defaults. |
| `monitor.max_observations` | `5000` | Row cap of the observations table. Not in the defaults. |
| `notifications.enabled` | `false` | Master switch of multi-channel notifications. |
| `notifications.language` | `en` | `en` or `es`: the language of Noust's own words in a notification. The evidence they carry (a probe, rclone's or certbot's output) is never translated. |
| `notifications.allow_private_hosts` | none | Hosts a webhook channel may deliver to although they resolve to a private address. Not in the defaults. |
| `notifications.events.deploy_started` | `false` | Notify when a deployment starts. Off: it fires once per attempt with no outcome, and would double the volume of every deploy that also succeeds or fails. |
| `notifications.events.deploy_success` | `true` | Notify when a deployment succeeds. |
| `notifications.events.deploy_failed` | `true` | Notify when a deployment fails. Also carries the warning that Noust's own trial build of an application in the sandbox failed before one of its updates, which then built as root as before (see [releases.md](releases.md#builds-in-the-sandbox)). |
| `notifications.events.deploy_rolled_back` | `true` | Notify when a failed activation put the previous release back. |
| `notifications.events.deploy_hook_failed` | `true` | Notify when a deployment went live with warnings: a `post_deploy` hook failed once the new version was serving. |
| `notifications.events.restore_success` | `true` | Notify when a restore succeeds. |
| `notifications.events.restore_failed` | `true` | Notify when a restore fails. |
| `notifications.events.cert_expiring` | `true` | Notify when a certificate is about to expire. |
| `notifications.events.unit_failed` | `true` | Notify when a watched unit fails. |
| `notifications.events.app_unreachable` | `true` | Notify when an application whose unit is running stops answering its health check: three failed probes over at least a minute, once per outage; not asked while the application is being deployed or runs a job, nor when its unit was stopped on purpose or failed (that is `unit_failed`). A stack with no published port is judged by its containers instead. |
| `notifications.events.app_recovered` | `true` | Notify when an application that was reported unreachable answers again. |
| `notifications.events.disk_threshold` | `true` | Notify when a disk crosses its threshold. |
| `notifications.events.backup_failed` | `true` | Notify when a backup fails. |
| `notifications.events.backup_success` | `false` | Notify on every successful scheduled backup: a heartbeat, not a problem. |
| `notifications.events.node_unreachable` | `true` | On a central: a server it manages stopped answering. |
| `notifications.events.node_recovered` | `true` | On a central: a server answers again. |
| `notifications.events.node_host_key_changed` | `true` | On a central: a server's SSH host key changed and the tunnel was closed. |
| `notifications.events.server_rebooted` | `true` | The server restarted when nobody asked for it from Noust (the provider, a crash, a `reboot` typed by hand). |
| `notifications.events.server_back` | `true` | A reboot or shutdown asked from Noust is over and the console runs again. |
| `notifications.events.approval_requested` | `true` | A change that needs a second person's approval is waiting for a decision. |
| `notifications.events.approval_decided` | `true` | A request for approval was approved or rejected. |
| `notifications.channels.webhook.webhook_url` | empty | The generic endpoint notifications are posted to (a secret: it is a capability URL). |
| `notifications.channels.webhook.secret` | empty | Signs every delivery with HMAC-SHA256 in `X-Noust-Signature` when set (a secret). |
| `notifications.channels.slack.webhook_url` | empty | A Slack incoming webhook (a secret). |
| `notifications.channels.discord.webhook_url` | empty | A Discord webhook (a secret). |
| `notifications.channels.telegram.bot_token` | empty | The bot's token (a secret). |
| `notifications.channels.telegram.chat_id` | empty | The chat: an integer id (a group's is negative) or `@channelname`. |
| `notifications.channels.email.enabled` | `false` | Send notifications by email, through the `monitor.smtp.*` account to `monitor.email_recipients`. |

## Databases

| Setting | Default | Meaning |
|---|---|---|
| `databases.credentials.mysql.user` | `root` | The administrative account Noust connects to MySQL/MariaDB with. |
| `databases.credentials.mysql.password` | empty | Its password (a secret). Empty uses the local socket as root. |
| `databases.credentials.postgresql.user` | `postgres` | The administrative role for PostgreSQL. |
| `databases.credentials.postgresql.password` | empty | Its password (a secret). |
| `databases.credentials.redis.password` | empty | The password Redis requires (a secret). |
| `databases.credentials.mongodb.user` | empty | The administrative user for MongoDB. |
| `databases.credentials.mongodb.password` | empty | Its password (a secret). |
| `databases.safety_copies_kept` | `5` | Safety copies a restore takes of a database before loading over it, kept per database; older ones are deleted after each restore. The newest is never deleted, whatever the value. |

## Adding a setting

A setting exists when code reads it. A new one goes in `DEFAULT_CONFIG` in
`src/noust/core/config.py` with a comment saying what reads it, in this page in the same change,
and, when a value can be wrong, in `_KEY_VALIDATORS` so every front end refuses it in the same
words. `tests/test_config_clean.py` fails when a default is not documented here. When a setting
stops being read, it moves from `DEFAULT_CONFIG` to `OBSOLETE_KEYS` with the reason.
