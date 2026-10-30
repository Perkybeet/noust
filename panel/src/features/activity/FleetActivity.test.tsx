import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { centralSession, fleetRoutes } from "../fleet/testFixtures";
import { ACTIVITY_ITEMS, AUDIT_REFUSAL, RAW_REFUSAL, REFUSED_NODES, envelope } from "../operation/testFixtures";

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

function routes(answer: (call: RecordedCall) => Response) {
  return { ...fleetRoutes(centralSession()), "GET /api/views/activity": answer };
}

async function fleetActivity(answer: (call: RecordedCall) => Response, path = "/fleet/activity") {
  const backend = fakeBackend(routes(answer));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: /^(Activity|Actividad)$/ });
  return { ...harness, backend };
}

describe("activity on every server", () => {
  it("is the page of Activity, with a title of its own and each event naming its server", async () => {
    await fleetActivity(() => json(200, envelope("activity", ACTIVITY_ITEMS)));
    const table = await screen.findByRole("region", { name: /Activity/ });
    await within(table).findByText("Application updated");
    // One h1: the fleet's header is not drawn over the area's own.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(within(table).getByRole("columnheader", { name: /Server/ })).toBeInTheDocument();
    // The view opens on the changes: web-2's update and the central's restart, not web-2's refused sign-in.
    expect(within(table).getAllByText("web-2")).toHaveLength(1);
    expect(within(table).getByText("web-01")).toBeInTheDocument();
    expect(within(table).queryByText("Sign-in")).not.toBeInTheDocument();
    // A person who acted through a central is named first, in the console's language.
    expect(within(table).getByText("joan (from the central web-01)")).toBeInTheDocument();
  });

  it("says in one place which servers did not let the operator in, with what is missing and no raw body", async () => {
    await fleetActivity(() => json(200, envelope("activity", ACTIVITY_ITEMS.slice(0, 2), { nodes: REFUSED_NODES, refusals: [AUDIT_REFUSAL], partial: true })));
    const notice = await screen.findByText("2 servers do not let you see their activity");
    const group = notice.closest("[data-tone]");
    if (!(group instanceof HTMLElement)) throw new Error("no notice around the title");
    // What is missing, in the console's words and with the fix; the servers are named.
    expect(within(group).getByText("Your role operator does not hold the permission audit.read on these servers")).toBeInTheDocument();
    expect(group).toHaveTextContent("Ask a security officer of each of them");
    expect(within(group).getByText("db-1")).toBeInTheDocument();
    expect(within(group).getByText("old-1")).toBeInTheDocument();
    // The body a node answered with is never shown to a person, nor said twice as "did not answer".
    expect(screen.queryByText(RAW_REFUSAL)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('"error":"permission_denied"');
    expect(screen.queryByText(/servers did not answer fully/)).not.toBeInTheDocument();
    // The rows of the servers that answered are all there.
    expect(await screen.findByText("Application updated")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says it in Spanish, the number of servers agreeing with the noun", async () => {
    await act(() => setLocale("es"));
    await fleetActivity(() => json(200, envelope("activity", ACTIVITY_ITEMS.slice(0, 1), { nodes: REFUSED_NODES, refusals: [AUDIT_REFUSAL], partial: true })), "/fleet/activity");
    expect(await screen.findByText("2 servidores no te dejan ver su actividad")).toBeInTheDocument();
    expect(screen.getByText("Tu rol operator no tiene el permiso audit.read en estos servidores")).toBeInTheDocument();
  });

  it("narrows to one server by asking the central for it, and says what one person did on all of them", async () => {
    const asked: string[][] = [];
    const { user } = await fleetActivity((call) => {
      asked.push(call.search.getAll("node"));
      const only = call.search.getAll("node");
      return json(200, envelope("activity", only.length === 0 ? ACTIVITY_ITEMS : ACTIVITY_ITEMS.filter((item) => only.includes(item.node === "web-01" ? "@central" : item.node))));
    });
    const table = await screen.findByRole("region", { name: /Activity/ });
    await within(table).findByText("Application updated");

    await user.click(screen.getByRole("combobox", { name: "Person" }));
    await user.click(await screen.findByRole("option", { name: "joan" }));
    await waitFor(() => {
      expect(within(table).queryByText("Application updated")).not.toBeInTheDocument();
    });
    expect(within(table).getByText("Application restarted")).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Servers" }));
    await user.click(await screen.findByRole("option", { name: "web-2" }));
    await waitFor(() => {
      expect(asked).toContainEqual(["web-2"]);
    });
  });

  it("draws no server column and no filter on a server with none beside it", async () => {
    await fleetActivity(() => json(200, envelope("activity", ACTIVITY_ITEMS.slice(0, 1), { fleet: false, servers: [] })));
    const table = await screen.findByRole("region", { name: /Activity/ });
    await within(table).findByText("Application updated");
    expect(within(table).queryByRole("columnheader", { name: /Server/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Servers" })).not.toBeInTheDocument();
  });
});
