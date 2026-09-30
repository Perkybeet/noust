import { describe, expect, it } from "vitest";

import { causeFallback, checkLabel, checkStatus, headline, leadingCheck, openChecks, opensByDefault, tally, verdictAnnouncement, verdictView } from "./view";

const CHECK = { name: "port", status: "fail", summary: "Nothing is listening on port 3000", evidence: "" };

describe("verdictView", () => {
  it("draws the three verdicts in the state language", () => {
    expect(verdictView("healthy")).toEqual({ tone: "ok", word: "Healthy" });
    expect(verdictView("degraded")).toEqual({ tone: "warn", word: "Degraded" });
    expect(verdictView("down")).toEqual({ tone: "fail", word: "Down" });
  });

  it("shows an unknown verdict verbatim, in the neutral tone", () => {
    expect(verdictView("half_up")).toEqual({ tone: "idle", word: "Half up" });
  });
});

describe("checkStatus", () => {
  it("names every status with a word, not only a colour", () => {
    expect(checkStatus("ok").word).toBe("Passed");
    expect(checkStatus("warn").word).toBe("Warning");
    expect(checkStatus("fail").word).toBe("Failed");
    expect(checkStatus("skip").word).toBe("Skipped");
    expect(checkStatus("flaky")).toEqual({ tone: "idle", word: "Flaky" });
  });
});

describe("checkLabel", () => {
  it("says what each probe looks at, and falls back to the probe's own name", () => {
    expect(checkLabel("http_nginx")).toBe("HTTP, through nginx");
    expect(checkLabel("last_deployment")).toBe("Last deploy");
    expect(checkLabel("swap_usage")).toBe("Swap usage");
  });
});

describe("tally", () => {
  it("counts each status, unknown ones as skipped", () => {
    const checks = [CHECK, { ...CHECK, status: "ok" }, { ...CHECK, status: "ok" }, { ...CHECK, status: "warn" }, { ...CHECK, status: "?" }];
    expect(tally(checks)).toEqual({ ok: 2, warn: 1, fail: 1, skip: 1 });
  });
});

describe("opensByDefault", () => {
  it("opens the output of what did not pass, when there is output", () => {
    expect(opensByDefault({ ...CHECK, evidence: "LISTEN 0 511 127.0.0.1:3001" })).toBe(true);
    expect(opensByDefault({ ...CHECK, status: "warn", evidence: "x" })).toBe(true);
    expect(opensByDefault({ ...CHECK, evidence: "  " })).toBe(false);
    expect(opensByDefault({ ...CHECK, status: "ok", evidence: "x" })).toBe(false);
  });
});

describe("what is said", () => {
  it("has a sentence when no single cause is named", () => {
    expect(causeFallback("healthy")).toMatch(/answers as it should/);
    expect(causeFallback("down")).toMatch(/^The app is down/);
  });

  it("announces the verdict and the cause after a re-run", () => {
    expect(verdictAnnouncement("shop.example.com", { domain: "shop.example.com", verdict: "down", probable_cause: "Port mismatch.", checks: [] })).toBe(
      "shop.example.com: Down. Port mismatch.",
    );
  });
});

describe("the headline", () => {
  const check = (name: string, status: string, evidence = "") => ({ name, status, summary: "", evidence });

  it("hangs on the service before its port, and the port before HTTP", () => {
    const checks = [check("http_direct", "fail"), check("port", "fail"), check("unit", "fail", "Result=exit-code")];
    expect(leadingCheck(checks)?.name).toBe("unit");
    expect(headline({ verdict: "down", checks })).toBe("The app stops when it starts");
    expect(headline({ verdict: "down", checks: [check("http_direct", "fail"), check("port", "fail")] })).toBe("Nothing answers on the app's port");
  });

  it("falls back to a warning, then to the sentence for no single cause", () => {
    expect(headline({ verdict: "degraded", checks: [check("unit", "ok"), check("certificate", "warn")] })).toBe("HTTPS is not working for this domain");
    expect(headline({ verdict: "degraded", checks: [check("unit", "ok")] })).toMatch(/do not point to one cause/);
    expect(headline({ verdict: "healthy", checks: [check("journal", "warn")] })).toMatch(/answers as it should/);
  });

  it("opens the check it hangs on and the logs it cites, and nothing on a healthy app", () => {
    const checks = [check("unit", "ok", "ActiveState=active"), check("port", "fail", "LISTEN 3001"), check("journal", "ok", "EADDRINUSE")];
    expect([...openChecks({ verdict: "down", checks })].sort()).toEqual(["journal", "port"]);
    expect(openChecks({ verdict: "healthy", checks })).toEqual(new Set());
  });
});
