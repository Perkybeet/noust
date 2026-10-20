import { describe, expect, it } from "vitest";

import type { CertList } from "../../api/queries/certs";
import { displayAddress, isTemporaryName } from "./displayAddress";

const TEMPORARY = "shop.203-0-113-10.sslip.io";

function certs(...entries: { domain: string; domains: string[] }[]): CertList {
  return { certificates: entries.map((entry) => ({ ...entry, days_remaining: 60 })), total: entries.length } as unknown as CertList;
}

describe("the address an application is said to answer on", () => {
  it("knows a temporary name by the service that answers for it", () => {
    expect(isTemporaryName(TEMPORARY)).toBe(true);
    expect(isTemporaryName("Shop.203-0-113-10.SSLIP.IO")).toBe(true);
    expect(isTemporaryName("shop.example.com")).toBe(false);
    expect(isTemporaryName("sslip.io.example.com")).toBe(false);
  });

  it("is the application's own name when it was never temporary, whatever aliases it has", () => {
    const names = [
      { domain: "shop.example.com", kind: "primary" },
      { domain: "blog.example.com", kind: "alias" },
    ];
    const list = certs({ domain: "shop.example.com", domains: ["shop.example.com", "blog.example.com"] });
    expect(displayAddress("shop.example.com", names, list)).toEqual({ name: "shop.example.com", secure: true });
  });

  it("prefers the first alias that is not temporary and that a certificate covers", () => {
    const names = [
      { domain: TEMPORARY, kind: "primary" },
      { domain: "uncovered.example.com", kind: "alias" },
      { domain: "www.example.com", kind: "redirect" },
      { domain: "shop.example.com", kind: "alias" },
    ];
    const list = certs({ domain: TEMPORARY, domains: [TEMPORARY, "shop.example.com", "www.example.com"] });
    expect(displayAddress(TEMPORARY, names, list)).toEqual({ name: "shop.example.com", secure: true });
  });

  it("stays on the temporary name until the real one is covered, and says so over HTTP when nothing is", () => {
    const names = [
      { domain: TEMPORARY, kind: "primary" },
      { domain: "shop.example.com", kind: "alias" },
    ];
    expect(displayAddress(TEMPORARY, names, certs({ domain: TEMPORARY, domains: [TEMPORARY] }))).toEqual({ name: TEMPORARY, secure: true });
    expect(displayAddress(TEMPORARY, names, certs())).toEqual({ name: TEMPORARY, secure: false });
  });

  it("assumes HTTPS while the certificates are read, which is how Noust deploys", () => {
    expect(displayAddress(TEMPORARY, undefined, undefined)).toEqual({ name: TEMPORARY, secure: true });
    expect(displayAddress("shop.example.com", [], undefined)).toEqual({ name: "shop.example.com", secure: true });
  });
});
