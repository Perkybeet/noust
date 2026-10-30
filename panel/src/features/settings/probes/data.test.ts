import { describe, expect, it } from "vitest";

import type { Probe } from "../../../api/queries/probes";
import { bindT } from "../../../i18n";
import { emptyProbe, lastAnswer, probeBody, probeFormFrom, standing, stretch, uptimeText } from "./data";

const en = bindT("en");

function probe(extra: Partial<Probe> = {}): Probe {
  return {
    name: "shop",
    url: "https://shop.example.com/health?key=abc",
    address: "https://shop.example.com/health",
    method: "HEAD",
    expect_status: "200,204",
    expect_text: "Welcome",
    interval_s: 120,
    timeout_s: 5,
    fail_after: 2,
    public: true,
    title: "Shop",
    enabled: true,
    status: { state: "up", since: 1, failures: 0, last_checked: 100, last_status: 200, last_latency_ms: 90, last_error: null },
    uptime: {},
    ...extra,
  };
}

describe("a probe's form", () => {
  it("opens a new probe with what the server would give it", () => {
    const form = emptyProbe();
    expect(form).toMatchObject({ method: "GET", expectStatus: "200-399", interval: "60", timeout: "10", failAfter: "3", publicOn: false });
    expect(probeBody({ ...form, name: "x", url: "https://x.example.com/" }, true)).toEqual({
      url: "https://x.example.com/",
      method: "GET",
      expect_status: "200-399",
      expect_text: "",
      interval_s: 60,
      timeout_s: 10,
      fail_after: 3,
      public: false,
      title: "",
      enabled: true,
    });
  });

  it("reads a probe into a form and writes the same probe back, whatever its switch", () => {
    const form = probeFormFrom(probe());
    expect(form).toMatchObject({ url: "https://shop.example.com/health?key=abc", method: "HEAD", interval: "120", publicOn: true, title: "Shop" });
    expect(probeBody(form, false)).toEqual({
      url: "https://shop.example.com/health?key=abc",
      method: "HEAD",
      expect_status: "200,204",
      expect_text: "Welcome",
      interval_s: 120,
      timeout_s: 5,
      fail_after: 2,
      public: true,
      title: "Shop",
      enabled: false,
    });
  });

  it("sends the server's default for a number left blank or mistyped, never NaN", () => {
    const body = probeBody({ ...probeFormFrom(probe()), interval: "", timeout: "abc", failAfter: " 4 " }, true);
    expect(body).toMatchObject({ interval_s: 60, timeout_s: 10, fail_after: 4 });
    expect(probeBody({ ...emptyProbe(), url: " https://x.example.com/ ", expectStatus: " " }, true)).toMatchObject({ url: "https://x.example.com/", expect_status: "200-399" });
  });
});

describe("how a probe stands", () => {
  it("draws up as running, down as failed, waiting as queued and a switched-off probe as stopped", () => {
    expect(standing(probe())).toEqual({ state: "running", key: "up" });
    expect(standing(probe({ status: { ...probe().status, state: "down" } }))).toEqual({ state: "failed", key: "down" });
    expect(standing(probe({ status: { ...probe().status, state: "unknown" } }))).toEqual({ state: "queued", key: "waiting" });
    expect(standing(probe({ enabled: false }))).toEqual({ state: "stopped", key: "off" });
  });

  it("says the last answer in one line, with the server's own words when it was bad", () => {
    expect(lastAnswer(en, probe().status)).toBe("200 90 ms");
    expect(lastAnswer(en, { ...probe().status, last_status: 503, last_latency_ms: 12, last_error: "HTTP 503 Service Unavailable" })).toBe("503 12 ms: HTTP 503 Service Unavailable");
    // A refused connection has no status and no time: only what went wrong.
    expect(lastAnswer(en, { ...probe().status, last_status: null, last_latency_ms: null, last_error: "connection refused" })).toBe("connection refused");
    expect(lastAnswer(en, { ...probe().status, last_checked: null })).toBeNull();
  });

  it("writes uptime the way a status page does, and a dash for a window never asked over", () => {
    expect(uptimeText(99.95, "en")).toBe("99.95%");
    expect(uptimeText(100, "en")).toBe("100%");
    expect(uptimeText(null, "en")).toBe("-");
    expect(uptimeText(undefined, "en")).toBe("-");
  });

  it("measures a stretch to its end, or to now while it goes on", () => {
    expect(stretch(1000, 1462, 9999, "en")).toBe("7m 42s");
    expect(stretch(1000, null, 1900, "en")).toBe("15m 00s");
    // A clock a little behind the server's is never a negative stretch.
    expect(stretch(1000, undefined, 900, "en")).not.toContain("-");
  });
});
