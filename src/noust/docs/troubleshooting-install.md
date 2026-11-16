# The installation did not work

The installer stops at the first thing that goes wrong, says what it was on a line that starts with
`error:`, and changes nothing after it. Exit status 3 means it refused the platform or the machine;
1 means a step failed. To see every step without doing any, run `sh install.sh --dry-run`.

Once Noust is installed, this lists what deployments need and the command that fixes each missing
piece:

```bash
noust setup doctor
```

In the console, the same list is under **Settings > About Noust > Installation check**.

## The platform is refused

```text
error: Noust does not install on Debian GNU/Linux 11 (bullseye) (amd64).
Debian 11 (Bullseye): Python 3.9; Noust needs 3.10 or later.
The platforms it does install on are listed in docs/PLATFORMS.md.
To try anyway, run it again with --force and, for a package, --target <repository directory>.
```

The distribution, its release or the processor is not on [the list](PLATFORMS.md); the second line
says why. Install on a release that is, or try at your own risk with `--force`.

## The machine is too small

```text
warning: Memory is 384 MB, under the 512 MB Noust needs to run. Builds are what need it; a swap file helps (noust server swap create).
error: This machine is smaller than Noust can run on.
The sizes are in docs/requirements.md. Run it again with --force to try anyway.
```

The warnings above the error say which size is short: memory, processors or free disk. Below
1 GB of memory or 10 GB of free disk, the installer warns and asks; below 512 MB or 3 GB, it
refuses. [Requirements](requirements.md) has the sizes, and what a build needs
on top.

## The repository key is not the expected one

```text
error: The repository key does not have the expected fingerprint. Nothing was installed.
```

The key the package repository serves is not the one the installer pins. Stop there: it can be a
rotated key or a wrong one. [When the repository key is rotated](PLATFORMS.md#when-the-repository-key-is-rotated)
says how to tell them apart.

## There is no package for the release

```text
error: There is no package for AlmaLinux 9.4 (Seafoam Ocelot).
Use --method pip, or name a repository directory with --target if you know one fits.
```

You asked for a package (`--method package`) on a release that has none. Leave the option out:
on the RHEL family the installer uses pip by itself, in a virtual environment under `/opt/noust`,
and links `noust` into `/usr/local/bin`.

## It is not run as root

```text
error: Noust installs system packages and writes to /etc: run this as root.
```

Run it as root, or with `sudo sh install.sh`.

## `noust: command not found`

After the one-line installer, `noust` is in `/usr/bin` (a package) or `/usr/local/bin` (pip). If you
installed with `pip` yourself, it is on the `PATH` of the user who ran pip, not necessarily root's.

## Preparing the server failed

```text
error: noust setup init did not finish.
Fix what it reports, then run: noust setup init -y
```

The lines above it are what failed, usually the package manager's own words (a mirror that does not
answer, a lock held by an automatic update). Fix it and run `noust setup init -y`.

## The console did not start

```bash
noust web install    # the console's Python packages
noust web enable     # run it as a service
```

## The package for the new version is not there yet

The packages are built after a release, one repository per distribution, which takes 15 to 30
minutes. See [An update says it is on the way](troubleshooting-update.md).
