import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { parseDiskLimit } from "./DiskSettings";
import { DOMAIN, NO_DISK, accepted, jobOf, renderSettings } from "./testKit";

const MB = 1024 * 1024;

const MEASURED = {
  ...NO_DISK,
  soft_mb: 128,
  usage: {
    parts: [
      { name: "shared", bytes: 140 * MB, path: "/var/www/apps/shop/shared", reclaimable_bytes: null },
      { name: "releases", bytes: 120 * MB, path: "/var/www/apps/shop/releases", reclaimable_bytes: 60 * MB },
      { name: "current", bytes: 100 * MB, path: "/var/www/apps/shop/releases/c", reclaimable_bytes: null },
      { name: "repo", bytes: 40 * MB, path: "/var/www/apps/shop/repo", reclaimable_bytes: null },
    ],
    total_bytes: 400 * MB,
    backups_bytes: 9 * MB,
    measured_at: "2026-10-16T10:00:00+00:00",
    errors: [],
  },
  verdict: { limit_bytes: 128 * MB, used_bytes: 400 * MB, percent: 312.5, state: "over" },
};

function resources(disk: object = NO_DISK, routes: Parameters<typeof renderSettings>[3] = {}) {
  return renderSettings("/resources", "Resources", undefined, {
    [`GET /api/apps/${DOMAIN}/disk`]: () => json(200, disk),
    ...routes,
  });
}

describe("the limit as typed", () => {
  it("is megabytes, from the floor, or nothing", () => {
    expect(parseDiskLimit("")).toBeNull();
    expect(parseDiskLimit("  ")).toBeNull();
    expect(parseDiskLimit("2048")).toBe(2048);
    expect(parseDiskLimit(" 64 ")).toBe(64);
    for (const bad of ["63", "0", "-5", "1.5", "2 GB", "abc"]) expect(parseDiskLimit(bad)).toBeUndefined();
  });
});

describe("the disk of an application", () => {
  it("says it was never measured and measures on request, as a job it waits for", async () => {
    let measured = false;
    const job = jobOf("d1", "disk");
    const { user, backend } = await resources(NO_DISK, {
      [`GET /api/apps/${DOMAIN}/disk`]: () => json(200, measured ? MEASURED : NO_DISK),
      [`POST /api/apps/${DOMAIN}/disk/measure`]: () => {
        measured = true;
        return json(202, accepted(job));
      },
      "GET /api/jobs/d1": () => json(200, { ...job, status: "completed", progress: 100 }),
    });
    expect(await screen.findByText(/Never measured/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Measure now" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/disk/measure`)).toHaveLength(1);
    });
    const list = await screen.findByRole("list", { name: "What the app takes, by place" });
    expect(within(list).getByText("Shared files (uploads, .env)")).toBeInTheDocument();
    expect(within(list).getByText("400 MB")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Measure again" })).toBeInTheDocument();
  });

  it("breaks down what it takes, what a prune would give back and how it stands against the limit", async () => {
    await resources(MEASURED);

    const list = await screen.findByRole("list", { name: "What the app takes, by place" });
    expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("Shared files (uploads, .env)"),
      expect.stringContaining("Other releases"),
      expect.stringContaining("The release that serves"),
      expect.stringContaining("Repository cache"),
      expect.stringContaining("In its folder"),
    ]);
    expect(within(list).getByText("60 MB a prune would give back")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Used of the limit" })).toBeInTheDocument();
    expect(screen.getByText(/Its backups take 9\.0 MB and are kept elsewhere, so they do not count\./)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Limit/ })).toHaveValue("128");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("names a place that could not be measured in time", async () => {
    await resources({ ...MEASURED, usage: { ...MEASURED.usage, errors: ["/var/www/apps/shop/shared: could not be measured in time"] } });

    expect(await screen.findByText("Some of it could not be measured in time")).toBeInTheDocument();
    expect(screen.getByText("/var/www/apps/shop/shared: could not be measured in time")).toBeInTheDocument();
  });

  it("saves the limit as megabytes and removes it, with nothing about sudo mode", async () => {
    let current: object = NO_DISK;
    const { user, backend } = await resources(NO_DISK, {
      [`GET /api/apps/${DOMAIN}/disk`]: () => json(200, current),
      [`PUT /api/apps/${DOMAIN}/disk`]: (call) => {
        const soft = (call.body as { soft_mb: number | null }).soft_mb;
        current = { ...NO_DISK, soft_mb: soft };
        return json(200, current);
      },
    });
    const field = await screen.findByRole("textbox", { name: /Limit/ });
    expect(screen.getByRole("button", { name: "Save limit" })).toBeDisabled();

    await user.type(field, "2048");
    await user.click(screen.getByRole("button", { name: "Save limit" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/disk`)[0]?.body).toEqual({ soft_mb: 2048 });
    });
    await user.click(await screen.findByRole("button", { name: "Remove the limit" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/disk`)[1]?.body).toEqual({ soft_mb: null });
    });
    expect(screen.getByRole("textbox", { name: /Limit/ })).toHaveValue("");
  });

  it("checks the floor before asking and shows the server's refusal under the field", async () => {
    const { user, backend } = await resources(NO_DISK, {
      [`PUT /api/apps/${DOMAIN}/disk`]: () => problem(400, "validation_error", "The disk limit must be between 64 MB and 1073741824 MB", { fields: { soft_mb: "Out of range" } }),
    });
    const field = await screen.findByRole("textbox", { name: /Limit/ });

    await user.type(field, "10");
    await user.click(screen.getByRole("button", { name: "Save limit" }));
    expect(await screen.findByText("Give whole megabytes, 64 or more, or leave it empty.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/disk`)).toHaveLength(0);

    await user.clear(field);
    await user.type(field, "99999999999");
    await user.click(screen.getByRole("button", { name: "Save limit" }));
    expect(await screen.findByText("Out of range")).toBeInTheDocument();
  });

  it.each([
    ["enabled", /mounted with project quotas, so a kernel quota can stop the writes themselves/],
    ["available", /can hold a kernel project quota but is not mounted with them/],
    ["unsupported", /has no project quotas, so only this limit applies/],
  ])("says what the filesystem could hold: %s", async (quota, words) => {
    await resources({ ...NO_DISK, filesystem: { mount: "/var", fstype: "xfs", options: [], project_quota: quota } });

    expect(await screen.findByText(words)).toBeInTheDocument();
    expect(screen.getByText(/This limit is Noust's: it is checked before a deploy, never when going back to an earlier release/)).toBeInTheDocument();
  });

  it("is there for a static site too, which has no unit to limit", async () => {
    await renderSettings("/resources", "Resources", { ...(await import("./testKit")).STATIC_APP });

    expect(await screen.findByRole("heading", { name: "Disk" })).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    await renderSettings("/resources", "Recursos", undefined, {
      [`GET /api/apps/${DOMAIN}/disk`]: () => json(200, MEASURED),
    });

    expect(await screen.findByRole("list", { name: "Lo que ocupa la app, por lugar" })).toBeInTheDocument();
    expect(screen.getByText("Ficheros compartidos (subidas, .env)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guardar límite" })).toBeInTheDocument();
  });
});
