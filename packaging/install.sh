#!/bin/sh
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Noust installer.
#
# Verify it before you run it. It is published with every release next to a
# SHA256SUMS file (and its signature), so the script you run is the one that was
# released:
#
#   curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/install.sh
#   curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/SHA256SUMS
#   sha256sum -c --ignore-missing SHA256SUMS
#   sh install.sh
#
# What it does, in this order, and nothing else:
#
#   1. reads /etc/os-release and the machine's architecture and checks them against
#      the platform table below (docs/PLATFORMS.md is the same table);
#   2. adds the Noust repository (the openSUSE Build Service project
#      home:Perkybeet) after checking that the key it serves has the fingerprint
#      pinned in this file; a different key stops it before anything is written;
#   3. installs the `noust` package with the system's package manager (apt, dnf or
#      zypper). On a release no package exists for (the RHEL family) it builds a
#      virtual environment in /opt/noust with pip instead, and says so;
#   4. runs `noust setup init -y` (nginx, git, certbot, Node.js, Noust's
#      directories) and `noust web enable` (the console, as a service, on the
#      loopback address only) and prints how to reach it.
#
# It never downloads another script and never pipes anything it fetched into a
# shell. Re-running it upgrades Noust and leaves an existing configuration and
# console alone.
#
# Usage: sh install.sh [options]
#
#   --version X.Y.Z    install this version instead of the newest
#   --dry-run          print what would be done and change nothing
#   --no-console       do not set the console up as a service
#   --skip-setup       do not run `noust setup init`
#   --method M         package (the default where a package exists) or pip
#   --target NAME      the repository directory to use (Debian_12, Fedora_43...)
#                      when this release is not in the table
#   --force            go on past a release or architecture the table refuses
#   -y, --yes          answer yes to every question
#   --admin-user NAME  create this administrator account once Noust is installed
#                      (skipped when it exists). The password is never an argument:
#                      give --admin-password-file FILE (its first line) or the
#                      NOUST_ADMIN_PASSWORD variable. The account enrols its
#                      authenticator at its first sign-in.
#   --admin-password-file FILE   where that password is read from
#   --json             print one JSON document on standard output when done (or when it
#                      fails) and everything else on standard error, for cloud-init
#                      and configuration management
#   --print-platform   print what was detected, as key=value lines, and stop
#   -h, --help         this text
#
# The same options as variables, for provisioning scripts: NOUST_VERSION,
# NOUST_DRY_RUN=1, NOUST_NO_CONSOLE=1, NOUST_SKIP_SETUP=1, NOUST_METHOD,
# NOUST_TARGET, NOUST_FORCE=1, NOUST_ASSUME_YES=1, NOUST_ADMIN_USER,
# NOUST_ADMIN_PASSWORD_FILE, NOUST_ADMIN_PASSWORD and NOUST_JSON=1. NOUST_REPO_BASE points at a
# mirror (https only) and NOUST_REPO_KEY_FPR replaces the pinned fingerprint
# when the project key has been rotated and this file is older than that.
# NOUST_OS_RELEASE and NOUST_ARCH replace /etc/os-release and `uname -m`, which
# is how the platform table is tested without those machines.
#
# Exit status: 0 done, 1 something failed, 2 wrong usage, 3 platform refused.

set -eu

# BEGIN TARGETS (generated: scripts/sync_install_targets.py; do not edit by hand)
REPO_KEY_FPR_PINNED="D9FA6122D7F154D75A588A501945D169A62CFF65"
REPO_BASE_DEFAULT="https://download.opensuse.org/repositories/home:/Perkybeet"

