import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import { DB1, WEB2, centralSession, fleetRoutes } from "../../fleet/testFixtures";

const SILENT = { failures: 2, since: "2026-09-29T07:55:00+00:00", announced: false, paused: false, last_error: "ssh: connect to host web2.example.com port 22: Connection timed out" };
const DOWN = { ...SILENT, failures: 5, announced: true };
const PAUSED = { failures: 0, since: null, announced: false, paused: true, paused_reason: "Kernel update", paused_by: "yago", paused_at: "2026-09-29T08:00:00+00:00" };

function serversWith(monitoring: Record<string, unknown> | null, other: Record<string, unknown> | null = null) {
  const nodes: unknown[] = [
    { ...WEB2, monitoring },
    { ...DB1, monitoring: other },
  ];
  return fakeBackend({
    ...fleetRoutes(centralSession({}, { totp_enabled: true }), nodes),
    "PUT /api/nodes/web-2/monitoring": (call) => {
      const body = call.body as { paused: boolean; reason: string | null };
      nodes[0] = { ...WEB2, monitoring: body.paused ? { ...PAUSED, paused_reason: body.reason } : null };
      return json(200, nodes[0]);
    },
  });
}

describe("Fleet > Servers, monitoring", () => {
  it("says nothing under a server that answers, and what is wrong under one that does not", async () => {
    serversWith(SILENT, DOWN);
    renderConsole("/fleet/servers");
    expect(await screen.findByText(/^Not answering since/)).toBeInTheDocument();
    expect(screen.getByText(/^Declared down, silent since/)).toBeInTheDocument();
  });

  it("shows a paused server as paused, with the reason", { timeout: 20_000 }, async () => {
    serversWith(PAUSED);
    const { container } = renderConsole("/fleet/servers");
    expect(await screen.findByText("Monitoring paused: Kernel update")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });

  it("pauses a server with a reason and resumes it", { timeout: 20_000 }, async () => {
    const backend = serversWith(null);
    const { user } = renderConsole("/fleet/servers");
    await user.click(await screen.findByRole("button", { name: "Actions for web-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Pause monitoring" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Pause the monitoring of web-2?" });
    await user.type(within(dialog).getByLabelText(/^Why/), "Kernel update");
    await user.click(within(dialog).getByRole("button", { name: "Pause monitoring" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/nodes/web-2/monitoring")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/nodes/web-2/monitoring")[0]?.body).toEqual({ paused: true, reason: "Kernel update" });
    expect(await screen.findByText("Monitoring paused: Kernel update")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Actions for web-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Resume monitoring" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/nodes/web-2/monitoring")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/nodes/web-2/monitoring")[1]?.body).toEqual({ paused: false, reason: null });
  });
});
