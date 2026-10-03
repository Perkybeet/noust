/**
 * Answers of the Overview's endpoints for tests: a calm server by default, patched per test.
 */

import type { MetricsRead } from "../../api/queries/metrics";
import type { Overview } from "../../api/queries/overview";

export function overviewFixture(patch: Partial<Overview> = {}): Overview {
  return {
    schema: 1,
    generated_at: "2026-09-29T21:40:00+00:00",
    server: { name: "web-01", version: "3.1.0", role: "server" },
    apps: { running: 3, failed: 0, stopped: 1, static: 1, unmanaged: 0, error: null },
    deploys: { since: "2026-09-29T00:00:00+02:00", total: 2, succeeded: 2, failed: 0, rolled_back: 0, running: 0, finished: 2, last_at: null, last_status: "success", last_domain: "shop.example.com", error: null },
    certificates: { total: 4, expiring: 0, expired: 0, next_days: 60, warning_days: 21, error: null },
    backups: { window_hours: 24, apps: 4, scheduled: 4, with_backup_24h: 4, unprotected_scheduled: 0, failed_24h: 0, last_at: null, error: null },
    disk: { used: 40e9, total: 100e9, percent: 40, free_percent: 60, forecast_full_days: null, forecast_reason: { code: "not_growing", message: "Not growing." }, error: null },
    updates: { supported: true, available: 0, security: 0, reboot_required: false, checked_at: null, reason: null, error: null },
    attention: [],
    attention_total: 0,
    activity: [],
    history: null,
    spark: null,
    ...patch,
  };
}

/**
 * A range read over the last `cells` minutes of one grid, every metric reading `value` in the
 * cells from `recordedFrom` on and nothing before (a young history).
 */
export function metricsReadFixture(metrics: readonly string[], { now, cells = 60, step = 60, recordedFrom = 0, value = 12 }: { now: number; cells?: number; step?: number; recordedFrom?: number; value?: number }): MetricsRead {
  const from = now - (cells - 1) * step;
  const times = Array.from({ length: cells }, (_, i) => from + i * step);
  return {
    from,
    to: now,
    step,
    resolution: step <= 5 ? "raw" : "1m",
    window: "24h",
    first_sample_at: times[recordedFrom] ?? null,
    last_sample_at: now,
    series: metrics.map((metric) => ({
      metric,
      points: times.map((at, i): [number, number | null, number | null] => (i >= recordedFrom ? [at, value, value] : [at, null, null])),
      ceiling: metric === "mem.used_bytes" ? 16e9 : metric === "disk.used_bytes" ? 500e9 : null,
    })),
    collector: { recording: true, host: "daemon", since: from, last_sample_at: now, interval_s: 5, last_error: null, reason: null, advice: null, retention_days: 400 },
  };
}
