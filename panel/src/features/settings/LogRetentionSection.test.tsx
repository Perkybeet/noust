import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { retentionBody } from "./LogRetentionSection";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const KEPT = {
  days: 30,
  max_use_mb: 2048,
  persistent: false,
  journal_on_disk: false,
  disk_usage: "Archived and active journals take up 1.2G in the file system.",
  configured: true,
  foreign: false,
  notes: ["The journal is kept in memory: everything in it is lost at a reboot."],
};

const DEFAULTS = { ...KEPT, days: null, max_use_mb: null, configured: false, notes: [] };

async function pageAt(retention: object = KEPT, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/session": () => json(200, ELEVATED),
    "GET /api/log-drains": () => json(200, { drains: [], total: 0, service: { installed: true, active: true, state: "active" } }),
    "GET /api/logs/retention": () => json(200, retention),
    ...extra,
  });
  const harness = renderConsole("/settings/log-shipping");
  await screen.findByRole("heading", { level: 2, name: "Keeping the logs" });
  return { ...harness, backend };
}

describe("how long the journal keeps the logs", () => {
  it("says what is set, what the journal takes and what journald warned about, in its own words", { timeout: 20_000 }, async () => {
    await pageAt();
    expect(await screen.findByText("Entries are kept for 30 days, using at most 2.0 GB.")).toBeInTheDocument();
    expect(screen.getByText("Archived and active journals take up 1.2G in the file system.")).toBeInTheDocument();
    expect(screen.getByText("The journal is kept in memory: everything in it is lost at a reboot.")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says journald's defaults apply when nothing is set, and offers no way back", { timeout: 20_000 }, async () => {
    await pageAt(DEFAULTS);
    expect(await screen.findByText("Nothing is set: journald keeps entries by its own defaults.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to the defaults" })).toBeNull();
  });

  it("changes it from a drawer, writing what is filled in and showing the server's refusal beside its field", { timeout: 30_000 }, async () => {
    let answers = 0;
    const { user, backend } = await pageAt(KEPT, {
      "PUT /api/logs/retention": () => {
        answers += 1;
        return answers === 1 ? problem(422, "validation_error", "days out of range", { fields: { days: "Keep the logs for 1 to 3650 days." } }) : json(200, { ...KEPT, days: 14 });
      },
    });
    await user.click(await screen.findByRole("button", { name: "Change" }));
    const drawer = await screen.findByRole("dialog", { name: "Keep the logs" });
    expect(within(drawer).getByLabelText("Days to keep")).toHaveValue("30");
    expect(within(drawer).getByLabelText("Most disk to use (MB)")).toHaveValue("2048");
    await user.clear(within(drawer).getByLabelText("Days to keep"));
    await user.type(within(drawer).getByLabelText("Days to keep"), "99999");
    await user.click(within(drawer).getByRole("button", { name: "Keep the logs" }));
    expect(await within(drawer).findByText("Keep the logs for 1 to 3650 days.")).toBeInTheDocument();
    await user.clear(within(drawer).getByLabelText("Days to keep"));
    await user.type(within(drawer).getByLabelText("Days to keep"), "14");
    await user.click(within(drawer).getByRole("checkbox", { name: "Keep the journal across reboots" }));
    await user.click(within(drawer).getByRole("button", { name: "Keep the logs" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/logs/retention")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/logs/retention").at(-1)?.body).toEqual({ days: 14, max_use_mb: 2048, persistent: true });
  });

  it("goes back to journald's defaults after one question", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt(KEPT, { "DELETE /api/logs/retention": () => json(200, DEFAULTS) });
    await user.click(await screen.findByRole("button", { name: "Back to the defaults" }));
    const question = await screen.findByRole("alertdialog", { name: "Go back to journald's defaults?" });
    await user.click(within(question).getByRole("button", { name: "Back to the defaults" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/logs/retention")).toHaveLength(1);
    });
  });

  it("will not touch a drop-in that Noust did not write", { timeout: 20_000 }, async () => {
    await pageAt({ ...DEFAULTS, foreign: true });
    expect(await screen.findByText("A file of that name is in the way")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change" })).toBeDisabled();
  });
});

describe("what the retention form writes", () => {
  it("sends only what is filled in, and never a number that is not one", () => {
    expect(retentionBody({ days: "30", size: "", persistent: false })).toEqual({ days: 30, persistent: false });
    expect(retentionBody({ days: " 7 ", size: "512", persistent: true })).toEqual({ days: 7, max_use_mb: 512, persistent: true });
    expect(retentionBody({ days: "abc", size: "", persistent: false })).toEqual({ persistent: false });
  });
});
