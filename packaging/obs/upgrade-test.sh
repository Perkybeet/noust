#!/bin/sh
# Upgrade a server running WASM 2.x from the published repository to the
# packages just built, the way an operator would, and check what they keep.
#
# Usage: packaging/obs/upgrade-test.sh deb|rpm OLD_REPOSITORY NEW_PACKAGES_DIR
#
#   OLD_REPOSITORY    the OBS repository users have configured, e.g.
#                     https://download.opensuse.org/repositories/home:/Perkybeet/Debian_12
#   NEW_PACKAGES_DIR  noust and the transitional wasm (deb) or wasm-cli (rpm).
#
# Runs as root in a throwaway container; packaging/obs/upgrade-in-systemd.sh
# gives it one with systemd as PID 1, so the units are checked too. What it
# proves:
#   - a plain upgrade (apt upgrade, dnf upgrade) brings noust in through the
#     transitional package, and both commands answer afterwards;
#   - the upgrade moves the server onto Noust's names even when it runs the
#     way unattended-upgrades and dnf-automatic do, inside a systemd unit
#     (INVOCATION_ID set), where noust's automatic migration stands aside:
#     /etc/wasm and /var/lib/wasm become links, the store is noust.db, and
#     wasm-web and wasm-monitor are replaced by noust-web and noust-monitor
#     in the state they were in;
#   - the operator's /etc/wasm/config.yaml survives it, with no .rpmsave copy;
#   - on Debian, dpkg no longer holds that file as a conffile of wasm, so
#     purging the transitional package cannot delete it (through the link at
#     /etc/wasm) - and purging it really does keep it.
#
# If the old repository cannot be reached, or already serves 3.x (it only ever
# keeps the latest version, so after the release it has no 2.x left), there is
# nothing to upgrade from: the script says so as a workflow warning and exits 0.
set -eu

kind=${1:?usage: upgrade-test.sh deb|rpm OLD_REPOSITORY NEW_PACKAGES_DIR}
old=${2:?usage: upgrade-test.sh deb|rpm OLD_REPOSITORY NEW_PACKAGES_DIR}
new=$(cd "${3:?usage: upgrade-test.sh deb|rpm OLD_REPOSITORY NEW_PACKAGES_DIR}" && pwd)

MARKER="# kept across the upgrade to noust"

skip() {
    echo "::warning::Upgrade from 2.x not exercised: $1"
    exit 0
}

fail() {
    echo "::error::$1"
    exit 1
}

have_systemd() {
    [ -d /run/systemd/system ]
}

# The configuration wherever it lives now.
config_kept() {
    for file in /etc/noust/config.yaml /etc/wasm/config.yaml; do
        if [ -f "$file" ] && grep -qF "$MARKER" "$file"; then
            echo "The operator's configuration is at $file"
            return 0
        fi
    done
    return 1
}

check_commands() {
    noust --version
    wasm --version
    noust --help > /dev/null
    wasm --help > /dev/null
}

# What a 2.x server has besides its configuration: a store, and with systemd
# the monitor and, where the distribution packages a recent enough FastAPI,
# the console as units.
prepare_old_server() {
    echo "$MARKER" >> /etc/wasm/config.yaml
    mkdir -p /var/lib/wasm
    wasm list > /dev/null 2>&1 || true
    [ -s /var/lib/wasm/wasm.db ] || fail "wasm 2.x did not create its store in /var/lib/wasm"

    web_was_active=
    monitor_was_enabled=
    if have_systemd; then
        if wasm monitor enable > /dev/null 2>&1 && systemctl is-enabled --quiet wasm-monitor.service; then
            monitor_was_enabled=1
        fi
        if python3 -c "import fastapi, sys; sys.exit(0 if tuple(map(int, fastapi.__version__.split('.')[:2])) >= (0, 100) else 1)" 2> /dev/null \
                && wasm web enable > /dev/null 2>&1; then
            for _ in $(seq 1 20); do
                systemctl is-active --quiet wasm-web.service && break
                sleep 1
            done
            systemctl is-active --quiet wasm-web.service && web_was_active=1
        fi
    fi
    echo "Before the upgrade: store present, wasm-monitor enabled: ${monitor_was_enabled:-no}, wasm-web active: ${web_was_active:-no}"
}

check_migrated() {
    [ -L /etc/wasm ] && [ "$(readlink -f /etc/wasm)" = /etc/noust ] \
        || fail "/etc/wasm is not a link to /etc/noust: the upgrade did not migrate"
    [ -L /var/lib/wasm ] && [ "$(readlink -f /var/lib/wasm)" = /var/lib/noust ] \
        || fail "/var/lib/wasm is not a link to /var/lib/noust"
    [ -s /var/lib/noust/noust.db ] || fail "/var/lib/noust/noust.db does not exist"
    for unit in wasm-web.service wasm-monitor.service; do
        if [ -e "/etc/systemd/system/$unit" ]; then
            fail "/etc/systemd/system/$unit is still there after the migration"
        fi
    done
    if [ -n "$monitor_was_enabled" ]; then
        systemctl is-enabled --quiet noust-monitor.service \
            || fail "wasm-monitor was enabled and noust-monitor is not"
    fi
    if [ -n "$web_was_active" ]; then
        systemctl is-active --quiet noust-web.service \
            || fail "wasm-web was the running console and noust-web is not running"
    fi
    echo "Migrated: /etc/wasm and /var/lib/wasm are links, noust.db exists, no wasm-web or wasm-monitor unit is left"
}

