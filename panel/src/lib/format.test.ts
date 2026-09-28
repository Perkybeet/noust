import { beforeAll, describe, expect, it } from "vitest";

import { setLocale } from "../app/locale";
import { loadCatalog } from "../i18n";
import {
  formatBytes,
  formatBytesRate,
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
  formatPercent,
  formatRelative,
  parseTimestamp,
  relativeRefreshMs,
} from "./format";

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [999, "999 B"],
    [1_000, "0.98 KB"],
    [1_536, "1.5 KB"],
    [96 * 1024 * 1024, "96 MB"],
    [6_613_762_048, "6.2 GB"],
    [243_682_394_112, "227 GB"],
    [1_081_101_176_832, "0.98 TB"],
  ])("%d bytes read as %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });

  it("keeps the sign of a negative delta", () => {
    expect(formatBytes(-2048)).toBe("-2.0 KB");
  });

  it("prints a dash for a value that is not a number", () => {
    expect(formatBytes(Number.NaN)).toBe("-");
  });

  it("adds the per-second unit for rates", () => {
    expect(formatBytesRate(1_258_291)).toBe("1.2 MB/s");
  });
});

describe("formatPercent", () => {
  it.each([
    [0, "0%"],
    [5.7, "5.7%"],
    [9.94, "9.9%"],
    [31.5, "32%"],
    [100, "100%"],
  ])("%d reads as %s", (value, text) => {
    expect(formatPercent(value)).toBe(text);
  });
});

describe("formatCount", () => {
  it("groups thousands in full up to ten thousand", () => {
    expect(formatCount(1284)).toBe("1,284");
  });

  it("goes compact above", () => {
    expect(formatCount(12_900)).toBe("12.9K");
    expect(formatCount(4_200_000)).toBe("4.2M");
  });
});

describe("formatDuration", () => {
  it.each([
    [0.003205, "3 ms"],
    [0.0001, "1 ms"],
    [2.46, "2.4s"],
    [14.9, "14s"],
    [125, "2m 05s"],
    [4_320, "1h 12m"],
    [273_600, "3d 4h"],
  ])("%d seconds read as %s", (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text);
  });

  it("refuses a negative span", () => {
    expect(formatDuration(-1)).toBe("-");
  });
});

describe("parseTimestamp", () => {
  it("reads an ISO time with an offset exactly", () => {
    expect(parseTimestamp("2026-09-25T19:20:35+02:00")?.toISOString()).toBe("2026-09-25T17:20:35.000Z");
    expect(parseTimestamp("2026-09-25T19:20:35Z")?.toISOString()).toBe("2026-09-25T19:20:35.000Z");
  });

  it("reads a naive store time as local time, microseconds and all", () => {
    const parsed = parseTimestamp("2026-09-25T19:20:35.378313");
    expect(parsed).not.toBeNull();
    expect(parsed?.getHours()).toBe(19);
    expect(parsed?.getMinutes()).toBe(20);
    expect(parsed?.getMilliseconds()).toBe(378);
  });

  it("reads systemd's timestamps when the zone is unambiguous", () => {
    expect(parseTimestamp("Fri 2026-09-25 13:06:35 UTC")?.toISOString()).toBe("2026-09-25T13:06:35.000Z");
    expect(parseTimestamp("Fri 2026-09-25 13:06:35 +0200")?.toISOString()).toBe("2026-09-25T11:06:35.000Z");
  });

  it("will not guess a zone abbreviation", () => {
    expect(parseTimestamp("Fri 2026-09-25 13:06:35 CEST")).toBeNull();
  });

  it("reads Unix time in seconds or milliseconds", () => {
    expect(parseTimestamp(1_790_360_435)?.toISOString()).toBe("2026-09-25T18:20:35.000Z");
    expect(parseTimestamp(1_790_360_435_000)?.toISOString()).toBe("2026-09-25T18:20:35.000Z");
  });

  it("returns null for nothing or nonsense", () => {
    expect(parseTimestamp(null)).toBeNull();
    expect(parseTimestamp(undefined)).toBeNull();
    expect(parseTimestamp("")).toBeNull();
    expect(parseTimestamp("yesterday")).toBeNull();
  });
});

