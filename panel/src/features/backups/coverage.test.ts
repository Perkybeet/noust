import { describe, expect, it } from "vitest";

import type { BackupSchedule } from "../../api/queries/backups";
import { UNSCHEDULED_STALE_AFTER, coverageOf, coverageRows, filterCoverage, isCoverageFiltered, staleAfter, validateCoverageSearch } from "./coverage";
import type { BackupRow } from "./coverage";

const NOW = Date.parse("2026-09-29T12:00:00Z");
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

function backup(domain: string, ageMs: number, size = 100): BackupRow {
  return {
    backup_id: `${domain}-${String(ageMs)}`,
    domain,
    timestamp: new Date(NOW - ageMs).toISOString(),
    size,
    size_human: `${String(size)} B`,
    age: "",
    description: "",
    includes_env: true,
    includes_node_modules: false,
    includes_build: false,
    has_database: false,
    database_backups: [],
    tags: [],
    last_verified_at: null,
    verified_ok: null,
  };
}

function schedule(domain: string, preset: string): BackupSchedule {
  return {
    domain,
    app_name: domain,
    timer: "",
    schedule: preset,
    on_calendar: "*-*-* 02:00:00",
    next_run: "pending",
    last_run: "never",
    retention_count: 7,
    retention_days: 30,
    include_databases: true,
    destinations: [],
  };
}

describe("staleAfter", () => {
  it("tolerates one missed run of a schedule, and a week with none", () => {
    expect(staleAfter(schedule("a", "hourly"))).toBe(2 * HOUR);
    expect(staleAfter(schedule("a", "daily"))).toBe(2 * DAY);
    expect(staleAfter(schedule("a", "weekly"))).toBe(14 * DAY);
    expect(staleAfter(schedule("a", "monthly"))).toBe(62 * DAY);
    // A calendar expression of its own is read as daily, the common case.
    expect(staleAfter(schedule("a", "Mon..Fri *-*-* 09:00:00"))).toBe(2 * DAY);
    expect(staleAfter(null)).toBe(UNSCHEDULED_STALE_AFTER);
  });
});

describe("coverageOf", () => {
  it("is never without a backup, current while young enough, stale after", () => {
    expect(coverageOf(null, null, NOW)).toBe("never");
    expect(coverageOf(backup("a", 6 * DAY), null, NOW)).toBe("current");
    expect(coverageOf(backup("a", 8 * DAY), null, NOW)).toBe("stale");
    expect(coverageOf(backup("a", 30 * HOUR), schedule("a", "daily"), NOW)).toBe("current");
    expect(coverageOf(backup("a", 3 * DAY), schedule("a", "daily"), NOW)).toBe("stale");
  });
});

describe("coverageRows", () => {
  it("has one row per application and per domain that still has backups, alphabetically", () => {
    const rows = coverageRows({
      apps: [{ domain: "shop.example.com" }, { domain: "blog.example.com" }],
      backups: [backup("shop.example.com", DAY, 10), backup("shop.example.com", HOUR, 20), backup("gone.example.com", 20 * DAY, 5)],
      schedules: [schedule("shop.example.com", "daily")],
      now: NOW,
    });
    expect(rows.map((row) => [row.domain, row.deployed, row.coverage])).toEqual([
      ["blog.example.com", true, "never"],
      ["gone.example.com", false, "stale"],
      ["shop.example.com", true, "current"],
    ]);
    const shop = rows[2];
    // Newest first, summed, with its schedule.
    expect(shop?.latest?.backup_id).toBe(`shop.example.com-${String(HOUR)}`);
    expect(shop?.size).toBe(30);
    expect(shop?.schedule?.schedule).toBe("daily");
    expect(rows[0]?.latest).toBeNull();
  });
});

describe("the Backups tab's search", () => {
  it("keeps the query, the attention filter and the open application, and drops the rest", () => {
    expect(validateCoverageSearch({ q: " shop ", show: "attention", domain: "shop.example.com", database: "1" })).toEqual({
      q: "shop",
      show: "attention",
      domain: "shop.example.com",
    });
    expect(validateCoverageSearch({ show: "everything", q: "" })).toEqual({});
  });

  it("filters by name and by what needs attention; the open drawer is not a filter", () => {
    const rows = coverageRows({
      apps: [{ domain: "shop.example.com" }, { domain: "blog.example.com" }],
      backups: [backup("shop.example.com", HOUR)],
      schedules: [],
      now: NOW,
    });
    expect(filterCoverage(rows, { q: "SHOP" }).map((row) => row.domain)).toEqual(["shop.example.com"]);
    expect(filterCoverage(rows, { show: "attention" }).map((row) => row.domain)).toEqual(["blog.example.com"]);
    expect(isCoverageFiltered({ domain: "shop.example.com" })).toBe(false);
    expect(isCoverageFiltered({ q: "x" })).toBe(true);
  });
});
