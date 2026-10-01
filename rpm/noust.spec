# spec file for package noust
#
# Copyright (c) 2024-2026 Yago López Prado
# License: AGPL-3.0-or-later
#
# Noust was called WASM until 3.0.0 and this package was named wasm-cli. That
# name lives on as a transitional package, built from packaging/transitional/wasm
# in the OBS package home:Perkybeet/wasm: an empty wasm-cli that Requires noust,
# so `dnf upgrade` and `zypper up` bring noust in as an ordinary upgrade.
#

Name:           noust
Version:        3.1.16
Release:        1%{?dist}
Summary:        Deploy and manage web applications on Linux servers
License:        AGPL-3.0-or-later
URL:            https://github.com/Perkybeet/noust
Source0:        noust-%{version}.tar.gz
BuildArch:      noarch

# Conflicts, not Obsoletes, and on purpose. With Obsoletes the solver replaces
# wasm-cli 2.x with noust outright and never installs the transitional package,
# so no wasm-cli is left after the transaction and rpm runs the old package's
# %%preun with $1 = 0, its "last copy removed" branch: on every upgraded server
# that stopped and disabled wasm-web and wasm-monitor and stopped the console.
# A conflict makes the solver upgrade wasm-cli to the transitional package
# instead, where the old %%preun sees $1 = 1 and leaves the services alone.
# The ~ takes in every 2.x and no pre-release of 3.0.0 (stage.sh --snapshot).
Conflicts:      wasm-cli < 3.0.0~

# On Leap 15.x, python3 is 3.6. It cannot parse this code, so building against
# it produced a package that installed and then failed with SyntaxError on the
# first command, which is worse than not building. The 3.11 flavor is packaged
# there, so name it explicitly; %%python3_sitelib follows %%__python3.
%if 0%{?suse_version} && 0%{?suse_version} < 1600
%define __python3 /usr/bin/python3.11
%define python3_pkgversion 311
%else
%define python3_pkgversion 3
%endif

# Build requirements. Deliberately only what builds a wheel: nothing here runs
# the package, so no runtime import is needed at build time.
BuildRequires:  python%{python3_pkgversion}-devel
BuildRequires:  python%{python3_pkgversion}-setuptools
BuildRequires:  python%{python3_pkgversion}-pip
BuildRequires:  python%{python3_pkgversion}-wheel

# %%py3_build, %%py3_install and %%python3_sitelib live here. python3-devel
# happens to pull it in, but python311-devel does not, and on Leap 15.x the
# build then ran the literal string "%%py3_build" as a shell command.
%if 0%{?suse_version}
BuildRequires:  python-rpm-macros
%endif

# Fedora/RHEL specific
%if 0%{?fedora} || 0%{?rhel}
Requires:       python3-click >= 8.0
Requires:       python3-jinja2 >= 3.1.0
Requires:       python3-pyyaml >= 6.0
Requires:       python3-rich
# questionary reached Fedora in 42. On 40 and 41 the package built and then
# refused to install: the automatic generator turns the wheel metadata into
# python3.Xdist(questionary), and nothing there provides it. Filter that and ask
# for it softly. Interactive mode checks for it and says so when it is missing;
# every other command works without it.
%if 0%{?fedora} && 0%{?fedora} < 42
%global __requires_exclude ^python3\\.[0-9]+dist\\(questionary\\)
Recommends:     python3-questionary
%else
Requires:       python3-questionary
%endif
# The panel is optional. Everything it needs is packaged in Fedora.
Suggests:       python3-fastapi
Suggests:       python3-starlette
Suggests:       python3-pydantic
Suggests:       python3-uvicorn
# The monitor, which records the charts' history and which the package enables
# by default ('noust monitor autoenable'), cannot run without it.
Recommends:     python3-psutil
# A central's proxy to its nodes (HTTP and WebSockets through each tunnel).
Suggests:       python3-httpx
Suggests:       python3-websockets
# Passkeys: the signature check (imported only when a passkey is verified).
Suggests:       python3-cryptography
# Remote backup destinations (noust backup destination); nothing else needs it.
Suggests:       rclone
# No separate venv package is Required here: python3-libs, pulled in
# transitively by python3 above, contains the venv module itself, which is
# what noust.deployers.python's 'python3 -m venv' needs. That call does not
# pass --without-pip, so it also runs ensurepip to seed the new virtualenv
# with pip; on Fedora, ensurepip's bundled wheels are unbundled into the
# python3-pip-wheel package, but its presence is not otherwise guaranteed and
# its availability across every RHEL derivative sharing this %if branch is
# not confirmed. A Requires on a name that does not exist on one of them
# breaks installation on all of them, so this is documented rather than
# declared: if 'python3 -m venv' ever fails to seed pip on a supported
# Fedora/RHEL release, install python3-pip-wheel (Fedora) or python3-pip
# (RHEL) by hand and revisit this comment.
%endif

