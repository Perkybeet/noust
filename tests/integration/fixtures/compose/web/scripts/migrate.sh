#!/bin/sh
# A pre_deploy hook, as the scenarios declare it in noust.yaml: it runs inside the image the
# deploy just built, against the service's own volume, before anything is recreated.
# A FAIL_HOOK file in the commit makes it fail the way a migration that cannot apply does.
set -eu
if [ -f /app/FAIL_HOOK ]; then
    echo "migration exploded: table users is locked" >&2
    exit 1
fi
echo "migrated by $(cat /app/VERSION)" >> /data/migrations.log
echo "migration applied by $(cat /app/VERSION)"
