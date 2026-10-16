import { describe, expect, it } from "vitest";

import type { SelfSchedule } from "../../../api/queries/selfBackup";
import { MAX_PASSPHRASE, MIN_PASSPHRASE, bodyOf, bundleKind, formOf, passphraseKnown, passphraseProblem, readSchedule, restoreCommand, restoreServerCommand } from "./data";

const SCHEDULE: SelfSchedule = {
  configured: true,
  schedule: "*-*-* 03:00:00",
  retention_count: 7,
  retention_days: 30,
  destinations: [{ name: "nas", retention_count: 14, retention_days: null, exists: true, encrypted: true }],
  enabled: true,
  passphrase_stored: true,
  timer: { installed: true },
  last_run_at: "2026-10-06T03:00:04+00:00",
  last_status: "ok",
  last_error: null,
  last_file: "noust-self-web-1-20261006-030004.tar.gz.enc",
  last_success_at: "2026-10-06T03:00:04+00:00",
  last_verified_at: "2026-10-06T03:00:30+00:00",
  last_verify_status: "ok",
  created_at: "2026-10-01T00:00:00+00:00",
  updated_at: "2026-10-01T00:00:00+00:00",
};

describe("whether Noust's own backup is working", () => {
  it("is off before there is a schedule, and paused while it is disabled", () => {
    expect(readSchedule({ ...SCHEDULE, configured: false })).toEqual({ health: "off", problems: [] });
    expect(readSchedule({ ...SCHEDULE, enabled: false })).toEqual({ health: "paused", problems: [] });
  });

  it("is working when the timer is there, the passphrase is kept and the last run went well", () => {
    expect(readSchedule(SCHEDULE)).toEqual({ health: "ok", problems: [] });
  });

  it("names what is wrong when it is on but cannot do what it says", () => {
    expect(readSchedule({ ...SCHEDULE, passphrase_stored: false }).problems).toEqual(["noPassphrase"]);
    expect(readSchedule({ ...SCHEDULE, passphrase_stored: null }).problems).toEqual(["locked"]);
    expect(readSchedule({ ...SCHEDULE, timer: {} }).problems).toEqual(["timerMissing"]);
    expect(readSchedule({ ...SCHEDULE, last_status: "failed" })).toEqual({ health: "problem", problems: ["lastFailed"] });
    expect(readSchedule({ ...SCHEDULE, timer: {}, last_status: "failed" }).problems).toEqual(["timerMissing", "lastFailed"]);
  });

  it("knows the passphrase only when the server keeps it", () => {
    expect(passphraseKnown(SCHEDULE)).toBe(true);
    expect(passphraseKnown({ passphrase_stored: false })).toBe(false);
    expect(passphraseKnown({ passphrase_stored: null })).toBe(false);
  });
});

describe("a bundle", () => {
  it("is scheduled, by hand, or the copy a restore took first", () => {
    expect(bundleKind({ kind: "scheduled", scheduled: true })).toBe("scheduled");
    expect(bundleKind({ kind: "manual", scheduled: false })).toBe("manual");
    expect(bundleKind({ kind: "pre-restore", scheduled: false })).toBe("pre-restore");
  });

  it("is restored by a command at a terminal, never from the console", () => {
    expect(restoreCommand("noust-self-web-1.tar.gz.enc")).toBe("noust self-backup restore noust-self-web-1.tar.gz.enc");
    expect(restoreServerCommand("noust-self-web-1.tar.gz.enc")).toBe("noust restore-server --from noust-self-web-1.tar.gz.enc");
  });
});

describe("the schedule form", () => {
  it("starts new on daily, keeping 7 for 30 days, with no passphrase typed", () => {
    const form = formOf(undefined);
    expect(form).toMatchObject({ count: "7", days: "30", passphrase: "", enabled: true });
    expect(bodyOf(form)).toMatchObject({ schedule: "*-*-* 02:00:00", retention_count: 7, retention_days: 30, passphrase: null });
  });

  it("is read from the stored schedule and keeps the stored passphrase when none is typed", () => {
    const form = formOf(SCHEDULE);
    expect(form.sends).toEqual([{ name: "nas", count: "14", days: "", gb: "" }]);
    expect(bodyOf(form)).toEqual({
      schedule: "*-*-* 03:00:00",
      retention_count: 7,
      retention_days: 30,
      retention_gb: null,
      destinations: [{ name: "nas", retention_count: 14, retention_days: null, retention_gb: null }],
      passphrase: null,
      enabled: true,
    });
  });

  it("sends the passphrase only when one is typed", () => {
    expect(bodyOf({ ...formOf(SCHEDULE), passphrase: "correct horse battery" }).passphrase).toBe("correct horse battery");
  });
});

describe("a passphrase", () => {
  it("is long enough, not past the limit, and typed the same twice", () => {
    expect(passphraseProblem("short", null)).toBe("short");
    expect(passphraseProblem("x".repeat(MIN_PASSPHRASE - 1), null)).toBe("short");
    expect(passphraseProblem("x".repeat(MIN_PASSPHRASE), null)).toBeNull();
    expect(passphraseProblem("x".repeat(MAX_PASSPHRASE + 1), null)).toBe("long");
    expect(passphraseProblem("correct horse battery", "correct horse batter")).toBe("mismatch");
    expect(passphraseProblem("correct horse battery", "correct horse battery")).toBeNull();
  });
});

describe("the size the bundles may weigh", () => {
  it("is read from the stored schedule and sent as gigabytes, empty meaning no limit", () => {
    const form = formOf({ ...SCHEDULE, retention_gb: 1.5, destinations: [{ name: "nas", retention_count: 14, retention_days: null, retention_gb: 10 }] });
    expect(form.gb).toBe("1.5");
    expect(form.sends).toEqual([{ name: "nas", count: "14", days: "", gb: "10" }]);
    expect(bodyOf(form)).toMatchObject({ retention_gb: 1.5, destinations: [{ name: "nas", retention_gb: 10 }] });
    expect(bodyOf({ ...form, gb: "" }).retention_gb).toBeNull();
  });

  it("starts a new schedule with no limit by size", () => {
    expect(formOf(undefined).gb).toBe("");
  });
});
