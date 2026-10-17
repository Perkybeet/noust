import { describe, expect, it } from "vitest";

import { backupKind, bodyOf, checkOf, formOf, parsePaths, stateOf, totalBytes, validPath, volumeRows } from "./data";
import { backup, policy } from "./testFixtures";

describe("a data policy's state", () => {
  it("is none, paused, failed, scheduled or working", () => {
    expect(stateOf(undefined)).toBe("none");
    expect(stateOf({ ...policy("a"), configured: false })).toBe("none");
    expect(stateOf({ ...policy("a"), enabled: false })).toBe("paused");
    expect(stateOf({ ...policy("a"), last_status: "failed" })).toBe("failed");
    expect(stateOf({ ...policy("a"), last_status: null })).toBe("scheduled");
    expect(stateOf(policy("a"))).toBe("ok");
  });
});

describe("the rows of the Volumes tab", () => {
  it("are one per application with a policy or backups, alphabetical, with the newest backup", () => {
    const rows = volumeRows(
      [policy("shop.example.com"), { ...policy("never.example.com"), configured: false }],
      [
        backup("a", "wiki.example.com", { created_at: "2026-10-01T00:00:00+00:00" }),
        backup("b", "wiki.example.com", { created_at: "2026-10-05T00:00:00+00:00" }),
        backup("c", "shop.example.com"),
      ],
    );
    expect(rows.map((row) => row.domain)).toEqual(["shop.example.com", "wiki.example.com"]);
    expect(rows[1]?.newest?.id).toBe("b");
    expect(rows[1]?.state).toBe("none");
    expect(rows[0]?.state).toBe("ok");
    expect(rows[0]?.policy?.domain).toBe("shop.example.com");
  });

  it("are nothing when there is nothing", () => {
    expect(volumeRows(undefined, undefined)).toEqual([]);
    expect(volumeRows([], [])).toEqual([]);
  });

  it("add up what an application's data backups weigh", () => {
    expect(totalBytes([backup("a", "x", { size_bytes: 10 }), backup("b", "x", { size_bytes: 32 })])).toBe(42);
  });
});

describe("a backup", () => {
  it("is scheduled, a safety copy, or by hand", () => {
    expect(backupKind({ tags: ["scheduled"] })).toBe("scheduled");
    expect(backupKind({ tags: ["safety"] })).toBe("safety");
    expect(backupKind({ tags: [] })).toBe("manual");
    expect(backupKind({ tags: ["scheduled", "safety"] })).toBe("safety");
  });

  it("is checked, failed or never checked", () => {
    expect(checkOf({ verified_ok: true, last_verified_at: "x" })).toBe("checked");
    expect(checkOf({ verified_ok: false, last_verified_at: "x" })).toBe("failed");
    expect(checkOf({ verified_ok: null, last_verified_at: null })).toBe("never");
  });
});

describe("the policy form", () => {
  it("starts a new policy on everything, daily, keeping 7 for 30 days", () => {
    const form = formOf("shop.example.com", null);
    expect(form).toMatchObject({ everything: true, targets: [], count: "7", days: "30", consistency: "live", enabled: true });
    expect(bodyOf(form)).toMatchObject({ schedule: "*-*-* 02:00:00", targets: [], extra_paths: [], retention_count: 7, retention_days: 30, consistency: "live" });
  });

  it("is read from a stored policy and sent back as it was", () => {
    const stored = policy("shop.example.com", { targets: ["path:uploads", "volume:shop_pgdata"], extra_paths: ["media"], consistency: "pause", retention_days: null });
    const form = formOf("shop.example.com", stored);
    expect(form.everything).toBe(false);
    expect(form.sends).toEqual([{ name: "nas", count: "14", days: "", gb: "" }]);
    expect(bodyOf(form)).toEqual({
      schedule: "*-*-* 02:00:00",
      targets: ["path:uploads", "volume:shop_pgdata"],
      extra_paths: ["media"],
      retention_count: 7,
      retention_days: null,
      retention_gb: null,
      destinations: [{ name: "nas", retention_count: 14, retention_days: null, retention_gb: null }],
      consistency: "pause",
      enabled: true,
    });
  });

  it("sends no keys when everything is asked for, whatever was ticked before", () => {
    const form = { ...formOf("a", policy("a", { targets: ["path:x"] })), everything: true };
    expect(bodyOf(form).targets).toEqual([]);
  });

  it("reads an unknown consistency as live", () => {
    expect(formOf("a", policy("a", { consistency: "weird" })).consistency).toBe("live");
  });
});

describe("the folders added to what an application declares", () => {
  it("are read one per line, once each", () => {
    expect(parsePaths("media\n  uploads \r\nmedia\n\n")).toEqual(["media", "uploads"]);
  });

  it("stay inside the application's data", () => {
    expect(validPath("media/images")).toBe(true);
    expect(validPath("/etc")).toBe(false);
    expect(validPath("../secrets")).toBe(false);
    expect(validPath("a/../b")).toBe(false);
    expect(validPath("~root")).toBe(false);
  });
});

describe("the size a policy's backups may weigh", () => {
  it("is read from a stored policy and sent as gigabytes, empty meaning no limit", () => {
    const stored = policy("shop.example.com", { retention_gb: 4, destinations: [{ name: "nas", retention_count: null, retention_days: null, retention_gb: 20, exists: true, encrypted: false }] });
    const form = formOf("shop.example.com", stored);
    expect(form.gb).toBe("4");
    expect(form.sends).toEqual([{ name: "nas", count: "", days: "", gb: "20" }]);
    expect(bodyOf(form)).toMatchObject({ retention_gb: 4, destinations: [{ name: "nas", retention_gb: 20 }] });
    expect(bodyOf({ ...form, gb: "2,5" }).retention_gb).toBe(2.5);
    expect(bodyOf({ ...form, gb: "" }).retention_gb).toBeNull();
  });

  it("starts a new policy with no limit by size", () => {
    expect(formOf("a", null).gb).toBe("");
    expect(bodyOf(formOf("a", null)).retention_gb).toBeNull();
  });
});
