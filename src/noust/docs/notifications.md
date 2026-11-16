# Notifications and alerts

Noust tells you when something happens: a deploy failed, a certificate is about to expire, a
backup did not run, a measurement stayed past a limit you set. This page is about where those
messages go, who hears which, and what happens when a destination does not answer. The monitor
that produces some of them is described in [MONITOR.md](MONITOR.md); every setting is listed in
[CONFIG.md](CONFIG.md).

Everything here is in the console under Settings > Notification channels and Settings > Alert rules, and in
the command line as `noust notify ...` and `noust alert ...`. They are two views of the same
`notifications.*` settings, so a change in one is a change in the other.

## Channels

A channel is a place a message can go. There are thirteen types:

| Type | What it needs | Notes |
|---|---|---|
| `webhook` | `webhook_url`, optionally `secret` and `webhook_headers` | JSON, signed with HMAC-SHA256 in `X-Noust-Signature` when a secret is set; `webhook_headers` adds the receiver's own headers |
| `slack` | `webhook_url` | An incoming webhook |
| `discord` | `webhook_url` | `mention_role_id` and `mention_user_id` ping someone on failures |
| `telegram` | `bot_token`, `chat_id` | `topics` posts each subject in a topic of a forum group, `thread_id` posts everything in one, `threads` gives one event its own: see [Keeping messages together](#keeping-messages-together) |
| `email` | `recipients` | Through the SMTP account of the monitor |
| `pushover` | `user_key`, `app_token`, optionally `device` | Push to a phone |
| `ntfy` | `webhook_url` ending in the topic, optionally `token` | ntfy.sh or your own server |
| `gotify` | `webhook_url` (the server), `token` | Your own Gotify |
| `teams` | `webhook_url` | A Microsoft Teams Workflows webhook |
| `mattermost` | `webhook_url` | An incoming webhook; the message is an attachment, which is what Mattermost reads (it ignores Slack's Block Kit) |
| `matrix` | `homeserver`, `room_id`, `access_token` | A room message with a formatted body; `room_id` is the id (`!abc:example.org`), not the alias |
| `lark` | `webhook_url`, optionally `secret` | A Lark (Feishu) custom bot; the card is signed when the bot has signature verification on (`secret`) |
| `pagerduty` | `routing_key`, optionally `region` | An Events API v2 integration: see [Paging someone](#paging-someone) |

Every credential is write-only: it is never shown again, in the console, the command line, the
API or the logs. Saving a form with a credential left alone keeps the stored one.

**At rest** the credentials of the channels (a webhook URL, a token, a bot's key, the signing
secret, the headers) are sealed in `/etc/noust/config.yaml` with AES-256-CBC and HMAC-SHA256 under a
key that stays beside it (`/etc/noust/secrets.key`, `0600`, root's), so a copy of the file, a
backup of `/etc` or a screenshot of it leaks nothing. They open when Noust reads the file and are
sealed again when it writes it; a configuration written before 4.0 is sealed the first time Noust
starts (or with `noust config protect`, which does nothing to a file with nothing in clear). Keep
the key with the file in a backup, or the credentials cannot be opened: the error says which file to
restore. See [security.md](security.md) and [downgrading.md](downgrading.md).

### Headers of the generic webhook

A receiver that wants its own credential in the request (an `Authorization: Bearer` token for n8n or
Home Assistant, a vendor's API key header) gets it from `webhook_headers`: one `Name: value` per line,
at most 20, kept sealed like the rest.

```bash
printf '%s' 'Authorization: Bearer 3f9a...' | \
  noust config set notifications.channels.webhook.webhook_headers --stdin
```

A name is a token (letters, digits and a few symbols, `-` included), a value is one line of printable
ASCII, a name cannot be given twice. The headers that frame the request or say what Noust says about
a delivery (`Content-Type`, `Content-Length`, `Host`, `User-Agent`, `Transfer-Encoding`, `Connection`,
`X-Noust-Event`, `X-Noust-Delivery`, `X-Noust-Signature`) cannot be replaced, so a configured header
cannot forge a signature. A line that is not accepted is refused when it is saved, naming the line.

### More than one channel of a type

The thirteen channels named after their type always exist. Any other name is another channel,
which has a `type` of its own: a second Slack for staging, a Pushover for whoever is on call.

```bash
noust notify channel set slack-staging --type slack --secret webhook_url
noust notify channel set oncall --type pushover --secret user_key --secret app_token
noust notify list
noust notify test oncall
noust notify channel remove oncall
```

A name is lower-case letters, digits, `-` and `_`, starting with a letter. At most 32 channels
may exist. Removing one of the thirteen defaults (one of each type) empties it instead.

In the console, Settings > Notification channels has an "Add a channel" button for these; the new
channel opens its own drawer to ask for the destination.

Credentials are asked for with `--secret KEY` (a hidden prompt) or `--secret-stdin KEY`, never as
an argument, because everything on a command line is visible in `ps`.

### Email through a provider's API

Mail goes through the SMTP account of the monitor unless you choose a provider whose API takes it
over HTTPS, which is the way out when your host blocks outgoing SMTP. The provider **replaces**
SMTP, it is not a second way to send the same alert (one switch: `monitor.email_api.provider`).

| Provider | `provider` | What it needs |
|---|---|---|
| Resend | `resend` | `api_key`, `from_address` |
| Postmark | `postmark` | `api_key` (the server token), `from_address` |
| Brevo | `brevo` | `api_key`, `from_address` |
| Amazon SES | `ses` | `region`, `access_key_id`, `secret_access_key`, `from_address` |

```bash
noust config set monitor.email_api.provider resend
noust config set monitor.email_api.from_address alerts@example.com
printf '%s' "$RESEND_KEY" | noust config set monitor.email_api.api_key --stdin
noust config set monitor.email_recipients ops@example.com
noust monitor test-email
```

The sender has to be one the provider has verified. A key the provider refuses, or a sender it does
not know, is final (no retries, the provider's own words are kept in the record of deliveries); a
provider that is busy or unreachable is tried again like any destination. Credentials are write-only
everywhere (`***` in `noust config show`, never in the API), and the address of every provider is
fixed: the only typed value that reaches a URL is the SES region, checked to be one. Leaving the
provider empty goes back to SMTP. In the console it is Settings > Notification channels > Email > "Send
through".

## Turning them off for a while

`notifications.enabled` is the master switch of every channel. Turning it off is almost always "for
a while" (a maintenance window, a night), and a switch left off is how the next real incident is
missed, so the console asks until when: for an hour, until tomorrow at 09:00 or until it is turned
on again. The first two write `notifications.muted_until`, a moment with its time zone before which
nothing is sent and after which everything is sent again with nothing to remember; the third turns
`notifications.enabled` off. The same question is asked from Alerts ("Silence notifications"), and
the test button of a channel ignores the pause, as it ignores the master switch.

## What each channel hears

By default every channel hears every event that is switched on under `notifications.events`.
Each channel can narrow that in three ways, all in its Routing drawer in the console or as
options of `noust notify channel set`.

**Its own events.** `--event deploy_started=on` or `--event backup_failed=off` gives this
channel its own switch for an event, whatever the server-wide one says. An event a channel does
not name follows the server's. `--clear-events` goes back to following it.

**A route.** A channel can be limited to some applications:

| Route | Option | Matches |
|---|---|---|
| `apps` | `--route-app` | Application domains; `*` and `?` make a pattern (`*.staging.example.com`) |
| `projects` | `--route-project` | The project's slug |
| `environments` | `--route-environment` | An environment's slug, or `project/environment` |
| `tags` | `--route-tag` | A tag the application carries |
| `min_severity` | `--min-severity` | `warning` or `failed`: nothing below it |
| `server_events` | `--server-events` | Also hear what belongs to no application (disks, restarts, certificates of the server), which the lists above would leave out |

A route with no list reaches every application. Lists work together: an application has to match
each list that is given. Tags need applications to carry them; an application without tags never
matches a tag route.

**Quiet hours.** `--quiet 22:00-07:00 --quiet-timezone Europe/Madrid` holds messages during those
hours. `--quiet-allow` says what still gets through: `failed` (the default), `warning` or
`none`. What was held is sent when the hours end: as one message listing them when there are
four or more (`--no-digest` sends them one by one instead), otherwise as they were. The held
messages are in the record of deliveries as `held` until then.

**A cap on how much a channel says.** `--flood 20/10` lets a channel send at most 20 messages in
any 10 minutes. A server that reboots, or an outage that touches thirty applications at once, is
thirty messages in a second; past the cap the rest are held and, once the window has passed, arrive
as one message that lists them (fewer than four are sent as they were). A cap that is still full
when they are due keeps them for another window. Held messages are in the record of deliveries as
`held (flood)`. `--clear-flood` removes it.

**A summary instead of a message at a time.** `--summary-at 08:00 --summary-timezone Europe/Madrid`
holds everything below `--summary-below` (`warning` by default, so a success, a recovery or a
heartbeat; `failed` also holds warnings) and sends it once a day at that time as one message. A
warning or a failure still goes at once. `--summary-days mon,wed,fri` limits it to those days. A
single item waiting for the hour is sent as it is, not dressed up as a summary. They show as
`held (summary)` until then. `--clear-summary` removes it.

In the console they are in the channel's Routing drawer, under Cap on messages and Summary.

The quiet hours are judged first, then the summary, then the cap; whatever the summary or a cap
releases during quiet hours waits for them to end. A paging channel (PagerDuty) ignores both: it
already collapses repeats by the incident's key, and an incident that waited for a summary would be
one nobody was paged for.

A channel's route, quiet hours, cap and summary live in `config.yaml` beside its settings:

```yaml
notifications:
  channels:
    oncall:
      type: pushover
      user_key: ...
      app_token: ...
      route: {min_severity: failed, projects: [shop]}
      quiet: {start: "22:00", end: "07:00", timezone: Europe/Madrid, allow: failed, digest: true}
      flood: {max: 20, minutes: 10}
      summary: {at: "08:00", timezone: Europe/Madrid, below: warning, days: [mon, tue, wed, thu, fri]}
      events: {deploy_started: false}
```

## Keeping messages together

A server that deploys, backs up and watches itself says a lot, and a chat that gets all of it in one
line is a chat nobody reads. Three things keep the messages of one thing together; each works only
where the channel can do it, and the console offers a channel only what it can do, and says why when it
cannot.

### Topics in a Telegram group

A group with topics turned on keeps conversations apart. Noust gives each **subject** a topic of its
own: deploys, alerts, backups, security and system. A subject is what an event is about, one table
shared by every channel (`noust notify events` lists each event with its subject):

| Subject | Events |
|---|---|
| `deploys` | a deploy started, succeeded, failed, was rolled back or had a hook fail |
| `alerts` | an application unreachable or recovered, a unit that failed, an alert rule, a probe, a scheduled task, a server of the fleet that went quiet, an application the kernel killed, a container that restarts in a loop |
| `backups` | a backup that failed, was missing or worked, a destination that stopped answering, a restore |
| `security` | a changed host key, a release that fixes a vulnerability, a token about to expire, an approval asked or decided, a change to the firewall or SSH that was undone |
| `system` | a disk over its limit, a reboot, a cleanup, a certificate or a domain about to expire, patches waiting, outdated recipes |

A Telegram channel posts each message in the first of these that has a number: the topic of **its
event** (`threads`, for one event only), else the topic of **its subject** (`topics`), else the one
topic of the channel (`thread_id`), else the group's general thread.

```bash
noust notify channel set telegram --topic deploys=12 --topic alerts=13   # by hand
noust notify channel set telegram --topic deploys=                       # this subject goes to the general thread
noust notify channel set telegram --clear-topics
noust notify telegram-topics                                             # what each subject has
noust notify telegram-topics --create                                    # have Noust create the missing ones
```

In the console it is the channel's Routing drawer, under **Where it is posted**: the general thread,
one topic, or a topic per subject.

**Noust creates the topics.** `noust notify telegram-topics --create`, or the "Create the missing
topics" button, asks the group through the channel's own bot (`getChat`, `getChatMember`,
`createForumTopic`), creates the topic of each subject that has none, called "Noust: deploys" and so
on in the language of the notifications, and saves the numbers. It is a request somebody makes: nothing
is asked of Telegram until the button is pressed. Asking again creates only what is missing, never one
twice, and what was made before a refusal is kept. When the group cannot take them it says why, with
Telegram's own words:

- **The group does not have topics turned on.** Only the group's owner can: in Telegram open the group,
  Edit, Topics, and switch them on.
- **The bot may not manage topics.** In the group, make the bot an administrator with the right to
  *Manage topics* (the group's owner already has it).
- **The bot is not in the group**, or Telegram cannot find the chat: add the bot as an administrator and
  check the chat ID.

**A topic that is gone does not lose the alert.** A topic that was deleted or closed, or of a group that
stopped being a forum, is refused by Telegram. Noust then sends the message once more in the general
thread, with a line that says which topic failed (once an hour for each topic, not in every message),
and marks the channel: the console says in the channel's row which topic stopped taking messages, since
when and what Telegram said, until it works again or the topics are set again. "Create it again" makes a
new topic for the subject (`noust notify telegram-topics --create --recreate`).

One topic per project or per environment is also possible today, with several Telegram channels and a
route each (`noust notify channel set tg-shop --type telegram --route-project shop --set thread_id=12`).

### A recovery answers its failure

Noust keeps the id of the message it posted (the reply to the send says it) and puts the next message
about the same thing as a **reply** to it, in the same topic: "Recovered" as the answer to
"Unreachable", the end of a deploy as the answer to its start (with `deploy_started` on). The rule is
short. The first message about a thing, since it was last fine, is its root. Every later one answers the
root. A message that says it is fine again answers it and closes the conversation, so the next problem
is a new one. A thing is one application, one unit, one disk, one deploy of one application on one
server (the same key the pager uses for its incident).

It never costs an alert: a root that is gone (Telegram refuses a reply to a deleted message), that was
posted in another chat or another topic, that is more than a week old, or that was never remembered
(a restored server, a notifier with no store) simply leaves the message to go on its own. The memory is
a small value in the store per channel and thing: a message id, the topic and a short fingerprint of the
chat; never the chat's ID, the bot's token or an address.

### Mail in one conversation

Every message about one thing has **the same subject**, `[Noust] Application: shop.example.com (web-1)`
or `[Noust] Deploy: shop.example.com (web-1)`: the noun of the thing and not how it went, because Gmail
breaks a conversation off when the subject changes and a failure and its recovery used to be two. The
state is the first line of the body. The later messages carry `In-Reply-To` and `References` pointing at
the first, also through Resend, Postmark, Brevo and Amazon SES; a provider that gives the message an id of
its own may not honour the reference, and the stable subject keeps the conversation in the clients that
thread by it. What has no way back (a certificate about to expire, patches waiting) keeps the subject
that says how it went, and filters on `[Noust]` and on `X-Noust-Event` work as they did.

### What each channel can do

| Channel | Topics | Replies | Why not |
|---|---|---|---|
| Telegram | by subject, one, or general | yes | |
| Email | | one conversation | |
| PagerDuty | | | Already one incident for each thing: a recovery resolves the incident its failure opened |
| Slack, Mattermost | | | The incoming webhook never says the id of the message it posted, so it cannot be answered or given a thread (a bot could; it is not offered yet) |
| Microsoft Teams, Lark | | | A Workflows webhook and a custom bot cannot answer a message |
| Pushover, Gotify | | | Each message is delivered on its own |
| Webhook | | | The receiver gets the event, the code and the application of every message, and decides how to group them |
| Discord, Matrix, ntfy | | | Not yet |

## A team's own channels

On a shared server a team keeps channels of its own and decides where the notices about its
applications go. A team's channel is an ordinary channel (same types, same retries, same record of
deliveries) that belongs to one team: it hears about **the applications of that team and nothing else**,
whatever its route says. It does not hear the server itself (a full disk, a node going down) and it
never hears another team's applications. The team's route narrows further inside the team
(`projects`, `environments`, `tags`, `min_severity`).

```bash
noust notify channel set alerts --team acme --type slack --secret-stdin webhook_url
noust notify channel set alerts --team acme --route-project web --min-severity failed
noust notify channel remove alerts --team acme
noust notify list                     # the Team column
```

The same through the API, under the team's path (`apps.read` to look, `apps.manage` to change, in that
team, and sudo mode to change one):

| Route | |
|---|---|
| `GET /api/teams/{team}/notifications/channels` | The team's channels without credentials, and the types it may add |
| `PUT`, `DELETE /api/teams/{team}/notifications/channels/{name}` | Create, change or remove one |
| `POST /api/teams/{team}/notifications/channels/{name}/test` | Send a test message through it |

* Two teams may both have an `alerts`; the key in `config.yaml` is `t<team id>-<name>` and carries
  `team: <slug>`.
* **Email is not offered to a team**: it goes out through the server's own mail account, which its
  administrator gives. A team uses a chat or push channel.
* A team has at most 8 channels, and a destination on a private network is refused unless the
  administrator lists it under `notifications.allow_private_hosts`, as for the server's own channels.
* Another team's channels do not exist for a member: the team's path answers 404, the configuration a
  member reads leaves them out, and testing one by its key says there is no such channel. The
  administrator sees them all, with their team, in Settings and in `GET /api/notifications/channels`.
* The team's own page has them too: **Projects > Teams > the team > Notifications**.

## Paging someone

A chat message is read; a page is answered for, and what a page has is state: the incident opens
when the thing breaks, a person acknowledges it, it escalates to the next on call when nobody does,
and it closes when the thing is fine again. PagerDuty keeps that state and its escalation policy;
Noust tells it, with one key per thing, when the thing breaks, when somebody takes it and when it is
fine again.

```bash
noust notify channel set oncall --type pagerduty --secret routing_key --set region=us \
    --min-severity failed
noust notify test oncall
```

Which notification is which is decided by its event and its state:

- **A problem opens an incident** (`trigger`): a failure pages as `critical`, a warning as `warning`.
- **The way back closes it** (`resolve`, same key): an application that answers again, a disk back
  under its limit, a node or a unit that recovered, an alert rule that cleared, the deploy that
  worked after the one that failed, the backup that ran after the one that did not. Two rules on one
  application are two incidents; the same problem twice is one.
- **Anything else that is a problem opens one of its own** and stays open until a person closes it
  (a certificate about to expire, a security update waiting).
- **What is not a problem and closes nothing is not sent**: a deploy that started, a heartbeat, a
  report, the digest of what quiet hours held, an approval.

A recovery always reaches the channel: it is not held by its quiet hours or stopped by its
`--min-severity` (an incident that opened would stay open), and it follows the switch of the problem
it closes, so a channel that hears `backup_failed` also hears the backup that fixed it though
`backup_success` ships off. `region` is `us` (the default) or `eu`, the service region of the account.

## When a destination does not answer

A notification is written to a record before it is sent. A delivery that fails is tried again
after 1 minute, 5 minutes, 30 minutes, 2 hours and 6 hours, and then given up on; a destination
that refuses it for good (a revoked webhook, a bad token) is not tried again, because nothing
about a second try would be different. The monitor drives the retries on its scans.

```bash
noust notify deliveries                 # newest first; --state failed, --channel oncall
noust notify retry                      # what is due now
noust notify retry 42                   # one delivery, with a fresh set of attempts
```

The same record is in the console under Settings > Notification channels, with what the destination
said, verbatim, and a "Send now" button on whatever can be sent again. A delivery that was given
up on raises `notify.delivery_failed` in the audit log, never the message itself.

A test (`noust notify test NAME`, or "Send test" in the console) is not part of the record: it
goes straight to the channel and shows what it answered.

## Events

The opening of Alerts in the console lists what Noust watches without being asked and, for each, which of
your channels would take what it says (`GET /api/alerts/watching`): the table below is the whole list of
messages, that page is the part of it that is a watch.

`notifications.events.<kind>` switches each one on or off for every channel; those marked
"off" ship switched off.

| Event | Sent when | Default |
|---|---|---|
| `deploy_started`, `deploy_success`, `deploy_failed`, `deploy_rolled_back`, `deploy_hook_failed` | A deploy, update or rollback | all on but `deploy_started` |
| `restore_success`, `restore_failed` | A backup is put back | on |
| `backup_failed`, `backup_success` | A backup runs | `backup_success` off |
| `backup_missing` | A backup schedule or policy (an application's, a database's, an application's data or Noust's own state) has no successful backup newer than `monitor.backup_missing_days` | on |
| `destination_unreachable` | A backup destination some policy sends to does not answer, and when it answers again | on |
| `cert_expiring` | A certificate is past the point where it is renewed (a third of its life left) and has fewer than `monitor.cert_warning_days` days left, and when it expires | on |
| `cert_renewal_failed` | certbot's last renewal failed (with its own words), or the timer that runs it is not running | on |
| `domain_expiring`, `domain_expired` | The registry of a domain Noust serves says its registration expires within 30 days or 7 days, or that it expired (only with `monitor.domain_expiry`, off by default) | on |
| `app_oom_killed` | The kernel killed an application for running out of memory: said once for every rise of its unit's `oom_kill` counter, with its memory limit and the most it used (cgroup v2) | on |
| `access_change_reverted` | A change to the firewall or to SSH that nobody confirmed from a new session in time was undone by its timer (it says which change, who applied it and what the undo printed) | on |
| `unit_failed`, `app_unreachable`, `app_recovered` | A service stops, or stops answering its health check | on |
| `disk_threshold` | A disk passes `monitor.disk_threshold` percent | on |
| `os_patches` | The package manager has updates waiting; security ones are named | on |
| `token_expiring` | An API token expires within `monitor.token_warning_days` days, expires today, or just has | on |
| `cron_failed` | A cron job ended with an error; its own last lines of output come with it | on |
| `cron_success` | A cron job ended well: a heartbeat | off |
| `cron_missed` | A cron job is enabled but its timer is not running, so it will not start (nothing failed, nothing runs); and once more when the timer runs again. A job you disabled is left alone | on |
| `cleanup_failed` | The disk cleanup the server's policy runs by timer ended with an error (all of its actions, or some, in which case it is a warning) | on |
| `cleanup_success` | That cleanup freed space | off |
| `probe_failed`, `probe_recovered` | An address a probe asks stopped answering, and answers again | on |
| `alert_firing`, `alert_resolved` | One of your alert rules fires, and when it clears | on |
| `alert_acknowledged` | Somebody took an alert that is firing | on |
| `server_rebooted`, `server_back` | A restart nobody asked for, or one asked from Noust ending | on |
| `node_unreachable`, `node_recovered`, `node_host_key_changed` | A server a central manages | on |
| `central_silent` | Said by a server about the central that manages it: the central, whose monitor probes the server every half minute, has not called for a quarter of an hour (`notifications.central_silent_minutes`); and once more when it calls again. Only a central that said it keeps watch is missed | on |
| `approval_requested`, `approval_decided` | A change waits for a second person | on |

`noust notify events` lists them with their state. The events produced by the monitor (the
backup, patches, cron and token ones, certificates, disks) are produced only while the monitor
runs (`noust monitor status`).

**A failed backup says where all of it is.** The excerpt of a `backup_failed` notice is the end of what
the tool said, as many lines as a chat message holds. The whole output is kept (512 KiB at most,
scrubbed, the newest 200 failures) and the notice carries `noust backup failures show ID` as the
command to run next and, when `web.public_url` is set, a second link, `output`, to
`GET /api/backups/failures/ID/output`: plain text, for a browser signed in to the console. The
webhook publishes the command as the fact `full_output` and the page in `links`.

The checks have their own settings, all under `monitor.`:

| Key | Default | |
|---|---|---|
| `disk_threshold` | `90.0` | Percent used |
| `disk_check_seconds` | `0` | Seconds between looks at the disks; 0 looks at every scan |
| `disk_renotify_hours` | `0` | Repeat the message while the disk stays over; 0 sends one per crossing |
| `cert_warning_days` | `14` | |
| `backup_missing_days` | `2` | 0 turns it off; weekly and monthly schedules are given their period |
| `backup_check_hours` | `6` | |
| `backup_missing_renotify_hours` | `24` | |
| `os_patches_check_hours` | `24` | 0 turns it off |
| `token_warning_days` | `7` | 0 turns it off |

They are in Settings > Alert rules, under "Built-in checks".

## Alert rules

A rule says "tell me when this measurement has been above (or below) that limit for this long".
It judges the history the monitor records every minute, so a rule needs the monitor on and the
[metrics history](MONITOR.md#metrics-history) recording.

```bash
noust alert set cpu-high --metric cpu --above 90% --for 5m
noust alert set low-memory --metric app_memory --app shop.example.com --below 256MB
noust alert set errors --metric app_5xx --above 20 --for 10m --severity failed
noust alert list
noust alert status
```

What a rule can watch (`noust alert metrics`):

| Metric | Of | Unit |
|---|---|---|
| `cpu`, `memory`, `swap`, `disk` | The server | Percent |
| `load` | The server | One-minute load average |
| `net_rx`, `net_tx` | The server | Bytes per second |
| `app_cpu` | An application | Percent of one core |
| `app_memory` | An application | Bytes |
| `app_requests`, `app_5xx` | An application | Per minute (from the access log) |
| `app_latency_p95` | An application | Milliseconds: the 95th percentile of the time requests took in the last minute (the access log of a site Noust wrote; see [MONITOR.md](MONITOR.md#traffic-of-an-application)) |
| `app_disk_read`, `app_disk_write` | An application | Bytes per second |
| `app_net_rx`, `app_net_tx` | An application | Bytes per second |

A limit is a number in the metric's unit, or text with it: `90%`, `512MB`, `2MB/s`.

- `--for` is how long the limit must be breached, without a gap, before the rule fires: at least
  a minute (the monitor looks once a minute) and at most a day. A spike is not an alert.
- `--hysteresis` is how far back inside the limit the metric must come before the rule clears.
  A CPU rule for "above 90" with a hysteresis of 5 clears at 85, so a value hovering at 90 does
  not fire and clear all night.
- `--cooldown` is the least time between two messages for the same rule and application
  (`1h` by default). A rule that fires again inside it is silent. With `--remind` it is also how
  often a rule that keeps firing says so again.
- `--severity` is `warning` or `failed`: the state the message carries, and so which channels'
  `min_severity` hear it.
- `--app` limits an application metric to one application; without it the rule watches each of
  them, and each is judged and announced on its own.

A rule fires only when most of the minutes in its window were actually measured, so a monitor
that was stopped for an hour does not fire a rule because of the silence, and it clears only when
every reading of the last minute is inside the limit.

Rules are in Settings > Alert rules, with what is firing now. Changing a rule forgets what the old
limit had decided: a rule moved from 90 to 95 is not firing because of 92.

### Taking an alert

Whoever is on call takes a firing alert so that the rest know somebody is on it:

```bash
noust alert ack cpu-high                              # the server's alert
noust alert ack errors --app shop.example.com --by alice
noust alert unack cpu-high                            # hand it back
```

In the console it is the button on the alert in Settings > Alert rules, and in the inbox. Taking it stops the rule's
reminders (`--remind`), tells every channel who took it (`alert_acknowledged`, "alice is looking
into it") and, on a PagerDuty channel, acknowledges the incident the firing opened, which stops
PagerDuty escalating it to the next person. It does not clear the alert: that still happens, and
is still told, when the metric is back inside its limit, and a taken alert that clears and fires
again is a new one that nobody has taken. Taking one that is taken changes nothing, and the first
name stays. Taking needs the permission to operate applications, not the one to change rules.
An acknowledgement made in PagerDuty's own app is not read back by Noust.

## Disk and network of an application

The Metrics tab of an application charts its disk reads and writes and its network traffic
beside CPU and memory, and the same series feed the alert rules. They come from the service's own
accounting (`IOAccounting` and `IPAccounting`), which the unit Noust writes now asks for. A unit
written before this one learns it on its next update or restart; until then the charts say
so. A Compose stack's network is read from its containers.

## Reading a site's web server logs

The Logs tab of an application also shows the web server's access log and error log for the site,
and `noust site logs DOMAIN` prints them: the last lines, `--kind error` for nginx's or Apache's
own account of what went wrong, and `--contains TEXT` to narrow them. A log that has not been
written yet says where it will appear. Tokens, passwords and credentials in a line are masked
like in every other log Noust shows.

## The API

| Route | |
|---|---|
| `GET /api/notifications/channels` | Every channel without credentials, and the types |
| `PUT`, `DELETE /api/notifications/channels/{name}` | Create, change or remove one (sudo mode) |
| `GET /api/notifications/events` | The events and whether they are on |
| `GET`, `PUT /api/config/email-api` | The email provider (sudo mode to change); no credential is ever returned |
| `GET /api/notifications/deliveries` | The record, newest first |
| `POST /api/notifications/deliveries/{id}/retry` | Send one again now |
| `GET /api/alerts/rules`, `PUT`, `DELETE /api/alerts/rules/{name}` | The rules and where each stands |
| `POST /api/alerts/rules/{name}/enable`, `/disable` | |
| `GET /api/apps/{domain}/site-logs` | The end of a site's access or error log |

Details and permissions are in [api.md](api.md). Probes of an address and the status page are in [probes.md](probes.md).
