import { act, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BackupStorage } from "../../api/queries/backups";
import { setLocale } from "../../app/locale";
import { loadCatalog } from "../../i18n";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { backupFilesystem, backupSummary } from "./StorageUsageBar";

const GB = 1024 ** 3;

function storage(overrides: Partial<BackupStorage> = {}): BackupStorage {
  return {
    path: "/mnt/backups/noust",
    total_size: 3 * GB,
    total_size_human: "3.00 GB",
    backup_count: 12,
    domains: ["shop-example-com", "api-example-com"],
    misplaced: [],
    filesystem_total: 500 * GB,
    filesystem_free: 120 * GB,
    ...overrides,
  };
}

function backupsPage(answer: BackupStorage) {
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/backups": () => json(200, { backups: [], total: 0 }),
    "GET /api/backups/storage": () => json(200, answer),
    "GET /api/backup-schedules": () => json(200, { schedules: [], total: 0 }),
    "GET /api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
  });
  return renderConsole("/backups");
}

describe("backup storage", () => {
  it("reads the filesystem the backup directory is on, and nothing when it could not be read", () => {
    expect(backupFilesystem(storage())).toEqual({ total: 500 * GB, free: 120 * GB, used: 380 * GB });
    expect(backupFilesystem(storage({ filesystem_total: null, filesystem_free: null }))).toBeNull();
    expect(backupSummary(storage())).toBe("3.00 GB in 12 backups of 2 applications, kept at ");
    expect(backupSummary(storage({ backup_count: 1, domains: ["a"] }))).toBe("3.00 GB in 1 backup of 1 application, kept at ");
  });

  it("measures the backup directory's own disk, used and free, and names the directory", async () => {
    const { container } = backupsPage(storage());
    const meter = await screen.findByRole("meter", { name: "Disk holding the backups" });
    expect(meter).toHaveAttribute("aria-valuetext", "380 GB used, 120 GB free of 500 GB");
    expect(screen.getByText("/mnt/backups/noust")).toBeInTheDocument();
    expect(screen.getByText(/^3\.00 GB in 12 backups of 2 applications, kept at/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /outside the backup directory/ })).toBeNull();
    await expectNoAxeViolations(container);
  });

  it("says so when the disk could not be read, instead of drawing an empty meter", async () => {
    backupsPage(storage({ filesystem_total: null, filesystem_free: null }));
    expect(await screen.findByText("Its size and free space could not be read")).toBeInTheDocument();
    expect(screen.queryByRole("meter", { name: "Disk holding the backups" })).toBeNull();
  });

  it("names backups found outside the backup directory, with the exact command that imports them", async () => {
    const { container } = backupsPage(
      storage({
        misplaced: [
          { directory: "/root", count: 3, command: "noust backup import /root" },
          { directory: "/var/backups/noust", count: 1, command: "noust backup import /var/backups/noust" },
        ],
      }),
    );
    const notice = await screen.findByRole("region", { name: "4 backups are outside the backup directory" });
    expect(notice).toHaveTextContent("3 backups in /root");
    expect(notice).toHaveTextContent("1 backup in /var/backups/noust");
    expect(notice).toHaveTextContent("noust backup import /root");
    expect(notice).toHaveTextContent("noust backup import /var/backups/noust");
    expect(notice).toHaveTextContent("Add --dry-run to the command to see what would move first, without moving anything.");
    await expectNoAxeViolations(container);
  });
});

describe("backup storage in Spanish", () => {
  it("translates the summary sentence and pluralises the counts", async () => {
    await loadCatalog("es");
    expect(backupSummary(storage(), "es")).toBe("3.00 GB en 12 copias de seguridad de 2 aplicaciones, guardadas en ");
    expect(backupSummary(storage({ backup_count: 1, domains: ["a"] }), "es")).toBe("3.00 GB en 1 copia de seguridad de 1 aplicación, guardadas en ");
  });

  it("shows the disk meter and the misplaced notice in Spanish, with no accessibility violations", async () => {
    await act(() => setLocale("es"));
    const { container } = backupsPage(
      storage({ misplaced: [{ directory: "/root", count: 3, command: "noust backup import /root" }] }),
    );
    const meter = await screen.findByRole("meter", { name: "Disco que contiene las copias de seguridad" });
    expect(meter).toHaveAttribute("aria-valuetext", "380 GB usados, 120 GB libres de 500 GB");
    const notice = await screen.findByRole("region", { name: "3 copias de seguridad están fuera del directorio de copias de seguridad" });
    expect(notice).toHaveTextContent("3 copias de seguridad en /root");
    await expectNoAxeViolations(container);
  });
});
