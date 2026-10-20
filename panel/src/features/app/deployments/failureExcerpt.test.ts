import { describe, expect, it } from "vitest";

import { failureExcerpt, parseLog, phaseLines } from "./buildLog";

const LOG = [
  "[2026-09-25 18:00:00] [1/9] Fetching source...",
  "[2026-09-25 18:00:01] HEAD is now at c07d5e3 Bump deps",
  "[2026-09-25 18:00:02] [2/9] Installing dependencies...",
  "[2026-09-25 18:00:03]       → Running: npm ci",
  "[2026-09-25 18:00:05] npm WARN deprecated left-pad@1.0.0",
  "[2026-09-25 18:00:07] npm ERR! code ELIFECYCLE",
  "[2026-09-25 18:00:08] npm ERR! errno 1",
  "[2026-09-25 18:00:09] npm ERR! shop@1.0.0 postinstall: `node setup.js`",
  "[2026-09-25 18:00:10] Deploy failed; the previous release keeps serving shop.example.com",
  "",
].join("\n");

const lines = parseLog(LOG);

describe("what a deploy printed in each phase", () => {
  it("is what follows the step that opened it, up to the next step", () => {
    expect(phaseLines(lines, "fetch").map((line) => line.text)).toEqual(["[1/9] Fetching source...", "HEAD is now at c07d5e3 Bump deps"]);
    const install = phaseLines(lines, "install").map((line) => line.text);
    expect(install[0]).toBe("[2/9] Installing dependencies...");
    expect(install).toContain("npm ERR! code ELIFECYCLE");
    expect(install.at(-1)).toBe("Deploy failed; the previous release keeps serving shop.example.com");
  });

  it("is nothing for a phase the log never reached", () => {
    expect(phaseLines(lines, "build")).toEqual([]);
  });

  it("stops at a step that belongs to no phase", () => {
    const withSite = parseLog(["[2026-09-25 18:00:00] [2/9] Installing dependencies...", "[2026-09-25 18:00:01] added 1 package", "[2026-09-25 18:00:02] [5/9] Setting permissions...", "[2026-09-25 18:00:03] chown ok"].join("\n"));
    expect(phaseLines(withSite, "install").map((line) => line.text)).toEqual(["[2/9] Installing dependencies...", "added 1 package"]);
  });
});

describe("the part of a failed deploy's log worth reading first", () => {
  it("is the first line read as an error, not the last, and the end of the phase that failed", () => {
    const excerpt = failureExcerpt(lines, "install");
    expect(excerpt.firstError?.text).toBe("npm ERR! code ELIFECYCLE");
    expect(excerpt.tail.at(-1)?.text).toBe("Deploy failed; the previous release keeps serving shop.example.com");
    expect(excerpt.tail.length).toBeLessThanOrEqual(15);
  });

  it("keeps at most the last lines of a long phase", () => {
    const long = parseLog(["[2026-09-25 18:00:00] [3/9] Building...", ...Array.from({ length: 40 }, (_, i) => `[2026-09-25 18:00:01] line ${String(i)}`)].join("\n"));
    const excerpt = failureExcerpt(long, "build", 15);
    expect(excerpt.tail).toHaveLength(15);
    expect(excerpt.tail.at(-1)?.text).toBe("line 39");
    expect(excerpt.firstError).toBeNull();
  });

  it("falls back to the whole log when no phase is known", () => {
    const excerpt = failureExcerpt(lines, null);
    expect(excerpt.firstError?.text).toBe("npm ERR! code ELIFECYCLE");
    expect(excerpt.tail.at(-1)?.text).toContain("Deploy failed");
  });

  it("looks for the error anywhere when the phase that failed has none", () => {
    const excerpt = failureExcerpt(lines, "fetch");
    expect(excerpt.firstError?.text).toBe("npm ERR! code ELIFECYCLE");
    expect(excerpt.tail.map((line) => line.text)).toEqual(["[1/9] Fetching source...", "HEAD is now at c07d5e3 Bump deps"]);
  });
});
