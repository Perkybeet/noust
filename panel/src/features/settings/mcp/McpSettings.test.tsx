import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

function tool(name: string, title: string, group: string, permission: string, read_only: boolean, destructive = false) {
  return { name, title, description: title, group, permission, read_only, destructive };
}

const TOOLS = [
  tool("list_applications", "List applications", "inspect", "apps.read", true),
  tool("deploy_application", "Update an application", "deploy", "apps.deploy", false),
  tool("rollback_application", "Roll an application back", "deploy", "apps.deploy", false, true),
  tool("restart_application", "Restart an application", "control", "apps.operate", false),
];

interface Status {
  enabled: boolean;
  groups: string[];
}

function mcpBackend(initial: Partial<Status> = {}, extra: Record<string, RouteHandler> = {}) {
  const state: Status = { enabled: false, groups: ["inspect", "deploy", "control"], ...initial };
  const status = () => ({
    enabled: state.enabled,
    endpoint: "https://noust.acme.test/api/mcp",
    groups: state.groups,
    all_groups: ["inspect", "deploy", "control"],
    protocol_versions: ["2025-06-18", "2025-03-26"],
    prompts: ["troubleshoot_application", "explain_failed_deploy"],
    resources: ["noust://applications"],
    tools: TOOLS.filter((item) => state.groups.includes(item.group)),
  });
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/mcp/status": () => json(200, status()),
    "PUT /api/mcp/settings": (call: RecordedCall) => {
      const body = call.body as Partial<Status>;
      if (body.enabled !== undefined) state.enabled = body.enabled;
      if (body.groups !== undefined) state.groups = body.groups;
      return json(200, status());
    },
    ...extra,
  });
  return { backend, state };
}

async function pageAt(initial: Partial<Status> = {}, extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const world = mcpBackend(initial, extra);
  const harness = renderConsole("/settings/mcp");
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  return { ...harness, ...world };
}

describe("Settings > MCP", () => {
  it("says the endpoint is off, gives its address and lists the tools by what they do, and passes axe", { timeout: 30_000 }, async () => {
    await pageAt();
    expect(await screen.findByText("Off", { selector: "[data-state]" })).toHaveAttribute("data-state", "stopped");
    expect(screen.getByRole("switch", { name: "Answer MCP clients" })).not.toBeChecked();
    expect(screen.getByText("https://noust.acme.test/api/mcp")).toBeInTheDocument();
    expect(screen.getByText("2025-06-18, 2025-03-26")).toBeInTheDocument();
    const table = await screen.findByRole("region", { name: "MCP tools" });
    const rollback = within(table).getByText("rollback_application").closest("tr");
    if (!rollback) throw new Error("no row for the rollback tool");
    // A destructive tool is one the client must say twice.
    expect(within(rollback).getByText("Asks twice")).toBeInTheDocument();
    const list = within(table).getByText("list_applications").closest("tr");
    if (!list) throw new Error("no row for the listing tool");
    expect(within(list).getByText("Reads")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is in the settings navigation, as the server's own section", async () => {
    await pageAt();
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "MCP" })).toHaveAttribute("aria-current", "page");
  });

  it("turns the endpoint on and then off", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt();
    await user.click(await screen.findByRole("switch", { name: "Answer MCP clients" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/mcp/settings")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/mcp/settings")[0]?.body).toEqual({ enabled: true });
    expect(await screen.findByText("On", { selector: "[data-state]" })).toHaveAttribute("data-state", "running");
    await user.click(screen.getByRole("switch", { name: "Answer MCP clients" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/mcp/settings")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/mcp/settings")[1]?.body).toEqual({ enabled: false });
  });

  it("turns a group of tools off at once, keeping the order of the others", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({ enabled: true });
    await user.click(await screen.findByRole("switch", { name: "Deploy" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/mcp/settings")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/mcp/settings")[0]?.body).toEqual({ groups: ["inspect", "control"] });
    // Its tools are no longer offered.
    await waitFor(() => {
      expect(screen.queryByText("deploy_application")).not.toBeInTheDocument();
    });
  });

  it("shows the server's refusal as it wrote it", { timeout: 30_000 }, async () => {
    const { user } = await pageAt(
      {},
      { "PUT /api/mcp/settings": () => problem(403, "forbidden", "Only a security officer can turn MCP on.", { hint: "Ask an account with the security role." }) },
    );
    await user.click(await screen.findByRole("switch", { name: "Answer MCP clients" }));
    expect(await screen.findByText("Only a security officer can turn MCP on.")).toBeInTheDocument();
    expect(screen.getByText("Ask an account with the security role.")).toBeInTheDocument();
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    await pageAt();
    await act(() => setLocale("es"));
    expect(await screen.findByText("Desactivado", { selector: "[data-state]" })).toHaveAttribute("data-state", "stopped");
    expect(screen.getByRole("region", { name: "Herramientas MCP" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
