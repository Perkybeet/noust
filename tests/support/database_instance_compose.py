# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_instance_compose.py`` that other tests build on."""

from __future__ import annotations

from noust.core.db_instance_store import READY, InstanceRecord
from noust.managers.database.instance_catalog import resolve_pin, spec_for

PASSWORD = "Zq8TnW3kLm0PxV7cYb2RdF5hGj9A"


def record(flavour: str = "postgresql", version: str | None = None, **kwargs) -> InstanceRecord:
    spec = spec_for(flavour)
    pin = resolve_pin(spec, version)
    values = {
        "id": 1,
        "name": "inst",
        "flavour": flavour,
        "version": pin.version,
        "image": spec.repository,
        "digest": pin.digest,
        "previous_digest": None,
        "host_port": 55432,
        "memory_limit_mb": 1024,
        "state": READY,
        "created_at": "2026-10-06T10:00:00+00:00",
        "created_by": "admin",
        "updated_at": "2026-10-06T10:00:00+00:00",
    }
    values.update(kwargs)
    return InstanceRecord(**values)
