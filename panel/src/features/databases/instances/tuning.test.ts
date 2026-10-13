import { describe, expect, it } from "vitest";

import type { TuningStatus } from "../../../api/queries/databaseInstances";
import { draftOf, readTuning } from "./tuning";

const STATUS = {
  name: "pg16",
  key: "postgresql@pg16",
  limits: { memory_mb: 1024, cpus: null, swap_mb: null },
  probe: { interval_s: 10, timeout_s: 5, retries: 12 },
  defaults: { interval_s: 10, timeout_s: 5, retries: 12 },
  server: { memory_mb: 8192, available_mb: 7000, cpus: 4 },
  health: { status: "healthy", failing_streak: 0, last: null },
} as TuningStatus;

describe("reading the limits and probe form", () => {
  it("starts from what the instance is held to, with the optional limits empty", () => {
    expect(draftOf(STATUS)).toEqual({ memory: "1024", cpus: "", swap: "", interval: "10", timeout: "5", retries: "12" });
  });

  it("sends nothing while nothing differs", () => {
    const reading = readTuning(STATUS, draftOf(STATUS));

    expect(reading.changed).toBe(false);
    expect(reading.body).toEqual({});
    expect(reading.invalid).toEqual({});
  });

  it("sends only what differs", () => {
    const reading = readTuning(STATUS, { ...draftOf(STATUS), memory: "2048", cpus: "1.5", retries: "5" });

    expect(reading.body).toEqual({ memory_mb: 2048, cpus: 1.5, retries: 5 });
    expect(reading.changed).toBe(true);
  });

  it("reads a comma as a decimal point and zero swap as no swap", () => {
    const reading = readTuning(STATUS, { ...draftOf(STATUS), cpus: "0,5", swap: "0" });

    expect(reading.body).toEqual({ cpus: 0.5, swap_mb: 0 });
  });

  it("asks for the default of an optional limit that is emptied", () => {
    const held = { ...STATUS, limits: { memory_mb: 1024, cpus: 2, swap_mb: 512 } } as TuningStatus;

    const reading = readTuning(held, { ...draftOf(held), cpus: "", swap: "" });

    expect(reading.body).toEqual({ clear: ["cpus", "swap"] });
  });

  it("does not ask for a default that is already there", () => {
    const reading = readTuning(STATUS, { ...draftOf(STATUS), cpus: "", swap: "" });

    expect(reading.changed).toBe(false);
  });

  it("asks for the default of a probe threshold that is emptied", () => {
    const reading = readTuning(STATUS, { ...draftOf(STATUS), interval: "", retries: "" });

    expect(reading.body.clear).toEqual(["interval", "retries"]);
  });

  it("reports what it cannot read instead of guessing", () => {
    const reading = readTuning(STATUS, { memory: "lots", cpus: "1.234", swap: "-1", interval: "x", timeout: "0", retries: "2.5" });

    expect(Object.keys(reading.invalid).sort()).toEqual(["cpus", "interval", "memory", "retries", "swap", "timeout"]);
  });
});
