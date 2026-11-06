import { describe, expect, it } from "vitest";

import { ownerOfProcess, processWords } from "./processOwner";

const APPS = [
  { domain: "shop.example.net", unit: "shop-example-net.service" },
  { domain: "blog.example.org", unit: null },
];

describe("whose a process is", () => {
  it("names the application whose unit started it, by the domain a person knows it by", () => {
    expect(ownerOfProcess({ unit: "shop-example-net.service" }, APPS)).toEqual({ kind: "app", domain: "shop.example.net" });
    // The store may record the unit with or without its suffix: the same application.
    expect(ownerOfProcess({ unit: "shop-example-net" }, APPS)).toEqual({ kind: "app", domain: "shop.example.net" });
  });

  it("names the service when no application runs from it", () => {
    expect(ownerOfProcess({ unit: "postgresql@16-main.service" }, APPS)).toEqual({ kind: "unit", unit: "postgresql@16-main.service" });
  });

  it("says nothing of a process no service started", () => {
    expect(ownerOfProcess({ unit: null }, APPS)).toBeNull();
    expect(ownerOfProcess({ unit: undefined }, APPS)).toBeNull();
    expect(ownerOfProcess({ unit: "" }, APPS)).toBeNull();
  });

  it("searches a process by its name, its command and whose it is", () => {
    const words = processWords({ name: "node", command: "/usr/bin/node server.js" }, { kind: "app", domain: "shop.example.net" });
    expect(words).toContain("node");
    expect(words).toContain("server.js");
    expect(words).toContain("shop.example.net");
    expect(processWords({ name: "cron" }, null)).toBe("cron");
  });
});
