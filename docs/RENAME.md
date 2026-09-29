# WASM is now Noust: what each channel publishes

From 3.0.0 the product is **Noust**: the command is `noust`, the Python package
is `noust`, and the distribution packages are named `noust`. `wasm` remains a
command alias for the whole 3.x series, so scripts and cron lines keep working.

Every name WASM was published under keeps existing as a **transitional
package**: it contains nothing, depends on `noust` at the same version, and is
published with every release. That is what lets a server running WASM 2.x move
to Noust with the upgrade command it already uses.

## What is published

Everything below is published by `.github/workflows/release.yml` when a `v*` tag
is pushed, and only after every gate in that workflow has passed.

| Channel | The product | Transitional (old name) |
|---|---|---|
| PyPI | [`noust`](https://pypi.org/project/noust/) | [`wasm-cli`](https://pypi.org/project/wasm-cli/): requires `noust==<same version>` and keeps the `wasm` command |
| OBS, Debian and Ubuntu | `home:Perkybeet/noust` builds `noust` | `home:Perkybeet/wasm` builds `wasm`: empty, section `oldlibs`, `Depends: noust` |
| OBS, Fedora and openSUSE | `home:Perkybeet/noust` builds `noust` | `home:Perkybeet/wasm` builds `wasm-cli`: empty, `Requires: noust` |
| GitHub Container Registry | `ghcr.io/perkybeet/noust:<version>` and `:latest`, amd64 and arm64 | none (new in 3.0.0) |
| GitHub Releases | sdist, wheel and the `noust` and `wasm` debs | |

The OBS repository is the same one as before
(`https://download.opensuse.org/repositories/home:/Perkybeet/<distribution>/`):
both OBS packages live in the `home:Perkybeet` project, so a server that already
has the repository configured sees `noust` without any change.

## What an operator does

### pip

```bash
pip install -U wasm-cli     # brings noust in; both commands keep working
pip install noust           # or install it by its name from now on
```

`wasm-cli` 3.x declares the `wasm` command itself. pip installs `noust` first and
only then removes `wasm-cli` 2.x, whose file list includes `bin/wasm`; without
that entry the upgrade deleted the alias `noust` had just installed.

`pip uninstall wasm-cli` afterwards removes `bin/wasm` too, even though `noust`
also provides it. Put it back with `pip install --force-reinstall --no-deps noust`.

With pipx, replace the application: `pipx uninstall wasm-cli && pipx install noust`.

### Debian and Ubuntu

```bash
sudo apt update && sudo apt upgrade
```

`apt upgrade` upgrades `wasm` to the transitional package, which installs
`noust`. Because the transitional package is in section `oldlibs`, apt moves the
"manually installed" mark from `wasm` to `noust`: `apt autoremove` later removes
the empty `wasm` and keeps `noust`.

`apt-get upgrade` (not `apt upgrade`) never installs a new package, so it lists
`wasm` as "kept back". Use `apt upgrade`, `apt-get dist-upgrade` or
`apt install noust`.

On Debian 12 and Ubuntu 22.04 the package does not recommend
`python3-questionary`, which those releases do not ship: an unsatisfiable
Recommends made `apt upgrade` keep `wasm` back instead of bringing `noust` in.

### Fedora and openSUSE

```bash
sudo dnf upgrade            # or: sudo zypper refresh && sudo zypper update
```

The upgrade replaces `wasm-cli` 2.x with the transitional `wasm-cli`, which
requires `noust`. To remove the transitional package later, mark `noust` as
wanted first, or dnf removes it along with `wasm-cli`:

```bash
sudo dnf mark user noust && sudo dnf remove wasm-cli
```

`noust` declares `Conflicts: wasm-cli < 3.0.0~` and deliberately not
`Obsoletes`. With `Obsoletes`, dnf replaced `wasm-cli` 2.x with `noust` outright,
so no `wasm-cli` was left after the transaction and rpm ran the old package's
removal scriptlet in its "last copy removed" branch, which stopped and disabled
`wasm-web` and `wasm-monitor` on every upgraded server.

### The container

```bash
docker pull ghcr.io/perkybeet/noust:<version>
```

`packaging/container/compose.yaml` is a ready example for UGOS Pro and any
Docker host. The image runs the console as the unprivileged user `noust`
(uid 10001) over TLS on port 8443, keeps everything under the `/data` volume and
answers `/health` without a token. Its command is `noust central run`: the
console as a hub, with a self-signed certificate minted under `/data/tls` and
only the private address ranges allowed (see [CENTRAL.md](CENTRAL.md)).

## The configuration during the upgrade

The package upgrade moves the server onto Noust's names: `noust`'s `postinst`
(deb) and `%posttrans` (rpm) run `noust migrate-from-wasm`, which renames
`/etc/wasm`, `/var/lib/wasm` (and `wasm.db` to `noust.db`) and
`/var/backups/wasm`, leaves links at the old names, and replaces `wasm-web`,
`wasm-monitor`, `wasm-cron-*` and `wasm-backup-*` with the same units named
`noust-*` in the same state. The scripts call it explicitly because the
automatic migration on a first privileged run stands aside inside a systemd
unit, which is where unattended-upgrades and dnf-automatic run. A migration
that does not finish does not fail the upgrade; it says so on stderr, and
`noust migrate-from-wasm` finishes it (see `docs/UPGRADING-3.0.md`).

The distribution packages install nothing under `/etc`: the rename of
`/etc/wasm` is atomic only while `/etc/noust` does not exist. The default configuration ships
as a reference at `/usr/share/noust/config.example.yaml`.

Two package-manager behaviours would otherwise lose the operator's
`/etc/wasm/config.yaml` on the way:

- **rpm** renames a modified config file of a package it erases to `.rpmsave`
  (and deletes an unmodified one) before `noust` has run. `noust`'s `%pre` keeps
  a hard link to the file and `%posttrans` puts it back.
- **dpkg** keeps a conffile the new version no longer ships registered to the
  old package, and deletes it when that package is purged, following the
  `/etc/wasm` -> `/etc/noust` link to the operator's real file. The transitional
  `wasm` moves the file aside in its `preinst`, which makes dpkg forget it, and
  `noust`'s `postinst` puts it back before `noust` first runs.

`packaging/obs/upgrade-test.sh` proves all of it in the Release workflow, on
Debian 12 and Fedora 43 with systemd as PID 1 (`packaging/obs/upgrade-in-systemd.sh`):
it installs 2.x from the OBS repository, enables its monitor and console,
upgrades with `INVOCATION_ID` set as a systemd unit would, and checks the links,
the store, the units and the configuration.

## For the owner

**Once, if it is not done yet:** the trusted publisher of the PyPI project
`wasm-cli` must name the renamed repository. On
<https://pypi.org/manage/project/wasm-cli/settings/publishing/>, edit (or add)
the GitHub publisher: owner `Perkybeet`, repository `noust`, workflow
`release.yml`, environment `pypi`. Until then the Release workflow publishes
`noust` and warns that `wasm-cli` was refused (the job does not fail). After
fixing it, open the "Publish to PyPI" job of that run and use "Re-run this job":
`noust` is skipped as already uploaded and `wasm-cli` goes out.

**After the first 3.0.0 release, check:**

- PyPI: <https://pypi.org/project/noust/> and <https://pypi.org/project/wasm-cli/>
  both show the version.
- OBS: <https://build.opensuse.org/package/show/home:Perkybeet/noust> and
  <https://build.opensuse.org/package/show/home:Perkybeet/wasm> build on every
  repository (15 to 30 minutes). The workflow log of "Publish to OBS" lists what
  each package held before and every file it removed.
- GHCR: a new container package is private. On
  <https://github.com/users/Perkybeet/packages/container/package/noust>, Package
  settings, change the visibility to public so `docker pull` works without a
  login.

## Where it lives

| Path | What |
|---|---|
| `rpm/noust.spec`, `obs/noust.dsc`, `obs/debian.*` | The `noust` packages |
| `packaging/transitional/wasm/` | The transitional `wasm` deb and `wasm-cli` rpm (OBS package `wasm`) |
| `packaging/transitional/wasm-cli/` | The transitional PyPI project |
| `packaging/obs/stage.sh` | Exactly the files each OBS package receives; the CI builds use the same set (`--snapshot`: a tree not yet released, as `X.Y.Z~dev`, never published) |
| `packaging/obs/build.sh` | Builds a staged package as OBS does (`debian/` from `debian.*` files only) |
| `packaging/obs/publish.sh` | Replaces an OBS package's sources with a staged set |
| `packaging/obs/upgrade-test.sh` | Upgrades a 2.x server from the OBS repository and checks what it keeps |
| `packaging/obs/upgrade-in-systemd.sh` | Runs that test in a container with systemd as PID 1 |
| `packaging/container/` | The central's image and the compose example |

`scripts/release.py` keeps the version of every one of these in step with
`pyproject.toml`, and writes the transitional packages' changelog entries.
