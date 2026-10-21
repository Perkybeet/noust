# Release policy

What a Noust version number promises, how a release reaches your server, and how long a line is
supported. The promises here are the ones the project's checks hold: where a promise has a check,
the check is named.

## What the numbers mean

Noust versions are `MAJOR.MINOR.PATCH`. The single source is `[project].version` in
`pyproject.toml`; `scripts/release.py` writes it everywhere else and CI refuses a release when the
files disagree.

| A release that is | May contain | May not contain |
|---|---|---|
| **Patch** (`3.3.1`) | Fixes for defects and security problems. | A new screen or a changed one, a changed documented behaviour, a new setting that changes what runs, a change to the store's schema. |
| **Minor** (`3.4.0`) | New capabilities, new screens and settings, a store schema change, behaviour changes that are listed. | Anything that makes the previous minor's documented API stop working: the API only grows. |
| **Major** (`4.0.0`) | Removals and incompatible changes, each listed in its upgrade guide, after the deprecation period. | Surprises: a removal that was not announced. |

What this means in practice:

- **A patch is safe to apply without reading anything.** Because it changes no schema, the
  [way back](downgrading.md) from a patch to the minor's first release is always the package
  manager's downgrade. (The 3.1 line had seventeen patch releases; from 3.4 they follow this
  rule: a change that adds capability waits for the next minor.)
- **A minor comes with an upgrade guide**, `UPGRADING-X.Y.md`, that says what you will notice,
  what changes on its own, how a fleet upgrades and how to roll back
  ([the guides](UPGRADING-3.3.md)), and a changelog, `CHANGELOG-X.Y.md`. A minor that changes the
  store schema says so in the guide, and [downgrading.md](downgrading.md) shows which release can
  open which schema.
- **Every change that needs your action is marked** in the release notes with a line that starts
  "Action required:". The console shows the notes of a version before you update to it (Settings >
  About), with those lines set apart.

## Compatibility promises

- **The HTTP API only grows within a major version.** `/api/v1` serves what `/api` serves, and a
  test fails when an operation that was there disappears, loses a response code or a field, or gains
  a required parameter, against the baseline the previous release published
  (`panel/openapi.baseline.json`, `scripts/api_compat.py`). A deliberate break is a decision
  recorded in [the API changelog](API-CHANGELOG.md).
