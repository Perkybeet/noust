/** The data backups a server answers with in a test: an application with a policy, backups of its pieces. */

import type { VolumeBackup, VolumePolicy, VolumeTarget } from "../../../api/queries/volumeBackups";

export function policy(domain: string, extra: Partial<VolumePolicy> = {}): VolumePolicy {
  return {
    domain,
    configured: true,
    schedule: "*-*-* 02:00:00",
    targets: [],
    extra_paths: [],
    retention_count: 7,
    retention_days: 30,
    destinations: [{ name: "nas", retention_count: 14, retention_days: null, exists: true, encrypted: false }],
    remote_only: false,
    consistency: "live",
    enabled: true,
    timer: { installed: true, next_run: "Wed 2026-10-07 02:00:00 UTC" },
    last_run_at: "2026-10-06T02:00:03+00:00",
    last_status: "ok",
    last_error: null,
    last_backup: null,
    last_success_at: "2026-10-06T02:00:03+00:00",
    created_at: "2026-10-01T00:00:00+00:00",
    updated_at: "2026-10-01T00:00:00+00:00",
    ...extra,
  };
}

export function backup(id: string, domain: string, extra: Partial<VolumeBackup> = {}): VolumeBackup {
  return {
    id,
    domain,
    target: "path:uploads",
    kind: "path",
    created_at: "2026-10-06T02:00:03+00:00",
    size_bytes: 1_048_576,
    checksum: "ab12",
    consistency: "live",
    tags: ["scheduled"],
    description: "",
    sqlite_files: [],
    skipped: [],
    remote_copies: [],
    last_verified_at: null,
    verified_ok: null,
    ...extra,
  };
}

export function target(key: string, extra: Partial<VolumeTarget> = {}): VolumeTarget {
  const [kind = "path", source = key] = key.split(":");
  return { key, kind: kind === "volume" ? "docker" : "path", source, path: kind === "volume" ? null : `/var/www/apps/shop/shared/${source}`, exists: true, declared_by: "persistent_paths", slug: source, ...extra };
}

export const TARGETS: VolumeTarget[] = [target("path:uploads"), target("volume:shop_pgdata", { declared_by: "compose" }), target("path:media", { exists: false })];
