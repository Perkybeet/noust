import { describe, expect, it } from "vitest";

import type { BackupList } from "../../api/queries/backups";
import type { CertEntry } from "../../api/queries/certs";
import type { Job } from "../../api/queries/jobs";
import type { Operation } from "../../api/queries/operations";
import { bindT, loadCatalog } from "../../i18n";
import { overviewFixture } from "./testFixtures";
import { backupDays, certificatesOf, figuresOf, fleetWorkOf, latestBackup, workOf } from "./widgetData";

const en = bindT("en");
const es = bindT("es");

function figure(overview: ReturnType<typeof overviewFixture>, id: string, t = en) {
  const found = figuresOf(overview, t).find((entry) => entry.id === id);
  if (found === undefined) throw new Error(`no figure ${id}`);
  return found;
}

describe("the six figures", () => {
  it("are the ones the Overview carries, in the order a person reads them", () => {
    expect(figuresOf(overviewFixture(), en).map((entry) => entry.id)).toEqual(["apps", "deploys", "certificates", "backups", "disk", "updates"]);
  });

  it("leave a server that is well in stone: no state anywhere", () => {
    for (const entry of figuresOf(overviewFixture(), en)) expect(entry.state, entry.id).toBeNull();
  });

  it("count the applications and colour them red when one fails", () => {
    const calm = figure(overviewFixture(), "apps");
    expect(calm).toMatchObject({ value: "5", context: "4 running", state: null, to: "/apps" });
    const failing = figure(overviewFixture({ apps: { running: 9, failed: 1, stopped: 1, static: 1, unmanaged: 0, error: null } }), "apps");
    expect(failing).toMatchObject({ value: "12", context: "10 running, 1 failing", state: "failed" });
  });

  it("say how the deploys of today went", () => {
    const base = overviewFixture().deploys;
    expect(figure(overviewFixture({ deploys: { ...base, total: 7, failed: 1, running: 1 } }), "deploys")).toMatchObject({ value: "7", context: "1 failed, 1 in progress", state: "warning" });
    expect(figure(overviewFixture({ deploys: { ...base, total: 0, succeeded: 0, finished: 0 } }), "deploys").context).toBe("No deploys yet");
    expect(figure(overviewFixture(), "deploys")).toMatchObject({ value: "2", context: "All succeeded", state: null });
  });

  it("count the certificates that need a look, and say when the next expires", () => {
    const base = overviewFixture().certificates;
    expect(figure(overviewFixture({ certificates: { ...base, expiring: 1, next_days: 12 } }), "certificates")).toMatchObject({ value: "1", context: "expires in 12 days", state: "warning" });
    expect(figure(overviewFixture({ certificates: { ...base, expiring: 1, next_days: 1 } }), "certificates").context).toBe("expires in 1 day");
    expect(figure(overviewFixture({ certificates: { ...base, expired: 2 } }), "certificates")).toMatchObject({ value: "2", context: "2 expired", state: "failed" });
    expect(figure(overviewFixture(), "certificates")).toMatchObject({ value: "0", context: "4 valid", state: null });
  });

  it("say how many applications have a backup from the last day, and what failed", () => {
    const base = overviewFixture().backups;
    expect(figure(overviewFixture({ backups: { ...base, apps: 6, with_backup_24h: 5, failed_24h: 1 } }), "backups")).toMatchObject({ value: "5 of 6", context: "1 backup failed", state: "warning" });
    expect(figure(overviewFixture({ backups: { ...base, apps: 6, with_backup_24h: 5 } }), "backups").context).toBe("1 without a copy");
    expect(figure(overviewFixture(), "backups")).toMatchObject({ value: "4 of 4", context: "All have a copy", state: null });
    expect(figure(overviewFixture({ backups: { ...base, apps: 0, with_backup_24h: 0 } }), "backups")).toMatchObject({ value: "0", context: "No applications" });
  });

  it("turn the disk amber from 75 % and red from 90 %, with when it fills", () => {
    const base = overviewFixture().disk;
    expect(figure(overviewFixture({ disk: { ...base, percent: 62 } }), "disk")).toMatchObject({ value: "62%", state: null, context: null });
    expect(figure(overviewFixture({ disk: { ...base, percent: 76, forecast_full_days: 140 } }), "disk")).toMatchObject({ state: "warning", context: "full in about 140 days" });
    expect(figure(overviewFixture({ disk: { ...base, percent: 93 } }), "disk").state).toBe("failed");
  });

  it("count the updates and say how many are security ones", () => {
    const base = overviewFixture().updates;
    expect(figure(overviewFixture({ updates: { ...base, available: 14, security: 2 } }), "updates")).toMatchObject({ value: "14", context: "2 security", state: "warning" });
    expect(figure(overviewFixture(), "updates")).toMatchObject({ value: "0", context: "Up to date", state: null });
    expect(figure(overviewFixture({ updates: { ...base, supported: false, available: null } }), "updates").context).toBe("Not managed on this system");
  });

  it("say they could not be read with a dash, never with a zero that reads as none", () => {
    const calm = overviewFixture();
    const broken = overviewFixture({
      apps: { ...calm.apps, error: "unit list failed" },
      deploys: { ...calm.deploys, error: "store locked" },
      certificates: { ...calm.certificates, error: "no certbot" },
      backups: { ...calm.backups, error: "no store" },
      disk: { ...calm.disk, error: "statvfs" },
      updates: { ...calm.updates, error: "apt", available: null },
    });
    for (const entry of figuresOf(broken, en)) {
      expect(entry.value, entry.id).toBe("-");
      expect(entry.state, entry.id).toBeNull();
    }
  });

  it("are said in the language of the console", async () => {
    await loadCatalog("es");
    expect(figuresOf(overviewFixture(), es).map((entry) => entry.label)).toEqual(["Aplicaciones", "Despliegues hoy", "Certificados", "Copias en 24 h", "Disco", "Actualizaciones del sistema"]);
    expect(figure(overviewFixture({ apps: { running: 9, failed: 1, stopped: 1, static: 1, unmanaged: 0, error: null } }), "apps", es).context).toBe("10 en marcha, 1 con fallo");
  });
});

