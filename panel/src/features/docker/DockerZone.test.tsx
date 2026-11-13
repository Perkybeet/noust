/**
 * The Docker zone as a person meets it: which pages a machine is offered, what a row offers by whose it is (an application's is
 * changed from the application, what nobody made is the instance's), and the one thing that loses data for good, which asks for its name.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { onDesktop, serverRoutes } from "../server/testing";
import { CAN_ALL, ENGINE_MISSING, NOBODY, NOUST, SHOP, container, dockerRoutes, dockerSession, image, listOf, network, stack, volume } from "./testing";

beforeEach(onDesktop);

function open(path: string, docker = true, more: Parameters<typeof dockerRoutes>[0] = {}) {
  const backend = fakeBackend({ ...signedInRoutes(dockerSession(docker)), ...serverRoutes(), ...dockerRoutes(more) });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

const REMOVED = { kind: "volume", name: "wiki_data", action: "remove", size_bytes: 3_650_722_201 };

describe("which pages of Docker a machine is offered", () => {
  it("offers the seven pages in the column of Server where Docker is at hand", async () => {
    open("/server/docker/containers");
    const column = await screen.findByRole("navigation", { name: "Server sections" });
    for (const name of ["Summary", "Stacks", "Containers", "Images", "Volumes", "Networks", "Engine"]) {
      expect(await within(column).findByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("offers nothing where a server has no Docker, and an address that was typed says why in the server's words and not as an error", async () => {
    open("/server/docker", false, { "GET /api/docker/summary": () => json(200, { available: false, can_manage: true, engine: ENGINE_MISSING, counts: {}, attention: [], disk: null }) });
    expect(await screen.findByText(/Not available on /)).toBeInTheDocument();
    expect(screen.getByText("Docker is not installed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See it on every server" })).toHaveAttribute("href", "/fleet/docker");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    // Not one page of the zone is a place to go: what cannot be done is not given as an option.
    const column = await screen.findByRole("navigation", { name: "Server sections" });
    for (const name of ["Summary", "Stacks", "Containers", "Images", "Volumes", "Networks", "Engine"]) {
      expect(within(column).queryByRole("link", { name })).not.toBeInTheDocument();
    }
  });
});

describe("what a container's menu offers", () => {
  const rows = [
    container("shop-example-com-web-1", SHOP, { service: "web" }),
    container("adminer", NOBODY),
    container("noust-buildkit", NOUST, { helper: "buildkit", can: { ...CAN_ALL, start: false, stop: false, restart: false, remove: false, console: false, blocked: "owner" } }),
  ];

  it("offers what an application owns its logs and one line that leads to the application, and no verb it would refuse", async () => {
    const { user } = open("/server/docker/containers", true, { containers: rows });
    await user.click(await screen.findByRole("button", { name: "Actions for shop-example-com-web-1" }));
    const menu = await screen.findByRole("menu");
    const items = within(menu).getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("See logs");
    expect(items[1]).toHaveTextContent("Change from shop.example.com");
    expect(items[1]).toHaveTextContent("Restarting, stopping and removing are done from the application");
    expect(within(menu).queryByRole("menuitem", { name: /Stop/ })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: /Remove/ })).not.toBeInTheDocument();
  });

  it("offers what nobody made everything, with remove last", async () => {
    const { user } = open("/server/docker/containers", true, { containers: rows });
    await user.click(await screen.findByRole("button", { name: "Actions for adminer" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["See logs", "Open console", "Restart", "Stop", "Remove…"]);
  });

  it("draws the link of an application's container in the filter its page opens with", async () => {
    open("/server/docker/containers?app=shop.example.com", true, { containers: rows });
    expect(await screen.findByText("shop-example-com-web-1")).toBeInTheDocument();
    expect(screen.queryByText("adminer")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Only shop.example.com" })).toBeInTheDocument();
  });
});

describe("removing what holds data", () => {
  const volumes = [volume("wiki_data", NOBODY), volume("shop-example-com_uploads", SHOP, { used_by: ["shop-example-com-web-1"] })];

  it("asks for the volume's name and does not remove it before it is typed", async () => {
    const { user, backend } = open("/server/docker/volumes", true, { volumes, "DELETE /api/docker/volumes/wiki_data": () => json(200, REMOVED) });
    await user.click(await screen.findByRole("button", { name: "Actions for wiki_data" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete the volume wiki_data" });
    const action = within(dialog).getByRole("button", { name: "Delete volume" });
    expect(action).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "wiki_data");
    expect(action).toBeEnabled();
    await user.click(action);
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/docker/volumes/wiki_data")).toHaveLength(1);
    });
    expect(backend.callsTo("DELETE /api/docker/volumes/wiki_data")[0]?.body).toEqual({ confirm_name: "wiki_data" });
  });

  it("does not offer to remove what an application's data is in: it goes with the application", async () => {
    const { user } = open("/server/docker/volumes", true, { volumes });
    await user.click(await screen.findByRole("button", { name: "Actions for shop-example-com_uploads" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).queryByRole("menuitem", { name: /Remove/ })).not.toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /Change from shop.example.com/ })).toBeInTheDocument();
  });

  it("says Docker's own networks are Docker's and offers nothing on them", async () => {
    open("/server/docker/networks", true, { networks: [network("bridge", NOBODY, { builtin: true, can: { ...CAN_ALL, remove: false, blocked: "builtin" } }), network("shop_default", SHOP)] });
    expect(await screen.findByText("Docker's own")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Actions for bridge" })).not.toBeInTheDocument();
  });
});

describe("what the lists say of a version", () => {
  it("marks a new version and a new major one, and an image an application keeps to go back to as that", async () => {
    const update = (state: string, change: string) => ({ state, change, mode: "with_net", apply: true, reference: "x", owner: NOBODY });
    open("/server/docker/images", true, {
      images: [
        image("sha256:aaaa", ["nginx:1.26"], NOBODY, { update: update("newer", "minor") as never }),
        image("sha256:bbbb", ["postgres:16"], NOBODY, { update: update("newer", "major") as never }),
        image("sha256:cccc", ["shop-example-com-web:release-40"], SHOP, { way_back: true }),
      ],
    });
    expect(await screen.findByText("New version")).toBeInTheDocument();
    expect(screen.getByText("New major version")).toBeInTheDocument();
    expect(screen.getByText("Keeps the way back")).toBeInTheDocument();
  });
});

describe("a stack's page", () => {
  const detail = (owner: typeof NOBODY, name: string) => ({
    available: true,
    engine: listOf([]).engine,
    can_manage: true,
    stack: stack(name, owner, { working_dir: `/srv/${name}`, config_files: [`/srv/${name}/compose.yaml`] }),
    containers: [container(`${name}-app-1`, owner, { project: name, service: "app" })],
  });

  it("offers a stack nobody made to be brought to Noust, says Noust never saw its document and does not ask for saved versions it cannot have", async () => {
    const { backend } = open("/server/docker/stacks/nextcloud", true, { "GET /api/docker/stacks/nextcloud": () => json(200, detail(NOBODY, "nextcloud")) });
    expect(await screen.findByRole("button", { name: "Bring to Noust…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.getByText("Noust never saw it")).toBeInTheDocument();
    expect(screen.getByText(/It is theirs: Noust does not edit it until the stack is brought to Noust\./)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit the Compose" })).not.toBeInTheDocument();
    // The server answers a 409 to the versions of a document of a stack that is not Noust's, which the browser would report as an error.
    expect(backend.callsTo("GET /api/docker/stacks/nextcloud/compose/versions")).toHaveLength(0);
    expect(backend.callsTo("GET /api/docker/stacks/nextcloud/compose")).toHaveLength(0);
  });

  it("says why an application's stack is not edited from here, and asks for no versions of a document that does not exist", async () => {
    const { backend } = open("/server/docker/stacks/shop-example-net", true, {
      "GET /api/docker/stacks/shop-example-net": () => json(200, { ...detail(SHOP, "shop-example-net"), stack: stack("shop-example-net", SHOP, { config_files: [] }) }),
      "GET /api/docker/stacks/shop-example-net/compose": () => json(200, { stack: "shop-example-net", domain: "shop.example.net", editable: false, kind: null, why_code: "not_compose", why: "raw", text: null, path: null, version: null }),
    });
    expect(await screen.findByText("This is not a Compose stack.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit the Compose" })).not.toBeInTheDocument();
    expect(backend.callsTo("GET /api/docker/stacks/shop-example-net/compose/versions")).toHaveLength(0);
  });

  it("leads an application's stack to the application and to its Compose, with the versions saved, and brings nothing", async () => {
    open("/server/docker/stacks/shop-example-com", true, {
      "GET /api/docker/stacks/shop-example-com": () => json(200, detail(SHOP, "shop-example-com")),
      "GET /api/docker/stacks/shop-example-com/compose": () => json(200, { stack: "shop-example-com", domain: "shop.example.com", editable: true, kind: "compose", why_code: null, why: "", text: "services: {}\n", path: "/srv/shop-example-com/compose.yaml", version: 2 }),
      "GET /api/docker/stacks/shop-example-com/compose/versions": () =>
        json(200, { items: [{ version: 2, sha256: "b", size: 400, source: "edit", actor: "marta", reason: "More memory", created_at: new Date(Date.now() - 3 * 86_400_000).toISOString() }, { version: 1, sha256: "a", size: 380, source: "found", actor: null, reason: "", created_at: new Date(Date.now() - 9 * 86_400_000).toISOString() }] }),
    });
    expect(await screen.findByRole("link", { name: "Open the application" })).toBeInTheDocument();
    // Whether the document is edited from here is asked of the server, so the way in comes after it answers.
    expect(await screen.findByRole("link", { name: "Edit the Compose" })).toHaveAttribute("href", "/server/docker/compose/shop-example-com");
    expect(await screen.findByText(/2 saved versions: the one before can be put back\./)).toBeInTheDocument();
    expect(screen.getByText(/by marta/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bring to Noust…" })).not.toBeInTheDocument();
  });
});

describe("the summary", () => {
  it("says what needs the operator first, worst first, and who it belongs to", async () => {
    open("/server/docker", true, {
      "GET /api/docker/summary": () =>
        json(200, {
          available: true,
          can_manage: true,
          engine: { ...listOf([]).engine },
          counts: { containers: { total: 2, running: 0, failing: 1, stopped: 1 }, stacks: { total: 0, unowned: 0 }, images: { total: 0 }, volumes: { total: 0 }, networks: { total: 0 } },
          attention: [
            { kind: "loop", subject: "api-example-org-api-1", owner: SHOP, count: 1, facts: { exit_code: 137, oom: true, memory_limit_bytes: 268435456, restarts_recent: 6, window_minutes: 10 } },
            { kind: "stopped", subject: "adminer", owner: NOBODY, count: 1, facts: { exit_code: 143, kind: "terminated", policy: "unless-stopped", finished_at: "2026-11-09T08:12:41Z" } },
          ],
          disk: null,
        }),
      stacks: [stack("shop-example-com", SHOP)],
    });
    const list = await screen.findByRole("list", { name: "What needs you on Docker" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("api-example-org-api-1 keeps restarting");
    expect(rows[0]).toHaveTextContent("It exited with 137: it ran out of memory (256 MB)");
    expect(rows[1]).toHaveTextContent("adminer is stopped and its policy says it should run");
    // An application's container is started from the application; what nobody made can be started here.
    const row = (text: RegExp): HTMLElement => {
      const found = rows.find((candidate) => text.test(candidate.textContent));
      if (found === undefined) throw new Error(`no row says ${String(text)}`);
      return found;
    };
    expect(within(row(/keeps restarting/)).queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
    expect(within(row(/adminer is stopped/)).getByRole("button", { name: "Start" })).toBeInTheDocument();
  });

  it("says nothing needs the operator when nothing does", async () => {
    open("/server/docker");
    expect(await screen.findByText(/Nothing needs you/)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container: page } = open("/server/docker/containers", true, { containers: [container("adminer", NOBODY)] });
    await screen.findByText("adminer");
    await expectNoAxeViolations(page, { page: true });
  });
});
