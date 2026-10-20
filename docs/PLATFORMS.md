# Platforms and the installer

Noust installs on the distributions and processors below, and on no others. The list is one
table in the code (`src/noust/managers/server/platforms.py`); the installer carries a generated
copy of it, `noust setup doctor` judges the machine it runs on against it, and the table in this
page is generated from it, so the three cannot disagree (`scripts/sync_install_targets.py
--check` is a CI gate).

## Install in one line

On a fresh server, as root:

```bash
curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/install.sh
curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/SHA256SUMS
sha256sum -c --ignore-missing SHA256SUMS && sh install.sh
```

`install.sh` is a POSIX `sh` script (it runs on dash and busybox; `shellcheck` is a CI gate). In
this order, and nothing else:

1. reads `/etc/os-release` and `uname -m`, and checks them against the table: a release in the
   table installs (a best-effort one after asking, or with `--yes`); one that is not is refused
   with the reason and exit status 3 (`--force --target <repository directory>` to try anyway);
2. adds the Noust repository, the project `home:Perkybeet` of the openSUSE Build Service, after
   downloading its signing key and checking that it is **one** public key, not expired, with the
   fingerprint pinned in the script (`D9FA6122D7F154D75A588A501945D169A62CFF65`); another key
   stops it before any file is written. The key it trusts afterwards is the file it just
   checked (`/usr/share/keyrings/noust.gpg`, or `/etc/pki/rpm-gpg/RPM-GPG-KEY-noust` imported with
   `rpm --import`), not whatever the server says later;
3. installs the `noust` package with `apt`, `dnf` or `zypper`. The RPM families only *suggest* the
   console's Python packages, which their managers do not install, so it installs them too;
