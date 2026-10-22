# Requirements

What a server needs to run Noust, and what Noust itself costs when it is doing nothing. The second
is measured, and the method is below so anyone can repeat it.

## The server

| | |
|---|---|
| **Operating system** | Debian 12 and 13, Ubuntu 22.04, 24.04 and 26.04, Fedora, openSUSE Leap and Tumbleweed; the RHEL family through pip. [platforms.md](PLATFORMS.md) says what each is and what was tested where. |
| **Processor** | amd64 and arm64, Raspberry Pi 64-bit included. |
| **Privileges** | root. Noust writes to `/etc`, `/var` and systemd. |
| **systemd** | Applications, the console and the monitor run as units. |
| **Python** | 3.10 to 3.14. The packages bring the interpreter; the installer uses the system's. |
| **Installed by `noust setup init` when missing** | nginx or Apache, certbot, git, Node.js. |
| **Per application type** | `python3-venv` for Python, PHP-FPM and its extensions for PHP, Docker with the Compose plugin for Compose and container applications, the engine for a database (`noust db install`), rclone for remote backup destinations. |
| **Network** | Ports 80 and 443 open from the internet (80 is how Let's Encrypt validates a name). Outbound: the Git hosts you deploy from, Let's Encrypt, the package repositories (`download.opensuse.org`), and GitHub for update checks (`updates.check` turns that off). The console listens on `127.0.0.1:8080` only, unless you publish it. |

`noust setup doctor` checks the list against the machine it runs on and prints the command that
fixes each gap.

## How big a server

Three sizes for each resource, because "does it fit" has three answers. Below the **floor** Noust
or its own installation stops working, so the installer refuses (`--force` tries anyway) and
`noust setup doctor` reports a failure. Below the **minimum** a small server is under-sized: the
installer says so and asks (`--yes` accepts), and the doctor warns. The **recommended** size is
what the figures below are comfortable at; nothing is judged against it.

<!-- BEGIN REQUIREMENTS (generated: scripts/sync_install_targets.py) -->

| | Floor: below it Noust is not installed | Minimum: below it the installer asks and `noust setup doctor` warns | Recommended |
|---|---|---|---|
| **Memory** | 512 MB | 1 GB | 2 GB |
| **Processors** | 1 processor | 1 processor | 2 processors |
| **Free disk** | 3 GB | 10 GB | 20 GB |

<!-- END REQUIREMENTS -->

The numbers are declared once, in `noust/managers/server/requirements.py`, and the installer and
this table are generated from them. Free disk is read where Noust writes (the applications'
directory, its own data and the system packages), not "the root": a server whose `/var` is a
volume of its own is judged by that volume. Memory is the kernel's, or the container's limit when
that is lower.

`noust setup doctor` (and **Settings > About Noust > Installation check**) shows the machine against
these sizes and says **who holds ports 80 and 443**: the web server, a container that publishes
them (another platform's proxy, a Compose stack), or another program such as Caddy or Apache,
with the command that frees them. The installer asks the same before it installs the web server.

## What Noust costs when it is idle

Noust has two long-running processes: the console (`noust-web`) and the monitor
(`noust-monitor`). Everything else it does happens in commands and jobs that finish.

| Process | Memory (median) | Memory (peak) | Processor | Threads | Open files |
|---|---|---|---|---|---|
| `noust-web` | 189.9 MB | 189.9 MB | 2.2 % of one core | 11 | 30 |
| `noust-monitor` | 96.9 MB | 149.2 MB | 0.13 % of one core | 3 | 21 |

Measured over 60 seconds after a 120 second warm-up, on the two units as the installer leaves them
(`install.sh --method pip` on Ubuntu 24.04 with systemd, Python 3.12.3, in a container on a
14-processor machine with 19.5 GB; no applications deployed). So **Noust is about 290 MB of resident
memory (about 340 MB at the monitor's peak) and a few percent of one core when nothing is
happening**. That leaves a server with 1 GB more than 650 MB for the web server and the
applications, and one at the 512 MB floor about 170 MB: enough to run Noust, not to build (add swap,
below).

How it is measured, so you can repeat it on your own server:

```bash
python scripts/measure_idle.py --attach --warmup 120 --seconds 60 --markdown
```

With `--attach` the script finds `noust-web` and `noust-monitor` through systemd and reads the
resident memory of each process and its children, the processor time used, the threads and the open
files from the kernel once a second, for the window you give. Without it the script starts its own
copy of the console (`scripts/console_server.py`: the same FastAPI application under uvicorn, over a
seeded store, with a model of systemd instead of the machine) and `noust monitor run` in a scratch
directory, which is how a development machine measures them and gave smaller numbers (about 210 MB in
all). Two cautions: the console's number grows with the store, so a server with hundreds of
applications keeps more in memory; and the monitor's grows with what it samples (applications and
processes).

## What your applications need

That is up to the applications, not to Noust. Two things are worth knowing:

- **Builds are the heaviest thing a small server does.** A JavaScript framework's production build
  can use well over a gigabyte for a minute or two; this is a property of those tools and varies a
  lot, so it is not a promise. On a server with 1 GB, add swap (`noust server swap create`) so a
  build that needs more memory than the machine has slows down instead of being killed
  ([troubleshooting.md](troubleshooting.md#a-build-or-a-deploy-failed)).
- **Every deploy is a release on disk.** A release keeps the code and its installed dependencies;
  Noust keeps a few and removes older ones (`noust releases keep`). `noust server storage` shows what
  takes the disk, and `noust server cleanup` removes what it can prove is unused.

## Disk

- Noust itself is about 15 MB installed (the Debian package's installed size is 14.3 MB).
- Its store, secrets and metrics are in `/var/lib/noust`; the metrics history is bounded by retention
  settings ([MONITOR.md](MONITOR.md)).
- Backups are in `/var/backups/noust` (change it with `backup.directory`), and a schedule's retention
  bounds them; copies off the server are the real protection ([disaster-recovery.md](disaster-recovery.md)).
- Docker images and volumes are Docker's, under `/var/lib/docker`.
