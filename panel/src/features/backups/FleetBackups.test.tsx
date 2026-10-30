import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { centralSession, fleetRoutes } from "../fleet/testFixtures";
import { AUDIT_REFUSAL, BACKUPS_ITEMS, RAW_REFUSAL, REFUSED_NODES, envelope } from "../operation/testFixtures";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
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

async function backupsAt(answer: (call: RecordedCall) => Response, path = "/fleet/backups") {
  const backend = fakeBackend({ ...fleetRoutes(centralSession()), "GET /api/views/backups": answer });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: /^(Backups|Copias de seguridad)$/ });
  return { ...harness, backend };
}

describe("backups on every server", () => {
  it("is the page of Backups: the gaps first, each application naming its server and opening its backups there", async () => {
    await backupsAt(() => json(200, envelope("backups", BACKUPS_ITEMS)));
    const table = await screen.findByRole("region", { name: "Backups of every application" });
    const rows = await within(table).findAllByRole("row");
    // The header, then what the central gave: the application with nothing backed up first.
    expect(rows[1]).toHaveTextContent("api.example.com");
    expect(rows[1]).toHaveTextContent("No backup");
    expect(rows[1]).toHaveTextContent("web-2");
    expect(rows[2]).toHaveTextContent("shop.example.com");
    expect(rows[2]).toHaveTextContent("Backed up");
    expect(within(table).getByRole("link", { name: "api.example.com" })).toHaveAttribute("href", "/n/web-2/backups?domain=api.example.com");
    // One h1, the area's own; the fleet's header is not drawn over it, and the count has no middle dot.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("2 applications")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(" · ");
    // What is done to all of them is one action in the header, and the primary one is the usual.
    expect(screen.getByRole("button", { name: "Back up now" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verify backups" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("narrows by state and to one server by asking the central for it", async () => {
    const asked: string[][] = [];
    const { user } = await backupsAt((call) => {
      asked.push(call.search.getAll("node"));
      return json(200, envelope("backups", BACKUPS_ITEMS));
    });
    await screen.findByRole("link", { name: "api.example.com" });
    await user.click(screen.getByRole("combobox", { name: "State" }));
    await user.click(await screen.findByRole("option", { name: "Backed up" }));
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "api.example.com" })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "shop.example.com" })).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Servers" }));
    await user.click(await screen.findByRole("option", { name: "web-2" }));
    await waitFor(() => {
      expect(asked).toContainEqual(["web-2"]);
    });
  });

  it("says once which servers do not let the operator read their backups, never as the body they answered", async () => {
    await backupsAt(() => json(200, envelope("backups", BACKUPS_ITEMS.slice(1), { nodes: REFUSED_NODES, partial: true, refusals: [{ ...AUDIT_REFUSAL, resource: "backups", permission: "backups.read" }] })));
    expect(await screen.findByText("2 servers do not let you see their backups")).toBeInTheDocument();
    expect(screen.queryByText(RAW_REFUSAL)).not.toBeInTheDocument();
    expect(screen.getByText("shop.example.com")).toBeInTheDocument();
  });

  it("reads in Spanish", async () => {
    await act(() => setLocale("es"));
    await backupsAt(() => json(200, envelope("backups", BACKUPS_ITEMS)));
    expect(await screen.findByText("2 aplicaciones")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hacer copia ahora" })).toBeInTheDocument();
  });
});
