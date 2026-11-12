import { afterEach, describe, expect, it, vi } from "vitest";

import { ERROR_LIMIT, MESSAGE_LIMIT, clearErrors, installErrorBuffer, recentErrors, recordError, recordFailedRequest } from "./errorBuffer";

afterEach(() => {
  clearErrors();
});

describe("the buffer of the last errors", () => {
  it("keeps what it is told, oldest first, with when and where from", () => {
    recordError("error", "TypeError: x is undefined", new Date("2026-11-05T10:00:00Z"));
    recordError("console", "A page failed to render", new Date("2026-11-05T10:00:01Z"));

    expect(recentErrors()).toEqual([
      { at: "2026-11-05T10:00:00.000Z", kind: "error", message: "TypeError: x is undefined" },
      { at: "2026-11-05T10:00:01.000Z", kind: "console", message: "A page failed to render" },
    ]);
  });

  it("keeps only the last ones", () => {
    for (let index = 0; index < ERROR_LIMIT + 5; index += 1) recordError("error", `failure ${String(index)}`);

    const kept = recentErrors();

    expect(kept).toHaveLength(ERROR_LIMIT);
    expect(kept[0]?.message).toBe("failure 5");
    expect(kept.at(-1)?.message).toBe(`failure ${String(ERROR_LIMIT + 4)}`);
  });

  it("does not repeat the same failure one after another, but remembers it again after another", () => {
    recordError("request", "GET /api/x 500");
    recordError("request", "GET /api/x 500");
    recordError("error", "boom");
    recordError("request", "GET /api/x 500");

    expect(recentErrors().map((entry) => entry.message)).toEqual(["GET /api/x 500", "boom", "GET /api/x 500"]);
  });

  it("cuts a very long message and ignores an empty one", () => {
    recordError("error", "x".repeat(MESSAGE_LIMIT * 3));
    recordError("error", "   ");

    expect(recentErrors()).toHaveLength(1);
    expect(recentErrors()[0]?.message).toHaveLength(MESSAGE_LIMIT + 3);
    expect(recentErrors()[0]?.message.endsWith("...")).toBe(true);
  });

  it("says a failed call as the method, the path and the status, or that nothing answered", () => {
    recordFailedRequest("GET", "/api/databases/shop/data?limit=50", 500);
    recordFailedRequest("POST", "/api/apps", 0);

    expect(recentErrors().map((entry) => entry.message)).toEqual(["GET /api/databases/shop/data?limit=50 500", "POST /api/apps (no answer)"]);
  });
});

describe("listening to the browser", () => {
  it("records an uncaught error with its stack, and an unhandled rejection", () => {
    const stop = installErrorBuffer();
    try {
      const error = new TypeError("Cannot read properties of undefined (reading 'rows')");
      window.dispatchEvent(new ErrorEvent("error", { error, message: error.message }));
      const rejection = new Event("unhandledrejection");
      Object.assign(rejection, { reason: new Error("the request was refused") });
      window.dispatchEvent(rejection);

      const kept = recentErrors();
      expect(kept.map((entry) => entry.kind)).toEqual(["error", "rejection"]);
      expect(kept[0]?.message).toContain("Cannot read properties of undefined (reading 'rows')");
      expect(kept[1]?.message).toContain("the request was refused");
    } finally {
      stop();
    }
  });

  it("records what the console logs as an error and still logs it", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const stop = installErrorBuffer();
    try {
      console.error("A page failed to render:", new Error("boom"), { component: "Datos" });

      expect(logged).toHaveBeenCalledOnce();
      const [entry] = recentErrors();
      expect(entry?.kind).toBe("console");
      expect(entry?.message).toContain("A page failed to render:");
      expect(entry?.message).toContain("boom");
      expect(entry?.message).toContain('{"component":"Datos"}');
    } finally {
      stop();
    }
  });

  it("hands the real console exactly what was logged, line breaks and objects as they came", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const stop = installErrorBuffer();
    try {
      const frame = '{"line": "first\nsecond';
      const detail = { frame };
      console.error("Ignored a malformed frame:", frame, detail);

      expect(logged).toHaveBeenCalledOnce();
      const received: unknown[] = logged.mock.calls[0] ?? [];
      expect(received).toHaveLength(3);
      expect(received[0]).toBe("Ignored a malformed frame:");
      expect(received[1]).toBe(frame);
      expect(received[2]).toBe(detail);
    } finally {
      stop();
    }
  });

  it("is installed once, and stopping puts the console back", () => {
    const original = console.error;
    const first = installErrorBuffer();
    const second = installErrorBuffer();

    expect(second).toBe(first);
    expect(console.error).not.toBe(original);

    first();

    expect(console.error).toBe(original);
    window.dispatchEvent(new ErrorEvent("error", { message: "after it stopped" }));
    expect(recentErrors()).toHaveLength(0);
  });
});

const ENTRY = import.meta.glob<string>("../main.tsx", { query: "?raw", import: "default", eager: true });

describe("the entry of the console", () => {
  it("starts the buffer before it draws anything, so an error of the first paint is remembered too", () => {
    const source = Object.values(ENTRY)[0] ?? "";

    expect(source).toContain("installErrorBuffer();");
    expect(source.indexOf("installErrorBuffer();")).toBeLessThan(source.indexOf("createRoot(container).render("));
  });
});
