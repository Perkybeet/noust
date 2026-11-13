import { describe, expect, it } from "vitest";

import { dockerLine } from "./logLines";

describe("a container's output line", () => {
  it("moves Docker's time to the time column and leaves what the program said as it said it", () => {
    const line = dockerLine({ id: 1, text: "2026-11-12T10:15:02.123456789Z [api] listening on :3000" });
    expect(line.ts).toBe("10:15:02");
    expect(line.text).toBe("[api] listening on :3000");
  });

  it("reads a level from the words, so a fatal line is an error", () => {
    expect(dockerLine({ id: 2, text: "2026-11-12T10:15:03Z FATAL ERROR: Reached heap limit Allocation failed" }).level).toBe("error");
  });

  it("leaves a line with no time exactly as it is, and keeps the one level it already had", () => {
    const plain = { id: 3, text: "no stamp here" };
    expect(dockerLine(plain).text).toBe("no stamp here");
    expect(dockerLine(plain).ts).toBeUndefined();
    expect(dockerLine({ id: 4, text: "2026-11-12T10:15:04Z fine", level: "warn" }).level).toBe("warn");
  });

  it("gives the same line back for the same line, so the viewer's cache holds", () => {
    const line = { id: 5, text: "2026-11-12T10:15:05Z once" };
    expect(dockerLine(line)).toBe(dockerLine(line));
  });
});
