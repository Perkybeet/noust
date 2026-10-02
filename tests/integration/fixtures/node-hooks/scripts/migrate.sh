#!/bin/sh
# A pre_deploy hook for a Node release. It prints what a migration prints; a FAIL_HOOK file in
# the commit makes it fail the way one that cannot apply does.
set -eu
if [ -f FAIL_HOOK ]; then
    echo "migration exploded: table orders is locked" >&2
    exit 1
fi
echo "migration applied for $(cat VERSION)"