# openSUSE specific. The package names are capitalised there, and on Leap 15.x
# they carry the flavor prefix.
%if 0%{?suse_version}
Requires:       python%{python3_pkgversion}-click >= 8.0
Requires:       python%{python3_pkgversion}-Jinja2 >= 3.1.0
Requires:       python%{python3_pkgversion}-PyYAML >= 6.0
Requires:       python%{python3_pkgversion}-rich
Suggests:       python%{python3_pkgversion}-fastapi
Suggests:       python%{python3_pkgversion}-starlette
Suggests:       python%{python3_pkgversion}-pydantic
Suggests:       python%{python3_pkgversion}-uvicorn
Recommends:     python%{python3_pkgversion}-psutil
Suggests:       python%{python3_pkgversion}-httpx
Suggests:       python%{python3_pkgversion}-websockets
Suggests:       python%{python3_pkgversion}-cryptography
Suggests:       rclone
%if 0%{?suse_version} < 1600
Requires:       python311
# questionary is not packaged for 3.11 on Leap. Interactive mode already checks
# for it and explains its absence, and every other command works without it, so
# it is recommended rather than required.
Recommends:     python311-questionary
%else
Requires:       python3 >= 3.10
Requires:       python3-questionary
%endif
# openSUSE does not split the venv module, or the ensurepip bootstrap it runs
# by default, out of the base python3 (or python311, on Leap 15.x) package the
# way Fedora does, so noust.deployers.python's 'python3 -m venv' works with
# nothing beyond the Requires already declared above.
%endif

# Runtime requirements (common)
%if 0%{?fedora} || 0%{?rhel}
Requires:       python3 >= 3.10
%endif

# Suggested packages (not required for installation)
Suggests:       nginx
Suggests:       certbot
Suggests:       git
Suggests:       nodejs
Suggests:       npm

%description
Noust deploys and manages web applications on Linux servers. It configures
Nginx or Apache, obtains and renews TLS certificates, supervises systemd
services, manages databases and backups, and builds every deploy as a
health-gated release with instant rollback. An optional browser console
serves the same operations over a JSON API.

Noust was called WASM until 3.0.0. The command is noust; wasm remains an
alias for the whole 3.x series, so existing scripts and cron lines keep
working.

%prep
%autosetup -n noust-%{version}

%build
# The pyproject macros where they exist (Fedora deprecated %%py3_build in 43 and
# the openSUSE guidelines forbid the legacy equivalents), and the old ones on
# the distributions that do not have them yet.
#
# Every %% above is doubled on purpose. A macro named in a comment inside a
# scriptlet is still expanded, and these expand to several lines: the # only
# comments out the first one and rpm runs the rest. That is what broke all
# thirteen RPM targets in 1.0.3, where this comment ran setup.py with the
# words "in 43 and" as its arguments.
#
# The branch is on the distribution, not on whether the macro exists. openSUSE
# defines %%pyproject_wheel too, but wires it to whichever Python flavor is
# primary that week (python314 as of writing) rather than to the interpreter
# python3-devel installs, so the build asked for /usr/bin/python3.14 and there
# was none. %%py3_build uses %%{__python3}, which is the one that is there.
# Refuse to build against an interpreter that cannot run the result. Without
# this, Leap 15.6 built a package against Python 3.6 and published it; it
# installed cleanly and raised SyntaxError on the first command.
%{__python3} -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || { \
    echo "noust needs Python 3.10 or newer, and %{__python3} is older."; \
    exit 1; }

%if 0%{?fedora} || 0%{?rhel}
%pyproject_wheel
%else
%py3_build
%endif

%install
%if 0%{?fedora} || 0%{?rhel}
%pyproject_install
%else
%py3_install
%endif

# Shell completion, for both names. Committed, not generated here: Click's
# scripts contain no command names, so they cannot drift, and running Python
# during the build made every runtime import a build dependency.
install -Dm644 src/noust/completions/noust.bash %{buildroot}%{_datadir}/bash-completion/completions/noust
install -Dm644 src/noust/completions/wasm.bash %{buildroot}%{_datadir}/bash-completion/completions/wasm

%if ! 0%{?suse_version}
install -Dm644 src/noust/completions/noust.fish %{buildroot}%{_datadir}/fish/vendor_completions.d/noust.fish
install -Dm644 src/noust/completions/wasm.fish %{buildroot}%{_datadir}/fish/vendor_completions.d/wasm.fish
install -Dm644 src/noust/completions/_noust %{buildroot}%{_datadir}/zsh/site-functions/_noust
install -Dm644 src/noust/completions/_wasm %{buildroot}%{_datadir}/zsh/site-functions/_wasm
%endif

# The manual, and `man wasm` for the alias.
install -Dm644 man/noust.1 %{buildroot}%{_mandir}/man1/noust.1
ln -sf noust.1 %{buildroot}%{_mandir}/man1/wasm.1

# The default configuration, as a reference only. Nothing is installed under
# /etc/noust: on a server coming from WASM 2.x, noust moves /etc/wasm to
# /etc/noust the first time it runs as root, and that rename is atomic only
# while /etc/noust does not exist. A packaged config.yaml there would also be
# a second, stale copy of the defaults noust.core.config already carries (the
# one OBS shipped until 2.3 still enabled the AI monitor removed in 1.x).
# noust writes /etc/noust/config.yaml itself, 0600 in a 0700 directory, when
# `noust setup` or `noust config` first needs it.
install -Dm644 obs/noust.default.yaml %{buildroot}%{_datadir}/noust/config.example.yaml

# noust-specific directories only (not /var/www or /var/backups)
install -d %{buildroot}/var/log/noust

%files
%license LICENSE
%doc README.md
%doc docs/
%doc docs/UPGRADING-3.0.md
%doc docs/UPGRADING-3.1.md
%{python3_sitelib}/noust/
%{python3_sitelib}/noust-*
%{_bindir}/noust
%{_bindir}/wasm
%{_mandir}/man1/noust.1*
%{_mandir}/man1/wasm.1*
%{_datadir}/noust/
%{_datadir}/bash-completion/completions/noust
%{_datadir}/bash-completion/completions/wasm
%if ! 0%{?suse_version}
%{_datadir}/fish/vendor_completions.d/noust.fish
%{_datadir}/fish/vendor_completions.d/wasm.fish
%{_datadir}/zsh/site-functions/_noust
%{_datadir}/zsh/site-functions/_wasm
%endif
%dir /var/log/noust

