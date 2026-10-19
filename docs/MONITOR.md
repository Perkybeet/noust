# Resource monitor

`noust monitor` watches the machine and writes down what stands out: resource use, processes
worth a look, units that stopped, certificates about to expire and full disks. It keeps what
it noticed in a local database and can send you a report by email, or through any notification
channel you configure.

It reports and does nothing else:

- it never signals, terminates or restarts a process;
- it never deletes or modifies a file, except the systemd unit it installs;
- it never decides anything from a process's command line (the process samples keep a redacted,
  truncated copy of the busiest processes' command lines so an operator can read what ran; see
  [Process samples](#process-samples));
- what it sends leaves the machine only through channels you configured: raw observations by
  email when `monitor.notify` is on, and the disk, certificate and unit events through
  whichever notification channels you set up - webhook, Slack, Discord, Telegram, email,
  Pushover, ntfy, Gotify, Microsoft Teams, Mattermost, Matrix, Lark or PagerDuty (see
  [Notifications](#notifications)). Nothing is sent
  anywhere else.

Before 1.0 the monitor sent process data to OpenAI and could kill processes and delete their
files. All of that is gone. The flags `--force-ai` and `--all` on `noust monitor scan` are
still accepted, ignored with a warning; the settings `monitor.auto_terminate`,
`monitor.terminate_malicious_only` and `monitor.dry_run` are pinned to their safe values
whatever the file says; `monitor.use_ai`, `monitor.ai_interval` and `monitor.openai.*` have no
effect and are ignored on load. `noust config show` lists them apart as obsolete, and `noust
config clean` removes them from `config.yaml` (comments kept, dated copy first), deleting the
OpenAI API key an old file may still hold. See [the configuration reference](CONFIG.md).

## Commands

```bash
noust monitor scan          # look at the machine once and print what stands out
noust monitor run           # scan on a loop in this terminal, until Ctrl+C
noust monitor install       # write the systemd unit, without starting it
noust monitor enable        # start it now and at every boot, installing it if needed
noust monitor autoenable    # what the package runs: enable it unless it was turned off
noust monitor disable       # stop it and keep it from starting at boot
noust monitor uninstall     # remove the unit; recorded observations stay
noust monitor status        # whether it runs, and what it watches
noust monitor config        # the settings in effect and where the database lives
noust monitor test-email    # send one email to the recipients, to prove the settings
```

The unit is `noust-monitor.service`. It runs `noust monitor run` as root, restarts on failure,
and is sandboxed (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`,
`PrivateDevices`). The console has the same controls under Server > System, and the Overview
says when the history is not being recorded, why, and what to run.

The package installs and enables the monitor on install, and on an upgrade that finds none,
because the charts' history is recorded here (see [Metrics history](#metrics-history)). It
never overrides a decision: a monitor that is installed stays as it is, and one you disabled or
removed stays off until you run `noust monitor enable`. Where there is no systemd (a container)
nothing is installed, and the console records the history while it runs.

"Turned off" is remembered in `/var/lib/noust/monitor-declined`, which `noust monitor disable`
and `noust monitor uninstall` write from 3.1 on. A monitor removed with 3.0's `noust monitor
uninstall` left no such file, so on the upgrade to 3.1 the package also leaves the monitor off
when the journal still has lines from `noust-monitor`, or the observation
database holds anything a monitor recorded, and writes the file then. Where neither is left
(a rotated journal), keep the monitor off with:

```bash
sudo touch /var/lib/noust/monitor-declined    # before upgrading; noust monitor enable removes it
```

## What it watches

Every scan interval (60 seconds by default, never less than 10):

| Watch | Recorded when | Severity |
|---|---|---|
| Process names | The executable's name starts with a known miner or malware name: `xmrig`, `minerd`, `cpuminer`, `cgminer`, `bfgminer`, `ethminer`, `ccminer`, `kdevtmpfsi`, `kinsing`, `kerberods`, `watchdogs`. Common daemons are never flagged. | `warning` |
| Process resource use | A process uses more CPU or memory than `monitor.cpu_threshold` or `monitor.memory_threshold` percent (80 by default). | `notice` |
| Units | A unit Noust manages, or one listed in `monitor.watch_units`, fails: it is `failed`, it crash-loops (its automatic restarts grow between two scans), or it stopped after a failed run. A unit stopped on purpose does not count. All units are read with one `systemctl show` per scan; one message per outage. Each failure and each recovery is also kept in the metrics database (for `metrics.retention_days`), where the [timeline](#what-happened-in-a-stretch) reads it. | notification `unit_failed` |
| Applications | An application whose unit runs stops answering what the deploy's health gate asks (its health path and expected statuses, on the port that serves now): three failed probes in a row, the first and the last at least a minute apart, so a restart or a deploy is never announced. One message per outage, and one when the first probe passes again. Not asked while a deploy, update, rollback or job of that application runs, nor when its unit was stopped on purpose or failed (that is `unit_failed`). A Compose stack that publishes no port is judged by its containers instead: one that exited with an error or keeps restarting is an outage. A static site and a PHP-FPM pool have no process to ask and are never reported. Up to eight applications are asked at once, so one that hangs does not hold up the rest. The run of failures is kept in `app-reachability.json` next to the observation database (`/var/lib/noust`), so a restart of the monitor neither forgets it nor repeats the message; a gap of more than ten minutes between two probes (the monitor was stopped) starts the count again, but an outage already announced is kept so its end is still told. | notifications `app_unreachable`, `app_recovered` |
| Certificates | A certificate has fewer than `monitor.cert_warning_days` (14) days left; at most one message per certificate per day. | notification `cert_expiring` |
| Disks | A filesystem crosses `monitor.disk_threshold` (90) percent used; one message per crossing, looked at every `monitor.disk_check_seconds` (0: every scan) and repeated every `monitor.disk_renotify_hours` while it stays over (0: not repeated). | notification `disk_threshold` |
| Backups | An application with a backup schedule, a database policy, an application's data policy or Noust's own state backup has no successful backup newer than `monitor.backup_missing_days` (2; 0 turns it off), stretched to a week or a month for those schedules. Looked at every `monitor.backup_check_hours` (6), repeated every `monitor.backup_missing_renotify_hours` (24) while it stays missing. | notification `backup_missing` |
| Backup destinations | A destination some policy sends to does not answer the test `noust backup destination test` makes. Asked every `monitor.backup_check_hours` (6); said once per outage with rclone's own words, repeated every `monitor.backup_missing_renotify_hours`, and once more when it answers. | notification `destination_unreachable` |
| Cron jobs | A job's last run ended with an error (with the job's own last lines of output), or ended well when the heartbeat is on. Each run is announced once; the first look at a job only remembers it. A job whose timer is enabled but not running (`inactive` or `failed`) will not start: that is told once per stretch, and once more when the timer runs again; a job you disabled is not a fault. | notifications `cron_failed`, `cron_success`, `cron_missed` |
| System updates | The package manager has updates waiting, every `monitor.os_patches_check_hours` (24; 0 turns it off). Security ones are named and repeated weekly; ordinary ones are announced once per new set and at most weekly; a check that cannot be made is announced once per stretch. Nothing is installed. | notification `os_patches` |
| API tokens | A token expires within `monitor.token_warning_days` (7; 0 turns it off), expires today, or expired in the last week; once per stage and token. | notification `token_expiring` |
| Probes | An address the monitor asks from outside the application stops answering what the probe asks of it (`fail_after` bad answers in a row), and answers again. See [probes.md](probes.md). | notifications `probe_failed`, `probe_recovered` |
| Alert rules | Every rule of [Settings > Alerts](notifications.md#alert-rules) is judged against the recorded history; a subject that stayed past its limit for the whole duration fires, and one that is back inside clears. | notifications `alert_firing`, `alert_resolved` |

Observations are kept in `/var/lib/noust/observations.db` (`~/.local/share/noust/` when run
without root). The same process and signal within an hour is recorded once; observations
older than `monitor.retention_days` (30) are purged hourly, and at most
`monitor.max_observations` (5000) are kept. Acknowledging one, from the console or
`POST /api/monitor/observations/{id}/acknowledge`, marks it seen and does nothing to the
process.

Every one of these checks, the alert rules and the retries of failed deliveries only happen
while the monitor runs.

## Metrics history

The monitor also records the history behind the console's charts: CPU, memory, swap, disk,
network and load of the machine, and CPU, memory, disk reads and writes and network traffic of
every application, every 5 seconds.
Until 3.1 the console recorded it, so it existed only while a console was running.

| Tier | Step | Kept for | Answers |
|---|---|---|---|
| raw | 5 s | 2 hours | the last hour |
| 1m | 1 minute | 26 hours | the last day |
| 10m | 10 minutes | 8 days | the last week |
| 1h | 1 hour | `metrics.retention_days` (400) | a month and beyond |

Every tier keeps the mean **and the maximum** of each bucket, so a five-minute spike is still
there in the month view. The history lives in `/var/lib/noust/metrics.db`. The whole of it
takes about 60 MB for twenty applications with the default retention (measured with 49
series), most of it the hourly tier: 24 rows per series per day, so `metrics.retention_days`
scales it directly.

Only one collector writes at a time: the monitor takes a lease in the database, and the console
samples only while no monitor does (a container, where there is no systemd). The console reads
the history with `GET /api/metrics/query`, several series per request, at the tier the window
needs. `noust monitor status` says whether the history is being
recorded and, when it is not, why and what to run.

What is measured per application depends on how it runs:

| Application | CPU and memory | When there is nothing |
|---|---|---|
| Node, Python, Go... (in place, releases, legacy `wasm-*` units) | its unit's cgroup, located by systemd (`ControlGroup`) | stopped, unit missing, accounting off |
| Zero-downtime | both instances, summed | as above |
| Monorepo | every workspace's unit, summed | as above |
| Docker Compose | the cgroup of each container of the project | no docker, no containers |
| PHP-FPM | the worker processes of its pool | the FPM service is stopped |
| Static site | none: there is no process | its requests and 5xx per minute come from the web server's access log |

An application behind the proxy has its requests and 5xx per minute counted from the same access log as well, whatever it runs as. See [Traffic of an application](#traffic-of-an-application).

A Docker Compose stack also has series for **each of its containers**
(`app.<domain>.ctr.<container>.<what>`: CPU, memory, disk reads and writes, network received and
sent), beside the stack's total, so a chart can say which service is the one reading the disk. The
Metrics tab of the stack charts them by container; a container that lives for one command
(`docker compose run`) is in the total and gets no series of its own.

Memory is the working set (`memory.current` less reclaimable page cache), CPU is a percentage
of one CPU. Disk is the bytes per second the unit's cgroup read and wrote (`IOAccounting`) and
network the bytes per second its processes received and sent (`IPAccounting`), both of which
the unit Noust writes switches on; a unit written before them has no counters until its next
update or restart, and a Compose stack's network is read from its containers' namespaces. To tell the workers of one PHP-FPM pool from another's, the monitor reads the title
FPM gives each worker (`php-fpm: pool <name>`): the only process title it reads, and nothing
is decided from it.

## Traffic of an application

The proxy is where an application's requests, its errors and its visitors are seen, and its access
log (`<domain>.access.log`, which every site Noust writes has) says all of it. The monitor reads
what each log gained since the last look (a few hundred kilobytes at most per tick, never twice,
rotation recognised) and keeps it by the hour in `metrics.db`:

- requests, how they ended (2xx, 3xx, 4xx, 5xx) and the bytes of the bodies served, kept as long as
  the hourly metrics (`metrics.retention_days`);
- the **pages** asked for most (`GET /orders/:n`), the **networks** they came from (`203.0.113.0/24`),
  the **status codes** and the **referrers** (a host), kept 90 days. Each hour of each application keeps
  at most 50 keys of each, so a scan of a thousand paths cannot make the table grow.

```bash
noust app traffic shop.example.com                    # the last 24 hours
noust app traffic shop.example.com --window 30d --top 20 --json
```

`GET /api/apps/{domain}/traffic?window=24h|7d|30d|90d&top=10` answers the same: by the hour up to two
days, by the day beyond, every step present (a quiet hour is a point of zeros), and the Metrics tab of
the application charts it. What is kept is chosen to be safe to keep:

- **No query string, ever.** It is where tokens, e-mail addresses and session ids live. A page is its
  method and path, and a segment that is a number, a UUID or a long hexadecimal string becomes `:n`,
  `:uuid` or `:hash`, so `GET /orders/48211` and `GET /orders/48212` are one row.
- **No address of a person.** A client is its network: the first 24 bits of an IPv4 address, the first
  48 of an IPv6 one. Whoever needs the exact address has the access log, which the web server rotates.
- **Behind a CDN** the log shows the CDN's addresses, not the visitors': tell the site where the real
  address is (`noust app policy set DOMAIN --real-ip cloudflare`, see [network.md](network.md)) and the
  networks become the visitors'.

There is no GeoIP: it needs a database whose licence is not Noust's to ship. The networks are the key
to put one on yourself.

## Comparing servers

A central keeps no series of its own. `noust fleet metrics --metric cpu --window 24h` (and the Fleet page's
metrics comparison, `GET /api/fleet/metrics`) asks every server for the same metric over the same window, from
each one's own recorded history, and puts the answers side by side: the points, the latest value, the average and
the peak. `--metric` is one of `cpu`, `memory`, `swap`, `disk`, `load`, `net_rx` and `net_tx` (memory, swap and
disk are the share of what each server has, so a small server and a large one compare); the windows are `1h`, `6h`,
`24h`, `7d` and `30d`. A server that is down is a gap in the comparison, said so in its own words, never an error;
one too old to offer the history is `unsupported`. Nothing is stored on the central.

## Exporting the metrics

The history answers the console. A Prometheus, a Grafana Alloy, a Netdata or an OpenTelemetry
Collector asks "what is it now?" every few seconds, in a text format they all read, and
`GET /api/metrics/prometheus` answers that: the newest value of every metric of the server, of each
application and each container of a stack, and of each database, as gauges.

```bash
curl -H "Authorization: Bearer $TOKEN" https://noust.example.com/api/metrics/prometheus
noust monitor export                       # the same text, from this server's own history
noust monitor export --format openmetrics
```

```yaml
# prometheus.yml
scrape_configs:
  - job_name: noust
    scheme: https
    metrics_path: /api/metrics/prometheus
    authorization:
      credentials_file: /etc/prometheus/noust.token
    static_configs:
      - targets: ["noust.example.com"]
```

The credential is an API token that cannot change anything (`noust token create prometheus --scope
read`, or narrowed to the one permission the route asks for, `--permission server.read`): there is no
separate export secret. Send `Accept: application/openmetrics-text` (a
Prometheus configured for it does) and the answer is OpenMetrics. An OpenTelemetry Collector reads the
same endpoint with its `prometheus` receiver, which is how OTLP consumers are served.

| Metric | Labels | Unit |
|---|---|---|
| `noust_cpu_percent`, `noust_load1` | | percent of all cores, load average |
| `noust_memory_used_bytes`, `noust_memory_total_bytes`, `noust_swap_*`, `noust_disk_*` | | bytes |
| `noust_network_receive_bytes_per_second`, `noust_network_transmit_bytes_per_second` | | bytes per second |
| `noust_app_cpu_percent`, `noust_app_memory_bytes` | `app` | percent of one core, bytes |
| `noust_app_disk_read_bytes_per_second`, `noust_app_disk_write_bytes_per_second` | `app` | bytes per second |
| `noust_app_network_receive_bytes_per_second`, `noust_app_network_transmit_bytes_per_second` | `app` | bytes per second |
| `noust_app_http_requests_per_minute`, `noust_app_http_errors_5xx_per_minute` | `app` | per minute, from the access log |
| `noust_container_*` (the same six as an application's) | `app`, `container` | |
| `noust_database_size_bytes`, `_connections`, `_cache_hit_percent`, `_transactions_per_second`, `_queries_per_second`, `_memory_bytes`, `_operations_per_second` | `engine`, `database` | |
| `noust_info` | `version`, `server` | always 1 |
| `noust_metrics_recording` | | 1 while the history is being recorded |

Only the series described here are exported, never whatever the store happens to hold, so a new
series cannot change the shape or the size of a scrape by accident. A value older than two minutes (an
application that stopped being recorded) is left out, and `noust_metrics_recording` says whether the
collector is running at all.

## Process samples

A chart says that the CPU peaked five hours ago, not what did it. Wherever the metrics history
is recorded (the monitor, or a console that records it itself), the collector also keeps, once a
minute, the five processes that used the most CPU and the five that used the most memory:

| Field | What it holds |
|---|---|
| `pid`, `name`, `user` | The process, and the account it runs as |
| `cpu_percent` | CPU over the minute that just ended, 100 being one core. Measured from the CPU seconds the process used between two samples, so the first minute after the collector starts only primes the counters |
| `memory_bytes`, `memory_percent` | Resident memory at the sample |
| `app`, `owner_kind`, `owner` | Who it belongs to, read from its control group like the application charts: a Noust application (`app` is its domain), a Docker Compose container of its stack, a PHP-FPM pool, or the systemd unit its cgroup names. Empty for a login shell or a kernel thread |
| `command` | Its command line, redacted and cut at 200 characters |

- **Retention.** The samples live as long as the per-minute metrics they explain (26 hours; see
  the table above), in the same database, `/var/lib/noust/metrics.db`. There is no history from
  before the collector first ran with 3.2, and a process that started and ended between two
  minutes is never seen. There is no setting to turn the samples off.
- **Command lines are secrets carriers**, so each argument is redacted before it is stored: the
  value of an option or a `NAME=value` whose name looks secret (`--password`, `--api-key`,
  `DB_PASS=`), `-p<password>` of the MySQL clients, any argument that looks like a token or a key
  on its own, and the password inside every URL. What is stored is shown only to a caller who
  holds `secrets.reveal`; anyone else gets the process name. The monitor decides nothing from
  the command line.
- **Where they are read.** `GET /api/timeline` (below), and the console's **Investigate this
  stretch** on a chart ([console.md](console.md)).

## What happened in a stretch

`GET /api/timeline?start=<epoch>&end=<epoch>` merges, ordered by time, everything Noust knows
about a stretch of up to 31 days: the process samples above, and

| Source | What | Needs |
|---|---|---|
| `journal` | The journal of every unit, warnings and worse (all priorities for one application) | `secrets.reveal` |
| `audit` | CLI commands, console and central actions, sign-ins | `audit.read` |
| `deployments` | Deployments that ran in the stretch | `apps.read` |
| `jobs` | Background jobs: deploys, backups, cron runs | `apps.read` |
| `monitor` | The observations above, unit failures and recoveries, server boots | `server.read` |
| `processes` | The process samples | `server.read` |

The route itself needs `server.read`, and each source keeps the permission its own page needs.
A source the caller may not read comes back as `withheld` with the permission it lacks, never
silently dropped; one that cannot be read says why, with the system's own words, and the rest
are still answered. `app=<domain>` narrows the stretch to one application: its units' journal,
the audit events that name it, its deployments and jobs, its units' failures and its processes.
`sources=journal,audit` (repeated or comma-separated) asks for only some. Each source has a
ceiling and says when it reached it. On a central the call goes through the node's proxy, and
each server answers for what it saw.

## Email

```bash
noust config set monitor.smtp.host smtp.example.com
noust config set monitor.smtp.port 465
noust config set monitor.smtp.username alerts@example.com
printf '%s' "$SMTP_PASSWORD" | noust config set monitor.smtp.password --stdin
noust config set monitor.email_recipients admin@example.com,oncall@example.com
noust config set monitor.notify true
noust monitor test-email
```

The password never has to be typed into the command line: `--stdin` reads it from standard
input (a single trailing newline is stripped), and `noust config set monitor.smtp.password
--prompt` asks for it interactively without echoing it back. Typing a secret straight into
`VALUE` on a real terminal prints a warning suggesting one of the two - see
[Secrets via `--stdin`](security.md#secrets-via---stdin). `monitor.email_recipients` takes a
comma-separated list directly, the same as `--list`, because it already defaults to a list.

Mail can go through a provider's HTTP API instead (Resend, Postmark, Brevo or Amazon SES), for a host
that blocks outgoing SMTP: `monitor.email_api.provider` and its credentials replace the SMTP account,
see [Email through a provider's API](notifications.md#email-through-a-providers-api).

With `monitor.notify` on, a scan that records new observations mails them to the
recipients. A process that keeps matching the same signal does not send a message every
scan. Credentials are only sent over TLS: `use_ssl` (implicit TLS, the default) or `use_tls`
(STARTTLS) - not both at once, and never neither when a username or password is set.

`monitor.smtp.host`, `.port`, `.from_address` and every address in `monitor.email_recipients`
are validated when set - a bad hostname, a port outside 1-65535, both `use_ssl` and `use_tls`
enabled, or an address that is not `name@domain` are refused with the reason, whether set with
`noust config set monitor.smtp.*` or through the console's Settings form
(`GET`/`PUT /api/config/smtp`, see [API](#api)).

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `monitor.enabled` | `false` | Whether it is meant to run at boot |
| `monitor.scan_interval` | `60` | Seconds between scans, at least 10. Above 300, `noust monitor status` warns that a failure may go unnoticed that long |
| `monitor.cpu_threshold` | `80.0` | CPU percent above which a process is recorded |
| `monitor.memory_threshold` | `80.0` | Memory percent above which a process is recorded |
| `monitor.watch_units` | `[]` | Units watched in addition to every unit Noust manages |
| `monitor.notify` | `false` | Mail new observations |
| `monitor.disk_threshold` | `90.0` | Percent used at which a disk is announced (1 to 100) |
| `monitor.disk_check_seconds` | `0` | Seconds between looks at the disks; 0 looks at every scan |
| `monitor.disk_renotify_hours` | `0` | Hours between repeats while a disk stays over; 0 sends one message per crossing |
| `monitor.cert_warning_days` | `14` | Days before a certificate expires that it is announced |
| `monitor.backup_missing_days` | `2` | Days without a new backup before it is announced; 0 turns it off |
| `monitor.backup_check_hours` | `6` | Hours between looks at the backups |
| `monitor.backup_missing_renotify_hours` | `24` | Hours between repeats of a missing backup |
| `monitor.os_patches_check_hours` | `24` | Hours between looks for system updates; 0 turns it off |
| `monitor.token_warning_days` | `7` | Days before an API token expires that it is announced; 0 turns it off |
| `metrics.retention_days` | `400` | Days the hourly tier of the metrics history is kept (35 to 3650); also how long the unit failures and recoveries the timeline reads are kept. The process samples are kept 26 hours whatever it says |
| `monitor.retention_days` | `30` | Days observations are kept |
| `monitor.max_observations` | `5000` | Observations kept at most |
| `monitor.email_recipients` | `[]` | Who receives the mail |
| `monitor.smtp.host`, `.port`, `.username`, `.password` | `""`, `465`, `""`, `""` | SMTP relay |
| `monitor.smtp.use_ssl`, `.use_tls` | `true`, `false` | Implicit TLS, or STARTTLS |
| `monitor.smtp.from_address` | the username | Sender |
| `monitor.smtp.timeout` | `30` | Seconds, at most 120 |

`noust config get` and `noust config set` take these keys; secret values print as `***`. Give
`monitor.watch_units` as a list: `noust config set monitor.watch_units
nginx.service,postgresql.service --list`.

## Notifications

The events above (`unit_failed`, `app_unreachable`, `app_recovered`, `cert_expiring`,
`disk_threshold`, `backup_missing`, `cron_failed`, `cron_success`, `os_patches`, `token_expiring`,
`alert_firing`, `alert_resolved`, `alert_acknowledged`, `cron_missed`, `cleanup_failed`, `cleanup_success`,
`probe_failed` and `probe_recovered`) go through Noust's notification channels (webhook, Slack,
Discord, Telegram, email, Pushover, ntfy, Gotify, Teams, Mattermost, Matrix, Lark, PagerDuty, and any
others you add by name), which are
configured under `notifications.*` in the console's Settings > Notifications or with `noust notify`
and `noust config set`, and only when `notifications.enabled` is on and the event is enabled. Each
channel can hear its own events, only some applications, and not at night; a delivery that fails
is tried again and kept on record. All of that is in [notifications.md](notifications.md). Test a
channel with `noust notify test <channel>`.

`app_unreachable` and `app_recovered` are on by default, like `unit_failed`
(`notifications.events.*`, see [CONFIG.md](CONFIG.md)). The first names the application, says
since when it has not answered, quotes the last probe verbatim (the request and what came back,
or one line per container that is not fine for a stack with no published port) and points at
`noust diagnose DOMAIN`; the second says how long it was down. An application stopped on
purpose, being deployed or running a job, or whose unit failed (that is `unit_failed`) is not
announced.

## API

| Endpoint | |
|---|---|
| `GET /api/monitor/status` | Unit state, and the list of things the monitor never does |
| `GET /api/monitor/config` | Settings in effect |
| `GET /api/monitor/metrics` | One live reading of CPU, load, memory, swap, disks, network |
| `GET /api/metrics/query` | The recorded history: several series, a window, mean and maximum per bucket |
| `GET /api/logs/search` | A search over the logs of every application you can see: [logs.md](logs.md) |
| `GET /api/fleet/metrics` | On a central, one metric of every server over a window, side by side |
| `GET /api/apps/{domain}/traffic` | An application's requests by the hour or day, how they ended, and its top pages, networks, status codes and referrers |
| `GET /api/metrics/prometheus` | The newest value of every metric, for a scraper: [Exporting the metrics](#exporting-the-metrics) |
| `GET /api/timeline` | Everything that happened in a stretch (journal, audit, deployments, jobs, what the monitor saw) with the process samples; see [What happened in a stretch](#what-happened-in-a-stretch) |
| `GET /api/monitor/processes` | The process table, sortable, at most 500 |
| `POST /api/monitor/scan` | Run one scan now |
| `GET /api/monitor/observations` | Recorded observations and their counts |
| `POST /api/monitor/observations/{id}/acknowledge` | Mark one as seen |
| `POST /api/monitor/install`, `/uninstall`, `/enable`, `/disable`, `/start`, `/stop` | Manage the unit |
| `GET /api/alerts/rules`, `PUT /api/alerts/rules/{name}` | The alert rules and where each stands, and a rule created or replaced |
| `POST`, `DELETE /api/alerts/rules/{name}/acknowledge` | Take a firing alert (`?subject=` an application), or hand it back |
| `POST /api/monitor/test-email` | Send a test email; a failure carries the SMTP server's own reply verbatim in `output` |
| `GET /api/config/smtp` | SMTP settings and recipients; the password is never returned, only whether one is set |
| `PUT /api/config/smtp` | Update them (sudo mode); a blank password keeps the one already stored |

## Troubleshooting

- **Nothing is recorded.** `psutil` must be installed (`apt install python3-psutil`,
  `dnf install python3-psutil`, `zypper install python3-psutil`, or `pip install psutil`; the
  RPM package only recommends it), and the monitor needs root to see every process.
- **An application has no chart.** The chart says why: the unit is stopped or missing, it is a
  static site (see its requests instead), or accounting is off for its unit. Units written from
  3.1 on carry `MemoryAccounting=yes` and `CPUAccounting=yes`; for an older one, redeploy it or
  add them (`systemctl edit <unit>`) and restart it.
- **No email.** Run `noust monitor test-email`: it prints the SMTP server's own error. Check
  that the firewall allows outbound 465 or 587.
- **Too many resource-use observations.** Raise `monitor.cpu_threshold` or
  `monitor.memory_threshold`. The same process with the same signal is recorded at most once
  an hour.
- **A chart has no process samples for the stretch.** They are kept 26 hours and only exist
  from the minute the collector first ran with 3.2; an older peak has the chart and nothing
  that explains it. The console says so instead of showing an empty list.
- **An application is announced as unreachable and answers when you try.** The monitor asks
  what the deploy's health gate asks: its health path and expected statuses
  (`noust app health DOMAIN`), on the port that serves now. Fix the check, not the monitor. A
  Compose stack with no published port is judged by its containers (`docker compose ps -a`).
