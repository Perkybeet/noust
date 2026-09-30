import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { bindT } from "../../../i18n";
import { cellWords, formatChartTime, momentNeedsDate, needsDateFormat, resolutionWords, spanNeedsDate } from "./time";

const t = bindT("en");
const local = (y: number, m: number, d: number, h: number, min: number, s = 0): number => new Date(y, m - 1, d, h, min, s).getTime() / 1000;

describe("formatChartTime", () => {
  it("is a clock, with seconds when readings are seconds apart, and a date when asked", () => {
    const at = local(2026, 9, 25, 14, 5, 7);
    expect(formatChartTime(at, false, "en")).toBe("14:05");
    expect(formatChartTime(at, false, "en", true)).toBe("14:05:07");
    expect(formatChartTime(at, true, "en")).toBe("Sep 25 14:05");
  });
  it("is the bare date on a tick at midnight", () => {
    expect(formatChartTime(local(2026, 9, 25, 0, 0), true, "en")).toBe("Sep 25");
  });
});

describe("dates on the axis", () => {
  it("stay off for a day, which names each hour once", () => {
    expect(spanNeedsDate([local(2026, 9, 24, 21, 40), local(2026, 9, 25, 21, 40)])).toBe(false);
  });
  it("appear for a week, and for an hour that crosses midnight", () => {
    expect(spanNeedsDate([local(2026, 9, 18, 12, 0), local(2026, 9, 25, 12, 0)])).toBe(true);
    expect(spanNeedsDate([local(2026, 9, 24, 23, 30), local(2026, 9, 25, 0, 30)])).toBe(true);
    expect(needsDateFormat([local(2026, 9, 24, 23, 30), local(2026, 9, 25, 0, 30)])).toBe(true);
  });
  it("appear on one reading of a day's window, where every clock happened twice", () => {
    expect(momentNeedsDate([local(2026, 9, 24, 21, 40), local(2026, 9, 25, 21, 40)])).toBe(true);
    expect(momentNeedsDate([local(2026, 9, 25, 10, 0), local(2026, 9, 25, 11, 0)])).toBe(false);
  });
});

describe("resolutionWords", () => {
  it("says what was read, from the tier and the step, not from the window", () => {
    expect(resolutionWords(t, "raw", 5)).toBe("a reading every 5 seconds");
    expect(resolutionWords(t, "1m", 60)).toBe("1-minute averages");
    expect(resolutionWords(t, "10m", 600)).toBe("10-minute averages");
    expect(resolutionWords(t, "1h", 3_600)).toBe("1-hour averages");
    // A cell wider than the tier is a mean over several of its buckets.
    expect(resolutionWords(t, "raw", 30)).toBe("30-second averages");
  });
  it("says it in Spanish too", async () => {
    await setLocale("es");
    expect(resolutionWords(bindT("es"), "1m", 120)).toBe("medias de 2 min");
    expect(resolutionWords(bindT("es"), "raw", 5)).toBe("una lectura cada 5 segundos");
  });
  it("names one cell for the readout, and nothing for a single reading", () => {
    expect(cellWords(t, "1m", 60)).toBe("1-minute average");
    expect(cellWords(t, "raw", 5)).toBeNull();
  });
});