%pre
# Coming from wasm-cli 2.x, rpm erases the old package after installing this
# one, and erasing it renames a modified /etc/wasm/config.yaml to .rpmsave (or
# deletes an unmodified one) before noust has had the chance to move /etc/wasm
# to /etc/noust. A hard link keeps the operator's file whatever rpm does to the
# name, and %%posttrans puts it back. Only while /etc/wasm is a real directory:
# once moved it is a link to /etc/noust, which no old package owns.
if [ -f /etc/wasm/config.yaml ] && [ ! -L /etc/wasm ] && [ ! -L /etc/wasm/config.yaml ]; then
    rm -f /etc/wasm/config.yaml.noust-keep
    ln /etc/wasm/config.yaml /etc/wasm/config.yaml.noust-keep || :
fi

%post
echo "Noust installed. Run 'noust setup' to configure it."

%preun
# $1 is 0 only when this is the last version being removed, never on an
# upgrade (where it is 1 or more, and the units are meant to survive the
# version bump untouched).
if [ $1 -eq 0 ]; then
    # 'noust monitor install' and 'noust web enable' write and enable these
    # units at runtime; this spec never packages them, so rpm has none of its
    # own to stop here and the daemons kept running under a binary that had
    # just been removed. Both names: the wasm-* ones are what a server keeps
    # until noust first runs as root and renames them. Data (the
    # configuration, /var/lib/noust, deployed applications) is untouched.
    systemctl stop noust-monitor.service >/dev/null 2>&1 || :
    systemctl disable noust-monitor.service >/dev/null 2>&1 || :
    systemctl stop wasm-monitor.service >/dev/null 2>&1 || :
    systemctl disable wasm-monitor.service >/dev/null 2>&1 || :

    systemctl stop noust-web.service >/dev/null 2>&1 || :
    systemctl disable noust-web.service >/dev/null 2>&1 || :
    systemctl stop wasm-web.service >/dev/null 2>&1 || :
    systemctl disable wasm-web.service >/dev/null 2>&1 || :

    # Without 'noust web enable' the console runs as a daemon (noust web start
    # -d), not as a unit; stop it while its binary still exists.
    if [ -x /usr/bin/noust ]; then
        /usr/bin/noust web stop --reason "package removal" >/dev/null 2>&1 || :
    fi
fi

%posttrans
# %%posttrans runs once the whole transaction is done, wasm-cli 2.x's files
# removed included, which is why everything that runs noust is here and not in
# %%post: first the configuration %%pre kept is put back, then noust runs.
if [ -f /etc/wasm/config.yaml.noust-keep ]; then
    if [ ! -e /etc/wasm/config.yaml ]; then
        mv /etc/wasm/config.yaml.noust-keep /etc/wasm/config.yaml
    else
        rm -f /etc/wasm/config.yaml.noust-keep
    fi
    # The copy rpm made of the same file when it erased wasm-cli 2.x.
    if [ /etc/wasm/config.yaml.rpmsave -ef /etc/wasm/config.yaml ]; then
        rm -f /etc/wasm/config.yaml.rpmsave
    fi
fi

# Belt and suspenders: the configuration directory holds config.yaml's
# credentials and, when the console is used, noust.web.auth's signing key and
# token hash, so both are tightened on every install and upgrade. Setting the
# exact target mode can only narrow permissions or leave them unchanged. The
# /etc/wasm lines cover a server noust has not yet moved to /etc/noust.
if [ -d /etc/noust ] && [ ! -L /etc/noust ]; then
    chown root:root /etc/noust
    chmod 0700 /etc/noust
fi
if [ -f /etc/noust/config.yaml ]; then
    chown root:root /etc/noust/config.yaml
    chmod 0600 /etc/noust/config.yaml
fi
if [ -d /etc/wasm ] && [ ! -L /etc/wasm ]; then
    chown root:root /etc/wasm
    chmod 0700 /etc/wasm
fi
if [ -f /etc/wasm/config.yaml ] && [ ! -L /etc/wasm/config.yaml ]; then
    chown root:root /etc/wasm/config.yaml
    chmod 0600 /etc/wasm/config.yaml
fi

# A server coming from WASM 2.x moves onto Noust's names here: /etc, /var/lib
# and /var/backups renamed (links left at the old names) and wasm-web,
# wasm-monitor, wasm-cron-*, wasm-backup-* replaced by the same units named
# noust-*, in the same state. Explicitly, because the automatic migration on a
# first privileged run is skipped inside a systemd unit, where unattended
# upgrades (dnf-automatic) run. A migration that does not finish must not fail
# the transaction: noust keeps reading the old locations, and says so, until
# it is re-run.
if { [ -d /etc/wasm ] && [ ! -L /etc/wasm ]; } \
        || { [ -d /var/lib/wasm ] && [ ! -L /var/lib/wasm ]; } \
        || { [ -d /var/backups/wasm ] && [ ! -L /var/backups/wasm ]; } \
        || [ -e /etc/systemd/system/wasm-web.service ] \
        || [ -e /etc/systemd/system/wasm-monitor.service ]; then
    echo "Moving this server from WASM's names to Noust's..."
    /usr/bin/noust migrate-from-wasm --reason "package upgrade" || echo "noust: the migration from wasm did not finish; run 'noust migrate-from-wasm' (see %{_docdir}/noust/UPGRADING-3.0.md)" >&2
fi

