import { describe, expect, it } from "vitest";

import type { PolicyFields } from "../../../api/queries/network";
import { changesOf, draftOf, errorsOf, formatHeaders, listOf, lockoutAddress, parseHeaders } from "./access";

const POLICY: PolicyFields = {
  basic_auth: false,
  realm: "Restricted",
  access: "all",
  allow_ips: [],
  deny_ips: [],
  real_ip: "off",
  real_ip_header: "",
  trusted_proxies: [],
  rate_limit: "",
  rate_burst: 20,
  connection_limit: 0,
  headers: {},
  hidden_headers: [],
  hsts: "default",
  noindex: false,
  canonical: "",
  routes: [],
};

describe("listOf", () => {
  it("splits on lines, commas and spaces, and keeps each entry once in the order typed", () => {
    expect(listOf("203.0.113.7\n198.51.100.0/24, 203.0.113.7  10.0.0.1\n\n")).toEqual(["203.0.113.7", "198.51.100.0/24", "10.0.0.1"]);
    expect(listOf("   ")).toEqual([]);
  });
});

describe("headers", () => {
  it("reads Name: value lines and says which line is not one", () => {
    expect(parseHeaders("X-Frame-Options: DENY\nPermissions-Policy: geolocation=()")).toEqual({
      headers: { "X-Frame-Options": "DENY", "Permissions-Policy": "geolocation=()" },
      badLine: null,
    });
    expect(parseHeaders("X-A: 1\n\nnot a header\nX-B: 2").badLine).toBe(3);
    expect(parseHeaders("X-A:").badLine).toBe(1);
    expect(parseHeaders(": value").badLine).toBe(1);
  });

  it("keeps a colon inside the value", () => {
    expect(parseHeaders("Link: <https://example.com/a>; rel=preload").headers).toEqual({ Link: "<https://example.com/a>; rel=preload" });
  });

  it("writes them back one per line", () => {
    expect(formatHeaders({ "X-A": "1", "X-B": "2" })).toBe("X-A: 1\nX-B: 2");
  });
});

describe("draftOf", () => {
  it("splits a rate into an amount and a unit and shows no connection limit as empty", () => {
    const draft = draftOf({ ...POLICY, rate_limit: "300r/m", connection_limit: 0, allow_ips: ["10.0.0.0/8"] });

    expect(draft).toMatchObject({ rate_amount: "300", rate_unit: "m", connection_limit: "", allow: "10.0.0.0/8", rate_burst: "20" });
  });

  it("starts with no rate when there is none", () => {
    expect(draftOf(POLICY)).toMatchObject({ rate_amount: "", rate_unit: "s" });
  });
});

describe("changesOf", () => {
  it("counts nothing for a form nobody touched", () => {
    expect(changesOf(draftOf(POLICY), POLICY)).toEqual({ count: 0, body: {} });
  });

  it("sends only the fields that changed", () => {
    const draft = { ...draftOf(POLICY), allow: "203.0.113.0/24\n198.51.100.7", noindex: true, rate_amount: "10", rate_unit: "s" as const };

    expect(changesOf(draft, POLICY)).toEqual({
      count: 3,
      body: { allow_ips: ["203.0.113.0/24", "198.51.100.7"], rate_limit: "10r/s", noindex: true },
    });
  });

  it("clears a rate by sending an empty one, and a connection limit by sending zero", () => {
    const stored = { ...POLICY, rate_limit: "10r/s", connection_limit: 5 };
    const draft = { ...draftOf(stored), rate_amount: "", connection_limit: "" };

    expect(changesOf(draft, stored).body).toEqual({ rate_limit: "", connection_limit: 0 });
  });

  it("sends the headers whole and the burst as a number", () => {
    const draft = { ...draftOf(POLICY), headers: "X-Frame-Options: DENY", rate_burst: "50", hidden_headers: "Referrer-Policy" };

    expect(changesOf(draft, POLICY).body).toEqual({ headers: { "X-Frame-Options": "DENY" }, rate_burst: 50, hidden_headers: ["Referrer-Policy"] });
  });

  it("sends the header and the proxies only for a proxy of its own", () => {
    const draft = { ...draftOf(POLICY), real_ip: "custom" as const, real_ip_header: "X-Real-IP", trusted_proxies: "10.0.0.1" };

    expect(changesOf(draft, POLICY).body).toEqual({ real_ip: "custom", real_ip_header: "X-Real-IP", trusted_proxies: ["10.0.0.1"] });
    expect(changesOf({ ...draft, real_ip: "cloudflare" }, POLICY).body).toEqual({ real_ip: "cloudflare" });
  });

  it("does not see a difference in how a list is spaced", () => {
    const stored = { ...POLICY, allow_ips: ["10.0.0.1", "10.0.0.2"] };

    expect(changesOf({ ...draftOf(stored), allow: "10.0.0.1, 10.0.0.2\n" }, stored).count).toBe(0);
  });
});

describe("errorsOf", () => {
  it("finds the mistakes that cost a round trip", () => {
    const draft = { ...draftOf(POLICY), rate_amount: "fast", rate_burst: "-1", connection_limit: "x", headers: "oops", real_ip: "custom" as const };

    expect(errorsOf(draft)).toEqual({
      rate_amount: "network.access.errors.rate",
      rate_burst: "network.access.errors.whole",
      connection_limit: "network.access.errors.whole",
      headers: "network.access.errors.header",
      real_ip_header: "network.access.errors.realIpHeader",
    });
    expect(errorsOf(draftOf(POLICY))).toEqual({});
  });

  it("refuses a rate of zero", () => {
    expect(errorsOf({ ...draftOf(POLICY), rate_amount: "0" }).rate_amount).toBe("network.access.errors.rate");
  });
});

describe("lockoutAddress", () => {
  it("reads the address the lists would refuse from the server's words", () => {
    expect(lockoutAddress("These lists would refuse your own address, 198.51.100.4")).toBe("198.51.100.4");
    expect(lockoutAddress("Not a list problem")).toBeNull();
    expect(lockoutAddress(undefined)).toBeNull();
  });
});
