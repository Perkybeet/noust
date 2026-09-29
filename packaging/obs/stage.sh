#!/bin/sh
# Lay out exactly the files an OBS package receives.
#
# Usage: packaging/obs/stage.sh noust|wasm OUTDIR
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
set -eu

package=${1:?usage: stage.sh noust|wasm OUTDIR}
out=${2:?usage: stage.sh noust|wasm OUTDIR}
root=$(cd "$(dirname "$0")/../.." && pwd)

version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$root/pyproject.toml" | head -n 1)
[ -n "$version" ] || { echo "No version in pyproject.toml" >&2; exit 1; }

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

echo "Staged OBS package '$package' $version in $out:"
ls -1 "$out" | sed 's/^/  /'