# Add new defaults to an existing configuration (user values are kept).
if [ -f /etc/noust/config.yaml ] || [ -f /etc/wasm/config.yaml ]; then
    /usr/bin/noust config upgrade --reason "package upgrade" --quiet >/dev/null 2>&1 || :
    # Remove the settings no version reads any more (the old AI monitor's
    # OpenAI key among them), as text and with a dated 0600 copy first.
    /usr/bin/noust config clean --reason "package upgrade" --quiet >/dev/null 2>&1 || :
fi

# Rewrite the monitor unit for this version if it is enabled. Only under its
# own name: 'noust monitor install' refuses while wasm-monitor.service exists,
# which after the migration above means the migration did not finish, and it
# says so already.
if systemctl is-enabled noust-monitor.service >/dev/null 2>&1; then
    /usr/bin/noust monitor install --reason "package upgrade" >/dev/null 2>&1 || :
    systemctl daemon-reload >/dev/null 2>&1 || :
    systemctl try-restart noust-monitor.service >/dev/null 2>&1 || :
elif systemctl is-enabled wasm-monitor.service >/dev/null 2>&1; then
    systemctl try-restart wasm-monitor.service >/dev/null 2>&1 || :
fi

# The charts' history is recorded by the monitor, so a server gets it without
# the operator having to know: installed and enabled on a first install, and on
# an upgrade that finds none. It never overrides a decision - a monitor that is
# installed stays as it is, and one an operator disabled or removed stays off -
# and it does nothing where there is no systemd. 'noust monitor autoenable'
# makes that call in one place; it must not fail the transaction.
if [ -d /run/systemd/system ]; then
    /usr/bin/noust monitor autoenable --reason "package upgrade" >/dev/null 2>&1 || :
fi

# 'noust web enable' runs the console as a unit, and %%preun leaves it running
# across an upgrade, still serving the code the upgrade replaced. try-restart
# only restarts a unit that is running: a console the operator stopped stays
# stopped, and a first install has none running.
# The unit is rewritten from this version's template first (keeping where the
# console listens): only 'noust web enable' wrote it before, so a fix to the
# unit never reached a server that had enabled the console already.
if [ -f /etc/systemd/system/noust-web.service ]; then
    /usr/bin/noust web refresh-unit --reason "package upgrade" >/dev/null 2>&1 || :
    systemctl try-restart noust-web.service >/dev/null 2>&1 || :
fi
if [ -f /etc/systemd/system/wasm-web.service ]; then
    systemctl try-restart wasm-web.service >/dev/null 2>&1 || :
fi