4. runs `noust setup init -y` (nginx, git, certbot, Node.js, Noust's directories) and `noust web
   enable` (the console as a service, on loopback only), unless the server already has them: a
   second run upgrades Noust and leaves the configuration, and the console's token, alone;
5. prints how to reach the console: the SSH tunnel, and `noust console expose` to publish it with
   a certificate ([console.md](console.md#reaching-it)).

It never downloads another script and never pipes anything it fetched into a shell: the only
things it downloads are the repository key (checked) and the packages (signed by that key, checked
by the package manager). Options, in `sh install.sh --help`:

| Option | Variable | What it does |
|--------|----------|--------------|
| `--version X.Y.Z` | `NOUST_VERSION` | Install this version instead of the newest |
| `--dry-run` | `NOUST_DRY_RUN=1` | Print everything it would do, change nothing (needs no root) |
| `--no-console` | `NOUST_NO_CONSOLE=1` | Do not set the console up as a service |
| `--skip-setup` | `NOUST_SKIP_SETUP=1` | Do not run `noust setup init` |
| `--method package\|pip` | `NOUST_METHOD` | Force the way (pip is the default only where no package exists) |
| `--target NAME` | `NOUST_TARGET` | The repository directory to use when the release is not in the table |
| `--force` | `NOUST_FORCE=1` | Go on past a release or an architecture the table refuses |
| `-y`, `--yes` | `NOUST_ASSUME_YES=1` | Answer yes to every question (there is only one) |
| `--admin-user NAME` | `NOUST_ADMIN_USER` | Create this administrator once Noust is installed (nothing happens if it exists) |
| `--admin-password-file FILE` | `NOUST_ADMIN_PASSWORD_FILE` | Where the administrator's password is read from (its first line) |
| | `NOUST_ADMIN_PASSWORD` | The administrator's password, from the environment, when a file is not convenient. Never an argument: `ps` shows every argument to every local user |
| `--json` | `NOUST_JSON=1` | Print one JSON document on standard output (when it ends, and when it fails or refuses) and everything else on standard error |
| `--print-platform` | | Print what it detected as `key=value` lines and stop |
| | `NOUST_REPO_BASE` | A mirror of the repository (https only) |
| | `NOUST_REPO_KEY_FPR` | Replaces the pinned fingerprint, see [key rotation](#when-the-repository-key-is-rotated) |
| | `NOUST_OS_RELEASE`, `NOUST_ARCH` | Replace `/etc/os-release` and `uname -m` (how the table is tested) |

For a script (cloud-init, Ansible): `NOUST_ASSUME_YES=1 sh install.sh --json --admin-user ops
--admin-password-file /root/ops.pw`, which installs Noust, creates the administrator and prints
one line of JSON. [Cloud-init](cloud-init.md) has the whole user-data file.

The JSON document, when it succeeds:

```json
{"status":"ok","dry_run":false,"version":"3.4.0","method":"package",
 "platform":{"id":"ubuntu","version_id":"24.04","arch":"amd64","support":"supported"},
 "console":{"state":"new","address":"127.0.0.1:8080"},
 "admin":{"username":"ops","state":"created"}}
```

`console.state` is `new`, `existing`, `skipped` or `failed`; `admin.state` is `created`,
`existing` (the account was already there and was left alone) or, in a dry run, `would-create`;
`admin` is `null` when none was asked for. A failure is `{"status":"error","error":"..."}` with
exit status 1, and a platform the table refuses is `{"status":"refused",...}` with exit status 3.

## What the status means

| Status | Meaning |
|--------|---------|
| supported | A package from the repository of that release, installed by the system's package manager, and run by the installer matrix below |
| best-effort | Installs, is not promised: an interim release, a release past its end of life, or a newer one that borrows the previous release's repository. The installer asks first, and `noust setup doctor` says so |
| pip | No package exists for it: the installer builds a virtual environment in `/opt/noust` with pip and links `noust` into `/usr/local/bin` |
| unsupported | Refused. The reason is in the table, or "not a release Noust declares support for" |

A derivative that names a parent in `ID_LIKE` (Pop!_OS 22.04 says `ubuntu debian`) is judged as the
parent and is never better than best-effort. A supported release past the end-of-life date its
distribution gives (`SUPPORT_END`, or Noust's own table) is best-effort from that day.

## Distributions

<!-- BEGIN TARGETS (generated: scripts/sync_install_targets.py) -->

| Distribution | Status | Packages from | Tested | Notes |
|---|---|---|---|---|
| Debian 12 (Bookworm) | supported | `Debian_12` | install:amd64, install:arm64 |  |
| Debian 13 (Trixie) | supported | `Debian_13` | install:amd64 |  |
| Debian 11 (Bullseye) | unsupported | - | not tested | Python 3.9; Noust needs 3.10 or later. |
| Ubuntu 22.04 LTS | supported | `xUbuntu_22.04` | install:amd64 |  |
| Ubuntu 24.04 LTS | supported | `xUbuntu_24.04` | install:amd64, harness:amd64 |  |
| Ubuntu 26.04 LTS | supported | `xUbuntu_26.04` | install:amd64 |  |
| Ubuntu 24.10, 25.04 and 25.10 | unsupported | - | not tested | Interim releases, all past their end of life: use an LTS release. |
| Ubuntu 20.04 LTS | unsupported | - | not tested | Python 3.8; Noust needs 3.10 or later. |
| Fedora 43 | supported | `Fedora_43` | install:amd64 |  |
| Fedora 42 | best-effort | `Fedora_42` | install:amd64 | Past its end of life (2026-05-27); its repository is kept for now. |
| Fedora 44 | best-effort | `Fedora_43` | install:amd64 | No Fedora 44 repository is published yet: the Fedora 43 one is used (noarch packages). |
| Fedora 40 and 41 | unsupported | - | not tested | End of life. |
| openSUSE Leap 16.0 | supported | `16.0` | install:amd64 |  |
| openSUSE Leap 15.6 | best-effort | `15.6` | install:amd64 | Past its end of life (2026-04-30); its repository is kept for now. |
| openSUSE Tumbleweed | supported | `openSUSE_Tumbleweed` | install:amd64 |  |
| Rocky Linux 9 and 10 | pip | pip | install:amd64 | OBS has no RHEL-family target: Noust is installed with pip into /opt/noust. |
| AlmaLinux 9 and 10 | pip | pip | install:amd64 | OBS has no RHEL-family target: Noust is installed with pip into /opt/noust. |
| Red Hat Enterprise Linux 9 and 10 | pip | pip | install:amd64 | Installed with pip into /opt/noust like Rocky and AlmaLinux; run in Red Hat's UBI 9 and 10 images, not on a subscribed host. |
| CentOS Stream 9 and 10 | pip | pip | install:amd64 | Installed with pip into /opt/noust like Rocky and AlmaLinux; run in the Stream 9 and 10 images. |
| Oracle Linux 9 and 10 | pip | pip | install:amd64 | Installed with pip into /opt/noust like Rocky and AlmaLinux; run in the Oracle Linux 9 and 10 images. |
| Arch Linux | unsupported | - | not tested | Rolling release with systemd, so it could run Noust, but Noust has no pacman support yet: neither its installer, its updates nor its setup would work. |
| Alpine Linux | unsupported | - | not tested | No systemd (OpenRC) and musl: Noust runs every application as a systemd unit. |

<!-- END TARGETS -->

`Packages from` is the directory of the repository (`https://download.opensuse.org/repositories/home:/Perkybeet/<directory>/`).
openSUSE Leap's directories are `15.6` and `16.0`, which is what OBS calls them; the longer
`openSUSE_Leap_15.6` of older instructions only works because the download server redirects it.
The packages are `noarch` (RPM) and `all` (Debian): the same file serves every processor below.

## Processors

| Architecture | Also called | Notes |
|--------------|-------------|-------|
| amd64 | x86_64 | Servers and virtual machines |
| arm64 | aarch64 | Ampere and Graviton servers, and the Raspberry Pi 3, 4 and 5 with **Raspberry Pi OS 64-bit** (which says `ID=debian`, so it is Debian 12 or 13) |

Not supported, and refused by the installer: 32-bit ARM (`armv7l`, `armhf`, including the 32-bit
Raspberry Pi OS, whose `ID` is `raspbian`) and 32-bit x86. Debian's `Release` file lists `i386
amd64` only; an arm64 host still gets the package, because apt takes an architecture-independent
(`all`) package from the index it does fetch: the installer matrix proves it on Debian 12 under
emulation (below).

## What is tested, and where

The `Tested` column of the table says where a release was actually run, with the published
`3.3.1` package. Three levels, each stronger and narrower than the one before:

- **install**: `tests/integration/install_matrix.py` (also `tests/integration/run.py
  --install-matrix`). One throwaway container per release: `--print-platform` and `--dry-run`, a
  repository key with another fingerprint is refused **and nothing is written**, the real install
  from the real OBS repository with the key checked, `noust --version`, and
  a second run that must succeed. The containers have no systemd, so `noust setup init` and the
  console are not part of it. `install:arm64` is run under QEMU on an amd64 host.
- **harness**: the scenario suite of `tests/integration/run.py`, in a container with systemd as PID
  1 (real nginx, real systemd, a real store), on Ubuntu 24.04 amd64 only. Its scenario
  `installer_platform_and_dry_run` is the installer's detection and refusals on that system.
- **unit tests**: `tests/test_platforms.py` checks every row of the table against a real
  `os-release` of that release, derivatives, 32-bit machines and end-of-life dates, that the
  installer and this page carry the current table, and that the script is POSIX `sh` that
  downloads nothing it runs.

What is **not** tested, so that nobody reads more into the table than is in it: a real
Raspberry Pi (arm64 is emulated, so speed, and anything that depends on the board, are not
measured); `noust setup init` and the console on any release but Ubuntu 24.04 (they run on a
machine booted with systemd, which a container is not); Let's Encrypt, which needs a real name.
The release that is declared and not tested says `not tested`.

## The RHEL family

Rocky Linux, AlmaLinux, CentOS Stream and Red Hat Enterprise Linux 9 and 10 have **no package**:
the openSUSE Build Service offers no RHEL-family target (its list of distributions, checked on
2026-10-06, has Fedora, openSUSE, Debian, Ubuntu, Raspbian, Arch, Amazon Linux and old CentOS and
RHEL 5 to 8, nothing for 9 or 10), and the Fedora packages cannot be installed there because EL 9's
Python 3.9 packages are not the Python 3.10 or later that Noust needs. So the installer does what
works: it installs Python 3.12 from AppStream, builds `/opt/noust` with `python -m venv` and `pip
install 'noust[all]'`, and links the commands. Noust's own updates then go through pip
(`noust update` reads how it was installed). `noust setup init` installs nginx, git and Node.js with
`dnf`; certbot is not in the base repositories, so `epel-release` has to be enabled for it.
The `Tested` column says how far that was run.

## Verifying the installer

Each release publishes `install.sh`, `SHA256SUMS` (its checksum) and `SHA256SUMS.sig` with
`SHA256SUMS.pem` (a keyless signature by the release workflow, through Sigstore). The one-line
command above checks the checksum. To check the signature too, which proves the checksum was written
by this repository's release workflow for a tag and not by someone who replaced both files:

```bash
curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/SHA256SUMS.sig
curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/SHA256SUMS.pem
cosign verify-blob \
  --certificate SHA256SUMS.pem --signature SHA256SUMS.sig \
  --certificate-identity-regexp '^https://github.com/Perkybeet/noust/\.github/workflows/release\.yml@refs/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  SHA256SUMS
```

To check the repository key's fingerprint yourself, independently of the script:

```bash
curl -fsSL https://download.opensuse.org/repositories/home:/Perkybeet/Debian_12/Release.key | gpg --show-keys --fingerprint
```

## When the repository key is rotated

OBS signs the whole project with one key (RSA 4096, created 2025-12-18, expires 2028-02-26). When
it is replaced, a script that pins the old fingerprint refuses the new key, which is the point. The
new fingerprint goes into `REPOSITORY_KEY_FINGERPRINT` in `platforms.py`, `scripts/sync_install_targets.py`
copies it into the script, and a release carries it **before** the old key expires. An operator
who must move first, and has confirmed the new fingerprint with the maintainers, sets
`NOUST_REPO_KEY_FPR=<the 40 hexadecimal digits>`.

## Adding a release

1. Add the target to the OBS project (`osc meta prj -e home:Perkybeet`; the release must be in
   OBS's own list of distributions) and wait for its build.
2. Add a `Target` to `TARGETS` in `platforms.py`, run `scripts/sync_install_targets.py`, add the
   image to `CASES` in `tests/integration/install_matrix.py` and run it for that image.
3. Put what was run in the row's `tested`, and say what is not promised in its `note`.

Fedora 44 and Raspbian are in OBS's list of distributions and **not** enabled in `home:Perkybeet`
yet: until a Fedora 44 target is built, the table has it best-effort on the Fedora 43 repository,
and a 32-bit Raspberry Pi OS stays refused.
