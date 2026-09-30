import { describe, expect, it } from "vitest";

import { journalFilters, journalLines, logsCommand, validateLogsSearch } from "./data";

describe("the Logs tab's data", () => {
  it("reads the filters from the URL and drops what is not one", () => {
    expect(validateLogsSearch({ unit: " nginx.service ", priority: "err", range: "boot", q: "denied" })).toEqual({
      unit: "nginx.service",
      priority: "err",
      range: "boot",
      q: "denied",
    });
    expect(validateLogsSearch({ priority: "loud", range: "1h", unit: "" })).toEqual({});
  });

  it("asks the API for the last hour by default, and for a boot by index", () => {
    expect(journalFilters({}, 300)).toEqual({ lines: 300, since: "-1h" });
    expect(journalFilters({ unit: "kernel", range: "previous" }, 300)).toEqual({ lines: 300, kernel: true, boot: -1 });
    expect(journalFilters({ unit: "nginx.service", priority: "err", range: "24h", q: "x" }, 1000)).toEqual({
      lines: 1000,
      unit: "nginx.service",
      priority: "err",
      q: "x",
      since: "-24h",
    });
  });

  it("says the command that reads the same thing", () => {
    expect(logsCommand({})).toBe("noust server logs --since -1h");
    expect(logsCommand({ unit: "nginx.service", priority: "err", range: "boot", q: "it's" })).toBe("noust server logs nginx.service -p err -b 0 -g 'it'\\''s'");
  });

  it("prints an entry the way journalctl does, the message verbatim", () => {
    const lines = journalLines({
      entries: [{ timestamp: "2026-09-29T10:00:00.123456+00:00", priority: 3, unit: "nginx.service", message: "bind() to 0.0.0.0:80 failed", pid: 812, cursor: "c" }],
      next_cursor: null,
      truncated: false,
    });
    expect(lines).toEqual([{ id: 0, text: "2026-09-29T10:00:00+00:00 nginx.service[812]: bind() to 0.0.0.0:80 failed" }]);
  });
});
