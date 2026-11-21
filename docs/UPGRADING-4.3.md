# Upgrading to 4.3

This guide is written against Noust 4.2.1. A server older than 4.2 reads [UPGRADING-4.2.md](UPGRADING-4.2.md)
first. The whole list of changes is in [CHANGELOG-4.3.md](CHANGELOG-4.3.md), and what a client of the HTTP API can
notice is in [API-CHANGELOG.md](API-CHANGELOG.md).

The short version: 4.3 does not touch what serves your applications. No site, application or Compose stack is
restarted or recreated by the update. The units it writes again are Noust's own two, `noust-web` and
`noust-monitor`, to raise their limit of open files, and, at the update, the unit of
each Compose stack, once, to hold its start to what was judged
([below](#what-starts-a-compose-stack-is-what-was-judged)); that is a `daemon-reload` and nothing else. The command line keeps every command and option and the HTTP API only gains
operations and fields (what the security fixes now refuse is in the Breaking changes of
[API-CHANGELOG.md](API-CHANGELOG.md)).
The store moves to schema 18 the first time Noust starts (a copy of the old one is kept beside it), and what an
API token changes in production waits for a person on a server under the ENS profile.

4.3 also closes a vulnerability in the check on Compose files that dates from 3.1.0
([CHANGELOG-4.3.md](CHANGELOG-4.3.md#security)). Nothing that runs is stopped by it, and a stack that already
runs keeps updating, with a warning, whatever its containers were given; the check refuses more in a new stack, in
what an update adds and in an adoption. Run `noust health` after updating and read
[the first section below](#compose-stacks-that-carry-more-than-a-new-stack-may).

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
- **Review the Compose stacks that carry something of the server.** After updating, `noust health` lists every
  stack that carries what gives the server to a container or a path of the server outside the application, by its
  compose file and by what its containers were given, each with the command to record why it needs it. They keep
  updating, with a warning. The one place an update stops is `privileged`, a container runtime's socket, Noust's
  own state or another application's files that a stack has without its file saying so plainly (it comes through
  a variable or an `include`): that asks for the recorded exception at its next update
  ([how](#compose-stacks-that-carry-more-than-a-new-stack-may)). Where untrusted repositories were deployed
  before, review their Compose files and rotate the secrets of that server.
- **Nothing, for the start of your Compose stacks, but read `noust health` after the update.** The unit of each
  stack gets a check that runs before `docker compose up`; the stacks that run today are taken over as they run,
  so each goes on starting, at the next boot too. `noust health` lists the ones that could not be taken over yet
  (and why), and the ones whose accepted state came from before 4.3 so you can look at it
  ([how](#what-starts-a-compose-stack-is-what-was-judged)). If you go back to a release before 4.3, run
  `noust setup compose-gate --remove` first.
- **Compose reads a closed list of Noust's environment.** A `${NAME}` in a compose file, or a secret read from the
  environment, is an empty string for any variable of Noust's own process that Docker and Compose do not read
  themselves (everything but `PATH`, `HOME`, the language, the proxy and certificates, the places of the credential
  helpers, `DOCKER_*`, `COMPOSE_*`, `BUILDX_*` and `BUILDKIT_*`). The unit of a stack never had them, so a stack
  that depended on one already failed on every restart; the variables of a stack are the ones of its `.env`. A
  private registry's logins and a proxy keep working.
- **Adopting, or bringing, a stack that runs with something of the server needs `--accept-host-access`**
  ([how](#adopting-or-bringing-a-stack-that-runs-with-access-to-the-server)).
- **To have a central collect a server's copies, set that server's ceiling to `admin`**
  ([how](#a-central-collects-copies-only-from-a-server-that-allows-admin)).
- **A browser that already opened the console on a name shared with other services may need clearing once**:
  [below](#the-console-says-strict-transport-security-only-on-a-name-it-owns).

## What you will notice

- **Server > Docker.** A machine with Docker has a new section of seven pages, which lists what Docker has
  whoever made it. Looking at it is `docker.read`, which every role holds; changing it is `docker.manage`, which the
  administrator holds, and a person of a team is refused all of it. A server without a usable Docker does not
  offer the section. Nothing in it changes anything of what already runs.
- **The built-in assistant is off.** Nothing of it is drawn until an administrator turns it on in **Settings > AI
  assistant**, with a model of their choice, and nothing leaves the server before that. Its permission,
  `assistant.use`, is held by the administrator and the operator, and by no API token: it is for people, and the
  approval policy is edited by a person too, however many permissions a token was issued with. Changing the
  provider's address to another origin without giving the key in the same request empties the saved key and
  leaves the assistant off. [assistant.md](assistant.md).
- **A hub does not use its machine's Docker until it is told to.** A central in a container with Docker's socket
  mounted shows no Server > Docker and answers `409 hub_role` to `/api/docker` and `noust docker`, and the disk page
  leaves Docker's figures out, until `NOUST_DEPLOY_HERE=containers` is in the file that creates the container. The
  console and the API cannot turn it on: a write of the configuration that would leave `central.deploy_here` on is
  refused whatever its route. [central-deploy-here.md](central-deploy-here.md).
- **Compose and image builds no longer see Noust's own environment.** `docker compose` and `docker build` start
  without any `NOUST_*` variable of Noust's process (a token, the key the secrets are sealed under, the data
  directory), so a `${NOUST_...}` in a compose file is an empty string. The stack's own `.env`, `DOCKER_HOST`,
  `COMPOSE_*` and variables of your own are read as before.
- **The user of a web address is hidden wherever an address is shown.** `noust env list`, `noust variables list`,
  the audit trail, what the MCP endpoint returns and the process monitor's samples show `https://***@host/...` for
  a token placed as the user of an address, and `https://***:***@host/...` for a user and a password: until 4.2.1
  only the password was hidden (`https://ana:***@host/...`).
- **A backup that cannot reach its destination no longer fails the run.** The copy stays on the server, is
  tried again by itself (`noust backup resend` shows and forces it; the first copy that is owed makes the timer
  `noust-backup-resend`, or the central's scheduler runs it) and is said once for the destination. If you
  watched the journal of `noust-backup-*` for a failed unit, watch `noust backup list` (it says which copies were
  not sent), the console (the copy is "Pending to X" in its list, with what the destination said) or the notice
  instead. A schedule with `remote_only` keeps the copy until every destination holds it.
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

## Compose stacks that carry more than a new stack may

Until 4.2.1 the check on a compose file refused `privileged`, the Docker socket and bind mounts of Noust's own
state or of another application's directories as they were spelled, and at most warned about capabilities,
devices, the host's namespaces and other paths of the server. It did not follow a link in the repository and did
not look at the files an `env_file`, a build context or a build secret names. 4.3 refuses all of that in a new
stack, judges a path by where it leads and asks Compose what the file resolves to
([the list](compose.md#what-a-stack-may-ask-of-the-server)). Nothing that runs is stopped by it, and no
application in production stops updating because of it: what a stack already had is what it was accepted with
(which Noust writes down when it takes the stack over and at every deployment) and what its containers were given
of the server, which Noust asks Docker for. At its next update:

- **What its containers already have, or its file says as written** (`privileged` written in the file, a device,
  a mount of `/srv/media`, `network_mode: host`; a mount made of a variable such as `${DATA_DIR}:/data`, whose
  value is in the stack's `.env`; a service that an `include` or an `extends` brings in from another file): the
  deploy log says each line and that a new stack would be refused it, and the update goes on. So do a redeploy, a
  rollback and an edit in the Docker zone.
- **What the update adds** (a directory, a device or a capability that no container of that service has, or a
  variable that now points somewhere else): refused with "asks for root on this server", the lines and the
  command, as in a new stack. The stack keeps serving what it served.
- **What 3.1 already refused without an exception** (`privileged`, the Docker socket, Noust's own state, another
  application's files) that a stack ran with when 4.3 took it over is accepted as it ran, however it comes (a
  variable, an `include`, written as it is), so no stack stops updating for it; `noust health` lists it, and what
  an update adds of it is refused.
- **Docker does not answer** when Noust has to know what the containers have: the update is refused saying
  why and what to do. Nothing is let through on a guess.
- **A key Noust does not know** (the file is written for a newer Compose than the check, or the key is a
  typo): the check knows every key of the Compose specification and refuses the one it does not, saying "Noust
  does not know what that key gives the container" and naming it. A stack that already has such a key only gets
  the warning, and `noust health` lists it; a new stack, or an update that adds one, asks for the exception
  once a person has read what the key does. Updating Noust to a version that knows the key is the other way.
  Keys with the prefix `x-` are Compose's own extensions and are never refused.
- **A file that an `include` or an `extends` brings in** is read and judged as the file it is, from its own
  directory (an `env_file` of another application's `.env` inside it is refused with no exception). One that is
  not there, is not a compose file or is larger than 1 MiB is refused, saying why and never what it holds.
  An `env_file` named through a variable (`${ENV_FILE:-.env}`) is judged where the Compose in use keeps its
  path (`config --no-env-resolution`: Compose 5.7.1 does) and refused as unseen where it keeps none (Compose
  2.40.3 and the older 2.17, 2.19 and 2.0.0): write the path out, or record the exception. A stack that
  already runs is taken over with it as it is.

Look before an update does:

```bash
noust health
```

Under Build sandbox it says how many applications build as root and how many Compose stacks run with what gives
the server to a container and, in its warnings, one sentence for each stack that carries something a new stack
would be refused, with the command below. It judges the compose file as written and what the stack's containers
were given, so a stack that hides it behind an `include`, an `extends` or a variable is there too. `noust ens
check` carries the same sentences in the evidence of ENS-BLD-01.

For each stack, take it out of the compose file and deploy, or record why the application needs it:

```bash
noust app sandbox compose-exception shop.example.com --reason "Mounts the Docker socket to start its own workers"
```

It asks for a confirmation (`-y` skips it) and is root-equivalent: root at a terminal, sudo mode through the API,
and in the console the **Containers that are root on the server** card of the application's Settings > Build. A team
role never can, and a central only with the `admin` ceiling and host access on that server. It can be recorded
before the first deployment, and `--revoke` takes it back. It is the application's, not one line's: once it is there
an update that adds something else of the same kind is allowed, and logged with who allowed it and why. Take it back
when the reason is gone.

An exception does not allow what is not the application's to take: Noust's own configuration, store and backups,
or another application's files, through an `env_file`, a build context, a build secret or an `include`; and a
project, volume, network, container name or image that belongs to another application or to Noust. Those are
taken out of the compose file, and a stack that already had one is only warned about.

Check: run `noust health` again. The stack is no longer named in Build sandbox, and its next update says
"allowed for shop.example.com by <who> (<reason>)" in the deploy log where it used to warn or refuse.

## What starts a Compose stack is what was judged

The unit of a stack runs `docker compose up -d` at every start, restart and boot, with the `.env` and the files
that are there then. Until 4.2.1 nothing looked at them at that moment, so someone who could write the stack's
`.env` or a file of its tree and could restart it changed what the containers got without a deployment. In 4.3 the
unit runs a check first (`noust app compose gate`, `ExecStartPre`), the restart of Noust, the API and the fleet
ask it before they pass the restart on, and the writer of the `.env` asks it before it writes
([compose.md](compose.md#what-starts-a-stack-is-what-was-judged)). A start that adds something that gives a
container the server is held back and says which line is new, with the two ways out: deploy again, or record why
it is needed. A restart that Noust refuses leaves the stack running.

How the update does not stop a stack from starting, in the order it is done:

1. The package's step at the update (`noust setup compose-gate`, which you can also run by hand) writes
   down, for each stack, what Compose resolves for it **today** and what its containers were given: what 4.2
   would have started. It is marked as taken over, and `noust health` lists the ones that carry something of the
   server so that you can look at them.
2. It records the Compose project each stack runs under today (nothing is renamed: the volumes are named after the
   project).
3. Only then it writes the unit of the stack again with the check in it and `-p`, and runs `daemon-reload`.
   Nothing is started, stopped or recreated, and a unit never has the check before its stack has the record.

A stack that cannot be resolved when the step reaches it (Docker is not up yet, a file is missing, Compose
fails) is left exactly as it was, so it starts as it always did, and `noust health` names it with the reason; the
console tries again every ten minutes, and `noust setup compose-gate --app DOMAIN` at once. A stack whose unit you made yours (it does not start with `# Generated by Noust`) is recorded and its unit
is left exactly as it is: Noust never rewrites a unit without that line, so it puts no check in one, and
`noust health` says that stack starts without it ([leaving.md](leaving.md#making-the-files-yours)).

What you may notice: a stack whose `.env` or tree somebody changed to lead somewhere that gives the server will
not start (at boot, after a restart) and its journal says why (`journalctl -u shop-example-com`). Mend the change
or record the exception; the other applications are not affected. `noust health` also says when two applications
run as one Compose project (two stacks whose file is in a subdirectory of the same name did): neither is changed,
and a new application always runs as its own name.

The line the unit runs is written to not matter when Noust is removed: it runs the command only where the
command exists (`ExecStartPre=/bin/sh -c '[ ! -x /usr/bin/noust ] || exec /usr/bin/noust app compose gate DOMAIN
--project PROJECT'`), so `apt remove noust`, `dnf remove`, `zypper remove` and `pip uninstall` leave the stacks
starting as they did before 4.3, and the package's removal script takes the line out first
([leaving.md](leaving.md#the-check-of-a-compose-stacks-start)). A unit that an earlier build of 4.3 wrote with the
command alone gets this form at the next pass.

Check: after the update, `grep -c "app compose gate" /etc/systemd/system/<app>.service` says 2 for each stack, and
`noust health` names none as waiting.

## Adopting or bringing a stack that runs with access to the server

Until 4.2.1 `noust app adopt` registered a stack whose containers mount the Docker socket or carry a device, with
a warning, because the stack was proven to run with it. From 4.3 it is refused, nothing is touched, and the
refusal lists each line and the command:

```bash
noust app adopt shop.example.com --path /opt/shop
noust docker stack bring shop --preview
```

Both answer with "puts under Noust a stack whose containers have what is on this server", the lines (for
example "service web mounts /var/run/docker.sock from this server") and the command to go on. The preview
refuses the same way, so you read the lines before deciding. To go on, say it:

```bash
noust app adopt shop.example.com --path /opt/shop --accept-host-access
noust docker stack bring shop --accept-host-access
```

`--accept-host-access` needs the root-equivalent permission, and `accept_host_access: true` in
`POST /api/apps/adopt` and `POST /api/docker/stacks/{name}/bring` does too (`403` without it). It registers the
stack as it runs and records the lines, who accepted them and when, as the application's compose exception (the
section above). In the console, **Bring to Noust** and the adoption step of the new-application wizard show the
same lines, each with the server's reason; whoever holds the root-equivalent permission ticks the box that accepts
them (the plan is asked for again, and sudo mode as ever), and whoever does not reads the lines and who can accept
them. Nothing changes until somebody does.

What is accepted is what was shown. A client that sends `accept_host_access: true` also sends `host_access_digest`,
the digest that the `409` which lists the lines (and the preview) gives: without it, or if the compose file says
other lines by then, the answer is a `409` with the lines as they are now and nothing is registered. The commands
show the lines they record, so a script that uses them needs nothing more.

Check: the **Containers that are root on the server** card of the application's Settings > Build shows the
exception with its reason, who and when (`compose_exception` in `GET /api/apps/{domain}/sandbox`), and
`noust app sandbox compose-exception shop.example.com --revoke` takes it back.

## A central collects copies only from a server that allows `admin`

Collecting is new in 4.3, and a copy carries the `.env` and the databases of every application, which is more than
the `deploy` ceiling ("update and go back on what exists") promises. A server whose ceiling for the central is
`deploy` or `read` does not hand its copies over: in **Backups > Copies on the central** its row says that its
access level does not allow it, with the command, `noust fleet collect status` shows `ceiling` in its State
column, and the central goes on with the other servers. To allow it, on that server, as root:

```bash
noust fleet access --level admin
```

Without options the command shows the ceiling in force, and it takes effect on the next request. Check it from
the central: after the next pass (`noust fleet collect run` makes one now) `noust fleet collect status` shows the
server as `followed` instead of `ceiling`. A `deploy` ceiling keeps doing what it promised: updating, going back
and taking backups on the server.

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

- **At the first start, the store moves to schema 18.** It opens and adds 22 tables and 6 columns, and rewrites
  no row. By area: what a first deployment that failed leaves behind (`first_deploys`); the registrations of
  domains (`domain_registrations`, `domain_registrables`); what a task that runs once leaves
  (`cron_once_history`); what a central takes of its servers' copies and what a server tells it holds
  (`collected_backups`, `collect_nodes`, `collect_settings`, `backup_central_held`, `backup_central_syncs`); the
  copies that were made and could not be sent to a destination (`pending_sends`); the
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
- **The units of the Compose stacks**: they run a command, `noust app compose gate`, that 4.2.1 does not have, so a
  stack would not start after going back. `noust store compat --to 4.2.1` says it first: run `noust setup
  compose-gate --remove` with 4.3 still installed, and the units are as 4.2 wrote them (the records stay, and
  bringing the stacks up again changes nothing).
- **The check on Compose files**: 4.2.1 has the check as it was before this release, with the limits described in
  [the first section above](#compose-stacks-that-carry-more-than-a-new-stack-may). The exceptions that were
  recorded stay in the store and 4.2 reads them as it always did, so going back reopens what 4.3 closed; update
  again as soon as you can.
- **A central that deploys on its machine's Docker**: the stacks and containers it started keep running in Docker,
  and 4.2 does not manage them, because its image has no Docker client and it cannot deploy there.
