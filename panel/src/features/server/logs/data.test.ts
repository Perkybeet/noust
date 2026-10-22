import { describe, expect, it } from "vitest";

import { journalLines, toLogsSearch, validateLegacySearch } from "./data";

describe("the address the system's logs had before they were a source of Logs", () => {
  it("reads what the old address carried and nothing else", () => {
    expect(validateLegacySearch({ unit: "nginx.service", priority: "err", range: "boot", q: "denied", junk: 1 })).toEqual({
      unit: "nginx.service",
      priority: "err",
      range: "boot",
      q: "denied",
    });
    expect(validateLegacySearch({ unit: 5, from: "1790000000", to: "1790000600" })).toEqual({ from: 1790000000, to: 1790000600 });
    // A stretch is both ends or neither, and it runs forward.
    expect(validateLegacySearch({ from: "1790000000" })).toEqual({});
  });

  it("becomes the system as a source of Logs: the range is `since`, the priority is `level`", () => {
    expect(toLogsSearch({ unit: "nginx.service", priority: "err", range: "24h", q: "x" })).toEqual({
      source: "system",
      unit: "nginx.service",
      level: "err",
      since: "24h",
      q: "x",
    });
    expect(toLogsSearch({ range: "previous" })).toEqual({ source: "system", since: "previous" });
    expect(toLogsSearch({ unit: "kernel", from: 100, to: 200 })).toEqual({ source: "system", unit: "kernel", from: 100, to: 200 });
  });

  it("drops the default hour, and what Logs does not read", () => {
    expect(toLogsSearch({ range: "1h" })).toEqual({ source: "system" });
    expect(toLogsSearch({ priority: "loud", range: "1y" })).toEqual({ source: "system" });
    expect(toLogsSearch({})).toEqual({ source: "system" });
  });

  it("reads the journal times its own documentation wrote", () => {
    expect(toLogsSearch({ since: "-24h" })).toEqual({ source: "system", since: "24h" });
    expect(toLogsSearch({ since: "-1h" })).toEqual({ source: "system" });
    expect(toLogsSearch({ since: "yesterday" })).toEqual({ source: "system" });
  });
});

describe("a journal entry", () => {
  it("is printed the way journalctl does, the message verbatim, with its level and where to find it again", () => {
    const lines = journalLines({
      entries: [
        { timestamp: "2026-09-29T10:00:00.123456+00:00", priority: 3, unit: "nginx.service", message: "bind() to 0.0.0.0:80 failed", pid: 812, cursor: "c1" },
        { timestamp: "2026-09-29T10:00:01+00:00", priority: 6, unit: "sshd.service", message: "ok", pid: null, cursor: "c2" },
        { timestamp: "2026-09-29T10:00:02+00:00", priority: 4, unit: "cron.service", message: "late", cursor: "c3" },
      ],
    });
    expect(lines).toEqual([
      { id: 0, text: "2026-09-29T10:00:00+00:00 nginx.service[812]: bind() to 0.0.0.0:80 failed", ref: "c1", level: "error" },
      { id: 1, text: "2026-09-29T10:00:01+00:00 sshd.service: ok", ref: "c2" },
      { id: 2, text: "2026-09-29T10:00:02+00:00 cron.service: late", ref: "c3", level: "warn" },
    ]);
  });
});
