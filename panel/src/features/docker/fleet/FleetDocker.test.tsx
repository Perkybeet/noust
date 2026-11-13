/**
 * Docker across the fleet: one list for every server with the server as a column and a filter, whose each thing is in view, a Noust that
 * does not count Docker said in a sentence and never as an error, and no verb on a row of a list that mixes machines.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { centralSession, fleetRoutes } from "../../fleet/testFixtures";
import { ALL_OK, envelope } from "../../operation/testFixtures";
import { NOBODY, SHOP, container } from "../testing";

// old-1 runs a Noust from before 4.3: the central says so in the answer, and the page says it in a sentence.
const NODES = ALL_OK.map((node) => (node.name === "old-1" ? { ...node, status: "unsupported", version: "1.8.0", age_seconds: null, fetched_at: null } : node));

const row = (node: string, fields: object) => ({ ...fields, node, local: node === "web-01", page: "/server/docker/containers", href: node === "web-01" ? "/server/docker/containers" : `/n/${node}/server/docker/containers` });

function open(path: string, rows: unknown[], kind = "containers") {
  screenWidth(1440);
  const backend = fakeBackend({ ...fleetRoutes(centralSession()), [`GET /api/views/docker/${kind}`]: () => json(200, envelope(`docker_${kind}`, rows, { nodes: NODES })) });
  return { ...renderConsole(path), backend };
}

describe("Docker of the fleet", () => {
  const rows = [row("web-01", container("shop-example-com-web-1", SHOP)), row("web-2", container("adminer", NOBODY)), row("web-2", container("nextcloud-app-1", NOBODY))];

  it("lists the containers of every server with the server as a column, and says whose each one is", async () => {
    open("/fleet/docker/containers", rows);
    expect(await screen.findByText("shop-example-com-web-1")).toBeInTheDocument();
    expect(screen.getByText("adminer")).toBeInTheDocument();
    expect(screen.getAllByText("web-2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Noust did not create it").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /shop\.example\.com, Application/ })).toBeInTheDocument();
  });

  it("says in one sentence that a server whose Noust does not count Docker is not in the list, and does not call it a fault", async () => {
    open("/fleet/docker/containers", rows);
    await screen.findByText("adminer");
    // The server's name is a mention inside the sentence, so the sentence is read from the lines of the list that holds it.
    const lines = screen.getAllByRole("listitem").map((line) => line.textContent);
    expect(lines.join("\n")).toContain("old-1 runs Noust 1.8.0, which does not know this view: it is not listed here.");
    // The console's two live regions are alerts too, and empty: what is never drawn is a notice with something in it.
    expect(screen.queryAllByRole("alert").filter((notice) => notice.textContent !== "")).toHaveLength(0);
  });

  it("offers no verb on a row: an action is that server's, on its own page", async () => {
    open("/fleet/docker/containers", rows);
    await screen.findByText("adminer");
    expect(screen.queryByRole("button", { name: /^Actions for/ })).not.toBeInTheDocument();
  });

  it("opens the page of the container on its own server when its row is pressed", async () => {
    const { router, user } = open("/fleet/docker/containers", rows);
    await user.click(await screen.findByText("adminer"));
    await waitFor(() => {
      expect(router.state.location.pathname).toContain("/server/docker/containers/adminer");
    });
  });

  it("has no accessibility violations", async () => {
    const { container: page } = open("/fleet/docker/containers", rows);
    await screen.findByText("adminer");
    await expectNoAxeViolations(page, { page: true });
    expect(within(page).getAllByRole("heading").length).toBeGreaterThan(0);
  });
});