# target_for ID VERSION_ID: sets T_NAME, T_FAMILY, T_STATUS, T_REPO, T_PYTHON and
# T_NOTE for a release the table knows, and returns 1 for one it does not.
target_for() {
    case "$1:$2" in
        debian:12) T_NAME='Debian 12 (Bookworm)' T_FAMILY=apt T_STATUS=supported T_REPO=Debian_12 T_PYTHON=python3 T_NOTE='' ;;
        debian:13) T_NAME='Debian 13 (Trixie)' T_FAMILY=apt T_STATUS=supported T_REPO=Debian_13 T_PYTHON=python3 T_NOTE='' ;;
        debian:11) T_NAME='Debian 11 (Bullseye)' T_FAMILY=apt T_STATUS=unsupported T_REPO=- T_PYTHON=python3 T_NOTE='Python 3.9; Noust needs 3.10 or later.' ;;
        ubuntu:22.04) T_NAME='Ubuntu 22.04 LTS' T_FAMILY=apt T_STATUS=supported T_REPO=xUbuntu_22.04 T_PYTHON=python3 T_NOTE='' ;;
        ubuntu:24.04) T_NAME='Ubuntu 24.04 LTS' T_FAMILY=apt T_STATUS=supported T_REPO=xUbuntu_24.04 T_PYTHON=python3 T_NOTE='' ;;
        ubuntu:26.04) T_NAME='Ubuntu 26.04 LTS' T_FAMILY=apt T_STATUS=supported T_REPO=xUbuntu_26.04 T_PYTHON=python3 T_NOTE='' ;;
        ubuntu:24.10|ubuntu:25.04|ubuntu:25.10) T_NAME='Ubuntu 24.10, 25.04 and 25.10' T_FAMILY=apt T_STATUS=unsupported T_REPO=- T_PYTHON=python3 T_NOTE='Interim releases, all past their end of life: use an LTS release.' ;;
        ubuntu:20.04) T_NAME='Ubuntu 20.04 LTS' T_FAMILY=apt T_STATUS=unsupported T_REPO=- T_PYTHON=python3 T_NOTE='Python 3.8; Noust needs 3.10 or later.' ;;
        fedora:43) T_NAME='Fedora 43' T_FAMILY=dnf T_STATUS=supported T_REPO=Fedora_43 T_PYTHON=python3 T_NOTE='' ;;
        fedora:42) T_NAME='Fedora 42' T_FAMILY=dnf T_STATUS=best-effort T_REPO=Fedora_42 T_PYTHON=python3 T_NOTE='Past its end of life (2026-05-27); its repository is kept for now.' ;;
        fedora:44) T_NAME='Fedora 44' T_FAMILY=dnf T_STATUS=best-effort T_REPO=Fedora_43 T_PYTHON=python3 T_NOTE='No Fedora 44 repository is published yet: the Fedora 43 one is used (noarch packages).' ;;
        fedora:40|fedora:41) T_NAME='Fedora 40 and 41' T_FAMILY=dnf T_STATUS=unsupported T_REPO=- T_PYTHON=python3 T_NOTE='End of life.' ;;
        opensuse-leap:16.0) T_NAME='openSUSE Leap 16.0' T_FAMILY=zypper T_STATUS=supported T_REPO=16.0 T_PYTHON=python3 T_NOTE='' ;;
        opensuse-leap:15.6) T_NAME='openSUSE Leap 15.6' T_FAMILY=zypper T_STATUS=best-effort T_REPO=15.6 T_PYTHON=python311 T_NOTE='Past its end of life (2026-04-30); its repository is kept for now.' ;;
        opensuse-tumbleweed:*) T_NAME='openSUSE Tumbleweed' T_FAMILY=zypper T_STATUS=supported T_REPO=openSUSE_Tumbleweed T_PYTHON=python3 T_NOTE='' ;;
        rocky:9|rocky:9.*|rocky:10|rocky:10.*) T_NAME='Rocky Linux 9 and 10' T_FAMILY=dnf T_STATUS=pip T_REPO=- T_PYTHON=python3 T_NOTE='OBS has no RHEL-family target: Noust is installed with pip into /opt/noust.' ;;
        almalinux:9|almalinux:9.*|almalinux:10|almalinux:10.*) T_NAME='AlmaLinux 9 and 10' T_FAMILY=dnf T_STATUS=pip T_REPO=- T_PYTHON=python3 T_NOTE='OBS has no RHEL-family target: Noust is installed with pip into /opt/noust.' ;;
        rhel:9|rhel:9.*|rhel:10|rhel:10.*) T_NAME='Red Hat Enterprise Linux 9 and 10' T_FAMILY=dnf T_STATUS=pip T_REPO=- T_PYTHON=python3 T_NOTE='Installed with pip into /opt/noust like Rocky and AlmaLinux, which are the ones run; RHEL itself is not.' ;;
        centos:9|centos:9.*|centos:10|centos:10.*) T_NAME='CentOS Stream 9 and 10' T_FAMILY=dnf T_STATUS=pip T_REPO=- T_PYTHON=python3 T_NOTE='Installed with pip into /opt/noust like Rocky and AlmaLinux, which are the ones run; CentOS Stream itself is not.' ;;
        ol:9|ol:9.*|ol:10|ol:10.*) T_NAME='Oracle Linux 9 and 10' T_FAMILY=dnf T_STATUS=pip T_REPO=- T_PYTHON=python3 T_NOTE='Installed with pip into /opt/noust like Rocky and AlmaLinux, which are the ones run; Oracle Linux itself is not.' ;;
        arch:*) T_NAME='Arch Linux' T_FAMILY=pacman T_STATUS=unsupported T_REPO=- T_PYTHON=python3 T_NOTE='Rolling release with systemd, so it could run Noust, but Noust has no pacman support yet: neither its installer, its updates nor its setup would work.' ;;
        alpine:*) T_NAME='Alpine Linux' T_FAMILY=apk T_STATUS=unsupported T_REPO=- T_PYTHON=python3 T_NOTE='No systemd (OpenRC) and musl: Noust runs every application as a systemd unit.' ;;
        *) return 1 ;;
    esac
}
# END TARGETS

WANT_VERSION="${NOUST_VERSION:-}"
DRY_RUN="${NOUST_DRY_RUN:-0}"
NO_CONSOLE="${NOUST_NO_CONSOLE:-0}"
SKIP_SETUP="${NOUST_SKIP_SETUP:-0}"
METHOD="${NOUST_METHOD:-auto}"
TARGET_OVERRIDE="${NOUST_TARGET:-}"
FORCE="${NOUST_FORCE:-0}"
ASSUME_YES="${NOUST_ASSUME_YES:-0}"
ADMIN_USER="${NOUST_ADMIN_USER:-}"
ADMIN_PASSWORD_FILE="${NOUST_ADMIN_PASSWORD_FILE:-}"
JSON="${NOUST_JSON:-0}"
PRINT_PLATFORM=0
REPO_BASE="${NOUST_REPO_BASE:-$REPO_BASE_DEFAULT}"
KEY_FPR="${NOUST_REPO_KEY_FPR:-$REPO_KEY_FPR_PINNED}"
OS_RELEASE_FILE="${NOUST_OS_RELEASE:-}"

