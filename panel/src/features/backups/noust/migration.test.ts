import { describe, expect, it } from "vitest";

import { readMigrationReport } from "./migration";

const RESULT = {
  host: "old-host",
  destination: "nas",
  ok: false,
  bundle: "noust-self-old-host-20261013.tar.gz.enc",
  steps: [
    { kind: "application", name: "shop.example.com", state: "done", detail: "", backup: "shop-b1" },
    { kind: "data", name: "shop.example.com/uploads", state: "failed", detail: "the destination refused it", backup: null },
  ],
  next_steps: ["Install Noust on the new server", "noust server migrate check"],
  warnings: ["A backup of a running application is as consistent as its own dump allows"],
};

describe("reading a preparation's result", () => {
  it("reads the steps, the bundle and what to run next", () => {
    const report = readMigrationReport(RESULT);

    expect(report?.host).toBe("old-host");
    expect(report?.ok).toBe(false);
    expect(report?.bundle).toBe("noust-self-old-host-20261013.tar.gz.enc");
    expect(report?.steps.map((step) => [step.name, step.state, step.backup])).toEqual([
      ["shop.example.com", "done", "shop-b1"],
      ["shop.example.com/uploads", "failed", null],
    ]);
    expect(report?.steps[1]?.detail).toBe("the destination refused it");
    expect(report?.nextSteps).toEqual(["Install Noust on the new server", "noust server migrate check"]);
  });

  it("is null for the result of any other job", () => {
    expect(readMigrationReport(null)).toBeNull();
    expect(readMigrationReport({ name: "noust-self.tar.gz.enc", size: 10 })).toBeNull();
    expect(readMigrationReport("done")).toBeNull();
    expect(readMigrationReport([])).toBeNull();
  });

  it("keeps a missing bundle as null and tolerates steps it cannot read", () => {
    const report = readMigrationReport({ ...RESULT, bundle: null, steps: [RESULT.steps[0], 7, null] });

    expect(report?.bundle).toBeNull();
    expect(report?.steps).toHaveLength(1);
  });
});
