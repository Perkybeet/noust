import { describe, expect, it } from "vitest";

import { ApiError } from "../api/errors";
import { loadCatalog } from "../i18n";
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

  it("says a sentence of the server's catalog in this console's language when the error carries its code", async () => {
    await loadCatalog("es");
    const { setLocale } = await import("../app/locale");
    await setLocale("es");
    const refused = new ApiError(
      409,
      "prerequisiteerror",
      "nginx is not installed",
      "Install it with: apt install nginx",
      null,
      null,
      null,
      null,
      { coded: { code: "streams.support.no_nginx", params: { command: "apt install nginx" } } },
    );

    expect(describeError(refused)).toEqual({
      detail: "nginx no está instalado",
      hint: "Instálalo con: apt install nginx",
      output: null,
    });
  });

  it("keeps the server's English where this console has no text for the code, and where a fix is a program's output", async () => {
    await loadCatalog("es");
    const { setLocale } = await import("../app/locale");
    await setLocale("es");
    const newer = new ApiError(400, "validationerror", "Something new.", "Do the new thing.", null, null, null, null, {
      coded: { code: "a.code.this.console.lacks", params: {} },
    });
    const output = new ApiError(409, "prerequisiteerror", "nginx cannot read its own configuration", "nginx: [emerg] unexpected end of file", null, null, null, null, {
      coded: { code: "streams.support.config_unreadable", params: {} },
    });

    expect(describeError(newer)).toEqual({ detail: "Something new.", hint: "Do the new thing.", output: null });
    expect(describeError(output)).toEqual({
      detail: "nginx no puede leer su propia configuración",
      hint: "nginx: [emerg] unexpected end of file",
      output: null,
    });
  });
});
