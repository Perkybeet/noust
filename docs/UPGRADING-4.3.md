# Upgrading to 4.3

This guide is written against Noust 4.2.1. A server older than 4.2 reads [UPGRADING-4.2.md](UPGRADING-4.2.md)
first. The whole list of changes is in [CHANGELOG-4.3.md](CHANGELOG-4.3.md), and what a client of the HTTP API can
notice is in [API-CHANGELOG.md](API-CHANGELOG.md).

The short version: 4.3 does not touch what serves your applications. No site, application or Compose stack is
rewritten by the update, and the only units it writes again are Noust's own two, `noust-web` and `noust-monitor`,
to raise their limit of open files. The command line keeps every command and option and the HTTP API only grows.
The store moves to schema 18 the first time Noust starts (a copy of the old one is kept beside it), what an API
token changes in production waits for a person on a server under the ENS profile, and the check on a Compose file
reads what Compose resolves it to. Run `noust health` after updating.

## Action required

- **Under the ENS profile, a pipeline that deploys to production through a token now waits for a person.** The
  control `token_production_approval` (asked by ENS MEDIA and ALTA, and `security.token_production_approval` for
  anyone) makes `propose` the least a token can have in production. A call that changes something there answers
  `202 approval_required` and becomes a request; another person decides it in the console or with
  `noust approval approve ID` as root, and the pipeline sends the same call again with `X-Noust-Approval: <ID>`,
  which runs once. What a token changes in any other environment is as it was. Under those frameworks no setting
  lets a token do it alone: an operator can only ask for more (`never`) with **Settings > Approval policy** or
  `noust approval policy set`. A server not under ENS changes nothing: a token is `alone` everywhere its
  permissions let it, until somebody changes the table
  ([security-approvals.md](security-approvals.md#tokens-and-the-built-in-assistant-the-approval-policy)).
- **A Compose application whose file hides a privileged container is refused at its next update, once.** The
  check that refuses a compose file asking for root on the server (`privileged`, the Docker socket) now also
  reads what Compose resolves the file to, which sees a service brought in by an `include` or an `extends`, a
  `jobs` entry, a service under a profile, the file of a `secrets` or `configs` entry, and `volumes_from` or
  `pid: container:`. A stack that is running keeps running. An update is judged against what the stack ran with,
  and the version it ran is only text, so a finding that shows only once Compose resolves the file counts as new
  and the update is refused with the finding named: remove it from the repository, or record why the application
  needs it with `noust app sandbox compose-exception DOMAIN --reason '...'` and update again. A redeploy from the
  file on disk, and bringing a stack that already runs to Noust, are proven against what runs and warn instead.
- **A browser that already opened the console on a name shared with other services may need clearing once**:
  [below](#the-console-says-strict-transport-security-only-on-a-name-it-owns).

## What you will notice

- **Server > Docker.** A machine with Docker has a new section of seven pages, which lists what Docker has
  whoever made it. Looking at it is `docker.read`, which every role holds; changing it is `docker.manage`, which the
  administrator holds, and a person of a team is refused all of it. A server without a usable Docker does not
  offer the section. Nothing in it changes anything of what already runs.
- **The built-in assistant is off.** Nothing of it is drawn until an administrator turns it on in **Settings > AI
  assistant**, with a model of their choice, and nothing leaves the server before that. Its permission,
  `assistant.use`, is held by the administrator and the operator. [assistant.md](assistant.md).
- **New permissions reach the roles by themselves, unless you edited the role.** A role Noust ships and nobody
  edited follows what Noust ships: `docker.read` on every role, `docker.manage` and `backups.collect` on the
  administrator, `assistant.use` on the administrator and the operator. A role somebody edited is offered them,
  which it takes or leaves. Checking a stack's edited Compose also asks for `secrets.reveal`, because Compose
  resolves the document with the stack's own variables.
- **Pages moved, and their old addresses lead to where they went.** Nothing of 4.2's fleet pages moved: what is
  new is that every section of an area has a page under All servers at `/fleet/<path>`.

  | 4.2 | 4.3 |
  |---|---|
  | Settings > Fleet settings (`/settings/fleet`) | The section each kind belongs to: channels in Settings > Notifications, alert rules in Alerts, backup destinations in Backups > Destinations, image registries, shared variables and the console address in Settings; `/settings/fleet?kind=channel` (or `alert_rule`, `backup_destination`, `registry`, `variable`, `console_link`) opens its own, and without `kind` the address opens Settings |
  | Settings > Manifest (`/settings/manifest`) | Server > Manifest (`/server/manifest`) |
  | Settings > Console address, which published the console on a name | Server > Console (`/server/console`); `/settings/console` is now the address the links of the notices open |
  | Settings > General: the applications directory, the web server, the backup folder and how much it builds | Server > Folders and builds (`/server/folders`) |
  | Settings > General: the server's name and the addresses it is reached at | Server > Machine |
  | Whether a server follows the central's settings | Server > With the central |

  On a central, Settings no longer follows the server chosen at the top: it is the central's, and says so.
- **Alerts opens with what Noust watches**, and what needs attention is one list with one count in the bell,
  Home, the Server page and the sidebar.
- **Certificates are judged on their own life.** `noust health`, the console and `GET /api/certs` say healthy,
  renewal pending, critical or expired from the share of its life that is left, and `cert_expiring` speaks when
  a certificate is past the point where it is renewed and has fewer than `monitor.cert_warning_days` days left
  (14 by default). A certificate of 90 days is announced when it was before; one that lives only 6 days is no
  longer in alarm for all of its life.
- **Notifications.** The e-mail channel counts as configured only when mail can leave (an e-mail service with
  everything it needs, or an SMTP host), so a channel that was on with nothing to send through now says it is not
  configured, and saving SMTP asks for the server. E-mail about one thing has one subject, without how it went, so
  a filter on the old subjects of failure and recovery needs `[Noust]` and `X-Noust-Event` instead. A change to the
  firewall or to SSH that was undone is the event `server.firewall_expired` or `server.ssh_expired` in the audit
  trail and Activity, where it was recorded as one more change.
- **A database copy made with Back up now is kept.** It is recorded as manual (`noust db backup-run --manual`) and
  retention only deletes what a schedule made. Copies made before 4.3 cannot be told from the rest and stay
  recorded as scheduled.
- **A token's narrowing is honoured.** A token issued by the master token or by root with `--permission` used to
  drop the permissions without a word and keep everything its scope allowed; it is narrowed now. Tokens that
  already exist keep what they hold.
- **An account's first second factor asks for a proof.** An account without one that sets it up from a session
  that signed in more than ten minutes ago must give its password with the request, or sign in again
  (`identity_proof_required`, `fresh_sign_in_required`). The console does it in the dialog.
- **ENS reports restores that were tried.** `noust ens check` has `ENS-BAK-03` (a restore of each application's
  backup tried in the last 30 days), `noust ens report` a section of them, and ENS MEDIA asks for 42 controls
  (39 before). It reads the rehearsals the server already kept (`noust backup drill`, which runs by itself every
  `backup.drill_days`, 30), so an application with none in that time is a warning ("never tested", or the date of
  its last good one) until the next run.
- **An invitation's link is built from `web.public_url`.** Through the SSH tunnel the address of the browser is a
  local one that means nothing to the person invited; the dialog says so when no public address is set, and the
  installer prints `http://localhost:8080/login`, because a passkey is refused on an address of numbers.
- **`noust health` shows the open files** `noust-web` and `noust-monitor` hold against their limit, and warns from
  80 %.

## The central's image carries Docker's client

The image of the central has Docker's command line (29.9.3), Compose (5.7.1, series 5) and buildx (0.39.0), each
fixed by the sum of its file. The 4.2 image carried none of them, and a central that is not given the machine's
Docker socket and `NOUST_DEPLOY_HERE=containers` never runs them: **updating the image changes nothing that a
central did, and no stack it managed has a different Compose**. A hub that already exists does not start deploying
on its machine when it updates; that is something its compose file asks for twice
([central-deploy-here.md](central-deploy-here.md)).

What the image's Compose does matter to is a stack something else started, brought to Noust on a central that
deploys here: the NAS's own Compose (Synology's Container Manager, a person at its shell) is another version, and
Compose keeps a hash of each service's configuration in a label of its containers, so another version reads the
same file to another hash and takes every container for changed.

- Bringing such a stack (`noust docker stack bring`, `noust app adopt`, **Bring to Noust**) is refused the first
  time, naming the Compose that made its containers and the one that is in the image ("These containers were made
  by Docker Compose 2.19.1 and this is Compose 5.7.1"), and nothing is touched.
- `--accept-recreate` brings it knowing that its containers are recreated once; after that the two agree. The
  other way is to bring the stack up once with the central's own Compose, which recreates them, and then bring it.
- A stack the central's own Compose started is brought as it runs, with nothing restarted.

A server, which is not a container, keeps using the Docker and Compose of the machine, and none of this applies.

## What changes on its own

- **At the first start, the store moves to schema 18.** It opens and adds 21 tables and 6 columns, and rewrites
  no row. By area: what a first deployment that failed leaves behind (`first_deploys`); the registrations of
  domains (`domain_registrations`, `domain_registrables`); what a task that runs once leaves
  (`cron_once_history`); what a central takes of its servers' copies and what a server tells it holds
  (`collected_backups`, `collect_nodes`, `collect_settings`, `backup_central_held`, `backup_central_syncs`); the
  approval policy and what each credential's client said it was (`caller_policy`, `credential_clients`, and the
  columns `caller`, `origin`, `environment`, `domain`, `needs` and `client` of `approval_requests`); the
  assistant (`assistant_settings`, `assistant_conversations`, `assistant_messages`, `assistant_usage`,
  `assistant_actions`, `assistant_turns`, `assistant_proposals`); the images' last look and the history of a
  stack's Compose (`docker_image_checks`, `docker_stack_versions`); and how a job failed as a sentence of the
  catalog (`job_errors`). The update refuses nothing for the lack of a self-backup, but it says so; take one first
  (`noust self-backup`).
- **The package writes `noust-web` and `noust-monitor` again**, with `LimitNOFILE=65536` where they had the
  system's 1024, and restarts the monitor and, if it is running, the console. A pip install does not: `noust setup
  doctor` names a unit that still has the old limit with the command that writes it again.
- **New settings take their defaults.** `noust config upgrade` (the package runs it) adds `web.hsts` (`auto`),
  `central.deploy_here` and `central.image_check` (empty), `monitor.domain_expiry` (`false`),
  `security.token_production_approval` and `security.assistant_flows` (`false`), `retention.assistant_days` (30)
  and the new `notifications.events.*` (all on). A value you set is kept.
- **Nothing is rewritten.** Sites, units of applications, Compose stacks, the tasks you scheduled and the store's
  existing rows stay as they were. A site that Noust generated stops asking for OCSP stapling (which nginx warned
  about at every reload) the next time Noust writes it, and an operator's site is never touched; a scheduled task
  of an application runs with its `.env`, its tools and its runtime only when it is written again, and
  `noust setup doctor` names each with the command that writes it again.
- **What leaves the server is as before.** Every request 4.3 adds is off until somebody turns it on: the
  assistant's model, the registry of a domain (`monitor.domain_expiry`), the registries of the images in use and
  the topics of a Telegram group ([PRIVACY.md](PRIVACY.md)).
- **The Debian package installs only its README** as a document of its own. The notes of the 3.x series
  (`UPGRADING-3.x`, `CHANGELOG-3.x`, `compose.md` and `databases.md`) are no longer put in `/usr/share/doc/noust`;
  they are on the documentation site, and the pages that are not history travel inside the Python package
  (`noust/docs`), which is where the assistant looks for them on every install.

## A fleet in the middle of an update

A 4.3 central drives servers still on 4.2, and a 4.2 central drives a 4.3 server, because 4.3 keeps every
operation, field and code of 4.2's API (`python scripts/api_compat.py` checks it against 4.2's schema). What a 4.2
server lacks the central does not offer or says so, and goes on with the rest:

- **The Docker zone**: the server's row of `Server > Docker` says that it needs 4.3 (its answer is a 404, which the
  central reads as "not offered", with the server's version), and the others' rows are never turned into an error.
- **The plan of an operation** is not offered for it, because its API does not publish one.
- **The copies**: the central takes its database dumps as it always could and says "only database dumps" in its row
  until it is updated.
- **The approval policy**: the page says the server has none. Its tokens are as they were, `alone`, because 4.2 has
  no policy to hold them.
- **The fleet's settings**: the kind `policy` and what 4.3 adds are `unsupported` for it, and the rest of its row is
  written as before.
- **The assistant** is the central's. A question about a server that lacks what a tool asks says so in words.

A 4.2 central does not draw what 4.3 added, and cannot collect copies: a 4.3 server keeps its own retention as it
did and, with no central that follows it, holds nothing back.

Update the servers first, a canary at a time (`noust fleet run noust_update --canary NAME`), then the central. The
central's own image: `docker compose pull && docker compose up -d`, after `noust central backup`. The app of a
UGREEN NAS is updated through the App Center, not with Docker's command: its version check gives none.

## The console says `Strict-Transport-Security` only on a name it owns

Until 4.2 the console sent `Strict-Transport-Security: max-age=31536000; includeSubDomains` on every response
over TLS. A browser that reads it refuses plain HTTP, and any certificate it cannot trust, for that **name in
every port**, and for every subdomain of it. That is a promise only whoever owns the name can make. A central
in a container on a NAS answers on `https://nas.example.net:8443`, and the same name is the NAS's own portal on
other ports: after one visit to the console the browser refused the portal, whose certificate it did not
trust, with no way to continue, and turned the NAS's `http://nas.example.net:9999` into `https`.

From 4.3 the console sends it on the name `noust console expose` published it on, and nowhere else, and never
with `includeSubDomains`. The setting is `web.hsts`:

| Value | What the console does |
|---|---|
| `auto` (the default) | Sends `max-age=31536000` over TLS only on the name `noust console expose` published the console on. A console reached by its address, by a name nobody published, or from a container, sends nothing. |
| `always` | Sends `max-age=31536000` over TLS on every name it is reached by. For a console whose name is yours in every port and that is only ever reached over HTTPS. |
| `never` | Never sends it. |

`noust config set web.hsts always` (or `never`) changes it, and the console reads it on the next request. The
header is never sent in cleartext, where a browser ignores it.

### If you already opened the console on a shared name

Updating the console does not make a browser forget what it was told: the old policy stays for up to a year
after the last visit. The console cannot take it back, because a browser only takes a policy from a name it
trusts, so it is removed in the browser, once, for the name:

- **Chrome and Edge:** open `chrome://net-internals/#hsts` (`edge://net-internals/#hsts`), type the name under
  **Delete domain security policies** and press **Delete**.
- **Firefox:** open the history (**Ctrl+Shift+H**), right-click the name and choose **Forget About This Site**.
- **Another browser:** clearing its history and site data for the name removes it.

A console published with `noust console expose` needs nothing: the next visit replaces the old policy with the
new one, which no longer binds the subdomains.

## Rolling back

4.3 writes the store at schema 18, which 4.2 does not understand (it opens schema 17 and no later, and says so:
"written by a newer Noust (schema v18; this version understands up to v17)"). The update keeps a copy of the older
store beside it, `noust.db.v17-<time>-<pid>-<id>.bak`, and `noust store compat --to 4.2.1` says which copy to put
back and what that loses: whatever changed in Noust since the copy was taken. [downgrading.md](downgrading.md) has
the steps and the table of which release opens which store. What 4.3 left that 4.2 ignores:

- **The assistant**: its settings, the sealed key, the conversations, the spending and the proposals are in
  tables the copy does not have, and 4.2 has no assistant. Turn it off before going back (`noust assistant
  disable`), which revokes every token it holds; those are in the tokens' own database, which is not rolled back.
- **The approval policy and who called**: the table the operator chose, what a credential's client said it was and
  the six columns of the approval requests are not in the copy, so a request that the policy held and nobody had
  decided is gone with it. 4.2 has no policy, so every token does what its permissions let it, also under the
  ENS profile.
- **The copies a central took**: the central forgets which it holds and 4.2 does not collect. The files stay under
  `<backup directory>/.collected`, which nothing of 4.2 reads and nothing removes: take them away by hand when you
  have what you need from them.
- **What a task that runs once left, the registrations of domains, the failed first deployments, the last look at
  the images and the history of a stack's Compose edits** are gone. A stack keeps running the document it has
  now. A failed job keeps its row and loses the coded sentence of its failure, and shows its plain one.
- **Settings 4.2 ignores**: `web.hsts`, `central.deploy_here`, `central.image_check`, `monitor.domain_expiry`,
  `security.token_production_approval`, `security.assistant_flows`, `retention.assistant_days`, the new
  notification events and the Telegram `topics` stay in `config.yaml`, ignored and not cleaned. 4.2 sends the console's
  `Strict-Transport-Security` as it did, with `includeSubDomains`, on every name over TLS.
- **Tasks that run once** still run at their moment, because the timer is systemd's, and 4.2 has no
  `noust cron settle` to retire them afterwards: remove their units with `noust cron delete NAME`.
- **Units**: the package of 4.2 writes `noust-web` and `noust-monitor` again without the limit of open files, which
  4.2.1 does not need because it closes a worker's connections itself.
- **A central that deploys on its machine's Docker**: the stacks and containers it started keep running in Docker,
  and 4.2 does not manage them, because its image has no Docker client and it cannot deploy there.
