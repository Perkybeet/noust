#!/bin/sh
# Packs Noust for the UGREEN App Center: stages this folder in a directory of its own, exports the
# release's image for amd64 and arm64 into it, and runs UGREEN's `ugcli pack` in a Debian 12
# container (their documentation asks for Linux, so the Unix modes in the package are right), as the
# calling user. docs/ugreen.md is the guide.
#
#   UGCLI=/path/to/ugcli-linux-amd64 sh packaging/ugreen/build.sh [build number, 1 by default]
#
# UGCLI is the binary downloaded from https://developer.ugnas.com. OUT is where the work is done and
# where the .upk files land (build_dir/pkgs/upk/ under it): a temporary directory by default, and
# never inside this repository, so a package cannot be committed by accident. The image has to be
# published already: the package carries exactly that image, by its version.
#
# Nothing here signs anything. UGREEN's developer signature is a file for the NAS you test on (App
# Center > Settings > developer settings), it never comes near this folder, and no .upk is kept in
# the repository: each version is packed again and sent to UGREEN for its review.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
repository=$(cd "$here/../.." && pwd -P)
build=${1:-1}
: "${UGCLI:?set UGCLI to the ugcli-linux-amd64 binary downloaded from developer.ugnas.com}"
[ -x "$UGCLI" ] || {
    echo "UGCLI is not an executable file: $UGCLI" >&2
    exit 2
}
case "$build" in
'' | *[!0-9]*)
    echo "the build number is a number: $build" >&2
    exit 2
    ;;
esac

app_id=$(sed -n 's/^app_id: *//p' "$here/project.yaml")
version=$(sed -n 's/^version: *//p' "$here/project.yaml")
[ -n "$app_id" ] && [ -n "$version" ] || {
    echo "project.yaml has no app_id or no version" >&2
    exit 1
}
image="ghcr.io/perkybeet/noust:$version"
grep -q "image: $image\$" "$here/rootfs_common/docker-compose.yaml" || {
    echo "docker-compose.yaml does not use $image (project.yaml says $version)" >&2
    exit 1
}

out=${OUT:-${TMPDIR:-/tmp}/noust-ugreen-$version}
case "$out" in
/*) ;;
*) out=$PWD/$out ;;
esac
# Judged by the nearest directory that exists, before anything is created, and with symbolic links
# and `..` resolved: a path that leads into the repository by a detour is inside it.
probe=$out
while [ ! -d "$probe" ]; do
    probe=$(dirname "$probe")
done
case "$(cd "$probe" && pwd -P)" in
"$repository" | "$repository"/*)
    echo "OUT is inside the repository ($out): choose a directory outside it" >&2
    exit 2
    ;;
esac
mkdir -p "$out"
out=$(cd "$out" && pwd -P)

project="$out/$app_id"
rm -rf "$project"
mkdir -p "$project"
cp "$here/project.yaml" "$project/project.yaml"
cp -R "$here/rootfs_common" "$project/rootfs_common"

for arch in amd64 arm64; do
    images="$project/rootfs_$arch/images"
    mkdir -p "$images"
    docker pull --quiet --platform "linux/$arch" "$image"
    docker save --platform "linux/$arch" -o "$images/noust-$version-$arch.tar" "$image"
    chmod 0644 "$images/noust-$version-$arch.tar"
done

docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$project:/work" -v "$UGCLI:/usr/local/bin/ugcli:ro" -w /work \
    debian:12 sh -c "ugcli check && ugcli pack --arch all --build $build"
ls -l "$project"/build_dir/pkgs/upk/