%changelog
* Thu Oct 01 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.16-1
- Updating Noust from the console or the fleet no longer leaves a failed 'Update Noust' job behind when the update succeeded: the restarted console follows the update's own record and records how it really ended
* Thu Oct 01 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.15-1
- The health gate (activation, rollback, migration, limits) and noust env migrate find an application's unit when its row lacks the legacy wasm- prefix the installed unit still has; before, the gate restarted a unit that did not exist
- noust env migrate refuses a unit outside the managed directory with a message instead of a traceback
* Thu Oct 01 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.14-1
- noust env migrate keeps the .env's value for build-time variables (NEXT_PUBLIC_*, VITE_*, REACT_APP_*...) when the unit had another: builds inlined the .env's, so it is the one in use
- noust env migrate keeps the previous .env under the state directory (env-migrations/, root only) and says where
- The noust.inline_secrets check no longer reports Noust's own units or a Compose stack's COMPOSE_FILE
* Thu Oct 01 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.13-1
- Applications created with WASM 1.x carried their variables (DATABASE_URL, secrets) as Environment= lines in a 0644 unit that any local user reads with systemctl show, and their builds ran without them; 'noust env migrate <domain>' (or --all) moves them into the 0600 .env the unit then loads, restarting behind the health gate and putting everything back if the application does not answer
- New security check noust.inline_secrets: critical when an application unit carries a secret inline
- noust env show needs no --reason under ens-medium unless --unmask prints the secrets
* Thu Oct 01 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.12-1
- pnpm installs recreate node_modules without asking: the first sandboxed update of an in-place pnpm application met a tree a root build had made with another store, and pnpm aborted with ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY
* Thu Oct 01 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.11-1
- The monitor consolidates the metrics history again: under its unit's ProtectSystem=strict, SQLite could not write the temporary file a large sort needs and every consolidation failed with 'disk I/O error' once the history had grown; temporary tables now live in memory and the unit has its own /tmp
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.10-1
- Server > Security is fast: one reading of sshd's login history serves every view for a minute; on a server the Internet probes all day it took 2.4 s and was read up to twice per view, so SSH, firewall and checks took 5 to 7 s and removing a key waited for it
- A view still loading after two seconds says what it is reading, with a spinner, instead of a bare skeleton
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.9-1
- The fleet summary's applications tile shows every application (static and stopped ones included), with the running ones below, so it matches the Applications tab
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.8-1
- An operating system update whose unit finds nothing left to install (Ubuntu phases updates in and out between two looks) is recorded as completed instead of failing with no record; asked directly, it says there is nothing to install
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.7-1
- Updates are no longer refused because packagekitd or aptd is running: those daemons stay up after every apt run, and now count as busy only while they hold the package manager's lock (read from /proc/locks, never taken); any process holding it counts, whatever its name
- noust audit show finds an event whose id is made only of digits
- The central asks a node once when several views load at the same moment
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.6-1
- The console and the monitor no longer set User=root in their units: combined with NoNewPrivileges and ProtectKernelLogs, systemd 255 started them without CAP_SETUID, so apt could not drop to _apt and every Noust or system update started from the console or a fleet job failed with 'seteuid 42 failed'
- New 'noust web refresh-unit' rewrites noust-web.service from this version's template, keeping where the console listens; the deb and rpm packages run it on upgrade, before restarting the console
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.5-1
- The Docker firewall check no longer counts a DOCKER-USER rule that names the host port with --dport: Docker has already translated it, so it filtered nothing where host and container ports differ; --ctorigdstport and --ctdir are read
- IPv6 publications on a server without an IPv6 default route are not reported as exposed
- Static sites no longer count as building as root in ENS-BLD-01
- The sandbox trial reads the commit of an in-place tree owned by the service's user (git's dubious ownership)
- app sandbox status, user list, user exception list, notify telegram-chats and github installations (without --sync) need no --reason under ens-medium
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.4-1
- Sign in in two steps, with username or email; the lockout no longer locks everyone behind an SSH tunnel
- The security report runs by itself; the console is found by its process; DOCKER-USER rules count as filtering
- The console comes back after an upgrade; the update notice says when the package index has not seen a release
- OS updates are no longer blocked by Ubuntu's idle unattended-upgrades helper
- Test suite runs in parallel
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.3-1
- Sandboxed builds use corepack's known-good package manager, as root builds did
- Activity no longer shifts while it loads
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.2-1
- An empty service_group no longer breaks sandboxed builds
- Activity's Operations view filters on the server and shows every job
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.1-1
- migrate-tunnel removes the central's previous key from root (closes the 3.0 hole on migrated nodes)
- Sandbox trials build an in-place tree as it is; sandboxed installs get devDependencies
- Fleet tunnels close with the console
* Wed Sep 30 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.1.0-1
- Accounts with roles, passkeys, four-eyes approvals and an audit trail shipped to syslog; ENS categoria MEDIA profile and evidence
- Manage the server: OS updates, reboots, storage, SSH and firewall changes that revert unless confirmed, hardening checks
- Fleet: an unprivileged tunnel account, per-node ceilings, fleet-wide views and bulk actions
- Builds, previews and monorepos run sandboxed as noust-build, never as root
- Databases rebuilt: per-app provisioning, data browser, SQL console, verified backup policies, metrics
- Metrics history in tiers; a dashboard Overview; notifications rebuilt per channel
- A normative design system and every console page rebuilt on it
* Tue Sep 29 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 3.0.0-1
- Renamed from wasm: the package, the command and the paths are noust now; wasm remains a command alias for the whole 3.x series
- Fleet: a central manages every server over SSH tunnels it opens outward, with a key that can only forward the node's console port and never runs a command
- Fleet: noust fleet authorize on the node prints a join code; noust node add|list|show|test|remove and noust fleet status on the central
- Fleet: fleet tokens are accepted only from loopback, audited on behalf of the central's operator, and the node's own elevation rules are enforced on the central
- Central: noust central run as a hub, with a container image for a NAS, self-signed TLS, a private-network allowlist and optional sealed secrets
- Console: server selector, Fleet page, Settings > Servers, the central's lock screen and a new logo
* Tue Sep 29 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 2.3.0-1
- The console in English and Spanish, with notifications in either language
- Recipes: WordPress (PHP-FPM, MariaDB), Uptime Kuma, Umami and n8n, with pinned or checksummed sources
- PHP-FPM applications: a confined pool per application, a fastcgi site that follows the release, a health gate on the pool
- Export and import an application; read Vercel, Railway, Render and Heroku configuration
- The update notice reports the version this server can install from its own package repository
* Mon Sep 28 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 2.2.1-1
- GitHub App creation works on a server without a public hooks URL: the App is created without events, which are switched on once hooks are exposed
* Mon Sep 28 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 2.2.0-1
- Blue/green activation per application: two instances behind an nginx upstream, a switch only after the health gate, no failed request
- Pull request previews for GitHub, GitLab and Gitea, with quota, expiry and removal on close; forks, outside authors and bots refused
- A GitHub App per server: private repositories with short-lived tokens, push and pull request events, deployment statuses, repository picker
- Remote backup destinations through rclone with verification, per-destination retention, encryption and restore; schedule retention applied
- Deployment notifications for every deploy, including started and rolled back; why each environment variable is hidden, and marks
* Sat Sep 26 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 2.1.0-1
- WASM is now free software under the GNU AGPL 3.0 or later
- Configurable health check and release retention; rebuild or roll back to a deployment's exact commit; nothing-new check before updates
- Docker Compose and monorepo updates pass the health gate and roll back
- wasm web enable runs the console as a service; SMTP, Telegram, health reasons and backups in the console; charts with readout and zoom
- Fixes: cron edits, credentials in source URLs, per-app metrics, backup ids, rate limiter
* Sat Sep 26 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 2.0.1-1
- Backups never land in the working directory (wasm backup import recovers them), unit failures alert by default, one definition of WASM's units, git never prompts for credentials, PostgreSQL read-only console on any port, per-credential rate limits
* Sat Sep 26 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 2.0.0-1
- WASM 2.0: releases with health-gated activation and instant rollback, a new browser console, domains, diagnosis, resource limits, API tokens and sudo mode; see docs/CHANGELOG-2.0.md and docs/UPGRADING-2.0.md
* Sat Sep 26 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.6.5-1
- Fix the Debian package pip-installing into the system Python with --break-system-packages on every install and upgrade
- Fix the Debian package recursively reassigning every file under /var/www/apps to www-data on every upgrade, which could break data directories bind-mounted into containers
- Fix the Debian package loosening /etc/wasm and config.yaml, which hold credentials, to group-readable on every upgrade; they stay root-only
* Fri Sep 25 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.6.4-1
- Fix wasm update deleting everything an application wrote into its own tree (uploads included) when it was deployed from a local directory or an archive: the source is now copied over the tree instead of replacing it
- Fix a forced git update running git clean, which deleted every untracked file that was not ignored
- npm projects without a lockfile install with npm install instead of failing on npm ci
- Regression tests pin these paths down; the bug was found by a new real-machine integration harness
* Fri Sep 25 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.6.3-1
- Fix data loss on panel and webhook updates: they re-ran the full deploy, whose fetch deletes the app directory, destroying the .env, uploaded files and generated secrets; every surface now runs one shared update (backup, git pull, rebuild, hand-over, restart)
- Fix a read-scope API token being able to read every application .env in clear, through the API with unmask and through the panel reveal page, and to read unit files that inline secrets
- Fix a read-scope token being able to cancel jobs over the jobs WebSocket
- A monorepo update where any workspace unit failed to restart is no longer reported as running
- Applications deployed from an archive or a local directory update by fetching their recorded source again, keeping the .env
* Fri Sep 25 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.6.2-1
- Fix EACCES at runtime after wasm update: the update now hands the app directory back to the service user after the build, so Next.js can write .next/cache/images and uploads work in directories the pull added
- Fix monorepo deploys and updates handing the tree over before the build, which left every workspace's build output owned by root
- Fix wasm update on a monorepo leaving the workspace .env.production files world-readable: it now runs the deployer's own update instead of a copy of its steps
- A failed chown or chmod during the hand-over is now a visible warning instead of a debug line
* Thu Aug 27 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.6.1-1
- Fix EACCES at service start: the deploy pipeline now hands the app directory over to the service user after the build
- One shared permissions implementation for the base pipeline and the monorepo deployer
* Fri Aug 14 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.6.0-1
- Visual redesign: Geist typography, indigo accent, refined components and a common page skeleton across the panel
* Fri Aug 14 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.5.1-1
- Fix: panel failed to start on distro pydantic v1 (Ubuntu 24.04); pydantic v1/v2 bridge with architecture guards and distro-import CI gates
* Fri Aug 14 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.5.0-1
- Observability and product polish: per-app charts with deploy markers, user cron jobs, CLI deep links, command palette, log search
* Fri Aug 14 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.4.0-1
- The panel manages the whole product: databases, services, sites, env vars, settings, scheduled backups, deployment history with rollback, live charts, git webhooks, notifications
* Fri Aug 14 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.3.0-1
- Panel security: TLS by default, TOTP 2FA, scoped API tokens, session management, browser E2E in CI
* Thu Aug 13 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.2.1-1
- Fix: wasm reported no applications on a machine whose records existed, after the monitor service created /var/lib/wasm and the store moved to it; a database that already exists now outranks an empty location
* Thu Aug 13 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.2.0-1
- Fix: the monitor service never started, failing every 30 seconds since installation, because its unit named a state directory systemd was never asked to create
- Fix: the panel's journal streams left a journalctl process running on every disconnection
- Fix: the panel's live event feed did not exist; /events now streams state changes and notices as server-sent events
- Fix: the log drawer and the mobile navigation never ran at all, blocked by the panel's own script-src policy
- Fix: the machine strip and the navigation scrolled off the top of every page
- Fix: an action that succeeded reported nothing, and a failure was shown as truncated JSON instead of the tool's own output
- Fix: page routes answered a manager failure with a plain-text Internal Server Error
- Fix: a session renewing itself silently invalidated the CSRF token every control was using
- Fix: text colours and badge backgrounds now meet WCAG AA on every surface they are placed on
- Feature: deploy an application from the panel
- Feature: start, stop, enable and disable services, enable and disable sites, revoke and delete certificates
- Feature: take a backup from an application's row, and verify that an archive is sound
- Enhancement: wasm web start explains how to reach a loopback panel over an SSH tunnel, and offers a port it has checked is free
- Refactor: remove Alpine, which the panel's content security policy had always refused to execute
* Wed Aug 12 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.1.0-1
- Fix: wasm list reports the state systemd and the port actually report, not the value written at deploy time; it and wasm health no longer contradict each other
- Fix: a service systemd is restarting every few seconds, or one that accepts no connections, is no longer reported as running
- Fix: static sites are no longer counted as stopped; they have no service to run
- Fix: the RPM builds on Fedora and openSUSE again; a macro named in a comment inside the build section was expanded and ran
- Fix: Leap 15.x builds against the packaged Python 3.11 instead of 3.6, which could not run the result
- Fix: the package installs on Debian 12, Ubuntu 22.04 and Fedora 41, where python3-questionary does not exist
- Fix: wasm web start refuses a port that is already taken instead of printing a token and then failing to bind
- Fix: wasm --no-color applies to wasm health, and the logger follows a redirected stdout
- Change: wasm list is a coloured table where colour encodes state, and it names what needs attention
- Change: publishing waits for the .deb and the RPM to be built, installed and run in clean containers
* Wed Aug 12 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.0.3-1
- Fix: the Debian build dependencies in wasm.dsc and debian.control agree, and are the minimum that builds a wheel
* Wed Aug 12 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.0.2-1
- Fix: interactive mode works again; it still imported inquirer after that stopped being a dependency
- Fix: distribution packages build; the completion scripts are committed instead of generated, so the build no longer needs to run the package
- Fix: the RPM spec no longer uses a macro as a tag, which failed every Fedora and openSUSE target
- Change: publishing waits for tests, lint, types, a clean install and a container-built .deb
* Wed Aug 12 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.0.1-1
- Fix: package manager availability is asked of the command runner, not of the process PATH
- Fix: a project whose lock file names a package manager refuses to install with a different one
* Wed Aug 12 2026 Yago Lopez Prado <yago.lopez.adeje@gmail.com> - 1.0.0-1
- Security: closes six critical issues, including arbitrary code execution as root through systemd unit environment injection
- Security: the process monitor no longer terminates processes or deletes directories; it reports instead of acting
- Security: the panel serves no third-party asset, uses HttpOnly session cookies with CSRF, and writes an audit log
- Security: database dumps, archive extraction and SQL privileges are validated instead of interpolated
- Feature: control panel rebuilt as server-rendered pages, with no build step and no CDN
- Feature: --dry-run is enforced at the execution and filesystem seams, so it holds for every command
- Change: command line migrated to Click; every command, alias and option is preserved
- Change: backup archives are self-contained and verified; format version 2.0.0
- Packaging: supports Ubuntu 26.04 and Python 3.14; drops python-jose and python3-inquirer
* Fri Mar 20 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.8-1
- Feature: Auto-verify and expand SSL certificates for www subdomain coverage
- Enhancement: cert_manager.obtain() checks domain coverage before skipping existing certs
- Enhancement: Site create verifies existing certs cover all required domains including www