describe("formatRelative", () => {
  const now = new Date(2026, 8, 25, 12, 0, 0);
  const ago = (seconds: number) => new Date(now.getTime() - seconds * 1000);

  it.each([
    [3, "just now"],
    [42, "42s ago"],
    [180, "3m ago"],
    [5 * 3600, "5h ago"],
    [4 * 86_400, "4d ago"],
  ])("%d seconds ago reads %s", (seconds, text) => {
    expect(formatRelative(ago(seconds), now)).toBe(text);
  });

  it("names the day after a week, and the year only when it differs", () => {
    expect(formatRelative(new Date(2026, 8, 12, 9), now)).toBe("Sep 12");
    expect(formatRelative(new Date(2025, 8, 12, 9), now)).toBe("Sep 12, 2025");
  });

  it("speaks of the future ahead", () => {
    expect(formatRelative(new Date(now.getTime() + 3 * 3600 * 1000), now)).toBe("in 3h");
    expect(formatRelative(new Date(now.getTime() + 2000), now)).toBe("just now");
  });
});

describe("formatDateTime", () => {
  it("prints the moment the way logs do", () => {
    expect(formatDateTime(new Date(2026, 8, 5, 7, 3, 9))).toMatch(/^2026-09-05 07:03:09( \S+)?$/);
  });
});

describe("relativeRefreshMs", () => {
  const now = new Date(2026, 8, 25, 12);
  it("refreshes each second while seconds are shown, then less and less often", () => {
    expect(relativeRefreshMs(new Date(now.getTime() - 30_000), now)).toBe(1_000);
    expect(relativeRefreshMs(new Date(now.getTime() - 10 * 60_000), now)).toBe(30_000);
    expect(relativeRefreshMs(new Date(now.getTime() - 5 * 3_600_000), now)).toBe(300_000);
    expect(relativeRefreshMs(new Date(now.getTime() - 3 * 86_400_000), now)).toBe(3_600_000);
  });
});

describe("in Spanish", () => {
  beforeAll(async () => {
    await loadCatalog("es");
  });

  it("writes sizes, rates and percentages with Spanish decimal marks", () => {
    expect(formatBytes(1_536, "es")).toBe("1,5 KB");
    expect(formatBytes(-2048, "es")).toBe("-2,0 KB");
    expect(formatBytes(512, "es")).toBe("512 B");
    expect(formatBytesRate(1_258_291, "es")).toBe("1,2 MB/s");
    expect(formatPercent(5.7, "es")).toBe("5,7\u00a0%");
    expect(formatPercent(31.5, "es")).toBe("32\u00a0%");
  });

  it("groups and abbreviates counts the Spanish way", () => {
    // Spanish leaves four-digit numbers ungrouped.
    expect(formatCount(1284, "es")).toBe("1284");
    expect(formatCount(12_900, "es")).toBe("12,9\u00a0mil");
    expect(formatCount(4_200_000, "es")).toBe("4,2\u00a0M");
  });

  it.each([
    [0.003205, "3 ms"],
    [2.46, "2,4 s"],
    [14.9, "14 s"],
    [125, "2 min 05 s"],
    [4_320, "1 h 12 min"],
    [273_600, "3 d 4 h"],
  ])("%d seconds read as %s", (seconds, text) => {
    expect(formatDuration(seconds, "es")).toBe(text);
  });

  const now = new Date(2026, 8, 25, 12, 0, 0);
  const ago = (seconds: number) => new Date(now.getTime() - seconds * 1000);

  it.each([
    [3, "ahora mismo"],
    [42, "hace 42 s"],
    [180, "hace 3 min"],
    [5 * 3600, "hace 5 h"],
    [4 * 86_400, "hace 4 d"],
  ])("%d seconds ago reads %s", (seconds, text) => {
    expect(formatRelative(ago(seconds), now, "es")).toBe(text);
  });

  it("speaks of the future and names older days in Spanish", () => {
    expect(formatRelative(new Date(now.getTime() + 3 * 3600 * 1000), now, "es")).toBe("dentro de 3 h");
    expect(formatRelative(new Date(2026, 8, 12, 9), now, "es")).toBe("12 sept");
    expect(formatRelative(new Date(2025, 8, 12, 9), now, "es")).toBe("12 sept 2025");
    expect(formatDate(new Date(2026, 11, 24), {}, "es")).toBe("24 dic 2026");
  });

  it("follows the active language when none is given", async () => {
    await setLocale("es");
    expect(formatBytes(1_536)).toBe("1,5 KB");
    expect(formatRelative(ago(180), now)).toBe("hace 3 min");
    expect(formatDuration(125)).toBe("2 min 05 s");
  });
});