case "$kind" in
    deb)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates curl gpg dpkg-dev

        curl -fsSL --retry 5 --retry-all-errors "$old/Release.key" \
            | gpg --dearmor > /usr/share/keyrings/wasm-old.gpg \
            || skip "cannot fetch $old/Release.key"
        echo "deb [signed-by=/usr/share/keyrings/wasm-old.gpg] $old/ /" \
            > /etc/apt/sources.list.d/wasm-old.list
        apt-get update || skip "cannot read $old"
        apt-get install -y wasm || skip "cannot install wasm from $old"

        installed=$(dpkg-query -W -f '${Version}' wasm)
        case "$installed" in
            2.*) echo "Installed wasm $installed from $old" ;;
            *) skip "$old serves wasm $installed, not 2.x" ;;
        esac
        prepare_old_server

        mkdir -p /srv/new
        cp "$new"/*.deb /srv/new/
        (cd /srv/new && dpkg-scanpackages . > Packages)
        echo "deb [trusted=yes] file:/srv/new ./" > /etc/apt/sources.list.d/new.list
        apt-get update

        # What `apt upgrade` does: upgrade, and install the new packages an
        # upgrade needs (plain `apt-get upgrade` keeps wasm back instead). With
        # INVOCATION_ID set, as unattended-upgrades runs inside a systemd unit.
        INVOCATION_ID=upgrade-test apt-get -y --with-new-pkgs upgrade

        dpkg-query -W -f '${Version}\n' noust || fail "the upgrade did not install noust"
        case "$(dpkg-query -W -f '${Version}' wasm)" in
            2.*) fail "wasm was not upgraded to the transitional package" ;;
        esac
        check_commands
        config_kept || fail "the configuration did not survive the upgrade"
        check_migrated

        # The transitional package is in section oldlibs, and apt moves the
        # manual-install mark of a package that moves into oldlibs to what it
        # depends on (APT::Move-Autobit-Sections). Without that, noust would be
        # "automatically installed" and `apt autoremove` would take it away
        # the day the operator removes wasm.
        apt-mark showmanual | grep -qx noust \
            || fail "noust is marked as automatically installed; autoremove would remove it"

        conffiles=$(dpkg-query -W -f '${Conffiles}' wasm)
        if [ -n "$conffiles" ]; then
            fail "dpkg still holds conffiles for wasm, which a purge would delete: $conffiles"
        fi

        apt-get -y purge wasm
        config_kept || fail "purging the transitional wasm package deleted the configuration"
        check_commands
        ;;

    rpm)
        dnf install -y createrepo_c
        repofile="$old/home:Perkybeet.repo"
        curl -fsSL --retry 5 --retry-all-errors "$repofile" \
            -o /etc/yum.repos.d/wasm-old.repo || skip "cannot fetch $repofile"
        dnf install -y wasm-cli || skip "cannot install wasm-cli from $old"
        # The console's stack, as an operator who runs it installs it (the
        # spec only suggests it).
        dnf install -y python3-fastapi python3-starlette python3-pydantic python3-uvicorn python3-psutil || true

        installed=$(rpm -q --qf '%{VERSION}' wasm-cli)
        case "$installed" in
            2.*) echo "Installed wasm-cli $installed from $old" ;;
            *) skip "$old serves wasm-cli $installed, not 2.x" ;;
        esac
        prepare_old_server

        mkdir -p /srv/new
        cp "$new"/*.noarch.rpm /srv/new/
        createrepo_c /srv/new
        printf '[new]\nname=new\nbaseurl=file:///srv/new\ngpgcheck=0\npriority=1\n' \
            > /etc/yum.repos.d/new.repo

        # With INVOCATION_ID set, as dnf-automatic runs inside a systemd unit.
        INVOCATION_ID=upgrade-test dnf upgrade -y

        rpm -q noust || fail "the upgrade did not install noust"
        case "$(rpm -q --qf '%{VERSION}' wasm-cli)" in
            2.*) fail "wasm-cli was not upgraded to the transitional package" ;;
        esac
        check_commands
        config_kept || fail "the configuration did not survive the upgrade"
        for leftover in /etc/wasm/config.yaml.rpmsave /etc/wasm/config.yaml.noust-keep \
            /etc/noust/config.yaml.rpmsave /etc/noust/config.yaml.noust-keep; do
            if [ -e "$leftover" ]; then
                fail "$leftover was left behind"
            fi
        done
        check_migrated

        # dnf records noust as installed for a dependency, and removing
        # wasm-cli would take it along (clean_requirements_on_remove). What
        # docs/RENAME.md tells the operator to do first:
        dnf -y mark user noust
        dnf remove -y wasm-cli
        rpm -q noust || fail "removing wasm-cli removed noust"
        config_kept || fail "removing the transitional wasm-cli package deleted the configuration"
        check_commands
        ;;

    *)
        echo "Unknown kind '$kind': deb or rpm" >&2
        exit 1
        ;;
esac

echo "The upgrade from 2.x to $(noust --version 2>&1 | head -n 1) keeps the configuration and both commands"
