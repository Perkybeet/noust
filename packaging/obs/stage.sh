#!/bin/sh
# Lay out exactly the files an OBS package receives.
#
# Usage: packaging/obs/stage.sh [--snapshot] noust OUTDIR
#
#   noust  the product: noust-VERSION.tar.gz (git archive of HEAD), noust.spec,
#          noust.dsc and every obs/debian.* file.
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
# A tree is released when obs/debian.changelog's top entry is noust at the
# version in pyproject.toml: scripts/release.py writes the two together. A tree
# between releases has a top entry for the previous one, so dpkg refuses the
# source outright. --snapshot, for builds that are tested and never published,
# stages such a tree as a pre-release (VERSION~dev, below the release it
# precedes in both dpkg and rpm), with a changelog entry saying so. Without it,
# a tree that is not released is refused, so a snapshot can never reach OBS.
set -eu

usage="usage: stage.sh [--snapshot] noust OUTDIR"
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
    snapshot="${version}~dev"
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
    *)
        echo "Unknown package '$package': noust" >&2
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
