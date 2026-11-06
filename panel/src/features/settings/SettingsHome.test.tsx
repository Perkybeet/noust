import { act, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { NODES, SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

const GROUPS = ["People and access", "Notices", "Connections", "Compliance and audit", "API", "Fleet", "Noust"];

const EVERYTHING = ["self", "apps.read", "accounts.read", "accounts.manage", "audit.read", "compliance.read", "settings.read", "fleet.read"];

function hub(extra: Record<string, () => Response> = {}, permissions: string[] = EVERYTHING) {
  return fakeBackend({
    ...signedInRoutes({ ...SESSION, permissions }),
    "GET /api/auth/accounts": () => json(200, { accounts: [{ username: "ana" }, { username: "bea" }, { username: "carlos" }], conflicts: [] }),
    "GET /api/roles": () => json(200, { roles: [{ name: "viewer" }, { name: "operator" }, { name: "owner" }, { name: "admin" }], permissions: {}, owners: 1, duties_apart: false }),
    ...extra,
  });
}

describe("Settings, the hub", () => {
  it("opens on every section as a door under the group that says what it is for, and passes axe", { timeout: 30_000 }, async () => {
    hub();
    const { container } = renderConsole("/settings");
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    // A server on its own has no fleet to set up: that group is for the console that manages others.
    for (const group of GROUPS.filter((title) => title !== "Fleet")) {
      expect(await screen.findByRole("heading", { level: 2, name: group })).toBeInTheDocument();
    }
    expect(screen.queryByRole("heading", { level: 2, name: "Fleet" })).toBeNull();
    // A door says where it goes and what is inside, so it is found by its purpose and not by its name.
    const channels = screen.getByRole("link", { name: /^Notification channels Where messages go, and how to silence them for a while/ });
    expect(channels).toHaveAttribute("href", "/settings/notifications");
    expect(screen.getByRole("link", { name: /^General Name, folders, web server and builds/ })).toHaveAttribute("href", "/settings/general");
    await expectNoAxeViolations(container);
  });

  it("says what it is for under its title, in the header, and not as a paragraph a gap away from it", { timeout: 30_000 }, async () => {
    hub();
    renderConsole("/settings");
    const title = await screen.findByRole("heading", { level: 1, name: "Settings" });
    const lead = await screen.findByText("Everything you can set up, by what it is for.");
    // The header of the page holds both, so the rhythm between them is the header's and not the 32px that separates a header from its content.
    expect(title.closest("header")?.contains(lead)).toBe(true);
  });

  it("says what is true now where the console already knows it: how many people, how many roles", { timeout: 30_000 }, async () => {
    hub();
    renderConsole("/settings");
    const people = await screen.findByRole("link", { name: /^Accounts Who has an account/ });
    expect(await within(people).findByText("3 people")).toBeInTheDocument();
    const roles = screen.getByRole("link", { name: /^Roles The roles that decide it/ });
    expect(await within(roles).findByText("4 roles")).toBeInTheDocument();
  });

  it("has no door for what a person may not see", { timeout: 30_000 }, async () => {
    hub({}, ["self", "apps.read"]);
    renderConsole("/settings");
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    expect(screen.queryByRole("link", { name: /^Accounts / })).toBeNull();
    expect(screen.queryByRole("link", { name: /^Audit log / })).toBeNull();
    expect(screen.getByRole("link", { name: /^Notification channels / })).toBeInTheDocument();
  });

  it("is the same on a fleet, with the fleet's settings as a door", { timeout: 30_000 }, async () => {
    hub({ "GET /api/nodes": () => json(200, { items: NODES }) });
    renderConsole("/settings");
    const door = await screen.findByRole("link", { name: /^Fleet settings What every server shares/ });
    expect(door).toHaveAttribute("href", "/settings/fleet");
  });

  it("speaks Spanish, with the group titles of the information architecture", { timeout: 30_000 }, async () => {
    hub();
    renderConsole("/settings");
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Personas y acceso" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Avisos" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Cumplimiento y auditoría" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Cuentas Quién tiene cuenta/ })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
