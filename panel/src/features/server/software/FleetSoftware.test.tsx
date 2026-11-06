import { screen, within } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import { OUTCOMES, fleetRoutes } from "../../fleet/testFixtures";
import { onDesktop } from "../testing";

beforeEach(onDesktop);

const NOW = new Date().toISOString();

function row(node: string, fields: Record<string, unknown>) {
  const local = node === "web-01";
  return { node, local, page: "/server/software", href: local ? "/server/software" : `/n/${node}/server/software`, family: "apt", managed: true, reason: null, ...fields };
}

/** What each server says of its software: web-01 and web-2 answer, db-1 from its last good answer, old-1 cannot (it runs a Noust of before 4.2). */
const SOFTWARE_VIEW = {
  resource: "software",
  generated_at: NOW,
  partial: true,
  nodes: OUTCOMES,
  items: [
    row("web-01", { installed: 20, total: 25, by_noust: 14 }),
    row("web-2", { installed: 16, total: 25, by_noust: 12 }),
    row("db-1", { installed: 12, total: 25, by_noust: 8 }),
  ],
};

function fleet() {
  const backend = fakeBackend({ ...fleetRoutes(), "GET /api/fleet/software": () => json(200, SOFTWARE_VIEW) });
  return { ...renderConsole("/fleet/software"), backend };
}

describe("the software of the fleet", () => {
  it("says what is installed on each server, what is not, and what Noust did not install", async () => {
    fleet();
    const table = await screen.findByRole("table", { name: "Software on each server" });
    const web = within(await within(table).findByRole("row", { name: /web-01/ }));
    expect(web.getByText("20")).toBeInTheDocument();
    expect(web.getByText("5")).toBeInTheDocument();
    // 20 installed, 14 of them by Noust: the other 6 were there before it.
    expect(web.getByText("6")).toBeInTheDocument();
    expect(web.getByRole("link", { name: "Open the software of web-01" })).toHaveAttribute("href", expect.stringContaining("/server/software"));
  });

  it("says a server that runs a Noust of before 4.2 will say its software when it is updated, and offers the update", async () => {
    fleet();
    const table = await screen.findByRole("table", { name: "Software on each server" });
    const old = within(await within(table).findByRole("row", { name: /old-1/ }));
    expect(old.getByText("It runs Noust 1.8.0: it will say its software once it is updated to 4.2.")).toBeInTheDocument();
    // Never "nothing installed": it has no numbers at all.
    expect(old.queryByText("0")).toBeNull();
    expect(old.getByRole("link", { name: "Update old-1" })).toHaveAttribute("href", expect.stringContaining("/fleet/updates"));
    expect(old.queryByRole("link", { name: /^Open the software/ })).toBeNull();
  });

  it("says it in Spanish", async () => {
    await act(() => setLocale("es"));
    fleet();
    const table = await screen.findByRole("table", { name: "Software de cada servidor" });
    expect(within(await within(table).findByRole("row", { name: /old-1/ })).getByText("Tiene Noust 1.8.0: dirá su software cuando se actualice a la 4.2.")).toBeInTheDocument();
  });

  it("is a view of the fleet's Servers, beside the list and the updates", async () => {
    fleet();
    await screen.findByRole("table", { name: "Software on each server" });
    expect(screen.getByRole("heading", { level: 1, name: "Servers" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    fleet();
    await screen.findByRole("table", { name: "Software on each server" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
