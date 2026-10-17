# Running an application

What an application is while it runs, and what an operator can do to it without opening a
shell: the work it does beside the web process, how many copies serve it, what its unit is
allowed to do, the files it is given, the files it has, where its logs go, and what has been
changed but not applied yet. Deploying and going back are in [releases.md](releases.md).

Every command here takes `--reason` (recorded in the audit log, required under the ENS
profile) and, where it changes something, `--dry-run`. Every one has a route in the API
([api.md](api.md)) and a place in the console, which is a client of that API. Over the API
and in the console every change needs [sudo mode](security.md#sudo-mode), and so does reading
what may be a secret: the text of a file, of a mount, or a download.

- [Processes](#processes)
- [Copies of the web process](#copies-of-the-web-process)
- [Unit security](#unit-security)
- [Tuning the unit](#tuning-the-unit)
- [Disk: what an application takes, and a limit](#disk-what-an-application-takes-and-a-limit)
- [Could it run on several servers?](#could-it-run-on-several-servers)
- [Mounted files and paths](#mounted-files-and-paths)
- [Files](#files)
- [Log shipping](#log-shipping)
- [Changes not applied yet](#changes-not-applied-yet)
- [Scheduled tasks: what ran and what did not](#scheduled-tasks-what-ran-and-what-did-not)
- [Reading a deployment's log](#reading-a-deployments-log)

## Processes

A queue worker, a scheduler, a stream consumer: a process that belongs to an application and
must run the code the application is serving, not a copy that was current once.

```bash
noust app process set shop.example.com worker "node dist/worker.js" --instances 2 --memory 512
noust app process list shop.example.com        # every process, with the state of each copy
noust app process logs shop.example.com worker # the journal of all its copies, interleaved
noust app process scale shop.example.com worker 4
noust app process remove shop.example.com worker
```

The command is split like a POSIX shell would split it and run without one. It runs as the
application's account, in the release that serves, with the application's environment and the
sandbox of its unit, and each copy has its own limits (`--memory`, `--cpu`, `--tasks`).

- **A process follows the release.** It runs from `<app>/workers`, a link that moves with
  `current`, so it is never left on code that was current once.
- **Atomic with the web process.** Every activation restarts the processes together with the
  web unit and judges them with it. A copy that does not stay up on the new release fails the
  health gate, and the release is put back for the web process and the processes alike.
- **Its unit is Noust's** (`<app>-<process>@.service`, one instance per copy). It is not a row
  of `noust service`: a generic unit that nothing ties to a release is the other tool.
- `--instances 0` defines a process and keeps it stopped.
- **A repository can declare them.** The `worker:` of a `Procfile` or of `processes:` in
  `noust.yaml` ([build.md](build.md#process-types)) becomes a process whose source is the
  repository (`noust app process list` says so). Every deployment makes the repository's processes
  what it declares, with the release that serves, and the number of copies stays what you set. A
  process you define under the same name is yours and the repository's is not used.
- **Going back brings the processes back.** A rollback of the history (console and API),
  `noust releases rollback`, `noust update --commit` onto a release that is still on disk and
  `noust rollback` to a backup of an application on releases declare the processes of the release
  they restore, the way a deployment does, before the health gate judges it: a worker the older
  release did not have is stopped once it holds, one it had comes back with its command, and a
  copy that does not stay up on it puts the release that served back with its own processes.

Over the API: `GET /api/apps/{domain}/processes`, `PUT` and `DELETE`
`/api/apps/{domain}/processes/{name}`, `POST .../scale` and `GET .../logs`. In the console it is
the **Processes** subsection of the application's settings.

## Copies of the web process

The application's own unit and some more, each on a port of its own, behind the same nginx site:

```bash
noust app replicas shop.example.com            # how it is scaled now
noust app replicas shop.example.com 3          # the unit and two more
noust app replicas shop.example.com 1          # alone again
```

nginx shares the traffic among the copies (`least_conn`, and a copy that fails twice is left out
for a few seconds). A new copy has to answer the application's [health
check](releases.md#configuring-the-health-check) on its own port before nginx is told about it;
one that does not leaves everything as it was. A release reaches the copies the way it reaches
the unit: they are restarted one after another behind the same gate.

Scaling out is one release in several copies; [zero-downtime mode](releases.md#zero-downtime-by-default)
is two releases side by side. They exclude each other. What cannot run twice (a static site, a
Compose stack, a monorepo, a site with routes of its own, Apache) is refused with the reason.
The ports are Noust's to pick and keep, so scaling down and up brings the same ones back.

## Unit security

An application's unit already had `NoNewPrivileges`, `PrivateTmp` and, since 3.2, an account of
its own. From 3.4 it can also have the rest of what systemd takes away for free:

| Profile | What the unit may do |
|---|---|
| `off` | What it could before 3.4. For an application that cannot run any other way. |
| `default` | The filesystem is read-only but for the application's own tree and the directories you name, no home directories, no device nodes, no kernel tunables or modules, only the socket families a web application uses, a system-call filter, and no capability. |
| `strict` | Also bans the privileged and resource system calls and, for a runtime without a JIT, writable executable memory. Opt-in: a runtime that maps its own code (Node's V8, Python with cffi) breaks under it. |

```bash
noust app hardening status shop.example.com   # the profile, what it allows, and how exposed the unit is
noust app hardening set shop.example.com default --writable /var/lib/shop
noust app hardening set shop.example.com off
```

- **New applications are hardened from their first deploy** with the profile `deploy.hardening`
  names (`default` unless changed; see [CONFIG.md](CONFIG.md)). **Applications that exist keep
  their unit** until you move them: a redeploy never changes the sandbox of a running one.
- Changing it restarts the application behind the health gate. When it does not answer, the
  previous unit is put back and the application is restarted as it was. `--no-restart` writes the
  unit and leaves the running process in the sandbox it has.
- **Who is hardened:** an application whose unit runs its own code (Node, Python and the like).
  Not a Compose stack or a container (their processes are Docker's), a monorepo (one unit per
  workspace) or a PHP site (its pool lives in the PHP-FPM every PHP site shares).
- `status` shows how exposed the unit is as `systemd-analyze security` scores it (a read-only
  probe), and `set` shows the score before and after.

Moving an application to an account of its own is the other half of this:
`noust app identity migrate --all` moves every application that can have one, one after the
other, and reports the ones that fail without stopping. While an application still runs as the
shared account, the server health card says so under **Application accounts**.
See [releases.md](releases.md#the-account-an-application-runs-as).

Over the API: `GET /api/apps/{domain}/hardening` (`apps.read`) and `PUT` (`apps.manage`). In the
console it is **Unit security** in the application's settings.

## Tuning the unit

`noust app limits` sets the hard limits (memory, CPU quota, tasks). Docker offers more knobs than
that, and systemd has a native one for each, acting on the application's own cgroup instead of a
container around it. `noust app tuning` sets them:

| Option | Unit directive | Docker's counterpart |
|---|---|---|
| `--memory-high MB` | `MemoryHigh=`: the kernel slows the application and reclaims memory above it, before the hard limit would kill it | `--memory-reservation` |
| `--swap-max MB` | `MemorySwapMax=`: `0` keeps it out of swap | `--memory-swap` |
| `--cpu-weight 1-10000` | `CPUWeight=`: its share of the CPU when the server is busy (100 is the usual) | `--cpu-shares` |
| `--allowed-cpus 0-3` | `AllowedCPUs=`: pins it to these CPUs | `--cpuset-cpus` |
| `--io-weight 1-10000` | `IOWeight=`: its share of the disk when it is busy | `--blkio-weight` |
| `--nofile`, `--nproc`, `--memlock MB`, `--core-dumps` | `LimitNOFILE=`, `LimitNPROC=`, `LimitMEMLOCK=`, `LimitCORE=` | `--ulimit` |
| `--cap NET_BIND_SERVICE` | `CapabilityBoundingSet=` and `AmbientCapabilities=` | `--cap-add` |
| `--device /dev/kvm:rw`, `--gpu nvidia` | `DeviceAllow=`, `DevicePolicy=closed`, and the sandbox's private `/dev` off | `--device`, `--gpus` |

```bash
noust app tuning status shop.example.com
noust app tuning set shop.example.com --memory-high 384 --swap-max 0 --cpu-weight 50
noust app tuning set shop.example.com --cap NET_BIND_SERVICE --nofile 100000
noust app tuning set shop.example.com --gpu nvidia          # the device groups an NVIDIA GPU needs
noust app tuning set shop.example.com --unset cpu_weight    # take one option away, keep the rest
noust app tuning clear shop.example.com
```

- **What is not named is kept.** `set` lays what you typed over what the unit has; `--cap` and
  `--device` replace their list. An empty tuning is the unit the template writes.
- **After the sandbox.** The lines go at the end of `[Service]`, after the sandbox's, so a
  capability or a device you ask for wins over its empty capability set and its private `/dev`.
  Everything you do not name stays taken away.
- **Capabilities are a short list.** `NET_BIND_SERVICE`, `NET_RAW`, `NET_BROADCAST`, `IPC_LOCK`,
  `SYS_NICE`, `SYS_RESOURCE` and `SYS_CHROOT`. A root-equivalent capability (`SYS_ADMIN`,
  `SYS_PTRACE`, `DAC_OVERRIDE`, `SETUID`...) is refused: an application that needs one is a custom
  unit, not a process Noust runs as an unprivileged account.
- **Devices** are a node under `/dev` (`/dev/dri/renderD128:r`) or a device group of
  `/proc/devices` (`char-nvidia`, `char-drm`), opened `r`, `w` or `rw`. The GPU presets are
  `nvidia` (`char-nvidia`, `char-nvidia-uvm`, `char-nvidia-caps`) and `dri` (`char-drm`, Intel and
  AMD); the directives are checked with `systemd-analyze verify`, and whether a given GPU works
  inside the sandbox is up to the driver.
- **Not here.** `shm_size`, DNS and the hostname of Docker's options have no counterpart in a native
  process: it shares the host's. A Compose stack or a container application keeps them in its compose
  file.
- Like the sandbox, a change rewrites every unit the application runs as (the two instances of a
  blue/green application are written once, into their template; the copies of the web process
  take it when the health gate restarts them) and restarts it behind the health gate. When it does
  not answer, the previous unit and the previous options are put back and it is restarted as it
  was. `--no-restart` writes the unit and leaves the running process as it is; it then shows under
  [changes not applied yet](#changes-not-applied-yet).
- **Who is tuned:** the same applications that can be hardened: those whose unit runs their own
  code.

Over the API: `GET /api/apps/{domain}/tuning` (`apps.read`) and `PUT` (`apps.manage`, sudo mode; a
job). `PUT` carries the whole tuning, so a client changes one option by sending the rest as it read
them. In the console the limits and ulimits are under **More limits** in the application's
**Resources**, and the capabilities and devices in **Unit security**.

## Disk: what an application takes, and a limit

The server's storage page (**Server > Storage**) says what takes space on the machine; it has no row per
application, so an uploads folder or a release history that keeps growing is invisible until the whole disk
is full. This says it per application:

```bash
noust app disk shop.example.com --measure       # walk its trees now, and keep the answer
noust app disk shop.example.com                 # the last measurement, and how it stands
noust app disk shop.example.com --limit 2048    # hold new releases to 2048 MB
noust app disk shop.example.com --no-limit
```

| Place | What it is |
|---|---|
| `current` | The release that serves (an application built in place is one `tree`). |
| `releases` | The others, with what a prune of the releases the application keeps would give back. |
| `shared` | `shared/`: the uploads and the `.env`. |
| `repo` | The repository cache. |

Its backups are listed apart: they are kept elsewhere and do not count against the limit. Docker named volumes
are not included, and a Compose stack's bind-mounted data is, because it is inside the folder.

- **Measuring is a job.** `du` over a release tree with a `node_modules` in it takes a minute, so nothing
  measures in a request: the console and `POST /api/apps/{domain}/disk/measure` queue it, the answer is kept
  with its time, and `GET /api/apps/{domain}/disk` (`apps.read`) only reads it. Each tree has its own deadline,
  so one slow tree is named in `errors` and does not cost the rest. It uses the same `du`, run at the lowest
  I/O and CPU priority, as the storage analysis.
- **The limit is Noust's, not the kernel's.** A deploy that would build a new release (`create`, `update`, a
  webhook, `update --commit`) is refused in its pre-flight while the application is over it, with the figures
  and the command that raises it. Going back to an earlier release, which builds nothing, is never held up by
  it: an outage is not a reason to wait for room. The limit does not stop the application from writing, and it
  is not checked while the application runs. An application with a limit is measured before each deploy, which
  costs a walk of its trees; a tree that could not be measured in time is not taken as evidence of being over.
  The least a limit can be is 64 MB. `PUT /api/apps/{domain}/disk` (`apps.manage`) sets it or, with
  `{"soft_mb": null}`, removes it.
- **The kernel's quota: the filesystem is the operator's, the project is Noust's.** A filesystem project quota
  stops the writes themselves, and it needs the filesystem mounted with `prjquota`, which is a change to a running
  machine that no process of Noust's makes. The page says whether the filesystem the application lives on
  (`findmnt -T`) would accept one: `enabled`, `available` (XFS or ext4, not mounted with it), `unsupported` or
  `unknown`. Once it is `enabled`, giving the application's limit to the kernel is one command:

  ```bash
  noust app disk shop.example.com --limit 2048
  noust app disk shop.example.com --kernel      # set the project on its tree and the limit on the filesystem
  noust app disk shop.example.com --no-kernel   # take both off again; Noust's own limit stays
  ```

  Over the API it is `PUT /api/apps/{domain}/disk/kernel` with `{"enabled": true}` (`apps.manage`, sudo mode; a
  job, because the project id is written on every file already in the tree), and the console offers it under
  **Settings > Disk** once the filesystem has project quotas. What it does, and does not:

  - It sets a project id of Noust's own range (3000000000 plus the application's id, kept in the store) on the
    application's directory, with `xfs_quota` on XFS and `chattr` and `setquota` on ext4, and the limit on the
    filesystem. It writes nothing to `/etc/projects` or `/etc/projid`, so it cannot meet a name someone wrote
    there nor the ids Docker uses. A limit changed afterwards with `--limit` follows into the kernel, and
    deleting the application takes the quota off.
  - It refuses, saying what to do, when the application has no limit, when the filesystem is not mounted with
    project quotas, for a site Noust adopted and for a directory outside the apps directory: those files are
    yours. It never mounts, remounts or tunes a filesystem.
  - It holds an account without `CAP_SYS_RESOURCE` at the limit on both filesystems, which is every account an
    application runs as and the build sandbox. XFS holds root too; ext4 does not, so Noust's own work as root
    (a restore, a backup extracted in place) can pass it there. Files moved into the tree from another
    directory keep the project they had, which is the kernel's rule, so move them in by copying.
  - The kernel's number is the one that stops the writes; what the page shows next to it is what the kernel
    counts (`xfs_quota report`, `repquota`), not Noust's measurement. They differ by what each counts
    (block sizes, hard links).

  To mount the filesystem, with the tools the kernel's documentation names, on a machine you can restart:

  ```bash
  # XFS: add prjquota to the mount options in /etc/fstab (a remount does not turn it on; the root
  # filesystem needs rootflags=prjquota on the kernel line), then reboot.
  # ext4: enable the feature on the unmounted filesystem, then mount with prjquota.
  tune2fs -O project -Q prjquota /dev/sdX
  mount -o prjquota /dev/sdX /var
  ```

  Both were checked on real XFS and ext4 loop filesystems mounted with `prjquota`
  (`tests/integration/run.py`, scenario `kernel_project_quota_stops_the_writes`): the application's account is
  refused with "Disk quota exceeded" at the limit, a changed limit follows, and `--no-kernel` lets it write
  again. Try it on a machine you can lose first.
- **Not done yet.** There is no alert for an application approaching its limit, and no per-application breakdown
  of Docker volumes.

## Could it run on several servers?

Putting an application on several servers is the central's, not the server's: it keeps a record of where the
application runs, rolls one commit out to each and copies it to a server that lacks it ([the central's
guide](CENTRAL.md)). None of that looks inside the application, and it cannot, because the central holds no
copy of what a server runs. What decides whether a second server would serve the same thing is on the server
the application is on, and this says it:

```bash
noust app replication shop.example.com
```

It reads this server's records of the application, its `.env` (the names of the variables, never their
values), a Compose stack's compose file and the list of the server's scheduled tasks, and changes nothing and
reaches no other server. `GET /api/apps/{domain}/replication` (`apps.read`) answers the same, and the console
shows it under **Settings > Export**.

| Finding | Says | |
|---|---|---|
| `persistent_paths` | It keeps files here between releases; a second server starts with them empty. | blocks |
| `local_database` | It uses a database Noust runs on this server (a record whose host is this machine). | blocks |
| `stack_database` | Its Compose stack runs a PostgreSQL, MySQL, MariaDB or MongoDB service. | blocks |
| `no_repository` | Its source is a folder on this server, so there is nothing to build from elsewhere. | blocks |
| `adopted` | It is a site Noust adopted, not one it built. | blocks |
| `named_volumes` | Its stack keeps data in Docker volumes, which belong to the server they were made on. | warns |
| `in_place` | It is built in place, so a rollout that stops cannot put a server back by activating a release. | warns |
| `local_service` | Variables (by name, never their value) point at a service on this machine. | warns |
| `scheduled_tasks` | Its scheduled tasks would run once on every server. | warns |
| `previews` | Its previews are made on this server and are not copied. | warns |

A finding that **blocks** is one a second server would get wrong; one that **warns** would work, with a cost,
and the operator decides. An application that runs a registry image (`--type container`) is told it can be
rolled out by tag, so no server builds it.

What is not here, on purpose: Noust is not a cluster. There is no autoscaling and no scale to zero, no
scheduler that decides where an application runs, and nothing moves a running application by itself. The
copies of one application on one server are [the web process's](#copies-of-the-web-process); across servers
they are the central's placement, behind a load balancer of yours.

## Mounted files and paths

A configuration file that is not in git (a `.htpasswd`, a certificate, a `config/production.json`
with a token) has to be where every release finds it, and be readable and editable by an
operator.

```bash
noust app mount file shop.example.com config/production.json --from ./production.json --mode 640
noust app mount list shop.example.com
noust app mount show shop.example.com config/production.json
noust app mount remove shop.example.com config/production.json
```

- **A file** lives in `shared/<target>` and is linked into every release, exactly like a
  persistent path, so a new release has it before it starts. In place it is written at
  `<app>/<target>`; for a container application it is a file under `<app>/mounts/`, bound at the
  target in the container. It is written with the mode you name (0600 by default), handed to the
  application's account, and never through a link: a path whose parent is one is refused. Its
  text is the file on disk, not a column, so there is one copy to edit and back up. At most 5 MB.
  A running application reads it at its next restart.
- **A path of the server** bound into a container application (`noust app mount bind
  /srv/data /data`), read-only unless `--writable`. It is root-equivalent, so the server's own
  state and the Docker socket are refused, and under the ENS profile only read-only is accepted.
- An `.env` file is not a mount: the environment has its own validation, masking and audit.
  A static site and a Compose stack take no mounts (the stack's own compose file mounts what it
  needs).

Over the API: `GET`, `PUT` and `DELETE /api/apps/{domain}/mounts`, and `GET .../mounts/content`,
which needs the permission that reveals secrets (`secrets.reveal`) because a mounted file may be
one. In the console it is **Mounts** in the application's settings.

## Files

The files of an application, from the console or the command line, as the application's own
account, never as root: the kernel's permissions decide what can be read and written, exactly as
for the application itself and for `noust app exec`. It is as powerful as a shell as the
application, so every route asks for the terminal's permission (`apps.terminal`).

```bash
noust app files list shop.example.com uploads
noust app files get shop.example.com uploads/logo.png ./logo.png
noust app files put shop.example.com ./robots.txt public/robots.txt
noust app files mkdir shop.example.com uploads/2026
noust app files move shop.example.com uploads/a.png uploads/b.png
noust app files remove shop.example.com uploads/old
```

- A path is relative to the application's directory. It never holds `..`, a NUL byte or a
  control character, and a link in the tree (`current` is one, and a repository can plant others)
  is shown, can be renamed or removed, and is never walked through.
- **`.env` files are not here.** They are the environment's, with their own masking.
- Bounded: a file edited in the console is at most 5 MB, an upload at most 100 MB, a listing is
  cut at 2000 entries and says so.
- An edit or an upload is written beside the file and renamed over it, keeping the mode of the
  file it replaces. The audit trail records who, what and how many bytes, never the content.

Over the API: `GET /api/apps/{domain}/files` (list), `DELETE` (remove), `GET`/`PUT .../content`,
`GET .../download`, `PUT .../upload`, `POST .../directory` and `POST .../move`. In the console,
**Files** is in an application's **More actions** menu; the folder is in the address
(`/apps/shop.example.com/files?path=uploads`).

## Log shipping

The logs of applications, sent besides the journal to Grafana Loki, Axiom, an HTTP endpoint
(Fluent Bit, Vector, Logstash), an OpenTelemetry collector or a remote syslog.

```bash
printf '%s' "$TOKEN" | noust logdrain add grafana --kind loki --url https://logs.example.com \
    --tenant acme --label env=prod --app shop.example.com --token-stdin
noust logdrain list                 # each destination, how it is doing, what it has sent
noust logdrain test grafana         # one line, and what the destination answered
noust logdrain disable grafana
noust logdrain remove grafana
noust logdrain service              # the unit that runs the shipper
```

- **The token is only written.** It is read from standard input (or a hidden prompt), kept in the
  secret store and never shown again; editing a destination without typing one keeps the one it
  has.
- **Each line is scrubbed** of the secrets of its application before it leaves.
- **Without `--app`, all applications.** Docker Compose stacks are shipped too, container by
  container, from where each left off.
- A destination that fails shows what it answered, verbatim. Its position only moves after the
  destination accepted a batch, so the next cycle sends the same lines again instead of losing
  them.
- **Destinations are not trusted by accident.** An address has no user or password in it. A
  plaintext `http` destination is allowed only on this machine (elsewhere it is refused under
  the ENS profile, and otherwise warned about: the console says **Not encrypted**). A syslog over
  `tls` checks the certificate and the name, with a CA file (`--ca`) or a pin (`--pin-sha256`).

Over the API: `GET /api/log-drains` (`settings.read`), and `PUT` and `DELETE
/api/log-drains/{name}` and `POST .../{name}/test` (`settings.manage`). In the console it is
**Log shipping** in the server's settings.

## Changes not applied yet

A variable edited, a memory limit raised, a build command changed: each is stored the moment it
is saved and reaches the process only at the next restart or the next build.

```bash
noust app pending shop.example.com
```

lists what the application is set to that it does not run with yet, and what each change needs:

| Needs | Meaning |
|---|---|
| `restart` | The process picks it up when it starts: variables, limits, the sandbox, mounted files, processes. |
| `rebuild` | It is an input of the build: build commands, container settings, persistent paths, a variable the framework bakes into the bundle. |
| `next_deploy` | It is read at the next activation, with nothing to restart: the health check, the deploy hooks. |

The baseline is taken when a deployment succeeds and when the unit restarts, so it is always the
configuration the application was last put to work with. **The values of secret settings are
never shown and never kept**: a variable, a hook or a build block is compared by a salted hash,
so the answer is "this changed", never "to what". Settings that are not secret (limits, the
health check, the sandbox) say what they were and what they are. The console shows the same list
as a banner at the top of the application, with a button for what a restart applies.
`GET /api/apps/{domain}/pending`.

## Scheduled tasks: what ran and what did not

A scheduled task (`noust cron`) is a systemd timer with `Persistent=true`: when the machine was off at
the moment it was due, systemd runs it **once** at boot. It says nothing about the other moments
that were due and are gone. The ledger sets every moment a schedule fell due against the runs the
journal saw:

```bash
noust cron ledger nightly-backup --days 7
```

| Verdict | Meaning |
|---|---|
| `ran` | A run started within two minutes of the moment it was due. |
| `late` | A later run started before the next moment was due: the run made up at boot is one, and so is a manual `noust cron run`. |
| `missed` | No run started before the next moment. `the server was off` when the journal's boots show the machine was down; otherwise it is left unexplained (the timer was stopped, a run was still going, a start failed). |

- **It judges only from when it can.** Not before the journal begins, not before the timer was
  written (the schedule may have been different), and not before the oldest run it read when the
  journal held more entries than it reads. The answer says from when.
- **A disabled timer has nothing due.** A moment still inside its two minutes is not judged yet.
- **The same facts are in the timeline.** A moment that did not run on time is an event of the
  `jobs` source (`cron_missed`, a warning for a missed one, a notice for a late one), with the task's
  unit and application, so the stretch around an outage shows what it cost.
- `GET /api/cron/{name}/ledger?days=7` (`apps.read`) is the same answer; `--json` prints it.

## Reading a deployment's log

```bash
noust app deploy-log shop.example.com                    # the latest deployment
noust app deploy-log shop.example.com 41 --level error   # only the errors of deployment 41
noust app deploy-log shop.example.com 41 --search prisma --no-timestamps
noust app deploy-log shop.example.com 41 --sanitize      # to paste it somewhere
```

The secrets the build knew are taken out when the log is written. `--sanitize` also masks what
only looks like one (a connection string's password, a token, a key block) and strips control
sequences, which is what the console's **Copy to share** does. `--level warn` keeps warnings
and errors; the same filters are query parameters of `GET /api/deployments/{id}/log`. In the
console a deployment's log has the same filters and says how many lines it hides.
