#!/bin/sh
# Run packaging/obs/upgrade-test.sh on a machine with systemd as PID 1.
#
# Usage: packaging/obs/upgrade-in-systemd.sh deb|rpm OLD_REPOSITORY
#
# From the root of a git checkout, on a host with Docker (a GitHub runner, a
# workstation). Starts a privileged Debian 12 (deb) or Fedora 43 (rpm)
# container with systemd as init, the same pattern tests/integration/run.py
# uses, builds the staged packages of this checkout inside it and upgrades a
# 2.x installation from OLD_REPOSITORY to them. systemd is what makes the
# units part of the test: wasm-web and wasm-monitor must come out of the
# upgrade as noust-web and noust-monitor, in the state they were in.
set -eu

kind=${1:?usage: upgrade-in-systemd.sh deb|rpm OLD_REPOSITORY}
old=${2:?usage: upgrade-in-systemd.sh deb|rpm OLD_REPOSITORY}
root=$(cd "$(dirname "$0")/../.." && pwd)
name="noust-upgrade-$kind-$$"
image="noust-upgrade-$kind"

case "$kind" in
    deb)
        dockerfile='FROM debian:12
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv dbus \
        devscripts equivs git ca-certificates \
    && systemctl mask getty.target console-getty.service systemd-udev-trigger.service \
        systemd-udevd.service systemd-remount-fs.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]'
        build='for package in noust wasm; do
                 mk-build-deps --install --remove --tool "apt-get -y --no-install-recommends" "/tmp/obs/$package/debian.control"
               done'
        ;;
    rpm)
        dockerfile='FROM fedora:43
RUN dnf install -y systemd rpm-build python3-devel python3-setuptools python3-pip python3-wheel git tar gzip \
    && systemctl mask getty.target console-getty.service systemd-udev-trigger.service \
        systemd-udevd.service systemd-remount-fs.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]'
        build=true
        ;;
    *)
        echo "Unknown kind '$kind': deb or rpm" >&2
        exit 1
        ;;
esac

echo "$dockerfile" | docker build -t "$image" -
docker run -d --name "$name" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    -v "$root:/src:ro" "$image" > /dev/null
trap 'docker rm -f "$name" > /dev/null 2>&1 || true' EXIT

state=
for _ in $(seq 1 60); do
    state=$(docker exec "$name" systemctl is-system-running 2> /dev/null || true)
    case "$state" in running|degraded) break ;; esac
    sleep 1
done
case "$state" in
    running|degraded) echo "systemd is up ($state)" ;;
    *) echo "systemd did not start in the container ($state)" >&2; exit 1 ;;
esac

docker exec "$name" sh -euc "
    cp -a /src /work && cd /work
    git config --global --add safe.directory /work
    packaging/obs/stage.sh noust /tmp/obs/noust
    packaging/obs/stage.sh wasm /tmp/obs/wasm
    $build
    packaging/obs/build.sh $kind /tmp/obs/noust /tmp/new
    packaging/obs/build.sh $kind /tmp/obs/wasm /tmp/new
    packaging/obs/upgrade-test.sh $kind '$old' /tmp/new
"
