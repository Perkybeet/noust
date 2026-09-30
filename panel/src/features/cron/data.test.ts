import { describe, expect, it } from "vitest";

import { loadCatalog } from "../../i18n";
import type { CronJob } from "./data";
import { calendarWords, filterJobs, isFiltered, runStatus, runTimes, validateCronSearch } from "./data";

function job(name: string, command: string): CronJob {
  return {
    name,
    command,
    user: "wasm",
    working_directory: "/var/www",
    app_domain: "",
    schedule: "daily",
    on_calendar: "*-*-* 02:00:00",
    enabled: true,
    next_run: "Fri 2026-09-26 02:00:00 UTC",
    last_run: "never",
    last_exit_code: null,
    last_result: "never ran",
  };
}

describe("runStatus", () => {
  it("reads a successful run as running/Succeeded", () => {
    expect(runStatus("success")).toEqual({ state: "running", label: "Succeeded" });
  });

  it("reads a job that never ran as unknown", () => {
    expect(runStatus("never ran")).toEqual({ state: "unknown", label: "Never run" });
    expect(runStatus(null)).toEqual({ state: "unknown", label: "Never run" });
    expect(runStatus(undefined)).toEqual({ state: "unknown", label: "Never run" });
  });

  it("reads any other systemd Result as failed, keeping the word", () => {
    expect(runStatus("exit-code")).toEqual({ state: "failed", label: "Failed", detail: "exit-code" });
    expect(runStatus("timeout")).toEqual({ state: "failed", label: "Failed", detail: "timeout" });
    expect(runStatus("signal")).toEqual({ state: "failed", label: "Failed", detail: "signal" });
  });
});

describe("validateCronSearch", () => {
  it("keeps a trimmed query and drops malformed input", () => {
    expect(validateCronSearch({ q: " nightly " })).toEqual({ q: "nightly" });
    expect(validateCronSearch({ q: "" })).toEqual({});
    expect(validateCronSearch({ q: 1 })).toEqual({});
  });
});

describe("filterJobs", () => {
  const jobs = [job("nightly-backup", "noust backup create example.com"), job("hourly-sync", "rsync -a /a /b")];

  it("matches the name or the command", () => {
    expect(filterJobs(jobs, { q: "BACKUP" }).map((j) => j.name)).toEqual(["nightly-backup"]);
    expect(filterJobs(jobs, { q: "rsync" }).map((j) => j.name)).toEqual(["hourly-sync"]);
  });

  it("keeps everything without a filter", () => {
    expect(filterJobs(jobs, {})).toHaveLength(2);
    expect(isFiltered({})).toBe(false);
    expect(isFiltered({ q: "x" })).toBe(true);
  });
});

describe("calendarWords", () => {
  it("says the shapes an operator writes in words, and names anything else by its preset or as custom", () => {
    expect(calendarWords("daily", "*-*-* 02:00:00")).toBe("Every day at 02:00");
    expect(calendarWords("custom", "*-*-* 3:05")).toBe("Every day at 03:05");
    expect(calendarWords("hourly", "*-*-* *:00:00")).toBe("Every hour, on the hour");
    expect(calendarWords("custom", "*-*-* *:00/15:00")).toBe("Every 15 minutes");
    expect(calendarWords("custom", "*-*-* *:0/1")).toBe("Every minute");
    expect(calendarWords("weekly", "Mon *-*-* 02:00:00")).toBe("Every Monday at 02:00");
    expect(calendarWords("custom", "Mon..Fri *-*-* 09:00:00")).toBe("Monday to Friday at 09:00");
    expect(calendarWords("monthly", "*-*-01 02:00:00")).toBe("On day 1 of every month at 02:00");
    expect(calendarWords("custom", "Sat,Sun *-*-* 04:00:00")).toBe("Custom schedule");
    // Nothing to read: the preset it was made from says it.
    expect(calendarWords("weekly", "")).toBe("Every Monday at 02:00");
    expect(calendarWords("custom", "*-01,07-01 00:00:00")).toBe("Custom schedule");
  });
});

describe("runTimes", () => {
  it("leads with the server's clock, the one the schedule is written in", () => {
    const run = runTimes("2026-09-30T02:00:00+00:00");
    expect(run.server).toBe("Wed, Sep 30, 02:00");
    expect(run.zone).toBe("UTC");
  });

  it("names a server clock that is not UTC by its offset", () => {
    const run = runTimes("2026-09-30T02:00:00+02:00");
    expect(run.server).toBe("Wed, Sep 30, 02:00");
    expect(run.zone).toBe("UTC+02:00");
  });

  it("keeps what it cannot place in time as it came", () => {
    expect(runTimes("Wed 2026-09-30 02:00:00 CEST")).toEqual({ server: "Wed 2026-09-30 02:00:00 CEST", zone: "", local: null });
  });
});

describe("in Spanish", () => {
  it("translates the run status words", async () => {
    await loadCatalog("es");
    expect(runStatus("success", "es")).toEqual({ state: "running", label: "Correcta" });
    expect(runStatus("never ran", "es")).toEqual({ state: "unknown", label: "Nunca se ejecutó" });
    expect(runStatus("timeout", "es")).toEqual({ state: "failed", label: "Fallida", detail: "timeout" });
  });

  it("translates the schedule words", async () => {
    await loadCatalog("es");
    expect(calendarWords("daily", "*-*-* 02:00:00", "es")).toBe("Cada día a las 02:00");
    expect(calendarWords("weekly", "Mon *-*-* 02:00:00", "es")).toBe("Cada lunes a las 02:00");
    expect(calendarWords("custom", "Mon..Fri *-*-* 09:00:00", "es")).toBe("De lunes a viernes a las 09:00");
    expect(calendarWords("custom", "*-01,07-01 00:00:00", "es")).toBe("Programación personalizada");
  });
});
