import { describe, expect, it } from "vitest";

import { EMPTY_DRAFT, bodyOf, crossesMidnight, daysWords, draftOf, problemsOf } from "./autoUpdate";

const SETTINGS = { enabled: true, channel: "security", window: "01:00-02:30", days: "mon,thu" };

describe("the automatic update as a form", () => {
  it("reads what the server stored", () => {
    expect(draftOf(SETTINGS)).toEqual({ enabled: true, channel: "security", from: "01:00", to: "02:30", everyDay: false, days: ["Mon", "Thu"] });
    expect(draftOf({ ...SETTINGS, days: "daily" })).toMatchObject({ everyDay: true, days: [] });
  });

  it("falls back to the patch channel for a word it does not know, rather than showing nothing chosen", () => {
    expect(draftOf({ ...SETTINGS, channel: "nightly" }).channel).toBe("patch");
  });

  it("sends the window, the days and the channel in the shape the server reads", () => {
    expect(bodyOf(draftOf(SETTINGS))).toEqual({ enabled: true, channel: "security", window: "01:00-02:30", days: "mon,thu" });
    expect(bodyOf({ ...EMPTY_DRAFT })).toEqual({ enabled: true, channel: "patch", window: "03:00-05:00", days: "daily" });
  });

  it("writes the days in the order of the week whatever order they were chosen in, and pads a short hour", () => {
    expect(bodyOf({ ...EMPTY_DRAFT, from: "3:00", to: "5:30", everyDay: false, days: ["Sun", "Mon"] })).toMatchObject({ window: "03:00-05:30", days: "mon,sun" });
  });

  it("finds what is wrong before anything is sent", () => {
    expect(problemsOf(EMPTY_DRAFT)).toEqual({});
    expect(problemsOf({ ...EMPTY_DRAFT, from: "25:00" })).toEqual({ from: true });
    expect(problemsOf({ ...EMPTY_DRAFT, to: "soon" })).toEqual({ to: true });
    expect(problemsOf({ ...EMPTY_DRAFT, from: "03:00", to: "03:00" })).toEqual({ same: true });
    expect(problemsOf({ ...EMPTY_DRAFT, everyDay: false, days: [] })).toEqual({ days: true });
  });

  it("says when a window ends the next day", () => {
    expect(crossesMidnight("23:00", "01:00")).toBe(true);
    expect(crossesMidnight("03:00", "05:00")).toBe(false);
    expect(crossesMidnight("later", "01:00")).toBe(false);
  });

  it("reads the days back in words, in the language shown", () => {
    expect(daysWords("daily", "en", "every day")).toBe("every day");
    expect(daysWords("mon,thu", "en", "every day")).toBe("Monday, Thursday");
    expect(daysWords("mon,thu", "es", "todos los días")).toMatch(/lunes/i);
  });
});
