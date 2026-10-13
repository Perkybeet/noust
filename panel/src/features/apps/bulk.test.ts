import { describe, expect, it } from "vitest";

import { counts, planBulk } from "./bulk";
import type { AppInfo } from "./data";

function app(domain: string, status: string, app_type = "nextjs"): AppInfo {
  return { domain, name: domain, status, active: status === "running", enabled: true, port: 3000, app_type, layout: "inplace" } as AppInfo;
}

const FLEET = [
  app("shop.example.com", "running"),
  app("admin.example.com", "failed"),
  app("blog.example.com", "stopped"),
  app("docs.example.com", "static", "static"),
  app("api.example.com", "deploying"),
];

function skips(action: Parameters<typeof planBulk>[0]): Record<string, string | null> {
  return Object.fromEntries(planBulk(action, FLEET).map((entry) => [entry.domain, entry.skip]));
}

describe("what a bulk action will do to each application", () => {
  it("restarts what has a process that is not being deployed, a failed one included", () => {
    expect(skips("restart")).toEqual({
      "shop.example.com": null,
      "admin.example.com": null,
      "blog.example.com": null,
      "docs.example.com": "static",
      "api.example.com": "busy",
    });
  });

  it("starts what is not running, and says what already is", () => {
    expect(skips("start")).toEqual({
      "shop.example.com": "running",
      "admin.example.com": null,
      "blog.example.com": null,
      "docs.example.com": "static",
      "api.example.com": "busy",
    });
  });

  it("stops what runs, and says what is already stopped", () => {
    expect(skips("stop")).toEqual({
      "shop.example.com": null,
      "admin.example.com": null,
      "blog.example.com": "stopped",
      "docs.example.com": "static",
      "api.example.com": "busy",
    });
  });

  it("updates everything, a static site and an application being deployed included, because updates queue", () => {
    expect(Object.values(skips("update"))).toEqual([null, null, null, null, null]);
  });

  it("keeps the order the applications were chosen in", () => {
    expect(planBulk("restart", FLEET).map((entry) => entry.domain)).toEqual(FLEET.map((entry) => entry.domain));
  });

  it("counts what will be done and what will not", () => {
    expect(counts(planBulk("stop", FLEET))).toEqual({ run: 2, skipped: 3 });
  });
});
