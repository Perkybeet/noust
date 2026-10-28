# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_importers_dokku.py`` that other tests build on."""

from __future__ import annotations

from noust.core.runner import FakeRunner

APPS = "node-js-app\nworker-app\n"

ENV = """\
export DATABASE_URL='postgres://postgres:s3cret@dokku-postgres-shop-db:5432/shop_db'
export REDIS_URL='redis://:pw@dokku-redis-cache:6379'
export NODE_ENV='production'
export GREETING='it'\\''s a "quoted" value'
export DOKKU_APP_RESTORE='1'
export DOKKU_PROXY_PORT='80'
export GIT_REV='abc123'
export SESSION_SECRET='hunter2'
"""

CRON = """\
ID                                    Schedule   Command
cGhwPT09cGhwIHRlc3QucGhwPT09QGRhaWx5  @daily     node index.js
cGhwPT09dHJ1ZT09PSogKiAqICogKg==      * * * * *  true
abcdef0123456789                      whenever   odd
"""

RESOURCES = """\
=====> node-js-app resource information
       web limit cpu: 1.5
       web limit memory: 512
       web limit memory swap:
       web reservation memory: 256
"""


def runner_for(
    *, ports: str = "http:80:5000 https:443:5000", builder: str = "herokuish"
) -> FakeRunner:
    fake = FakeRunner()
    fake.script(["dokku", "--quiet", "apps:list"], stdout=APPS)
    fake.script(["dokku", "--quiet", "config:export", "node-js-app"], stdout=ENV)
    fake.script(
        ["dokku", "--quiet", "domains:report", "node-js-app", "--domains-app-enabled"],
        stdout="true\n",
    )
    fake.script(
        ["dokku", "--quiet", "domains:report", "node-js-app", "--domains-app-vhosts"],
        stdout="shop.example.com www.shop.example.com\n",
    )
    fake.script(
        ["dokku", "--quiet", "ports:report", "node-js-app", "--ports-map"], stdout=f"{ports}\n"
    )
    fake.script(
        ["dokku", "--quiet", "builder:report", "node-js-app", "--builder-computed-selected"],
        stdout=f"{builder}\n",
    )
    fake.script(
        ["dokku", "--quiet", "git:report", "node-js-app", "--git-computed-deploy-branch"],
        stdout="main\n",
    )
    fake.script(
        ["dokku", "--quiet", "storage:report", "node-js-app", "--storage-run-mounts"],
        stdout="-v /var/lib/dokku/data/storage/node-js-app:/app/storage "
        "-v /srv/shared:/var/shared:ro\n",
    )
    fake.script(["dokku", "--quiet", "resource:report", "node-js-app"], stdout=RESOURCES)
    fake.script(["dokku", "--quiet", "cron:list", "node-js-app"], stdout=CRON)
    fake.script(["dokku", "--quiet", "ps:scale", "node-js-app"], stdout="web: 2\nworker: 1\n")
    return fake