#: Where the pip method puts Noust, the same place the integration harness does.
VENV_DIR=/opt/noust
#: What the package method calls the files it adds.
KEYRING_FILE=/usr/share/keyrings/noust.gpg
APT_SOURCE=/etc/apt/sources.list.d/noust.list
RPM_KEY_FILE=/etc/pki/rpm-gpg/RPM-GPG-KEY-noust
DNF_REPO_FILE=/etc/yum.repos.d/noust.repo
ZYPPER_ALIAS=noust

WORK=""
OS_FILE=""
KEY_FILE=""
REPO_URL=""
INSTALL_METHOD=""
CONSOLE_STATE=skipped
ADMIN_STATE=none
OS_ID=""
OS_VERSION=""
OS_LIKE=""
OS_PRETTY=""
ARCH_NAME=""
STATUS=unsupported
DERIVATIVE=""
T_NAME=""
T_FAMILY=""
T_STATUS=""
T_REPO="-"
T_PYTHON=python3
T_NOTE=""
REASONS=""

say() {
    printf '%s\n' "$*"
}

warn() {
    printf 'warning: %s\n' "$*" >&2
}

die() {
    printf 'error: %s\n' "$1" >&2
    if [ "$JSON" = 1 ]; then
        printf '{"status":"error","error":"%s"}\n' "$(json_escape "$1")" >&3
    fi
    if [ "$#" -gt 1 ]; then
        shift
        printf '%s\n' "$*" >&2
    fi
    exit 1
}

# json_escape TEXT: TEXT as the inside of a JSON string.
json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\t' '  '
}

usage() {
    # The header comment is the documentation: print it without its marks.
    if [ -r "$0" ]; then
        sed -n '/^# Noust installer\./,/^set -eu$/p' "$0" | sed -e '/^set -eu$/d' -e 's/^# \{0,1\}//'
    else
        say "Usage: sh install.sh [--version X.Y.Z] [--dry-run] [--no-console] [--yes] [--help]"
    fi
}

have() {
    command -v "$1" >/dev/null 2>&1
}

cleanup() {
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf "$WORK"
    fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

# run COMMAND [ARGS]: execute it, or only say so under --dry-run.
run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '  [dry-run] %s\n' "$*"
        return 0
    fi
    "$@"
}

# confirm QUESTION: yes with --yes, an answer on a terminal, otherwise a refusal
# that says how to go on.
confirm() {
    if [ "$ASSUME_YES" = 1 ] || [ "$DRY_RUN" = 1 ]; then
        return 0
    fi
    if [ ! -t 0 ]; then
        die "$1" "Nobody is at the terminal to ask: run it again with --yes to accept."
    fi
    printf '%s [y/N] ' "$1"
    read -r answer || answer=""
    case "$answer" in
        y | Y | yes | YES) return 0 ;;
        *) die "Stopped: nothing was changed." ;;
    esac
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version)
                [ "$#" -ge 2 ] || usage_error "--version needs a value"
                WANT_VERSION="$2"
                shift
                ;;
            --version=*) WANT_VERSION="${1#--version=}" ;;
            --dry-run) DRY_RUN=1 ;;
            --no-console) NO_CONSOLE=1 ;;
            --skip-setup) SKIP_SETUP=1 ;;
            --method)
                [ "$#" -ge 2 ] || usage_error "--method needs a value"
                METHOD="$2"
                shift
                ;;
            --method=*) METHOD="${1#--method=}" ;;
            --target)
                [ "$#" -ge 2 ] || usage_error "--target needs a value"
                TARGET_OVERRIDE="$2"
                shift
                ;;
            --target=*) TARGET_OVERRIDE="${1#--target=}" ;;
            --force) FORCE=1 ;;
            -y | --yes) ASSUME_YES=1 ;;
            --admin-user)
                [ "$#" -ge 2 ] || usage_error "--admin-user needs a value"
                ADMIN_USER="$2"
                shift
                ;;
            --admin-user=*) ADMIN_USER="${1#--admin-user=}" ;;
            --admin-password-file)
                [ "$#" -ge 2 ] || usage_error "--admin-password-file needs a value"
                ADMIN_PASSWORD_FILE="$2"
                shift
                ;;
            --admin-password-file=*) ADMIN_PASSWORD_FILE="${1#--admin-password-file=}" ;;
            --json) JSON=1 ;;
            --print-platform) PRINT_PLATFORM=1 ;;
            -h | --help)
                usage
                exit 0
                ;;
            *) usage_error "unknown option: $1" ;;
        esac
        shift
    done
}

usage_error() {
    printf 'error: %s\n' "$1" >&2
    printf 'Run "sh install.sh --help" for the options.\n' >&2
    exit 2
}

validate_inputs() {
    case "$METHOD" in
        auto | package | pip) ;;
        *) usage_error "--method is package or pip, not $METHOD" ;;
    esac
    if [ -n "$WANT_VERSION" ]; then
        case "$WANT_VERSION" in
            *[!0-9A-Za-z.+~-]* | "" | -* | .*)
                usage_error "--version is a release number such as 3.4.0, not $WANT_VERSION"
                ;;
        esac
    fi
    if [ -n "$TARGET_OVERRIDE" ]; then
        case "$TARGET_OVERRIDE" in
            *[!0-9A-Za-z._-]* | "" | -* | .*)
                usage_error "--target is a repository directory such as Debian_12, not $TARGET_OVERRIDE"
                ;;
        esac
    fi
    validate_admin
    case "$REPO_BASE" in
        https://*) ;;
        *) usage_error "the repository address must be https: $REPO_BASE" ;;
    esac
    case "$KEY_FPR" in
        *[!0-9A-F]* | "") usage_error "NOUST_REPO_KEY_FPR is 40 upper-case hexadecimal digits" ;;
    esac
    [ "${#KEY_FPR}" -eq 40 ] || usage_error "NOUST_REPO_KEY_FPR is 40 upper-case hexadecimal digits"
}

