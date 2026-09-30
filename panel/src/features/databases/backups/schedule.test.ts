import { describe, expect, it } from "vitest";

import { DEFAULT_SCHEDULE, scheduleExpression, scheduleForm, validTime } from "./schedule";

describe("a backup policy's schedule", () => {
  it("reads the calendar expressions the form writes, and writes them back the same", () => {
    for (const expression of ["*-*-* *:00:00", "*-*-* 03:30:00", "Sun *-*-* 01:15:00", "*-*-05 02:00:00"]) {
      expect(scheduleExpression(scheduleForm(expression))).toBe(expression);
    }
  });

  it("keeps an expression of another shape as a custom one, word for word", () => {
    expect(scheduleForm("Mon..Fri *-*-* 03:30:00")).toEqual({ ...DEFAULT_SCHEDULE, frequency: "custom", custom: "Mon..Fri *-*-* 03:30:00" });
  });

  it("starts a new policy every day at 02:00, the server's own default", () => {
    expect(scheduleExpression(scheduleForm(null))).toBe("*-*-* 02:00:00");
  });

  it("does not take a day of the month some months lack as a monthly schedule", () => {
    expect(scheduleForm("*-*-31 02:00:00").frequency).toBe("custom");
  });

  it("accepts a time of day as HH:MM only", () => {
    expect(validTime("02:30")).toBe(true);
    expect(validTime("2:30")).toBe(true);
    expect(validTime("24:00")).toBe(false);
    expect(validTime("02:60")).toBe(false);
    expect(validTime("noon")).toBe(false);
  });
});