- **Deprecation comes first.** An operation that will go carries the `Deprecation` and `Sunset`
  headers on every response, and is removed in a release after its sunset, never before
  ([api.md](api.md#deprecation)).
- **The command line keeps what it had.** A command, a flag or an alias that existed is not removed
  within a major version, and a
  test compares the command tree with a frozen contract (`scripts/cli_parity.py`).
- **A name from before 4.0 is read, and the new one is written.** What lives outside Noust (a
  script's header, an API token in a CI secret, a file in a repository, a backup or an export) keeps
  working under its old name until 5.0, backups and exports for as long as the file exists;
  [UPGRADING-4.0.md](UPGRADING-4.0.md) lists them, and `noust.core.legacy_names` says why each is
  kept and until when.
- **An application is never converted behind your back.** An existing application keeps how it
  updates and how it is laid out; a release that offers a better way offers it, and does not apply it.

## How a release reaches you

1. **The gates.** A tag publishes nothing until the whole test suite passes on three Python
   versions, lint and types are clean, the wheel installs on a clean machine, the Debian and
   RPM packages are built as the openSUSE Build Service builds them (in containers with only the
   declared build dependencies) and installed, a 2.x server upgrades in a systemd container, and the
   central's image starts and answers `/health`.
2. **The channels.** PyPI, the openSUSE Build Service repositories (Debian, Ubuntu, Fedora,
   openSUSE: one repository per distribution), the container image `ghcr.io/perkybeet/noust` and the
   GitHub release, with the installer and its signed checksums ([platforms.md](PLATFORMS.md)).
3. **Announced only when it is real.** The GitHub release is created as a pre-release, which servers'
   update checks and the installer's `latest` link do not see. The release workflow then waits until
   **every supported distribution's repository serves the new version**, and only then makes it the latest. Until then a server's update check says
   "on the way" instead of offering an upgrade that would do nothing. `python scripts/obs_published.py
   vX.Y.Z` shows who is behind.
4. **Your fleet is the canary.** To update a fleet, update one server first and let the rest follow
   only if it is fine: `noust fleet run noust_update --canary SERVER`
   ([CENTRAL.md](CENTRAL.md)). The fleet's Updates view shows the target version and how many servers
   are on it.

There is **one channel**, stable. There is no "next" channel and no long-term-support line yet;
the support window below is how long a line gets fixes.

## Updating by itself

A server can install Noust's new releases without anyone pressing a button. It is off until you turn it
on (Settings > About > Automatic updates, or `noust self-update set --enable`), and it is narrower than
an update you start:

| You choose | Values | What it means |
|---|---|---|
| **Releases taken** | `security`, `patch` (the default), `stable` | `security`: only a release that fixes a vulnerability. `patch`: also the patch releases of the version you run, which by the rules above change no screens and no schema. `stable`: also a new minor version, which comes with an upgrade guide. |
| **Hours** | `03:00-05:00` (the default) | The window an update may start in, on the server's clock. It may cross midnight (`23:00-01:00`). |
| **Days** | `daily` (the default) or `mon,thu` | The days the window opens on. A window that crosses midnight belongs to the day it opened on. |

What does not depend on what you choose:

- **A new major version is never installed by itself.** A major removes what it announced, and that is
  read first by a person.
- **It is the same update as "Update Noust":** the one command of the installation's method, in its own
  systemd unit that outlives the console's restart, behind the same checks before anything is installed
  (room on disk, a store the new version opens, a package manager that answers). When a check fails,
  nothing is installed and the reason is written down.
- **Never while the console is working.** A build in progress would end with the console's restart, so
  a run that finds a job queued or running waits for the next look. The timer looks every ten minutes
  while the window is open; a run that updated, found nothing to take, or was refused for a reason a
  person must read is not repeated until the next window.
- **Only what the update check sees.** With `updates.check` off the server does not know what there is
  to install, so it installs nothing. A release that is published but whose package your server's
  source has not built yet (`on the way`) is waited for inside the window.
- **Every run is written down:** in the audit trail (`system.auto_update`, with the versions and why)
  and in Settings > About, with the server's own words when it did not install or failed. There is no
  notification for it yet; the audit trail ships to journald or syslog like every event.

`noust self-update` shows what is set, when the window next opens and what the last run did; `noust
self-update set --channel security --window 01:00-02:30 --days sun` changes it; `noust self-update run`
decides now (the timer's command is `run --scheduled`, which also respects the window). The timer is
`noust-auto-update.timer`; turning the feature off removes it, and removing the package leaves nothing
running.

## How long a line is supported

The two newest minor versions receive security fixes, as patch releases published to all channels
at the same time. The table is in [SECURITY.md](../SECURITY.md), kept by `scripts/release.py`:
releasing a new minor adds its line, keeps the previous one until the minor after it is released,
and retires the line before that. CI fails when the table does not list the version being released as
supported.

A 2.x server upgrades to 3.3.x first: [upgrading-3.0.md](UPGRADING-3.0.md).

## Where to read what changed

| You want | Read |
|---|---|
| What changed in a minor | `CHANGELOG-X.Y.md` ([3.3](CHANGELOG-3.3.md), [3.2](CHANGELOG-3.2.md), [3.1](CHANGELOG-3.1.md)) |
| What to do when you upgrade | `UPGRADING-X.Y.md` ([3.3](UPGRADING-3.3.md)), and the console: Settings > About > What's new |
| Changes to the API | [the API changelog](API-CHANGELOG.md) |
| How to go back | [downgrading.md](downgrading.md) |
