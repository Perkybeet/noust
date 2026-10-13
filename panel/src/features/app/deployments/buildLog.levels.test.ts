import { describe, expect, it } from "vitest";

import { levelOf, parseLine } from "./buildLog";
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
