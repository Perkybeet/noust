import { describe, expect, it } from "vitest";

import { heldBy, isNamedKind, onlyHere } from "./onlyHere";

describe("what a server holds of a kind, from its own list", () => {
  it("names the channels that are set up and not the types a channel could be", () => {
    const answer = {
      enabled: true,
      items: [
        { name: "slack", type: "slack", configured: true },
        { name: "email", type: "email", configured: false },
        { name: "webhook", type: "webhook", configured: true },
      ],
      types: [{ name: "slack", keys: [] }],
    };
    expect(heldBy("channel", answer)).toEqual([{ name: "slack" }, { name: "webhook" }]);
  });

  it("reads the rules, the destinations, the registry logins and the variables where each list keeps them", () => {
    expect(heldBy("alert_rule", { items: [{ name: "disk" }], firing: 0 })).toEqual([{ name: "disk" }]);
    expect(heldBy("backup_destination", { destinations: [{ name: "nas" }, { name: "r2" }], total: 2 })).toEqual([{ name: "nas" }, { name: "r2" }]);
    expect(heldBy("registry", { logins: [{ name: "ghcr" }], kinds: [] })).toEqual([{ name: "ghcr" }]);
    expect(heldBy("variable", { variables: [{ name: "TZ", value: "UTC" }] })).toEqual([{ name: "TZ" }]);
  });

  it("holds the console link only when the server has an address, and says which", () => {
    expect(heldBy("console_link", { url: "https://web-01.example.com" })).toEqual([{ name: "console", detail: "https://web-01.example.com" }]);
    expect(heldBy("console_link", { url: "" })).toEqual([]);
    expect(heldBy("console_link", { url: "  " })).toEqual([]);
  });

  it("holds nothing from an answer it does not understand, and skips an entry with no name", () => {
    expect(heldBy("alert_rule", null)).toEqual([]);
    expect(heldBy("registry", { logins: "none" })).toEqual([]);
    expect(heldBy("variable", { variables: [{ value: "x" }, { name: "" }, 4] })).toEqual([]);
  });
});

describe("what only a server has", () => {
  it("leaves out what the central has a setting of, whether for the fleet or for that server alone", () => {
    const held = [{ name: "slack" }, { name: "pager-web-01" }, { name: "webhook" }];
    expect(onlyHere(held, new Set(["slack", "webhook"]))).toEqual([{ name: "pager-web-01" }]);
  });

  it("keeps everything when the central has none, in the order the server gave it", () => {
    expect(onlyHere([{ name: "b" }, { name: "a" }], new Set())).toEqual([{ name: "b" }, { name: "a" }]);
  });

  it("is not asked of the policy, which every server has", () => {
    expect(isNamedKind("policy")).toBe(false);
    expect(isNamedKind("channel")).toBe(true);
  });
});
