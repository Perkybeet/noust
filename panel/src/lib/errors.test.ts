import { describe, expect, it } from "vitest";

import { ApiError } from "../api/errors";
import { describeError } from "./errors";

describe("describeError", () => {
  it("keeps the API's hint and detail apart", () => {
    expect(describeError({ error: "unit_failed", detail: "Job failed.", hint: "Check the logs." })).toEqual({
      hint: "Check the logs.",
      detail: "Job failed.",
      output: null,
    });
  });

  it("falls back to an Error's message, verbatim", () => {
    expect(describeError(new Error("nginx: [emerg] unknown directive"))).toEqual({
      hint: null,
      detail: "nginx: [emerg] unknown directive",
      output: null,
    });
  });

  it("stringifies anything else", () => {
    expect(describeError("boom")).toEqual({ hint: null, detail: "boom", output: null });
  });

  it("carries a failing tool's own output apart from the one-line detail", () => {
    expect(
      describeError({
        error: "query_failed",
        detail: "ERROR: syntax error at or near \"SELCT\"",
        hint: null,
        output: 'psql:query.sql:1: ERROR:  syntax error at or near "SELCT"\nLINE 1: SELCT * FROM apps;\n        ^',
      }),
    ).toEqual({
      hint: null,
      detail: 'ERROR: syntax error at or near "SELCT"',
      output: 'psql:query.sql:1: ERROR:  syntax error at or near "SELCT"\nLINE 1: SELCT * FROM apps;\n        ^',
    });
  });

  it("words a full deploy queue with the wait the server asked for, keeping the server's own sentence", () => {
    const full = new ApiError(429, "queue_full", "The deploy queue is full: 25 requests are waiting", "Builds run a few at a time... noust config set deploy.queue_limit <number>.", null, 30);
    const described = describeError(full);
    expect(described.detail).toBe("The deploy queue is full: 25 requests are waiting");
    expect(described.hint).toMatch(/Try again in 30 seconds, or cancel what no longer needs to wait/);
    expect(described.hint).not.toMatch(/noust config set/);
    expect(describeError(new ApiError(429, "queue_full", "The deploy queue is full", null, null, null)).hint).toMatch(/Try again in a moment/);
  });

  it("leaves the rate limit's own hint alone: it is about how often a client asks", () => {
    const limited = new ApiError(429, "rate_limited", "Too many requests", "Wait for the time in Retry-After.", null, 12);
    expect(describeError(limited).hint).toBe("Wait for the time in Retry-After.");
  });

  it("has no output when the error does not carry one", () => {
    expect(describeError({ detail: "Not found", output: null }).output).toBeNull();
    expect(describeError({ detail: "Not found" }).output).toBeNull();
  });
});
