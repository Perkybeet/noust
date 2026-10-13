import { describe, expect, it } from "vitest";

import type { LogLine } from "../../../components/ui/LogViewer";
import { filterByLevel, levelOf, parseLine, withoutTimes } from "./buildLog";
import cases from "./logLevels.cases.json";

/**
 * The words that make a line a warning or an error are written twice: here, to colour the
 * viewer, and in `noust.deployers.logs`, to filter and to share. Both are tested against the
 * same lines (`logLevels.cases.json`; `tests/test_deployment_log_view.py` reads the same file),
 * so a change to one that the other does not follow fails a test on each side.
 */
describe("the level a log line reads at", () => {
  it.each(cases.cases)("reads %j the way the server does", ({ line, level }) => {
    // "info" is a line the viewer draws plainly: no level at all.
    expect(parseLine(line, 0).level ?? "info").toBe(level);
  });

  it("sees an error through the colour codes that surround it", () => {
    expect(levelOf("\u001b[31mError: boom\u001b[0m")).toBe("error");
    expect(levelOf("\u001b[33mwarning: careful\u001b[0m")).toBe("warn");
  });
});

describe("reading a log at a level", () => {
  const lines: LogLine[] = [
    { id: 0, text: "installing", ts: "10:00:00" },
    { id: 1, text: "npm WARN deprecated left-pad", ts: "10:00:01", level: "warn" },
    { id: 2, text: "Error: boom", ts: "10:00:02", level: "error" },
    { id: 3, text: "resolved 14 packages", ts: "10:00:03", level: "debug" },
  ];

  it("keeps every line at all", () => {
    expect(filterByLevel(lines, "all")).toBe(lines);
  });

  it("keeps warnings and errors, in order", () => {
    expect(filterByLevel(lines, "warn").map((line) => line.id)).toEqual([1, 2]);
  });

  it("keeps only errors", () => {
    expect(filterByLevel(lines, "error").map((line) => line.id)).toEqual([2]);
  });

  it("leaves the times out without touching the text", () => {
    const plain = withoutTimes(lines);
    expect(plain.map((line) => line.text)).toEqual(lines.map((line) => line.text));
    expect(plain.every((line) => line.ts === undefined)).toBe(true);
    expect(lines[0]?.ts).toBe("10:00:00");
  });
});