function job(patch: Partial<Job>): Job {
  return {
    id: "j1",
    type: "deploy",
    name: "Deploy shop.example.com",
    title: null,
    description: "",
    status: "running",
    state: "running",
    progress: 2,
    total_steps: 5,
    current_step: "Building",
    created_at: "2026-09-29T21:40:00+00:00",
    cancellable: false,
    cancel_requested: false,
    ...patch,
  };
}

function operation(patch: Partial<Operation> = {}): Operation {
  return { id: "op1", title: { code: "operation.restart_service", params: { name: "nginx" } }, started_at: "2026-09-29T21:40:00+00:00", ...patch };
}

describe("work in progress", () => {
  it("lists what runs first, then the long operations that are not jobs, then what waits", () => {
    const work = workOf([job({ id: "q", state: "queued", status: "queued", name: "Backup shop", waiting_for: "a free build slot" }), job({ id: "r" })], [operation()], en);
    expect(work.running).toBe(2);
    expect(work.queued).toBe(1);
    expect(work.lines.map((line) => line.key)).toEqual([":job:r", ":operation:op1", ":job:q"]);
  });

  it("says how far a job has got, and what a queued one waits for", () => {
    const work = workOf([job({}), job({ id: "q", state: "queued", status: "queued", waiting_for: "a free build slot" })], [], en);
    expect(work.lines[0]).toMatchObject({ title: "Deploy shop.example.com", step: "Building", percent: 40, state: "deploying" });
    expect(work.lines[1]).toMatchObject({ step: "a free build slot", percent: null, state: "queued" });
  });

  it("has no percentage for a job that has no steps, nor for an operation", () => {
    const work = workOf([job({ total_steps: 0, progress: 0 })], [operation()], en);
    expect(work.lines.map((line) => line.percent)).toEqual([null, null]);
  });

  it("says the title of a job in the console's language when the server sent it as a code", () => {
    const sent = job({ title: { code: "job.deploy", params: { domain: "shop.example.com" } } });
    expect(workOf([sent], [], en).lines[0]?.title).toBeTruthy();
  });

  it("leaves out a job that is no longer running", () => {
    expect(workOf([job({ state: "succeeded", status: "completed" })], [], en).lines).toEqual([]);
  });

  it("names the server of each line when it is the fleet's work", () => {
    const work = fleetWorkOf(
      [
        { server: "web-01", jobs: [job({})], operations: [] },
        { server: "web-02", jobs: [], operations: [operation()] },
      ],
      en,
    );
    expect(work.lines.map((line) => line.server)).toEqual(["web-01", "web-02"]);
    expect(work.running).toBe(2);
  });
});

