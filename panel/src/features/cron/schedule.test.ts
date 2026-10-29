import { describe, expect, it } from "vitest";

import { DEFAULT_CHOICE, choiceOf, scheduleOf, timeProblem } from "./schedule";

describe("the schedule a person chooses, as the calendar expression the server takes", () => {
  it("is every hour, as the alias the server expands", () => {
    expect(scheduleOf({ ...DEFAULT_CHOICE, frequency: "hourly" })).toBe("hourly");
  });

  it("is every day at the time chosen", () => {
    expect(scheduleOf({ ...DEFAULT_CHOICE, frequency: "daily", time: "03:30" })).toBe("*-*-* 03:30:00");
  });

  it("is every week on the day and at the time chosen", () => {
    expect(scheduleOf({ ...DEFAULT_CHOICE, frequency: "weekly", weekday: "Fri", time: "18:05" })).toBe("Fri *-*-* 18:05:00");
  });

  it("is every month on the day chosen", () => {
    expect(scheduleOf({ ...DEFAULT_CHOICE, frequency: "monthly", day: 15, time: "00:00" })).toBe("*-*-15 00:00:00");
  });

  it("is what is written when it is custom, without the spaces round it", () => {
    expect(scheduleOf({ ...DEFAULT_CHOICE, frequency: "custom", calendar: "  Mon..Fri *-*-* 09:00:00 " })).toBe("Mon..Fri *-*-* 09:00:00");
  });

  it("starts as every day at the hour the old presets meant", () => {
    expect(DEFAULT_CHOICE).toMatchObject({ frequency: "daily", time: "02:00", weekday: "Mon", day: 1 });
  });
});

describe("reading the schedule of a task that exists", () => {
  it("finds the choice again from the calendar systemd normalised", () => {
    expect(choiceOf({ schedule: "custom", on_calendar: "*-*-* 03:30:00" })).toMatchObject({ frequency: "daily", time: "03:30" });
    expect(choiceOf({ schedule: "custom", on_calendar: "Wed *-*-* 18:05:00" })).toMatchObject({ frequency: "weekly", weekday: "Wed", time: "18:05" });
    expect(choiceOf({ schedule: "custom", on_calendar: "*-*-1 02:00:00" })).toMatchObject({ frequency: "monthly", day: 1, time: "02:00" });
    expect(choiceOf({ schedule: "hourly", on_calendar: "*-*-* *:00:00" })).toMatchObject({ frequency: "hourly" });
  });

  it("keeps what it cannot name as a custom expression, as written", () => {
    expect(choiceOf({ schedule: "custom", on_calendar: "Mon..Fri *-*-* 09:00:00" })).toMatchObject({ frequency: "custom", calendar: "Mon..Fri *-*-* 09:00:00" });
    expect(choiceOf({ schedule: "custom", on_calendar: "*-*-* *:0/15:00" })).toMatchObject({ frequency: "custom", calendar: "*-*-* *:0/15:00" });
  });

  it("falls back to the alias a task was made with when no calendar was read", () => {
    expect(choiceOf({ schedule: "weekly", on_calendar: "" })).toMatchObject({ frequency: "weekly", weekday: "Mon", time: "02:00" });
    expect(choiceOf({ schedule: "monthly", on_calendar: "" })).toMatchObject({ frequency: "monthly", day: 1 });
    expect(choiceOf({ schedule: "daily", on_calendar: "" })).toMatchObject({ frequency: "daily", time: "02:00" });
  });
});

describe("the time of day", () => {
  it("is two digits, a colon and two digits, within a day", () => {
    expect(timeProblem("03:30")).toBe(false);
    expect(timeProblem("23:59")).toBe(false);
    expect(timeProblem("24:00")).toBe(true);
    expect(timeProblem("7:30")).toBe(true);
    expect(timeProblem("")).toBe(true);
    expect(timeProblem("12:60")).toBe(true);
  });
});
