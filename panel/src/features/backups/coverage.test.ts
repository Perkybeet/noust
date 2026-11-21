import { describe, expect, it } from "vitest";

import type { BackupSchedule } from "../../api/queries/backups";
import { COVERAGE_RANK, UNSCHEDULED_STALE_AFTER, coverageOf, coverageRows, filterCoverage, isCoverageFiltered, staleAfter, validateCoverageSearch } from "./coverage";
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
    remote_only: false,
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

describe("a backup that has not reached a destination", () => {
  const owed = (destination: string, extra: Record<string, unknown> = {}) => ({ destination, reason: "connection timed out", attempts: 1, since: new Date(NOW - HOUR).toISOString(), next_attempt_at: new Date(NOW + HOUR).toISOString(), permanent: false, ...extra });
  const waiting = (domain: string, ageMs: number, destination = "office-nas"): BackupRow => ({ ...backup(domain, ageMs), pending_sends: [owed(destination)] });

  // "Up to date" was said, in green, of an application whose newest copy had not reached the place it was made for: the age of the
  // copy is not the whole of whether it protects, and a copy that is owed is a state of the row of its own, before "up to date".
  it("is a state of the row of its own, ahead of up to date, and never of a copy that is missing or old", () => {
    const pending = [{ destination: "office-nas", reason: "", attempts: 0, since: null, nextAttemptAt: null, permanent: false }];
    expect(coverageOf(backup("a", HOUR), schedule("a", "daily"), NOW, pending)).toBe("pending");
    expect(coverageOf(backup("a", HOUR), schedule("a", "daily"), NOW, [])).toBe("current");
    // What is worse is still said: a backup that is not there, or that is old, is that, whatever else is owed.
    expect(coverageOf(null, null, NOW, pending)).toBe("never");
    expect(coverageOf(backup("a", 30 * DAY), null, NOW, pending)).toBe("stale");
  });

  it("is read from the copies of the application: its row says to which destinations, soonest first", () => {
    const rows = coverageRows({
      apps: [{ domain: "shop.example.com" }, { domain: "blog.example.com" }],
      backups: [waiting("shop.example.com", HOUR), { ...backup("shop.example.com", 2 * HOUR), pending_sends: [owed("cloud-bucket")] }, backup("blog.example.com", HOUR)],
      schedules: [schedule("shop.example.com", "daily"), schedule("blog.example.com", "daily")],
      now: NOW,
    });
    const [blog, shop] = rows;
    expect(blog?.coverage).toBe("current");
    expect(blog?.pending).toEqual([]);
    expect(shop?.coverage).toBe("pending");
    expect(shop?.pending.map((send) => send.destination).sort()).toEqual(["cloud-bucket", "office-nas"]);
  });

  it("counts as needing attention, and sorts after what is missing or old and before what is up to date", () => {
    const rows = coverageRows({
      apps: [{ domain: "shop.example.com" }, { domain: "blog.example.com" }],
      backups: [waiting("shop.example.com", HOUR), backup("blog.example.com", HOUR)],
      schedules: [],
      now: NOW,
    });
    expect(filterCoverage(rows, { show: "attention" }).map((row) => row.domain)).toEqual(["shop.example.com"]);
    expect(COVERAGE_RANK.never).toBeLessThan(COVERAGE_RANK.stale);
    expect(COVERAGE_RANK.stale).toBeLessThan(COVERAGE_RANK.pending);
    expect(COVERAGE_RANK.pending).toBeLessThan(COVERAGE_RANK.current);
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
