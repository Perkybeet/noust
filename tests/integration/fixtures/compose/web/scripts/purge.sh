#!/bin/sh
# A post_deploy hook: it runs once the new version answers. A FAIL_POST file in the commit
# makes it fail, which leaves the deploy done with a warning instead of undoing it.
set -eu
if [ -f /app/FAIL_POST ]; then
    echo "cache purge failed: the cache server is down" >&2
    exit 1
fi
echo "purged by $(cat /app/VERSION)" >> /data/purge.log
echo "cache purged by $(cat /app/VERSION)"