* Fri Mar 20 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.7-1
- Fix: Site create fails with "site already exists" when config file remains after deletion
- Fix: Site create SSL step fails with "site already exists" on second create_site call
- Enhancement: Site create updates existing config instead of failing

* Fri Mar 20 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.6-1
- Feature: Site create now obtains SSL certificates automatically
- Feature: Add --no-ssl flag to site create to skip SSL
- Enhancement: Site create reuses existing valid certificates instead of re-obtaining
- Enhancement: Interactive site create prompts for SSL configuration

* Fri Mar 20 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.5-1
- Feature: Add --www flag to site create for www subdomain in web server config
- Enhancement: Interactive site create prompts for www inclusion

* Fri Mar 20 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.4-1
- Fix: Interactive mode crashes due to missing Namespace attributes (6 bugs)
- Fix: Service create uses wrong attribute name (command vs exec_command)
- Fix: Webapp delete, logs, update missing required attributes
- Fix: Service logs and cert revoke missing required attributes
- Feature: Add --www flag for including www subdomain in SSL certificates
- Feature: Add --expand flag for expanding existing SSL certificates
- Enhancement: Interactive mode prompts for log lines, follow mode, and branch
- Enhancement: Nginx/Apache templates support server_names with www

* Wed Mar 11 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.3-1
- Fix: SourceManager.fetch() parameter mismatch in Docker Compose deployer
- Fix: ServiceManager.create_service() API mismatch in Docker Compose deployer
- Fix: Docker Compose build/update timeouts (600000s to 600s/300s)
- Feature: Headless worker support for Docker Compose apps with no exposed ports
- Enhancement: Add _PASS pattern to EnvManager secret detection
- Enhancement: ServiceManager.create_service() accepts extra template context

