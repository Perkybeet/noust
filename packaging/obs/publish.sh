#!/bin/sh
# Replace the sources of an OBS package with a staged set, whatever it held.
#
# Usage: packaging/obs/publish.sh PROJECT PACKAGE STAGED_DIR MESSAGE
#
# Needs a configured osc. The package must exist (it is created on
# build.opensuse.org, not here). What this does to it:
#   - prints what the package holds and its metadata, so a failed or odd
#     build can be explained from the workflow log alone;
#   - turns a linked package (_link, from "branch" or "link" in the web UI)
#     into one with sources of its own, since committing files on top of a
#     link commits them as changes to the other package's sources;
#   - removes every file that is not in the staged set. OBS turns every
#     debian.* file into debian/, so one stale file uploaded by hand years ago
#     is part of every build: the wasm package shipped a config.yaml that
#     still enabled the AI monitor removed in 1.x because of exactly that;
#   - commits the staged files. A commit with nothing changed is a no-op, so
#     re-running a release job is safe.
set -eu

project=${1:?usage: publish.sh PROJECT PACKAGE STAGED_DIR MESSAGE}
package=${2:?usage: publish.sh PROJECT PACKAGE STAGED_DIR MESSAGE}
staged=$(cd "${3:?usage: publish.sh PROJECT PACKAGE STAGED_DIR MESSAGE}" && pwd)
message=${4:?usage: publish.sh PROJECT PACKAGE STAGED_DIR MESSAGE}

echo "::group::$project/$package as OBS has it now"
if ! listing=$(osc api "/source/$project/$package"); then
    echo "::error::$project/$package does not exist on OBS, or these credentials cannot read it. Create the package at https://build.opensuse.org/project/show/$project and re-run this job."
    exit 1
fi
echo "$listing"
meta=$(osc meta pkg "$project" "$package")
echo "$meta"
echo "::endgroup::"

if echo "$meta" | grep -q "<disable"; then
    echo "::warning::$project/$package disables some builds in its metadata (<disable>). They stay disabled; check https://build.opensuse.org/package/show/$project/$package if a distribution is missing."
fi

if echo "$listing" | grep -q 'name="_link"'; then
    echo "::warning::$project/$package was a link to another package (_link). Removing the link so it holds its own sources."
    osc api -X DELETE "/source/$project/$package/_link"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
osc checkout "$project" "$package"
cd "$project/$package"

for existing in * .[!.]*; do
    [ -e "$existing" ] || continue
    [ "$existing" = ".osc" ] && continue
    if [ ! -e "$staged/$existing" ]; then
        echo "::notice::$project/$package: removing $existing, which this release does not ship"
        rm -rf "$existing"
    fi
done

cp "$staged"/* .
osc addremove
osc status
osc commit -m "$message"

echo "Committed to https://build.opensuse.org/package/show/$project/$package"
