/**
 * Changing where a rule of the firewall admits from, in one step: the menu of a rule that can move says so, the dialog
 * asks for the one origin and says it is one change that waits once, the request is the one-step route and not a delete
 * and a create, and a server that does not have the route does not offer it.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, onNode, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { WEB2, centralSession, fleetRoutes } from "../fleet/testFixtures";
import { FIREWALL, onDesktop, serverRoutes } from "../server/testing";
import type { Firewall } from "../server/queries";

beforeEach(onDesktop);

const ROUTE = "/api/server/security/firewall/rules/{rule_id}/source";

const RULES: Firewall["firewall"]["rules"] = [
  { id: "ssh", backend: "ufw", action: "allow", ports: [[22, 22]], proto: "tcp", source: "any", spec: "22/tcp ALLOW IN Anywhere", comment: "", noust: false, known: true, service: null, source_changeable: true },
  { id: "db", backend: "ufw", action: "allow", ports: [[5432, 5432]], proto: "tcp", source: "10.0.0.0/24", spec: "5432/tcp ALLOW IN 10.0.0.0/24", comment: "", noust: false, known: true, service: null, source_changeable: true },
  { id: "web", backend: "ufw", action: "allow", ports: [[80, 80], [443, 443]], proto: "tcp", source: "any", spec: "80,443/tcp ALLOW IN Anywhere", comment: "", noust: false, known: true, service: null, source_changeable: false },
];

const FIREWALL_WITH_RULES: Firewall = {
  ...FIREWALL,
  firewall: { ...FIREWALL.firewall, rules: RULES },
  protected_ports: [{ port: 22, reason: "SSH" }],
};

const queued = () => json(202, { job_id: "j1", message: "queued", status: "pending" });

function firewall(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...serverRoutes(),
    "GET /api/server/security/firewall": () => json(200, FIREWALL_WITH_RULES),
    [`POST ${ROUTE}`]: queued,
    ...extra,
  });
  return { ...renderConsole("/network/firewall"), backend };
}

async function menuOf(user: ReturnType<typeof firewall>["user"], name: string): Promise<void> {
  await user.click(await screen.findByRole("button", { name: `Actions for ${name}` }));
}

describe("the menu of a rule", () => {
  it("offers to change where it admits from for a rule that can move, and not for one that cannot", async () => {
    const { user } = firewall();
    await screen.findByRole("table", { name: "Firewall rules" });

    await menuOf(user, "5432/tcp");
    expect(await screen.findByRole("menuitem", { name: "Change where it admits from" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Close the port" })).toBeInTheDocument();

    // A rule of two ports is closed or made again, not moved: it has a menu for closing only.
    expect(screen.queryByRole("button", { name: "Actions for 80:80, 443/tcp" })).toBeNull();
    await menuOf(user, "80, 443/tcp");
    expect(screen.queryByRole("menuitem", { name: "Change where it admits from" })).toBeNull();
  });

  it("keeps the rule that opens SSH from being closed here and still lets its origin be narrowed", async () => {
    const { user } = firewall();
    await screen.findByRole("table", { name: "Firewall rules" });

    await menuOf(user, "22/tcp");

    expect(await screen.findByRole("menuitem", { name: "Change where it admits from" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Close the port" })).toBeNull();
  });
});

describe("the dialog", () => {
  it("is one change that waits once, asks for the origin and sends it to the one-step route", async () => {
    const { user, backend } = firewall();
    await screen.findByRole("table", { name: "Firewall rules" });
    await menuOf(user, "5432/tcp");
    await user.click(await screen.findByRole("menuitem", { name: "Change where it admits from" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Change where 5432/tcp admits from" });
    expect(within(dialog).getByText(/as one change that you confirm once/)).toBeInTheDocument();
    expect(within(dialog).getByText("10.0.0.0/24")).toBeInTheDocument();
    expect(within(dialog).getByText(/undoes itself unless/i)).toBeInTheDocument();
    const field = within(dialog).getByRole("textbox", { name: "Address or network" });
    await user.clear(field);
    await user.type(field, "203.0.113.7");
    await user.click(within(dialog).getByRole("button", { name: "Change the origin" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROUTE.replace("{rule_id}", "db")}`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST ${ROUTE.replace("{rule_id}", "db")}`)[0]?.body).toEqual({ source: "203.0.113.7" });
    expect(backend.callsTo("DELETE /api/server/security/firewall/rules/db")).toHaveLength(0);
    expect(backend.callsTo("POST /api/server/security/firewall/rules")).toHaveLength(0);
  });

  it("can open a rule to anyone again", async () => {
    const { user, backend } = firewall();
    await screen.findByRole("table", { name: "Firewall rules" });
    await menuOf(user, "5432/tcp");
    await user.click(await screen.findByRole("menuitem", { name: "Change where it admits from" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Change where 5432/tcp admits from" });

    await user.click(within(dialog).getByRole("radio", { name: "Anyone" }));
    await user.click(within(dialog).getByRole("button", { name: "Change the origin" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROUTE.replace("{rule_id}", "db")}`)[0]?.body).toEqual({ source: "any" });
    });
  });

  it("refuses the origin the rule already has and what is not an address, in the field", async () => {
    const { user, backend } = firewall();
    await screen.findByRole("table", { name: "Firewall rules" });
    await menuOf(user, "5432/tcp");
    await user.click(await screen.findByRole("menuitem", { name: "Change where it admits from" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Change where 5432/tcp admits from" });
    const field = within(dialog).getByRole("textbox", { name: "Address or network" });

    await user.type(field, "{Backspace}");
    await user.type(field, "4");
    expect(await within(dialog).findByText("The rule already admits from there.")).toBeInTheDocument();
    await user.clear(field);
    await user.type(field, "office");
    expect(await within(dialog).findByText("That is not an address or a network.")).toBeInTheDocument();
    await user.clear(field);
    await user.type(field, "203.0.113.7, 198.51.100.1");
    expect(await within(dialog).findByText(/A rule has one origin/)).toBeInTheDocument();

    expect(within(dialog).getByRole("button", { name: "Change the origin" })).toBeDisabled();
    expect(backend.callsTo(`POST ${ROUTE.replace("{rule_id}", "db")}`)).toHaveLength(0);
  });

  it("says before it is asked that the rule keeps the server reachable", async () => {
    const { user } = firewall();
    await screen.findByRole("table", { name: "Firewall rules" });
    await menuOf(user, "22/tcp");
    await user.click(await screen.findByRole("menuitem", { name: "Change where it admits from" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Change where 22/tcp admits from" });
    expect(within(dialog).getByText("This rule keeps the server reachable")).toBeInTheDocument();
    // A rule for everyone has no origin to keep: only the one to give it.
    expect(within(dialog).getByText("Anywhere")).toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: "Anyone" })).toBeNull();
    await expectNoAxeViolations(dialog);
  });

  it("is in Spanish", async () => {
    const { user } = firewall();
    await act(() => setLocale("es"));
    await screen.findByRole("table", { name: "Reglas del cortafuegos" });
    await user.click(await screen.findByRole("button", { name: "Acciones de 5432/tcp" }));
    await user.click(await screen.findByRole("menuitem", { name: "Cambiar desde dónde admite" }));

    expect(await screen.findByRole("alertdialog", { name: "Cambiar desde dónde admite 5432/tcp" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});

describe("on another server", () => {
  function onWeb2(paths: readonly string[]) {
    const backend = fakeBackend({
      ...fleetRoutes(centralSession(), [WEB2]),
      ...onNode("web-2", {
        ...serverRoutes(),
        "GET /api/openapi.json": () => json(200, { paths: Object.fromEntries(["/api/server/summary", ...paths].map((entry) => [entry, { get: {}, post: {} }])) }),
        "GET /api/server/security/firewall": () => json(200, FIREWALL_WITH_RULES),
      }),
    });
    return { ...renderConsole("/n/web-2/network/firewall"), backend };
  }

  it("offers it when the server's own API has the route", async () => {
    const { user } = onWeb2([ROUTE]);
    await screen.findByRole("table", { name: "Firewall rules" });

    await menuOf(user, "5432/tcp");

    expect(await screen.findByRole("menuitem", { name: "Change where it admits from" })).toBeInTheDocument();
  });

  it("does not offer it when the server runs a Noust that has no such route, though the rule says nothing against it", async () => {
    const { user } = onWeb2([]);
    await screen.findByRole("table", { name: "Firewall rules" });

    await menuOf(user, "5432/tcp");

    expect(await screen.findByRole("menuitem", { name: "Close the port" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Change where it admits from" })).toBeNull();
  });
});
