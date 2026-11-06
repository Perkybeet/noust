import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SERVICES, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { onDesktop, serverRoutes } from "../server/testing";
import { changedLines } from "../../lib/lineDiff";

beforeEach(onDesktop);

const NAME = SERVICES[0]?.name ?? "";
const UNIT = `[Unit]\nDescription=worker\n\n[Service]\nExecStart=/usr/bin/node worker.js\n`;

function servicePage(path: string, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...serverRoutes(),
    [`GET /api/services/${NAME}/config`]: () => json(200, { config: UNIT, path: `/etc/systemd/system/${NAME}.service`, service: NAME }),
    ...extra,
  });
  return { ...renderConsole(path), backend };
}

describe("a service's page", () => {
  it("is a page of its own under the server, with Overview and Logs as tabs", async () => {
    servicePage(`/server/services/${NAME}`);
    expect(await screen.findByRole("heading", { level: 1, name: NAME })).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["/server", "/server/services"]);
    // The tabs come once it is known whether the unit is an application's (its banner sits above them).
    const tabs = await screen.findByRole("navigation", { name: "Service sections" });
    expect(within(tabs).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview", "Logs"]);
    expect(await screen.findByText("Process ID")).toBeInTheDocument();
    // Starting at boot is an on/off at a glance, not a bare "Yes" (item 56).
    const boot = screen.getByText("Starts at boot").closest("div");
    if (boot === null) throw new Error("no boot row");
    expect(within(boot).getByText(/^(Yes|No)$/)).toHaveAttribute("data-state", expect.stringMatching(/^(running|stopped)$/));
    // Restart is the one primary action; the unit file is its own page.
    expect(screen.getByRole("button", { name: "Restart" })).toHaveAttribute("data-variant", "primary");
    expect(screen.getByRole("link", { name: "Configuration" })).toHaveAttribute("href", `/server/services/${NAME}/unit`);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("holds the sections of its column until the service is known: its state is a line the head gains then, and a list drawn before it would move", async () => {
    let answer: () => void = () => undefined;
    const record = SERVICES.find((service) => service.name === NAME);
    servicePage(`/server/services/${NAME}`, {
      [`GET /api/services/${NAME}`]: () => new Promise<Response>((resolve) => (answer = () => resolve(json(200, record)))),
    });
    await screen.findByRole("heading", { level: 1, name: NAME });

    expect(await screen.findByText("Loading Service sections")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Service sections" })).toBeNull();

    await act(async () => {
      answer();
    });
    const sections = await screen.findByRole("navigation", { name: "Service sections" });
    expect(within(sections).getAllByRole("link").length).toBeGreaterThan(0);
    expect(screen.queryByText("Loading Service sections")).toBeNull();
  });

  it("lists the sections of its column all the same when the service cannot be read: each page says why", async () => {
    servicePage(`/server/services/${NAME}`, { [`GET /api/services/${NAME}`]: () => problem(404, "not_found", "No such service") });
    await screen.findByRole("heading", { level: 1, name: NAME });
    expect(await screen.findByRole("navigation", { name: "Service sections" }, { timeout: 8000 })).toBeInTheDocument();
  });

  it("keeps the row of the name in the head of its column as tall with the switch to the other services as without it", async () => {
    servicePage(`/server/services/${NAME}`);
    await screen.findByRole("heading", { level: 1, name: NAME });
    const column = await screen.findByRole("complementary", { name: "Service sections" });
    // The row is the switch's height (28px) from the first frame, so the switch arriving with the list of services moves nothing.
    expect(within(column).getByText(NAME).parentElement).toHaveClass("min-h-7");
  });

  it("keeps the old address working", async () => {
    const { location } = servicePage(`/services/${NAME}`);
    expect(await screen.findByRole("heading", { level: 1, name: NAME })).toBeInTheDocument();
    expect(location().pathname).toBe(`/server/services/${NAME}`);
  });

  it("deletes behind More actions, after the name is typed", async () => {
    const { user, backend, location } = servicePage(`/server/services/${NAME}`, {
      [`DELETE /api/services/${NAME}`]: () => json(200, { success: true, message: "Deleted" }),
    });
    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete service" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Delete ${NAME}` });
    await user.type(within(dialog).getByRole("textbox"), NAME);
    await user.click(within(dialog).getByRole("button", { name: "Delete service" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/services/${NAME}`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(location().pathname).toBe("/server/services");
    });
  });

  it("sends an application's unit to the application, and deletes nothing there", async () => {
    const { user } = servicePage(`/server/services/${NAME}`, {
      "GET /api/apps": () =>
        json(200, { total: 1, apps: [{ domain: "shop.example.com", name: "shop", status: "running", active: true, enabled: true, layout: "inplace", keep_releases: 5, webhook_enabled: false, zero_downtime: false, unit: `${NAME}.service` }] }),
    });
    expect(await screen.findByText("This service runs shop.example.com")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the application" })).toHaveAttribute("href", "/apps/shop.example.com");
    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await screen.findByRole("menuitem", { name: "Open in Server logs" });
    expect(screen.queryByRole("menuitem", { name: "Delete service" })).not.toBeInTheDocument();
  });

  it("reads a unit Noust did not create, and offers nothing to change it", async () => {
    servicePage("/server/services/postgresql.service", {
      "GET /api/services/postgresql.service": () => problem(404, "not_found", "Service postgresql.service is not managed by Noust"),
      "GET /api/services": () =>
        json(200, {
          services: [{ name: "postgresql.service", description: null, active: true, enabled: true, status: "running", pid: 908, uptime: null, memory: null, managed: false }],
          total: 1,
        }),
    });
    expect(await screen.findByText("Noust did not create this service")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart" })).not.toBeInTheDocument();
  });
});

const FOREIGN: Record<string, RouteHandler> = {
  "GET /api/services/postgresql.service": () => problem(404, "not_found", "Service postgresql.service is not managed by Noust"),
  "GET /api/services": () =>
    json(200, {
      services: [{ name: "postgresql.service", description: null, active: true, enabled: true, status: "running", pid: 908, uptime: null, memory: null, managed: false }],
      total: 1,
    }),
};

describe("the journal and the configuration of a unit", () => {
  it("is not offered for a unit Noust did not create, which the server refuses to follow", async () => {
    servicePage("/server/services/postgresql.service", FOREIGN);
    await screen.findByText("Noust did not create this service");
    const tabs = await screen.findByRole("navigation", { name: "Service sections" });
    expect(within(tabs).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview"]);
  });

  it("says where the journal is when somebody arrives at the address of the one that is not there, and opens no socket for it", async () => {
    servicePage("/server/services/postgresql.service/logs", FOREIGN);
    expect(await screen.findByText("The journal of this unit is the system's")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in Server logs" })).toBeInTheDocument();
  });

  it("is not a link of the header for a role that cannot read a unit file, which can hold secrets", async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "secrets.reveal") };
    servicePage(`/server/services/${NAME}`, { ...signedInRoutes(reader) });
    await screen.findByRole("heading", { level: 1, name: NAME });
    expect(await screen.findByRole("button", { name: "Restart" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Configuration" })).not.toBeInTheDocument();
  });

  it("says what a refusal to read the file means, in place of the server's bare 403", async () => {
    servicePage(`/server/services/${NAME}/unit`, {
      [`GET /api/services/${NAME}/config`]: () => problem(403, "permission_denied", "This needs the 'secrets.reveal' permission"),
    });
    expect(await screen.findByText("Opening this file takes more access")).toBeInTheDocument();
    expect(screen.getByText("secrets.reveal")).toBeInTheDocument();
    expect(screen.queryByText(/This needs the 'secrets.reveal' permission/)).not.toBeInTheDocument();
  });
});

describe("a service's unit file", () => {
  it("counts the lines that changed as a site's configuration does: a block inserted counts its own lines", () => {
    expect(changedLines("a\nb\nc", "a\nb\nc")).toBe(0);
    expect(changedLines("a\nb\nc", "a\nB\nc\nd")).toBe(2);
    // One line added at the top of a unit is one change, not every line after it.
    expect(changedLines("[Unit]\nDescription=x\n[Service]\nExecStart=/bin/x", "# note\n[Unit]\nDescription=x\n[Service]\nExecStart=/bin/x")).toBe(1);
  });

  it("is tested before it is saved, and says what systemd-analyze said when it refuses", async () => {
    const { user, backend } = servicePage(`/server/services/${NAME}/unit`, {
      "POST /api/services/verify": () => json(200, { success: false, output: "worker.service:5: Unknown key name 'ExecStrat'" }),
    });
    const editor = await screen.findByRole("textbox", { name: `Configuration of ${NAME}` });
    expect(screen.getByText(`/etc/systemd/system/${NAME}.service`)).toBeInTheDocument();
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
    await user.type(editor, "ExecStrat=x");
    await user.click(screen.getByRole("button", { name: "Test and save" }));
    expect(await screen.findByText("worker.service:5: Unknown key name 'ExecStrat'")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/services/${NAME}/config`)).toHaveLength(0);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("saves what passes, and offers the restart that applies it", async () => {
    const { user, backend } = servicePage(`/server/services/${NAME}/unit`, {
      "POST /api/services/verify": () => json(200, { success: true, output: "" }),
      [`PUT /api/services/${NAME}/config`]: () => json(200, { success: true, message: "Saved" }),
    });
    const editor = await screen.findByRole("textbox", { name: `Configuration of ${NAME}` });
    await user.type(editor, "#");
    await user.click(screen.getByRole("button", { name: "Test and save" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/services/${NAME}/config`)).toHaveLength(1);
    });
    expect(await screen.findByRole("button", { name: "Restart now" })).toBeInTheDocument();
  });
});
