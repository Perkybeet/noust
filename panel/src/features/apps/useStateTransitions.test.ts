import { beforeAll, describe, expect, it } from "vitest";

import { loadCatalog } from "../../i18n";
import { describeTransitions } from "./useStateTransitions";

describe("describeTransitions", () => {
  beforeAll(() => loadCatalog("es"));

  const before = new Map([
    ["shop.example.net", "Running"],
    ["example.org", "Running"],
  ]);

  it("says nothing when nothing changed", () => {
    expect(
      describeTransitions(before, [
        { domain: "shop.example.net", status: "running" },
        { domain: "example.org", status: "Running" },
      ]),
    ).toBeNull();
  });

  it("names every app that changed, in one sentence", () => {
    expect(
      describeTransitions(before, [
        { domain: "shop.example.net", status: "deploying" },
        { domain: "example.org", status: "stopped" },
      ]),
    ).toEqual({ message: "shop.example.net: Deploying. example.org: Stopped.", failed: false });
  });

  it("flags a failure so it is said assertively", () => {
    expect(describeTransitions(before, [{ domain: "shop.example.net", status: "failed" }])?.failed).toBe(true);
  });

  it("says the new state in the language it is given, and a language switch is no transition", () => {
    expect(describeTransitions(before, [{ domain: "shop.example.net", status: "failed" }], "es")).toEqual({
      message: "shop.example.net: Fallido.",
      failed: true,
    });
    expect(describeTransitions(before, [{ domain: "shop.example.net", status: "running" }], "es")).toBeNull();
  });

  it("does not count an app that just appeared", () => {
    expect(describeTransitions(before, [{ domain: "new.example.com", status: "running" }])).toBeNull();
  });
});