# validate_admin: the administrator to create is a plain name, and its password comes from a
# file or the environment, never from an argument (an argument is visible to every local user).
validate_admin() {
    if [ -z "$ADMIN_USER" ]; then
        if [ -n "$ADMIN_PASSWORD_FILE" ] || [ -n "${NOUST_ADMIN_PASSWORD:-}" ]; then
            usage_error "a password for the administrator was given, but no --admin-user to give it to"
        fi
        return 0
    fi
    case "$ADMIN_USER" in
        *[!0-9A-Za-z._-]* | -* | .*) usage_error "--admin-user is a name of letters, digits, dots, dashes and underscores, not $ADMIN_USER" ;;
    esac
    [ "${#ADMIN_USER}" -le 64 ] || usage_error "--admin-user is at most 64 characters"
    if [ -n "$ADMIN_PASSWORD_FILE" ]; then
        [ -n "${NOUST_ADMIN_PASSWORD:-}" ] &&
            usage_error "give the administrator's password as a file or as NOUST_ADMIN_PASSWORD, not both"
        [ -r "$ADMIN_PASSWORD_FILE" ] ||
            usage_error "the password file $ADMIN_PASSWORD_FILE cannot be read"
        [ -s "$ADMIN_PASSWORD_FILE" ] || usage_error "the password file $ADMIN_PASSWORD_FILE is empty"
    elif [ -z "${NOUST_ADMIN_PASSWORD:-}" ]; then
        usage_error "--admin-user needs its password: --admin-password-file FILE, or the NOUST_ADMIN_PASSWORD variable"
    fi
}

# os_field NAME: one value of the os-release file, without its quotes. The file is
# read, never sourced: it is data, and a shell would run whatever it held.
os_field() {
    sed -n "s/^$1=//p" "$OS_FILE" | head -n 1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

detect_platform() {
    OS_FILE="$OS_RELEASE_FILE"
    if [ -z "$OS_FILE" ]; then
        OS_FILE=/etc/os-release
        [ -r "$OS_FILE" ] || OS_FILE=/usr/lib/os-release
    fi
    if [ ! -r "$OS_FILE" ]; then
        REASONS="No os-release file could be read ($OS_FILE), so the distribution is unknown."
        OS_ID=unknown
        OS_PRETTY="an unknown system"
    else
        OS_ID=$(os_field ID)
        OS_VERSION=$(os_field VERSION_ID)
        OS_LIKE=$(os_field ID_LIKE)
        OS_PRETTY=$(os_field PRETTY_NAME)
        [ -n "$OS_ID" ] || OS_ID=unknown
        [ -n "$OS_PRETTY" ] || OS_PRETTY="$OS_ID $OS_VERSION"
    fi

    machine="${NOUST_ARCH:-$(uname -m)}"
    case "$machine" in
        x86_64 | amd64) ARCH_NAME=amd64 ;;
        aarch64 | arm64) ARCH_NAME=arm64 ;;
        *) ARCH_NAME="$machine" ;;
    esac

    if target_for "$OS_ID" "$OS_VERSION"; then
        STATUS="$T_STATUS"
    else
        found=""
        for like in $OS_LIKE; do
            if target_for "$like" "$OS_VERSION"; then
                found="$like"
                break
            fi
        done
        if [ -n "$found" ]; then
            DERIVATIVE="$found"
            STATUS="$T_STATUS"
            if [ "$STATUS" = supported ]; then
                STATUS=best-effort
                REASONS="$OS_PRETTY is a derivative of $found: its packages are not the ones the repository was tested against."
            fi
        else
            STATUS=unsupported
            T_NAME="$OS_PRETTY"
            T_FAMILY=""
            T_REPO="-"
            REASONS="$OS_PRETTY is not a release Noust declares support for (docs/PLATFORMS.md lists them)."
        fi
    fi
    if [ "$STATUS" != unsupported ] && [ -n "$T_NOTE" ]; then
        REASONS="${REASONS:+$REASONS }$T_NAME: $T_NOTE"
    fi
    if [ "$STATUS" = unsupported ] && [ -n "$T_NOTE" ] && [ -n "$T_NAME" ]; then
        REASONS="$T_NAME: $T_NOTE"
    fi

    case "$ARCH_NAME" in
        amd64 | arm64) ;;
        *)
            STATUS=unsupported
            REASONS="${REASONS:+$REASONS }The architecture $machine is not supported: Noust declares amd64 and arm64."
            ;;
    esac

    if [ -n "$TARGET_OVERRIDE" ]; then
        T_REPO="$TARGET_OVERRIDE"
        if [ -z "$T_FAMILY" ]; then
            if have apt-get; then
                T_FAMILY=apt
            elif have dnf; then
                T_FAMILY=dnf
            elif have zypper; then
                T_FAMILY=zypper
            fi
        fi
    fi
}

print_platform() {
    printf 'id=%s\n' "$OS_ID"
    printf 'version_id=%s\n' "$OS_VERSION"
    printf 'arch=%s\n' "$ARCH_NAME"
    printf 'name=%s\n' "$T_NAME"
    printf 'family=%s\n' "$T_FAMILY"
    printf 'status=%s\n' "$STATUS"
    printf 'repo=%s\n' "$T_REPO"
    printf 'python=%s\n' "$T_PYTHON"
    printf 'derivative_of=%s\n' "$DERIVATIVE"
    printf 'reasons=%s\n' "$REASONS"
}

