import { describe, expect, it } from "vitest";

import { isAddressOrNetwork, parseAddresses } from "./addresses";

describe("addresses and networks", () => {
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
    expect(parseAddresses("203.0.113.7, 10.0.0.0/24\n203.0.113.7 nope; 2001:db8::1")).toEqual({
      entries: ["203.0.113.7", "10.0.0.0/24", "2001:db8::1"],
      invalid: ["nope"],
    });
    expect(parseAddresses("  \n ")).toEqual({ entries: [], invalid: [] });
  });
});
