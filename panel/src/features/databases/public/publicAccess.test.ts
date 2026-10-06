import { describe, expect, it } from "vitest";

import { engineFamilyOf } from "../engineKey";
import { canRequireTls, initialForm, isAddressOrNetwork, parseSources, requestOf, validPort } from "./publicAccess";

describe("the addresses a public database is reachable from", () => {
  it("accepts addresses and networks, IPv4 and IPv6", () => {
    for (const ok of ["203.0.113.7", "10.0.0.0/24", "0.0.0.0/0", "2001:db8::1", "2001:db8::/32", "::1"]) {
      expect(isAddressOrNetwork(ok), ok).toBe(true);
    }
  });

  it("refuses what is neither", () => {
    for (const bad of ["300.1.1.1", "10.0.0.0/33", "example.com", "203.0.113", "10.0.0.1/", "2001:db8::/129", "anyone"]) {
      expect(isAddressOrNetwork(bad), bad).toBe(false);
    }
  });

  it("reads commas, spaces and lines, once each, and keeps the invalid ones apart", () => {
    expect(parseSources("203.0.113.7, 10.0.0.0/24\n203.0.113.7 nope; 2001:db8::1")).toEqual({
      sources: ["203.0.113.7", "10.0.0.0/24", "2001:db8::1"],
      invalid: ["nope"],
    });
    expect(parseSources("  \n ")).toEqual({ sources: [], invalid: [] });
  });
});

describe("the port", () => {
  it("is empty, or whole and between 1024 and 65535", () => {
    expect(validPort("")).toBe(true);
    expect(validPort(" 5433 ")).toBe(true);
    expect(validPort("1024")).toBe(true);
    expect(validPort("65535")).toBe(true);
    expect(validPort("1023")).toBe(false);
    expect(validPort("65536")).toBe(false);
    expect(validPort("54.3")).toBe(false);
    expect(validPort("abc")).toBe(false);
  });
});

describe("TLS", () => {
  it("can be required of PostgreSQL and MySQL accounts, and only said optional for the rest", () => {
    expect(canRequireTls("postgresql")).toBe(true);
    expect(canRequireTls("mysql")).toBe(true);
    expect(canRequireTls("redis")).toBe(false);
    expect(canRequireTls("mongodb")).toBe(false);
    expect(canRequireTls("postgresql@proggest.postgres")).toBe(true);
    expect(engineFamilyOf("postgresql@proggest.postgres")).toBe("postgresql");
  });

  it("starts on required where it can be, and on optional, said so, where it cannot", () => {
    expect(initialForm("postgresql").tls).toBe("required");
    expect(initialForm("redis").tls).toBe("optional");
  });
});

describe("the request", () => {
  it("leaves out what is empty or invalid, and carries the rest", () => {
    const form = { ...initialForm("postgresql"), port: "5433", sources: "203.0.113.7 nope", profile: "read_only" as const, bind: " 10.0.0.5 " };
    expect(requestOf("postgresql", "shop", form)).toEqual({
      engine: "postgresql",
      database: "shop",
      port: 5433,
      bind_address: "10.0.0.5",
      sources: ["203.0.113.7"],
      tls: "required",
      profile: "read_only",
    });
    expect(requestOf("postgresql", "shop", { ...form, port: "22", bind: "" })).toMatchObject({ port: null, bind_address: null });
  });
});