# decide_method: package where one exists, pip where none does, as asked otherwise.
decide_method() {
    case "$METHOD" in
        pip) INSTALL_METHOD=pip ;;
        package) INSTALL_METHOD=package ;;
        *)
            if [ "$STATUS" = pip ]; then
                INSTALL_METHOD=pip
            else
                INSTALL_METHOD=package
            fi
            ;;
    esac
    if [ "$INSTALL_METHOD" = package ]; then
        if [ -z "$T_FAMILY" ] || [ "$T_REPO" = "-" ]; then
            die "There is no package for $OS_PRETTY." \
                "Use --method pip, or name a repository directory with --target if you know one fits."
        fi
        REPO_URL="$REPO_BASE/$T_REPO/"
    fi
}

check_platform() {
    case "$STATUS" in
        supported) return 0 ;;
        best-effort)
            warn "$REASONS"
            warn "The platform list does not promise it: it may work (docs/PLATFORMS.md says what was run)."
            confirm "Install on $OS_PRETTY anyway?"
            ;;
        pip)
            warn "$REASONS"
            confirm "Install Noust with pip into $VENV_DIR on $OS_PRETTY?"
            ;;
        *)
            if [ "$FORCE" = 1 ]; then
                warn "$REASONS"
                warn "Going on because of --force."
                return 0
            fi
            if [ "$JSON" = 1 ]; then
                printf '{"status":"refused","error":"%s","platform":{"id":"%s","version_id":"%s","arch":"%s"}}\n' \
                    "$(json_escape "Noust does not install on $OS_PRETTY ($ARCH_NAME).")" \
                    "$(json_escape "$OS_ID")" "$(json_escape "$OS_VERSION")" "$ARCH_NAME" >&3
            fi
            printf 'error: %s\n' "Noust does not install on $OS_PRETTY ($ARCH_NAME)." >&2
            printf '%s\n' "$REASONS" >&2
            printf '%s\n' "The platforms it does install on are listed in docs/PLATFORMS.md." >&2
            printf '%s\n' "To try anyway, run it again with --force and, for a package, --target <repository directory>." >&2
            exit 3
            ;;
    esac
}

require_root() {
    if [ "$DRY_RUN" = 1 ]; then
        return 0
    fi
    if [ "$(id -u)" != 0 ]; then
        die "Noust installs system packages and writes to /etc: run this as root." \
            "For example: sudo sh install.sh"
    fi
}

# download URL DEST
download() {
    if have curl; then
        curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
            --retry 3 --connect-timeout 15 --max-time 180 --output "$2" "$1"
    elif have wget; then
        wget --quiet --https-only --tries=3 --timeout=60 --output-document="$2" "$1"
    else
        die "Neither curl nor wget is installed."
    fi
}

# pkg_install PACKAGE...
pkg_install() {
    case "$T_FAMILY" in
        apt) run env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
        dnf) run dnf install -y "$@" ;;
        zypper) run zypper --non-interactive install "$@" ;;
        *) die "No package manager is known for $OS_PRETTY." ;;
    esac
}

install_prerequisites() {
    missing=""
    if ! have curl && ! have wget; then
        missing="curl"
    fi
    if [ "$INSTALL_METHOD" = package ] && ! have gpg; then
        case "$T_FAMILY" in
            apt) missing="$missing gnupg" ;;
            dnf) missing="$missing gnupg2" ;;
            zypper) missing="$missing gpg2" ;;
        esac
    fi
    if [ "$T_FAMILY" = apt ] && [ ! -d /etc/ssl/certs ]; then
        missing="$missing ca-certificates"
    fi
    if [ -z "$missing" ]; then
        return 0
    fi
    say "Installing what the installer itself needs:$missing"
    if [ "$T_FAMILY" = apt ]; then
        run apt-get update
    fi
    # shellcheck disable=SC2086 # a list of package names, split on purpose
    pkg_install $missing
}

# fingerprint_of FILE: the fingerprint of the one public key the file holds, read
# with a throwaway keyring so nothing is imported anywhere.
fingerprint_of() {
    # Only sed, grep and head: a minimal image has no awk.
    GNUPGHOME="$WORK/gnupg" gpg --batch --no-tty --show-keys --with-colons --fingerprint "$1" 2>/dev/null |
        sed -n 's/^fpr:::::::::\([0-9A-F]*\):.*/\1/p' | head -n 1
}

# key_state FILE: how many public keys it holds, and the validity of the first.
key_state() {
    pubs=$(GNUPGHOME="$WORK/gnupg" gpg --batch --no-tty --show-keys --with-colons "$1" 2>/dev/null |
        sed -n 's/^pub:\([^:]*\):.*/\1/p')
    if [ -z "$pubs" ]; then
        printf '0 \n'
        return 0
    fi
    printf '%s %s\n' "$(printf '%s\n' "$pubs" | grep -c .)" "$(printf '%s\n' "$pubs" | head -n 1)"
}

