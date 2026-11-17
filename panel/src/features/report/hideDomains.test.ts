import { describe, expect, it } from "vitest";

import { browserBlock } from "./browserBlock";
import type { BrowserFacts } from "./browserBlock";
import { OFF, hideDomains, hidingFrom } from "./hideDomains";

const PAIRS = [
  { real: "shop.example.com", alias: "app-1" },
  { real: "shop-example-com", alias: "app-1" },
  { real: "vps-arenna-01", alias: "host" },
];

describe("replacing the names the server replaced", () => {
  it("leaves a text as it is when nothing is hidden", () => {
    expect(hideDomains("GET /api/apps/shop.example.com/logs 500", OFF)).toBe("GET /api/apps/shop.example.com/logs 500");
  });

  it("puts the alias the server used where the name was, wherever it is", () => {
    const text = "GET /api/apps/shop.example.com/logs 500 (unit shop-example-com on vps-arenna-01)";

    expect(hideDomains(text, { kind: "names", pairs: PAIRS })).toBe("GET /api/apps/app-1/logs 500 (unit app-1 on host)");
  });

  it("goes in the order the server gives them, so a domain goes whole before a name inside it", () => {
    const pairs = [
      { real: "api.shop.example.com", alias: "app-2" },
      { real: "shop.example.com", alias: "app-1" },
    ];

    expect(hideDomains("api.shop.example.com and shop.example.com", { kind: "names", pairs })).toBe("app-2 and app-1");
  });

  it("takes anything shaped like a host name out when the names could not be asked of the server", () => {
    const hidden = hideDomains("GET https://shop.example.com/a failed; then tienda.example.net; main.tsx:12", { kind: "shape" });

    expect(hidden).not.toContain("shop.example.com");
    expect(hidden).not.toContain("tienda.example.net");
    // One name is one alias in the whole text.
    expect(hideDomains("shop.example.com and shop.example.com", { kind: "shape" })).toMatch(/^(domain-\d+) and \1$/);
  });

  it("is told what to do by what was asked and what the server answered", () => {
    expect(hidingFrom(false, undefined)).toEqual(OFF);
    expect(hidingFrom(true, [{ real: "a.example.com", alias: "app-1" }])).toEqual({ kind: "names", pairs: [{ real: "a.example.com", alias: "app-1" }] });
    expect(hidingFrom(true, undefined)).toEqual({ kind: "shape" });
  });
});

const FACTS: BrowserFacts = {
  page: "/apps/shop.example.com/logs",
  version: "4.3.0",
  userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
  viewport: { width: 1440, height: 900 },
  theme: "dark",
  language: "en",
  errors: [
    { at: "2026-11-05T10:00:00.000Z", kind: "request", message: "GET /api/apps/shop.example.com/logs 500" },
    { at: "2026-11-05T10:00:01.000Z", kind: "error", message: "TypeError: shop-example-com has no rows" },
  ],
};

describe("the block of the browser when domains are hidden", () => {
  it("replaces the names in the page and in every error, as the server did in the support report", () => {
    const block = browserBlock(FACTS, { kind: "names", pairs: PAIRS });

    const parsed = JSON.parse(block.text) as { page: string; errors: { message: string }[] };
    expect(parsed.page).toBe("/apps/app-1/logs");
    expect(parsed.errors.map((error) => error.message)).toEqual(["GET /api/apps/app-1/logs 500", "TypeError: app-1 has no rows"]);
    expect(block.text).not.toContain("shop.example.com");
    expect(block.text).not.toContain("shop-example-com");
  });

  it("keeps the names when they are not hidden", () => {
    expect(browserBlock(FACTS).text).toContain("/apps/shop.example.com/logs");
  });
});
