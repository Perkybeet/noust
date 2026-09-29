#!/bin/sh
# Build a staged OBS package the way OBS does, on the host it runs on.
#
# Usage: packaging/obs/build.sh deb|rpm STAGED_DIR OUTDIR
#
# deb: unpacks the tarball and makes debian/ from the debian.* files only, as
#      OBS's debtransform does, then dpkg-buildpackage -b. The build
#      dependencies must already be installed (mk-build-deps on
#      STAGED_DIR/debian.control does it).
# rpm: rpmbuild -ba with the staged directory as its SOURCES.
#
# The built packages are copied into OUTDIR.
set -eu

kind=${1:?usage: build.sh deb|rpm STAGED_DIR OUTDIR}
staged=$(cd "${2:?usage: build.sh deb|rpm STAGED_DIR OUTDIR}" && pwd)
out=${3:?usage: build.sh deb|rpm STAGED_DIR OUTDIR}
mkdir -p "$out"
out=$(cd "$out" && pwd)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

case "$kind" in
    deb)
        dsc=$(ls "$staged"/*.dsc)
        name=$(sed -n 's/^Source: //p' "$dsc")
        upstream=$(sed -n 's/^Version: \(.*\)-[^-]*$/\1/p' "$dsc")
        tar -xzf "$staged/${name}-${upstream}.tar.gz" -C "$work"
        src="$work/${name}-${upstream}"
        mkdir -p "$src/debian"
        for file in "$staged"/debian.*; do
            base=$(basename "$file")
            cp "$file" "$src/debian/${base#debian.}"
        done
        chmod +x "$src/debian/rules"
        (cd "$src" && dpkg-buildpackage -us -uc -b)
        cp "$work"/*.deb "$out/"
        ;;
    rpm)
        spec=$(ls "$staged"/*.spec)
        rpmbuild --define "_topdir $work" --define "_sourcedir $staged" -ba "$spec"
        find "$work/RPMS" -name '*.rpm' -exec cp {} "$out/" \;
        ;;
    *)
        echo "Unknown kind '$kind': deb or rpm" >&2
        exit 1
        ;;
esac

echo "Built into $out:"
ls -1 "$out" | sed 's/^/  /'