# fetch_key URL: download the repository key and refuse it unless it is the one
# this file pins. Leaves it in $KEY_FILE.
fetch_key() {
    KEY_FILE="$WORK/repository.key"
    say "Downloading the repository key: $1"
    download "$1" "$KEY_FILE"
    state=$(key_state "$KEY_FILE")
    count="${state%% *}"
    validity="${state#* }"
    if [ "$count" != 1 ]; then
        die "The repository key file holds $count keys, not one. Nothing was installed."
    fi
    case "$validity" in
        e) die "The repository key has expired." \
            "Install a newer installer: https://github.com/Perkybeet/noust/releases/latest" ;;
        r) die "The repository key was revoked. Nothing was installed." ;;
    esac
    found=$(fingerprint_of "$KEY_FILE")
    if [ "$found" != "$KEY_FPR" ]; then
        die "The repository key does not have the expected fingerprint. Nothing was installed." \
            "Expected: $KEY_FPR
Received: ${found:-none}
If the project key was rotated, take a newer install.sh from https://github.com/Perkybeet/noust/releases/latest
(or, once you have confirmed the new fingerprint with the maintainers, pass it as NOUST_REPO_KEY_FPR)."
    fi
    say "Key fingerprint verified: $found"
}

# repository_configured: another file already names this repository (the
# instructions Noust used to give wrote home_Perkybeet.list).
repository_configured() {
    case "$T_FAMILY" in
        apt)
            for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do
                [ -f "$f" ] || continue
                [ "$f" = "$APT_SOURCE" ] && continue
                if grep -qs "repositories/home:/\{0,1\}Perkybeet" "$f"; then
                    return 0
                fi
            done
            ;;
        dnf)
            for f in /etc/yum.repos.d/*.repo; do
                [ -f "$f" ] || continue
                [ "$f" = "$DNF_REPO_FILE" ] && continue
                if grep -qs "repositories/home:/\{0,1\}Perkybeet" "$f"; then
                    return 0
                fi
            done
            ;;
        zypper)
            if have zypper && zypper --non-interactive repos --uri 2>/dev/null | grep -q "repositories/home:/\{0,1\}Perkybeet"; then
                return 0
            fi
            ;;
    esac
    return 1
}

setup_repository() {
    say "Repository: $REPO_URL"
    if [ "$DRY_RUN" = 1 ]; then
        say "  [dry-run] download the key, check that its fingerprint is $KEY_FPR"
    fi
    case "$T_FAMILY" in
        apt) setup_apt_repository ;;
        dnf) setup_dnf_repository ;;
        zypper) setup_zypper_repository ;;
        *) die "No package manager is known for $OS_PRETTY." ;;
    esac
}

setup_apt_repository() {
    if repository_configured; then
        say "The Noust repository is already configured by another file; keeping it."
        run apt-get update
        return 0
    fi
    if [ "$DRY_RUN" = 1 ]; then
        say "  [dry-run] write $KEYRING_FILE (the verified key) and $APT_SOURCE"
        say "  [dry-run] deb [signed-by=$KEYRING_FILE] $REPO_URL /"
        say "  [dry-run] apt-get update"
        return 0
    fi
    fetch_key "${REPO_URL}Release.key"
    GNUPGHOME="$WORK/gnupg" gpg --batch --no-tty --dearmor <"$KEY_FILE" >"$WORK/noust.gpg"
    install -d -m 755 "$(dirname "$KEYRING_FILE")"
    install -m 644 "$WORK/noust.gpg" "$KEYRING_FILE"
    printf 'deb [signed-by=%s] %s /\n' "$KEYRING_FILE" "$REPO_URL" >"$WORK/noust.list"
    install -m 644 "$WORK/noust.list" "$APT_SOURCE"
    apt-get update
}

setup_dnf_repository() {
    if repository_configured; then
        say "The Noust repository is already configured by another file; keeping it."
        return 0
    fi
    if [ "$DRY_RUN" = 1 ]; then
        say "  [dry-run] write $RPM_KEY_FILE (the verified key) and $DNF_REPO_FILE"
        say "  [dry-run] rpm --import $RPM_KEY_FILE"
        return 0
    fi
    fetch_key "${REPO_URL}repodata/repomd.xml.key"
    install -d -m 755 "$(dirname "$RPM_KEY_FILE")"
    install -m 644 "$KEY_FILE" "$RPM_KEY_FILE"
    # The key is the file just verified, not whatever the server says later, and
    # it is in rpm's keyring before dnf could ask whether to trust one.
    rpm --import "$RPM_KEY_FILE"
    {
        printf '[noust]\n'
        printf 'name=Noust (home:Perkybeet, %s)\n' "$T_REPO"
        printf 'type=rpm-md\n'
        printf 'baseurl=%s\n' "$REPO_URL"
        printf 'enabled=1\n'
        printf 'gpgcheck=1\n'
        printf 'repo_gpgcheck=1\n'
        printf 'gpgkey=file://%s\n' "$RPM_KEY_FILE"
    } >"$WORK/noust.repo"
    install -m 644 "$WORK/noust.repo" "$DNF_REPO_FILE"
}

setup_zypper_repository() {
    if repository_configured; then
        say "The Noust repository is already configured; keeping it."
        return 0
    fi
    if [ "$DRY_RUN" = 1 ]; then
        say "  [dry-run] rpm --import (the verified key)"
        say "  [dry-run] zypper addrepo --refresh $REPO_URL $ZYPPER_ALIAS"
        return 0
    fi
    fetch_key "${REPO_URL}repodata/repomd.xml.key"
    install -d -m 755 "$(dirname "$RPM_KEY_FILE")"
    install -m 644 "$KEY_FILE" "$RPM_KEY_FILE"
    rpm --import "$RPM_KEY_FILE"
    zypper --non-interactive removerepo "$ZYPPER_ALIAS" >/dev/null 2>&1 || true
    zypper --non-interactive addrepo --refresh --name Noust "$REPO_URL" "$ZYPPER_ALIAS"
    zypper --non-interactive refresh "$ZYPPER_ALIAS"
}

install_package() {
    case "$T_FAMILY" in
        apt)
            if [ -n "$WANT_VERSION" ]; then
                say "Installing noust $WANT_VERSION"
                run env DEBIAN_FRONTEND=noninteractive apt-get install -y --install-recommends \
                    --allow-downgrades "noust=$WANT_VERSION-*"
            else
                say "Installing noust"
                run env DEBIAN_FRONTEND=noninteractive apt-get install -y --install-recommends noust
            fi
            ;;
        dnf)
            if [ -n "$WANT_VERSION" ]; then
                say "Installing noust $WANT_VERSION"
                run dnf install -y "noust-$WANT_VERSION"
            else
                say "Installing noust"
                run dnf install -y noust
            fi
            install_console_packages
            ;;
        zypper)
            if [ -n "$WANT_VERSION" ]; then
                say "Installing noust $WANT_VERSION"
                run zypper --non-interactive install "noust=$WANT_VERSION"
            else
                say "Installing noust"
                run zypper --non-interactive install noust
            fi
            install_console_packages
            ;;
    esac
}

# install_console_packages: the RPM packages only Suggest what the console runs on,
# and neither dnf nor zypper installs a Suggests. A name a release lacks is a
# warning, not a failure: `noust web install` finds the rest with pip.
install_console_packages() {
    say "Installing the console's Python packages"
    packages=""
    for name in fastapi starlette pydantic uvicorn psutil httpx websockets cryptography; do
        packages="$packages $T_PYTHON-$name"
    done
    # One transaction when every name exists, which is the usual case and one
    # metadata refresh instead of eight; one by one only to say which is missing.
    # shellcheck disable=SC2086 # a list of package names, split on purpose
    if pkg_install $packages; then
        return 0
    fi
    for package in $packages; do
        if ! pkg_install "$package"; then
            warn "$package could not be installed; 'noust web install' will look for it."
        fi
    done
}

# python_for_pip: print an interpreter of 3.10 or later, installing one on the
# releases that keep it beside an older default.
python_for_pip() {
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if have "$candidate" && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

install_with_pip() {
    say "Installing Noust with pip into $VENV_DIR"
    if [ "$DRY_RUN" = 1 ]; then
        say "  [dry-run] a Python of 3.10 or later (installed with $T_FAMILY when missing)"
        say "  [dry-run] python -m venv $VENV_DIR"
        say "  [dry-run] $VENV_DIR/bin/pip install 'noust[all]${WANT_VERSION:+==$WANT_VERSION}'"
        say "  [dry-run] link $VENV_DIR/bin/noust and wasm into /usr/local/bin"
        return 0
    fi
    if ! python_bin=$(python_for_pip); then
        case "$T_FAMILY" in
            dnf) pkg_install python3.12 python3.12-pip ;;
            apt) pkg_install python3 python3-venv python3-pip ;;
            zypper) pkg_install python311 python311-pip ;;
        esac
        python_bin=$(python_for_pip) ||
            die "No Python 3.10 or later could be installed." \
                "Install one by hand (python3.12 on the RHEL family), then run this again."
    fi
    "$python_bin" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    if [ -n "$WANT_VERSION" ]; then
        "$VENV_DIR/bin/pip" install --quiet "noust[all]==$WANT_VERSION"
    else
        "$VENV_DIR/bin/pip" install --quiet --upgrade "noust[all]"
    fi
    ln -sf "$VENV_DIR/bin/noust" /usr/local/bin/noust
    ln -sf "$VENV_DIR/bin/wasm" /usr/local/bin/wasm
}

have_systemd() {
    [ -d /run/systemd/system ]
}

console_configured() {
    [ -f /etc/systemd/system/noust-web.service ] || [ -f /etc/systemd/system/wasm-web.service ]
}

setup_initialised() {
    [ -f /etc/noust/config.yaml ] || [ -f /etc/wasm/config.yaml ]
}

post_install() {
    if [ "$DRY_RUN" = 1 ]; then
        if [ "$SKIP_SETUP" != 1 ]; then
            say "  [dry-run] noust setup init -y"
        fi
        if [ "$NO_CONSOLE" != 1 ]; then
            say "  [dry-run] noust web enable"
        fi
        return 0
    fi
    have noust || die "noust is not on the PATH after the installation." \
        "Look at the output above; the package manager says why."
    say "Installed: $(noust --version 2>/dev/null || echo noust)"

    if [ "$SKIP_SETUP" = 1 ]; then
        say "Skipping noust setup init (--skip-setup)."
    elif setup_initialised; then
        say "This server is already initialised; leaving its configuration alone."
    else
        say "Preparing the server: noust setup init -y"
        noust setup init -y ||
            die "noust setup init did not finish." \
                "Fix what it reports, then run: noust setup init -y"
    fi

    if [ "$NO_CONSOLE" = 1 ]; then
        say "Not setting the console up (--no-console)."
        CONSOLE_STATE=skipped
    elif console_configured; then
        say "The console is already set up as a service; leaving it as it is."
        CONSOLE_STATE=existing
    elif ! have_systemd; then
        warn "This machine is not running systemd, so the console cannot be a service here."
        CONSOLE_STATE=skipped
    else
        say "Starting the console: noust web enable"
        if noust web enable; then
            CONSOLE_STATE=new
        else
            warn "The console did not start. Run 'noust web install' and then 'noust web enable'."
            CONSOLE_STATE=failed
        fi
    fi
}

# create_admin: the administrator asked for with --admin-user, once, whatever number of
# times the installer runs. The password is read from the file or the environment by
# `noust user create --stdin`; it is on no command line and in no log.
create_admin() {
    [ -n "$ADMIN_USER" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        say "  [dry-run] noust user create $ADMIN_USER --role admin --stdin"
        ADMIN_STATE=would-create
        return 0
    fi
    if noust user list --json 2>/dev/null | grep -qF "\"username\": \"$ADMIN_USER\""; then
        say "The account $ADMIN_USER exists already; leaving it as it is."
        ADMIN_STATE=existing
        return 0
    fi
    say "Creating the administrator account $ADMIN_USER"
    if [ -n "$ADMIN_PASSWORD_FILE" ]; then
        noust user create "$ADMIN_USER" --role admin --stdin <"$ADMIN_PASSWORD_FILE" ||
            die "The account $ADMIN_USER was not created." \
                "Read what noust said above (the password may be too short); create it with: noust user create $ADMIN_USER --role admin"
    else
        printf '%s\n' "$NOUST_ADMIN_PASSWORD" | noust user create "$ADMIN_USER" --role admin --stdin ||
            die "The account $ADMIN_USER was not created." \
                "Read what noust said above (the password may be too short); create it with: noust user create $ADMIN_USER --role admin"
    fi
    unset NOUST_ADMIN_PASSWORD
    ADMIN_STATE=created
}

# print_json: the one document --json promises, on the standard output kept in descriptor 3.
print_json() {
    if [ "$DRY_RUN" = 1 ]; then
        installed="${WANT_VERSION:-latest}"
    else
        installed=$(noust --version 2>/dev/null | sed -n 's/^[A-Za-z]* \([0-9][^ ]*\).*/\1/p' | head -n 1)
    fi
    if [ "${CONSOLE_STATE:-skipped}" = new ] || [ "${CONSOLE_STATE:-skipped}" = existing ]; then
        console_address='"127.0.0.1:8080"'
    else
        console_address=null
    fi
    if [ -n "$ADMIN_USER" ]; then
        admin=$(printf '{"username":"%s","state":"%s"}' "$ADMIN_USER" "$ADMIN_STATE")
    else
        admin=null
    fi
    printf '{"status":"ok","dry_run":%s,"version":"%s","method":"%s","platform":{"id":"%s","version_id":"%s","arch":"%s","support":"%s"},"console":{"state":"%s","address":%s},"admin":%s}\n' \
        "$([ "$DRY_RUN" = 1 ] && echo true || echo false)" \
        "$(json_escape "$installed")" "$INSTALL_METHOD" "$(json_escape "$OS_ID")" \
        "$(json_escape "$OS_VERSION")" "$ARCH_NAME" "$STATUS" "$CONSOLE_STATE" \
        "$console_address" "$admin" >&3
}

