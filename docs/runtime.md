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
- [Mounted files and paths](#mounted-files-and-paths)
- [Files](#files)
- [Log shipping](#log-shipping)
- [Changes not applied yet](#changes-not-applied-yet)
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
