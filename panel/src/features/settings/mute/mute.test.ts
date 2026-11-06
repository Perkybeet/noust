import { describe, expect, it } from "vitest";

import { MORNING_HOUR, mutedUntilFor, pausedUntil, untilLabel } from "./mute";

/** A moment of the person's own clock: the tests are about that clock, whatever zone the machine that runs them is in. */
const at = (year: number, month: number, day: number, hour: number, minute = 0) => new Date(year, month - 1, day, hour, minute, 0, 0);

describe("until when", () => {
  it("is an hour from now for an hour", () => {
    const now = at(2026, 11, 5, 10, 30);
    expect(mutedUntilFor("hour", now).getTime() - now.getTime()).toBe(3_600_000);
  });

  it("is the next day at nine for tomorrow morning, whatever the hour it is asked at", () => {
    for (const hour of [0, 5, 8, 9, 14, 23]) {
      const until = mutedUntilFor("morning", at(2026, 11, 5, hour, 20));
      expect([until.getFullYear(), until.getMonth() + 1, until.getDate(), until.getHours(), until.getMinutes()], `asked at ${String(hour)}:20`).toEqual([2026, 11, 6, MORNING_HOUR, 0]);
    }
  });

  it("goes over the end of a month and of a year", () => {
    const month = mutedUntilFor("morning", at(2026, 11, 30, 22));
    expect([month.getMonth() + 1, month.getDate()]).toEqual([12, 1]);
    const year = mutedUntilFor("morning", at(2026, 12, 31, 22));
    expect([year.getFullYear(), year.getMonth() + 1, year.getDate()]).toEqual([2027, 1, 1]);
  });

  it("is a moment the server reads: ISO 8601 with the zone of the instant", () => {
    const until = mutedUntilFor("hour", at(2026, 11, 5, 10));
    expect(until.toISOString()).toMatch(/^2026-11-05T\d{2}:00:00\.000Z$/);
  });
});

describe("a pause as the server stores it", () => {
  const now = new Date("2026-11-05T10:00:00Z");

  it("is the moment while it is still to come", () => {
    expect(pausedUntil("2026-11-05T11:00:00+00:00", now)?.toISOString()).toBe("2026-11-05T11:00:00.000Z");
    expect(pausedUntil("2026-11-06T09:00:00+01:00", now)?.toISOString()).toBe("2026-11-06T08:00:00.000Z");
  });

  it("is nothing once it has ended, and nothing for what nobody can read", () => {
    expect(pausedUntil("2026-11-05T10:00:00+00:00", now)).toBeNull();
    expect(pausedUntil("2026-11-05T09:00:00+00:00", now)).toBeNull();
    expect(pausedUntil("", now)).toBeNull();
    expect(pausedUntil("   ", now)).toBeNull();
    expect(pausedUntil("after the weekend", now)).toBeNull();
  });
});

describe("a pause's end in words", () => {
  it("is the clock alone when it ends today, in minutes and never in seconds", () => {
    const now = at(2026, 11, 5, 10, 30);
    expect(untilLabel(at(2026, 11, 5, 11, 30), now)).toBe("11:30");
    expect(untilLabel(new Date(at(2026, 11, 5, 11, 30).getTime() + 15_000), now)).toBe("11:30");
  });

  it("says the day as well when it ends on another one, so tomorrow at nine is not read as nine today", () => {
    const label = untilLabel(at(2026, 11, 6, MORNING_HOUR), at(2026, 11, 5, 22));
    expect(label).toMatch(/Nov/);
    expect(label).toMatch(/6/);
    expect(label).toMatch(/09:00$/);
  });
});