next_steps() {
    say ""
    if [ "$DRY_RUN" = 1 ]; then
        say "Dry run: nothing was changed."
        return 0
    fi
    say "Noust is installed."
    case "${CONSOLE_STATE:-skipped}" in
        new | existing)
            say ""
            say "The console listens on 127.0.0.1:8080 of this server, and on purpose nowhere else."
            if [ "${CONSOLE_STATE}" = new ]; then
                say "Its access token is in the box printed above; it is shown only once."
            fi
            say ""
            say "To open it from your own computer, through SSH:"
            say "  ssh -N -L 8080:127.0.0.1:8080 root@<this server's address>"
            say "  then browse to http://127.0.0.1:8080 and sign in with the token."
            say ""
            say "To publish it on a name of your own, with a certificate (opt-in; it needs two-factor"
            say "sign-in on every account, so create yours first):"
            say "  noust console expose --domain console.example.com --allow-ip <your address>"
            ;;
        *)
            say "Set the console up when you want it: noust web enable"
            ;;
    esac
    if [ "$ADMIN_STATE" = created ]; then
        say ""
        say "Sign in as $ADMIN_USER: the console asks it to enrol its authenticator first."
    fi
    say ""
    say "Then deploy something: noust create --help"
}

main() {
    parse_args "$@"
    if [ "$JSON" = 1 ]; then
        # Descriptor 3 is the real standard output, kept for the one JSON document; everything
        # the installer and the programs it runs say goes to standard error.
        exec 3>&1 1>&2
    fi
    validate_inputs
    detect_platform
    if [ "$PRINT_PLATFORM" = 1 ]; then
        print_platform
        exit 0
    fi
    say "Noust installer: $OS_PRETTY, $ARCH_NAME ($STATUS)"
    check_platform
    decide_method
    require_root
    if [ "$DRY_RUN" = 1 ]; then
        say "Dry run: no file is written and no command that changes the system is run."
    else
        # One private directory for the key and the files written from it, removed
        # on every way out (cleanup is the EXIT trap).
        WORK=$(mktemp -d)
        chmod 700 "$WORK"
        mkdir -m 700 "$WORK/gnupg"
    fi
    install_prerequisites
    if [ "$INSTALL_METHOD" = pip ]; then
        install_with_pip
    else
        setup_repository
        install_package
    fi
    post_install
    create_admin
    next_steps
    if [ "$JSON" = 1 ]; then
        print_json
    fi
}

main "$@"
