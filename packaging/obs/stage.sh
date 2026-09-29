#!/bin/sh
# Lay out exactly the files an OBS package receives.
#
# Usage: packaging/obs/stage.sh [--snapshot] noust|wasm OUTDIR
#
#   noust  the product: noust-VERSION.tar.gz (git archive of HEAD), noust.spec,
#          noust.dsc and every obs/debian.* file.
#   wasm   the transitional package: wasm-VERSION.tar.gz (a README and the
#          licence), the wasm-cli spec, wasm.dsc and its debian.* files.
#
# One implementation of "what OBS gets": the Release workflow uploads these
# directories, and the build gates in ci.yml and release.yml build from them
# with packaging/obs/build.sh. OBS only ever turns files named debian.* into
# debian/ (debtransform); a file a recipe expected under any other name was
# silently missing there while the GitHub build, which copied it by hand,
# passed. Building from the staged set is what makes the gate mean something.
#
# Only committed files ship: the tarball is `git archive HEAD`.
#
# A tree is released under its own name when obs/debian.changelog's top entry
# is noust at the version in pyproject.toml: scripts/release.py writes the two
# together. Between the rename and the release that makes it (3.0.0), the top
# entry still says wasm, so dpkg refuses the source outright, and the
# transitional packages would carry a 2.x version that noust itself Breaks and
# Conflicts with. --snapshot, for builds that are tested and never published,
# stages such a tree as a pre-release of the first version under the new name
# (VERSION~dev, below the release it precedes in both dpkg and rpm), with a
# changelog entry saying so. Without it, a tree that is not released is
# refused, so a snapshot can never reach OBS.
set -eu

usage="usage: stage.sh [--snapshot] noust|wasm OUTDIR"
snapshot_allowed=no
if [ "${1:-}" = --snapshot ]; then
    snapshot_allowed=yes
    shift
fi
package=${1:?$usage}
out=${2:?$usage}
root=$(cd "$(dirname "$0")/../.." && pwd)

version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$root/pyproject.toml" | head -n 1)
[ -n "$version" ] || { echo "No version in pyproject.toml" >&2; exit 1; }

top=$(sed -n '1s/^\([^ ]*\) (\([^)]*\)).*/\1 \2/p' "$root/obs/debian.changelog")
snapshot=
if [ "$top" != "noust ${version}-1" ]; then
    if [ "$snapshot_allowed" != yes ]; then
        echo "obs/debian.changelog starts with '$top', not 'noust ${version}-1':" >&2
        echo "this tree is not a release (scripts/release.py writes both). Pass" >&2
        echo "--snapshot to stage it for a build that is not published." >&2
        exit 1
    fi
    # The first version under the new name, from the one place that states it.
    renamed=$(sed -n 's/^Breaks: wasm (<< \([0-9.]*\)~)$/\1/p' "$root/obs/debian.control")
    [ -n "$renamed" ] || { echo "No 'Breaks: wasm (<< X~)' in obs/debian.control" >&2; exit 1; }
    base=$(printf '%s\n%s\n' "$version" "$renamed" | sort -V | tail -n 1)
    snapshot="${base}~dev"
fi
tree_version=$version
version=${snapshot:-$version}

mkdir -p "$out"
out=$(cd "$out" && pwd)
if [ -n "$(ls -A "$out")" ]; then
    echo "$out is not empty; stage into an empty directory" >&2
    exit 1
fi

case "$package" in
    noust)
        git -C "$root" archive --format=tar.gz --prefix="noust-${version}/" HEAD ':!debian' \
            > "$out/noust-${version}.tar.gz"
        cp "$root/rpm/noust.spec" "$root/obs/noust.dsc" "$out/"
        cp "$root"/obs/debian.* "$out/"
        ;;
    wasm)
        dir="$root/packaging/transitional/wasm"
        work=$(mktemp -d)
        trap 'rm -rf "$work"' EXIT
        mkdir "$work/wasm-${version}"
        cp "$dir/README" "$root/LICENSE" "$work/wasm-${version}/"
        # Reproducible: the same commit gives the same tarball, so OBS sees no
        # change (and rebuilds nothing) when a release does not touch it.
        stamp=$(git -C "$root" log -1 --format=%ct HEAD)
        tar --sort=name --mtime="@${stamp}" --owner=0 --group=0 --numeric-owner \
            -C "$work" -cf - "wasm-${version}" | gzip -n > "$out/wasm-${version}.tar.gz"
        cp "$dir/wasm.spec" "$dir/wasm.dsc" "$out/"
        cp "$dir"/debian.* "$out/"
        # The licence text Debian requires, from the one copy of it.
        cp "$root/obs/debian.copyright" "$out/debian.copyright"
        ;;
    *)
        echo "Unknown package '$package': noust or wasm" >&2
        exit 1
        ;;
esac

if [ -n "$snapshot" ]; then
    commit=$(git -C "$root" rev-parse --short HEAD)
    date=$(git -C "$root" log -1 --format=%cD HEAD)
    changelog="$out/debian.changelog"
    signature=$(sed -n 's/^ -- \(.*>\)  .*/\1/p' "$changelog" | head -n 1)
    {
        printf '%s (%s-1) UNRELEASED; urgency=medium\n\n' "$package" "$snapshot"
        printf '  * Snapshot of %s (%s), not a release\n\n' "$commit" "$tree_version"
        printf ' -- %s  %s\n\n' "$signature" "$date"
        cat "$changelog"
    } > "$changelog.new"
    mv "$changelog.new" "$changelog"
    for spec in "$out"/*.spec; do
        sed -i "s/^Version:\\([[:space:]]*\\).*/Version:\\1${snapshot}/" "$spec"
    done
    sed -i -e "s/^Version: .*/Version: ${snapshot}-1/" \
        -e "s/ ${package}-${tree_version}\\.tar\\.gz\$/ ${package}-${snapshot}.tar.gz/" "$out"/*.dsc
    echo "Staged as snapshot $snapshot of $commit: this tree is not a release"
fi

echo "Staged OBS package '$package' $version in $out:"
ls -1 "$out" | sed 's/^/  /'
