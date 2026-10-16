/**
 * The command palette does things to an application, not only opens it (checklist M-25): the verb
 * is the application's own button, so it asks what the button asks, is refused what the button is
 * refused, and asks to confirm it's you when the server says so.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../test/fakes";
import type { RouteHandler } from "../test/fakes";

const JOB = { id: "job-1", kind: "update", status: "queued", domain: "shop.example.com" };

function routes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
    "GET /api/databases/databases": () => json(200, { databases: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "POST /api/apps/shop.example.com/restart": () => json(200, { success: true, message: "Restarted" }),
    "POST /api/apps/shop.example.com/stop": () => json(200, { success: true, message: "Stopped" }),
    "POST /api/apps/admin.example.com/start": () => json(200, { success: true, message: "Started" }),
    "POST /api/jobs/update": () => json(202, { job: JOB }),
    ...extra,
  };
}

async function open(extra: Record<string, RouteHandler> = {}, path = "/") {
  const backend = fakeBackend(routes(extra));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return { ...harness, backend };
}

async function ask(user: ReturnType<typeof renderConsole>["user"], text: string) {
  await user.keyboard("{Control>}k{/Control}");
  const input = await screen.findByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
  await user.type(input, text);
  return input;
}

describe("the palette's verbs on an application", () => {
  it("restarts an application with the page's own call, without leaving where the operator is", async () => {
    const { user, backend, location } = await open();
    await ask(user, "restart shop");
    await user.click(await screen.findByRole("option", { name: "Restart shop.example.com" }));

    expect(await screen.findByText("Restarted shop.example.com")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/shop.example.com/restart")).toHaveLength(1);
    expect(location().pathname).toBe("/");
  });

  it("finds the verb by what it says, grouped as operations, and not when only the name is typed", async () => {
    const { user } = await open();
    await ask(user, "shop.example.com");
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).queryByRole("option", { name: /^Restart/ })).not.toBeInTheDocument();

    await user.clear(screen.getByRole("combobox"));
    await user.type(screen.getByRole("combobox"), "restart");
    const group = within(await screen.findByRole("listbox")).getByRole("group", { name: "Operations" });
    expect(within(group).getAllByRole("option").map((option) => option.textContent)).toEqual(["Restart shop.example.com", "Restart admin.example.com"]);
    await expectNoAxeViolations(document.body);
  });

  it("asks the page's question before stopping, and stops only when it is answered", async () => {
    const { user, backend } = await open();
    await ask(user, "stop shop");
    await user.click(await screen.findByRole("option", { name: "Stop shop.example.com" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Stop shop.example.com?" });
    expect(backend.callsTo("POST /api/apps/shop.example.com/stop")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Stop application" }));

    expect(await screen.findByText("Stopped shop.example.com")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/shop.example.com/stop")).toHaveLength(1);
  });

  it("stops nothing when the question is cancelled", async () => {
    const { user, backend } = await open();
    await ask(user, "stop shop");
    await user.click(await screen.findByRole("option", { name: "Stop shop.example.com" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop shop.example.com?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(backend.callsTo("POST /api/apps/shop.example.com/stop")).toHaveLength(0);
  });

  it("starts an application that is not running, and offers Start instead of Stop for it", async () => {
    const { user, backend } = await open();
    await ask(user, "start");
    expect(screen.queryByRole("option", { name: "Start shop.example.com" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("option", { name: "Start admin.example.com" }));
    expect(await screen.findByText("Started admin.example.com")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/admin.example.com/start")).toHaveLength(1);
  });

  it("queues an update as the button does", async () => {
    const { user, backend } = await open();
    await ask(user, "update shop");
    await user.click(await screen.findByRole("option", { name: "Update shop.example.com" }));
    expect(await screen.findByText("Update of shop.example.com queued")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/jobs/update")[0]?.body).toEqual({ domain: "shop.example.com", force: false });
  });

  it("asks whether to rebuild when there is nothing new, as the button does", async () => {
    const { user, backend } = await open({
      "POST /api/jobs/update": (call) => {
        const forced = (call.body as { force?: boolean } | undefined)?.force === true;
        return forced ? json(202, { job: JOB }) : problem(409, "nothing_new", "The branch has nothing the live build lacks.");
      },
    });
    await ask(user, "update shop");
    await user.click(await screen.findByRole("option", { name: "Update shop.example.com" }));
    const dialog = await screen.findByRole("dialog", { name: /shop\.example\.com/ });
    await user.click(within(dialog).getByRole("button", { name: "Rebuild anyway" }));
    await waitFor(() => expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(2));
    expect(backend.callsTo("POST /api/jobs/update")[1]?.body).toEqual({ domain: "shop.example.com", force: true });
  });

  it("opens the choice of version for a rollback, the dialog the Roll back button opens", async () => {
    const { user } = await open({
      "GET /api/apps/shop.example.com/rollback-points": () => json(200, { items: [{ id: "backup-1", created_at: "2026-10-01T10:00:00+00:00", description: "before deploy", size_bytes: 1024 }], total: 1 }),
    });
    await ask(user, "roll back shop");
    await user.click(await screen.findByRole("option", { name: "Roll back shop.example.com" }));
    const dialog = await screen.findByRole("dialog", { name: "Roll back shop.example.com" });
    expect(await within(dialog).findByRole("button", { name: "Go back to backup-1" })).toBeInTheDocument();
  });

  it("confirms it's you when the server asks, and retries the restart by itself", async () => {
    let elevated = false;
    const { user, backend } = await open({
      "POST /api/apps/shop.example.com/restart": () => (elevated ? json(200, { success: true }) : problem(403, "elevation_required", "Confirm it's you to continue.")),
      "POST /api/auth/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
      },
    });
    await ask(user, "restart shop");
    await user.click(await screen.findByRole("option", { name: "Restart shop.example.com" }));

    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("Restarted shop.example.com")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/shop.example.com/restart")).toHaveLength(2);
  });

  it("offers a viewer no verb at all: what the buttons would be refused is not in the palette", async () => {
    const viewer = { ...SESSION, permissions: SESSION.permissions.filter((permission) => permission !== "apps.operate" && permission !== "apps.deploy") };
    const { user } = await open({ ...signedInRoutes(viewer), "GET /api/auth/session": () => json(200, viewer) });
    await ask(user, "restart");
    expect(await screen.findByText(/Nothing matches/)).toBeInTheDocument();
  });

  it("offers the verbs of the application on screen before anything is typed", async () => {
    const { user } = await open({}, "/apps/shop.example.com");
    await screen.findByRole("heading", { level: 1, name: /shop\.example\.com/ });
    await user.keyboard("{Control>}k{/Control}");
    const listbox = await screen.findByRole("listbox");
    await within(listbox).findByRole("option", { name: "Restart shop.example.com" });
    expect(within(listbox).queryByRole("option", { name: "Restart admin.example.com" })).not.toBeInTheDocument();
  });
});
