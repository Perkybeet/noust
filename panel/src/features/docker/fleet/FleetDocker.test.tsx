/**
 * Docker across the fleet: one list for every server with the server as a column and a filter, whose each thing is in view, a Noust that
 * does not count Docker said in a sentence and never as an error, and no verb on a row of a list that mixes machines.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { centralSession, fleetRoutes } from "../../fleet/testFixtures";
import { ALL_OK, envelope } from "../../operation/testFixtures";
import { NOBODY, SHOP, container, image } from "../testing";

// old-1 runs a Noust from before 4.3: the central says so in the answer, and the page says it in a sentence.
const NODES = ALL_OK.map((node) => (node.name === "old-1" ? { ...node, status: "unsupported", version: "1.8.0", age_seconds: null, fetched_at: null } : node));

const row = (node: string, fields: object) => ({ ...fields, node, local: node === "web-01", page: "/server/docker/containers", href: node === "web-01" ? "/server/docker/containers" : `/n/${node}/server/docker/containers` });

function open(path: string, rows: unknown[], kind = "containers", extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({ ...fleetRoutes(centralSession()), [`GET /api/views/docker/${kind}`]: () => json(200, envelope(`docker_${kind}`, rows, { nodes: NODES })), ...extra });
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

describe("looking for new versions on every server", () => {
  const rows = [row("web-2", image("sha256:aaaa", ["nginx:1.26"], NOBODY))];
  const node = (name: string, state: string, more: Record<string, unknown> = {}) => ({ node: name, state, reason: null, step: "", requires_elevation: false, ...more });
  const PLAN = {
    action: "docker_images_check",
    title: "Look for new versions of the images in use",
    batches: [["web-2"]],
    nodes: [node("web-2", "queued"), node("old-1", "skipped", { reason: "unsupported" }), node("nas", "skipped", { reason: "not_needed" })],
    options: { pull: false },
    requires_elevation: false,
    strategy: { serial: 2, max_failures: 1, canary: null },
    summary: { run: 1, skipped: 2 },
    notes: [],
  };
  const JOB = { job_id: "job-9", message: "queued", status: "queued", job: { id: "job-9", type: "fleet_action", status: "queued", subject: "docker_images_check", created_at: new Date().toISOString() } };

  it("shows the plan first, which servers look and which are left out and why, and asks nothing of a registry until it is confirmed", async () => {
    const { user, backend } = open("/fleet/docker/images", rows, "images", {
      "POST /api/fleet/docker/images/check": (call) => (JSON.stringify(call.body).includes('"plan":true') ? json(200, { plan: PLAN, job: null }) : json(202, { plan: PLAN, job: JOB })),
    });
    await screen.findByText("nginx:1.26");
    expect(backend.callsTo("POST /api/fleet/docker/images/check")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Look for new versions" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Look for new versions on every server" });
    expect((await within(dialog).findAllByText("web-2")).length).toBeGreaterThan(0);
    expect(within(dialog).getByText("old-1")).toBeInTheDocument();
    // The plan queued nothing: it is a question.
    expect(backend.callsTo("POST /api/fleet/docker/images/check")).toHaveLength(1);
    expect(backend.callsTo("POST /api/fleet/docker/images/check")[0]?.body).toEqual({ plan: true, pull: false, nodes: [], labels: {} });
    await user.click(within(dialog).getByRole("button", { name: "Look for new versions" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/docker/images/check")).toHaveLength(2);
    });
    expect(backend.callsTo("POST /api/fleet/docker/images/check")[1]?.body).toEqual({ plan: false, pull: false, nodes: [], labels: {} });
  });

  it("offers to download what moved from the menu, with the same plan", async () => {
    const { user, backend } = open("/fleet/docker/images", rows, "images", { "POST /api/fleet/docker/images/check": () => json(200, { plan: { ...PLAN, options: { pull: true } }, job: null }) });
    await screen.findByText("nginx:1.26");
    await user.click(screen.getByRole("button", { name: "More options for Look for new versions" }));
    await user.click(await screen.findByRole("menuitem", { name: /Look by downloading what moved/ }));
    await screen.findByRole("alertdialog", { name: "Look for new versions on every server" });
    expect(backend.callsTo("POST /api/fleet/docker/images/check")[0]?.body).toEqual({ plan: true, pull: true, nodes: [], labels: {} });
  });

  it("says a plan that could not be made in a sentence, and not as a failure of the page", async () => {
    const { user } = open("/fleet/docker/images", rows, "images", { "POST /api/fleet/docker/images/check": () => problem(403, "forbidden", "You may not look for new versions.") });
    await screen.findByText("nginx:1.26");
    await user.click(screen.getByRole("button", { name: "Look for new versions" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Look for new versions on every server" });
    expect(await within(dialog).findByText("Could not work out which servers would look")).toBeInTheDocument();
  });

  it("is not offered on a list that is not the images: only they are looked at", async () => {
    open("/fleet/docker/containers", [row("web-2", container("adminer", NOBODY))]);
    await screen.findByText("adminer");
    expect(screen.queryByRole("button", { name: "Look for new versions" })).not.toBeInTheDocument();
  });
});