* Tue Mar 10 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.2-1
- Fix: Web interface token display uses print() for reliable visibility
- Enhancement: Release workflow validates OBS credentials before proceeding

* Mon Mar 09 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.1-1
- Fix: Python deployer compatibility with Python 3.10 (os.walk instead of Path.walk)
- Fix: Django WSGI/ASGI detection break placement
- Fix: Docker Compose false-positive detection when framework config files present
- Enhancement: Update command uses stored app type from database
- Enhancement: Verbose command output logging via Logger.command_output()
- Enhancement: Web jobs use specific WASM exceptions instead of bare Exception
- Enhancement: Web server uses logging module instead of print()
- Enhancement: setup.py adds web/monitor/all extras and fixes entry point
- Enhancement: .gitignore updated for rpm/*.spec, .claude/, .ruff_cache/

* Mon Mar 09 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.15.0-1
- Feature: Add Docker Compose deployer with full deployment lifecycle
- Feature: Add environment variable manager with .env.example discovery and secret auto-generation
- Feature: Add advanced Nginx configuration builder with multi-route proxying via wasm.nginx.yaml
- Feature: Add backup scheduler with systemd timer integration
- Feature: Add wasm env CLI command for configure, show, and export operations
- Feature: Add wasm backup schedule CLI subcommand for create, list, and delete
- Enhancement: Add DockerError exception type for Docker-related failures
- Enhancement: Add DOCKER_COMPOSE app type to store and registry
- Enhancement: Update web dashboard UI with improved styling and page components
- Enhancement: Add create_advanced_site method to NginxManager
- Enhancement: Improve PostgreSQL and Redis database managers

* Fri Feb 06 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.14.4-1
- Fix: service_exists() not finding legacy wasm- prefixed services
- Fix: Update command fails to restart legacy services

* Fri Feb 06 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.14.3-1
- Fix: Monorepo detection too aggressive (single Next.js apps misdetected)
- Fix: Update command crashes with MonorepoDeployer (missing pre_install)
- Fix: Update checker shows false positive when already on latest version
- Fix: Update checker recommends pip when installed via apt/dnf
- Fix: Release workflow supports manual re-trigger via workflow_dispatch

* Wed Feb 04 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.14.2-1
- Feature: Add MonorepoDeployer for Turborepo/pnpm workspace deployments
- Feature: New CLI options --subdomains, --workspaces, --no-database
- Fix: Update command now queries database for app_path (supports legacy apps)
- Add workspace and turbo helpers for monorepo detection

* Wed Jan 28 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.14.1-1
- Fix: API verify() returns correct keys (checksum_ok, files_ok)
- Fix: BackupMetadata now persists includes_build field
- Fix: Backup rotation uses direct app_name lookup
- Fix: Remove non-ASCII characters from CLI output
- Feature: Database backup integration (MySQL, PostgreSQL, MongoDB, Redis)
- Feature: New --include-databases flag for backup create
- Enhancement: API and CLI now expose all backup options

* Thu Jan 15 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.14.0-1
- Feature: New 'wasm health' command for system diagnostics
- Feature: New 'wasm config' command (upgrade, show, path)
- Feature: Automatic config migration on package upgrade
- Feature: Persistent threat storage with SQLite (threat_store.py)
- Feature: New API endpoints /api/monitor/threats/history and resolve
- Fix: Monitor module duplicate return statement (dead code)
- Fix: Inconsistent scan_interval defaults (30s local, 3600s AI)
- Fix: API /scan now respects global config (auto_terminate, use_ai)
- Fix: CPU/Memory thresholds now used for logging and alerts
- Enhancement: Monitor frontend uses WebSocket instead of HTTP polling
- Enhancement: Auto-update wasm-monitor service on package upgrade
- Enhancement: Improved process fallback with better ps parsing
- Enhancement: Notification report includes all threats for audit

* Thu Jan 15 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.16-1
- Fix: Move python3-inquirer from Depends to Recommends
- Fix: Package installs on systems without python3-inquirer in repos
- Enhancement: Interactive mode now optional (install inquirer via pip)

* Thu Jan 15 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.15-1
- Enhancement: Set update checker interval to instant (CHECK_INTERVAL = 0)
- Enhancement: Change update notification color to softer yellow
- Feature: Add --changelog flag to view current version changelog
- Feature: New version.py module for version information display

* Thu Jan 15 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.14-1
- Fix: OBS build failures - add missing python3-inquirer dependency
- Enhancement: Ensure all OBS package dependencies are declared

* Thu Jan 15 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.13-1
- Fix: Critical bare except clause in monitor API
- Fix: Static apps (Vite) trying to restart non-existent services
- Fix: Update checker now detects installation method
- Feature: Update banner in web dashboard
- Feature: /api/system/version endpoint for update checking

* Wed Jan 14 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.12-1
- chore: Clean distribution packages (remove Docker files, dev tools)
- feat: Add .gitattributes to exclude dev files from git archive
- feat: Add MANIFEST.in to control PyPI source distribution

* Wed Jan 14 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.11-1
- Fix: Environment variables with quotes properly stripped from .env files
- Feature: Automatic update checker via GitHub Releases API
- Enhancement: Update checker runs post-command to avoid delays

* Thu Jan 08 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.7-1
- Fix: 'wasm store sync' now updates app status along with service status

* Thu Jan 08 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.6-1
- Fix: 'wasm store sync' attribute naming (service.status vs service.active)

* Thu Jan 08 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.5-1
- Fix: 'wasm store import' finds app directories with multiple naming conventions

* Thu Jan 08 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.4-1
- Fix: 'wasm store import' using wrong attribute name (unit_file)

* Thu Jan 08 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.3-1
- Fix: Corrupted debian.postrm script causing upgrade failure

* Thu Jan 08 2026 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.2-1
- Feature: SQLite persistence store for tracking deployed apps
- New: Store tracks apps, sites, services, and databases
- New: wasm store commands (init, stats, import, export, sync, path)
- Enhancement: webapp list/status commands now use SQLite store
- Enhancement: Database create/drop commands track in store
- Enhancement: Managers (nginx, apache, service, cert) register in store
- Fix: GitHub Actions .deb build missing pybuild-plugin-pyproject

* Tue Dec 30 2025 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.1-1
- Fix: Systemd services failing with 'Permission denied' when using nvm
- Fix: Detect and avoid private paths (nvm, ~/.local) in service ExecStart
- Fix: Prefer global Node.js installation over user-specific nvm paths
- Enhancement: Add helpful error messages for nvm path issues

* Mon Feb 24 2025 Perkybeet <yago.lopez.adeje@gmail.com> - 0.13.0-1
- Feature: Database UI overhaul with logs, tabs, and SQL import
- Feature: Database credential management via config.yaml
- Fix: MySQL connection with password protection
- Fix: Local environment installation issues
- Real-time WebSocket updates for logs and events
- Token-based authentication with JWT
- Rate limiting and brute force protection
- API endpoints: /api/apps, /api/services, /api/sites, /api/certs
- API endpoints: /api/backups, /api/monitor, /api/system, /api/config
- Background job processing with progress tracking
- Optional dependencies: pip install wasm-cli[web]
- Detect OOM (Out of Memory) build failures with exit code 137
- Provide actionable suggestions for resolving memory issues
- Add OutOfMemoryError exception with swap/memory configuration tips
- CI: Automatic deployment to OBS on release
- Fix: OBS deployment configuration in GitHub Actions
- Fix: Git pull with unstaged/uncommitted changes during wasm update
- Auto-stash local changes before pull, restore after
- Handle divergent branches with automatic reset to remote
- Handle rebase conflicts gracefully
- Preserve .env and untracked files during force updates
- Fix: Git "dubious ownership" error during wasm update
- Auto-configure git safe.directory for app directories
- Add man page (wasm.1) for all distributions
- Fix RPM packaging to include man page
- Improve documentation
- Feature: wasm store import auto-detects app type

* Wed Dec 18 2024 Perkybeet <yago.lopez.adeje@gmail.com> - 0.10.0-1
- Initial RPM package for OBS
- Backup and rollback system
- AI-powered security monitoring
- Shell completions for bash, zsh, fish
- Support for Next.js, Node.js, Vite, Python, and static sites