function cert(domain: string, days: number | null, autoRenew = true): CertEntry {
  return { domain, domains: [domain], serves: [domain], days_remaining: days, auto_renew: autoRenew, challenge: "http", expires_on: null, issuer: null, key_path: null, path: null, source: "certbot", valid_until: null };
}

describe("certificates", () => {
  it("are the ones that expire within the days Noust warns, soonest first", () => {
    const view = certificatesOf([cert("a.example.com", 60), cert("b.example.com", 12), cert("c.example.com", 3), cert("d.example.com", 21)]);
    expect(view.needing.map((row) => [row.domain, row.days])).toEqual([
      ["c.example.com", 3],
      ["b.example.com", 12],
    ]);
    expect(view.well).toBe(2);
    expect(view.total).toBe(4);
  });

  it("count how many of the fine ones renew by themselves", () => {
    const view = certificatesOf([cert("a.example.com", 60), cert("b.example.com", 80, false), cert("c.example.com", 90)]);
    expect(view.renewing).toBe(2);
  });

  it("do not guess at one with no date", () => {
    const view = certificatesOf([cert("a.example.com", null), cert("b.example.com", 5)]);
    expect(view.needing.map((row) => row.domain)).toEqual(["b.example.com"]);
    expect(view.well).toBe(0);
    expect(view.total).toBe(2);
  });
});

function backup(timestamp: string, patch: Partial<BackupList["backups"][number]> = {}): BackupList["backups"][number] {
  return { backup_id: timestamp, domain: "shop.example.com", timestamp, age: "", size: 1, size_human: "1 B", description: "", tags: [], has_database: false, includes_build: false, includes_env: true, includes_node_modules: false, git_branch: null, git_commit: null, app_type: null, last_verified_at: null, verified_ok: null, database_backups: {}, ...patch } as BackupList["backups"][number];
}

describe("backups", () => {
  const now = new Date(2026, 8, 29, 12, 0, 0);

  it("count the backups of each of the last days, oldest first", () => {
    const days = backupDays([backup(new Date(2026, 8, 29, 2, 0, 0).toISOString()), backup(new Date(2026, 8, 29, 9, 0, 0).toISOString()), backup(new Date(2026, 8, 27, 2, 0, 0).toISOString())], now, 5);
    expect(days.map((day) => [day.date, day.count])).toEqual([
      ["2026-09-25", 0],
      ["2026-09-26", 0],
      ["2026-09-27", 1],
      ["2026-09-28", 0],
      ["2026-09-29", 2],
    ]);
  });

  it("are fourteen days unless asked for another number", () => {
    expect(backupDays([], now)).toHaveLength(14);
  });

  it("ignore a backup that has no readable time", () => {
    expect(backupDays([backup("never")], now, 3).every((day) => day.count === 0)).toBe(true);
  });

  it("know the newest one, and whether it was verified", () => {
    const last = latestBackup([backup("2026-09-27T02:00:00+00:00"), backup("2026-09-29T02:00:00+00:00", { domain: "blog.example.com", verified_ok: true })]);
    expect(last?.domain).toBe("blog.example.com");
    expect(last?.verified).toBe(true);
    expect(latestBackup([])).toBeNull();
  });
});
