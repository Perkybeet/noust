/**
 * The Docker zone as a person meets it: which pages a machine is offered, what a row offers by whose it is (an application's is
 * changed from the application, what nobody made is the instance's), and the one thing that loses data for good, which asks for its name.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import { formatBytes } from "../../lib/format";
import { onDesktop, serverRoutes } from "../server/testing";
import { CAN_ALL, ENGINE_MISSING, ENGINE_OK, NOBODY, NOUST, PG16, SHOP, container, dockerRoutes, dockerSession, image, listOf, network, stack, volume } from "./testing";

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

  /** What the server answers for the history of a document: always a 200, with whether it is edited from here and why not. */
  const history = (more: Record<string, unknown> = {}) => ({ editable: false, items: [], why: "", why_code: "", ...more });

  it("offers a stack nobody made to be brought to Noust, says Noust never saw its document, and reads its history, which is empty and says why", async () => {
    const { backend } = open("/server/docker/stacks/nextcloud", true, {
      "GET /api/docker/stacks/nextcloud": () => json(200, detail(NOBODY, "nextcloud")),
      "GET /api/docker/stacks/nextcloud/compose/versions": () => json(200, history({ why_code: "bring_first", why: "raw" })),
    });
    expect(await screen.findByRole("button", { name: "Bring to Noust…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.getByText("Noust never saw it")).toBeInTheDocument();
    expect(screen.getByText(/It is theirs: Noust does not edit it until the stack is brought to Noust\./)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit the Compose" })).not.toBeInTheDocument();
    // The history is read whatever the stack is; the document is not: looking at a stack is not a reveal.
    await waitFor(() => {
      expect(backend.callsTo("GET /api/docker/stacks/nextcloud/compose/versions")).toHaveLength(1);
    });
    expect(backend.callsTo("GET /api/docker/stacks/nextcloud/compose")).toHaveLength(0);
  });

  it("says why an application's stack is not edited from here, in the words of the reason the history gives", async () => {
    open("/server/docker/stacks/shop-example-net", true, {
      "GET /api/docker/stacks/shop-example-net": () => json(200, { ...detail(SHOP, "shop-example-net"), stack: stack("shop-example-net", SHOP, { config_files: [] }) }),
      "GET /api/docker/stacks/shop-example-net/compose/versions": () => json(200, history({ why_code: "not_compose", why: "raw" })),
    });
    expect(await screen.findByText("This is not a Compose stack.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit the Compose" })).not.toBeInTheDocument();
  });

  it("says a stack that an engine or Noust keeps is changed where it is managed, and a reason it has no words for as the server says it", async () => {
    open("/server/docker/stacks/postgres", true, {
      "GET /api/docker/stacks/postgres": () => json(200, detail(PG16, "postgres")),
      "GET /api/docker/stacks/postgres/compose/versions": () => json(200, history({ why_code: "owned", why: "raw" })),
    });
    expect(await screen.findByText("A database engine or Noust itself keeps this stack: change it where it is managed.")).toBeInTheDocument();
  });

  it("reads the history of a document that is edited elsewhere when it has one, and says it can only be read", async () => {
    open("/server/docker/stacks/shop-example-org", true, {
      "GET /api/docker/stacks/shop-example-org": () => json(200, detail(SHOP, "shop-example-org")),
      "GET /api/docker/stacks/shop-example-org/compose/versions": () =>
        json(200, history({ why_code: "repository", items: [{ version: 1, sha256: "a", size: 380, source: "found", actor: null, reason: "", created_at: new Date(Date.now() - 9 * 86_400_000).toISOString() }] })),
    });
    expect(await screen.findByText("It comes from the application's repository: edit it there, and the next update applies it.")).toBeInTheDocument();
    expect(await screen.findByText("1 saved version, kept by Noust")).toBeInTheDocument();
    expect(screen.queryByText(/the one before can be put back/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit the Compose" })).not.toBeInTheDocument();
  });

  it("says in the stack page whether the volumes it touches are in a copy", async () => {
    open("/server/docker/stacks/nextcloud", true, {
      "GET /api/docker/stacks/nextcloud": () => json(200, { ...detail(NOBODY, "nextcloud"), containers: [container("nextcloud-app-1", NOBODY, { project: "nextcloud", service: "app", mounts: ["nextcloud_data", "nextcloud_db"] })] }),
      "GET /api/docker/stacks/nextcloud/compose/versions": () => json(200, history({ why_code: "bring_first" })),
      volumes: [volume("nextcloud_data", NOBODY, { backup: { state: "none", via: "noust backup volume create nextcloud_data" } }), volume("nextcloud_db", NOBODY, { backup: { state: "none", via: "noust backup volume create nextcloud_db" } })],
    });
    expect(await screen.findByText("2 without a copy")).toBeInTheDocument();
  });

  it("leads an application's stack to the application and to its Compose, with the versions saved, and brings nothing", async () => {
    open("/server/docker/stacks/shop-example-com", true, {
      "GET /api/docker/stacks/shop-example-com": () => json(200, detail(SHOP, "shop-example-com")),
      "GET /api/docker/stacks/shop-example-com/compose/versions": () =>
        json(200, { editable: true, why_code: "", why: "", items: [{ version: 2, sha256: "b", size: 400, source: "edit", actor: "marta", reason: "More memory", created_at: new Date(Date.now() - 3 * 86_400_000).toISOString() }, { version: 1, sha256: "a", size: 380, source: "found", actor: null, reason: "", created_at: new Date(Date.now() - 9 * 86_400_000).toISOString() }] }),
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

describe("the use of each container", () => {
  const web = container("shop-example-com-web-1", SHOP, { service: "web", id: "a".repeat(64) });
  const restarting = container("api-example-org-api-1", SHOP, { state: "restarting", state_kind: "loop", id: "b".repeat(64) });
  const parked = container("adminer", NOBODY, { state: "exited", state_kind: "stopped", id: "c".repeat(64) });
  const reading = (id: string, name: string, usage: Record<string, unknown>) => ({ id, name, usage: { available: true, reason: "", ...usage } });
  const stats = (items: unknown[]) => ({ available: true, can_manage: true, engine: ENGINE_OK, items, read_at: new Date().toISOString() });

  it("draws the processor and the memory of each container from one reading for all of them, and a dash with the reason where there is no reading", async () => {
    const { backend } = open("/server/docker/containers", true, {
      containers: [web, restarting, parked],
      "GET /api/docker/containers/stats": () =>
        json(
          200,
          stats([
            reading("a".repeat(64), "shop-example-com-web-1", { cpu_percent: 14, memory_bytes: 222_000_000 }),
            reading("b".repeat(64), "api-example-org-api-1", { available: false, reason: "restarting" }),
            reading("c".repeat(64), "adminer", { available: false, reason: "not_running" }),
          ]),
        ),
    });
    const table = await screen.findByRole("table", { name: "Containers" });
    expect(await within(table).findByRole("columnheader", { name: "Processor" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Memory" })).toBeInTheDocument();
    const row = async (name: string): Promise<HTMLElement> => (await within(table).findByText(name)).closest("tr") as HTMLElement;
    expect(await within(await row("shop-example-com-web-1")).findByText("14%")).toBeInTheDocument();
    expect(within(await row("shop-example-com-web-1")).getByText(formatBytes(222_000_000, "en"))).toBeInTheDocument();
    // Nothing is drawn as zero where nothing was read: the reason is there for whoever cannot see the dash.
    expect(within(await row("api-example-org-api-1")).queryByText(/^0 ?%$/)).not.toBeInTheDocument();
    expect(within(await row("api-example-org-api-1")).getAllByText("It is restarting, and a container that restarts has no steady reading.")).toHaveLength(2);
    expect(within(await row("adminer")).getAllByText("It is not running, so it uses nothing.")).toHaveLength(2);
    // One request for every container, and not one for each.
    expect(backend.callsTo("GET /api/docker/containers/stats")).toHaveLength(1);
  });

  it("has no columns of use where the server has no such reading (a node of 4.2), and says nothing wrong for it", async () => {
    const { backend } = open("/server/docker/containers", true, { containers: [web], "GET /api/docker/containers/stats": () => problem(404, "not_found", "No such route") });
    await screen.findByText("shop-example-com-web-1");
    await waitFor(() => {
      expect(backend.callsTo("GET /api/docker/containers/stats")).toHaveLength(1);
      expect(screen.queryByRole("columnheader", { name: "Processor" })).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Could not load the containers")).not.toBeInTheDocument();
  });

  it("joins a container by its name when the list has a short id, and one called stats by its own id", async () => {
    open("/server/docker/containers", true, {
      containers: [container("stats", NOBODY, { id: "d".repeat(64) }), container("redis", NOBODY, { id: "eeeeeeeeeeee" })],
      "GET /api/docker/containers/stats": () => json(200, stats([reading("d".repeat(64), "stats", { cpu_percent: 3, memory_bytes: 10_000_000 }), reading("e".repeat(64), "redis", { cpu_percent: 7, memory_bytes: 20_000_000 })])),
    });
    const table = await screen.findByRole("table", { name: "Containers" });
    expect(await within(table).findByText("3.0%")).toBeInTheDocument();
    expect(within(table).getByText("7.0%")).toBeInTheDocument();
  });
});

describe("whether a volume is in a copy", () => {
  const day = 86_400_000;
  const covered = volume("shop-example-com_uploads", SHOP, { used_by: ["shop-example-com-web-1"], backup: { state: "scheduled", last_at: new Date(Date.now() - day).toISOString(), via: "noust backup volume create shop-example-com_uploads" } });
  const bare = volume("wiki_data", NOBODY, { size_bytes: 3_650_722_201, backup: { state: "none", via: "noust backup volume create wiki_data" } });
  const once = volume("old_data", NOBODY, { size_bytes: 1_000_000, backup: { state: "copied", last_at: new Date(Date.now() - 9 * day).toISOString(), via: "noust backup volume create old_data" } });

  it("says in the row what each volume has, with a shape where it asks for a look, and counts the ones with data and no copy above the list", async () => {
    open("/server/docker/volumes", true, { volumes: [covered, bare, once] });
    const table = await screen.findByRole("table", { name: "Volumes" });
    expect(await within(table).findByRole("columnheader", { name: "Backup status" })).toBeInTheDocument();
    const row = async (name: string): Promise<HTMLElement> => (await within(table).findByText(name)).closest("tr") as HTMLElement;
    expect(within(await row("shop-example-com_uploads")).getByText(/^Scheduled,/)).toBeInTheDocument();
    expect(within(await row("wiki_data")).getByText("No copy")).toBeInTheDocument();
    expect(within(await row("old_data")).getByText(/not scheduled$/)).toBeInTheDocument();
    // The banner counts what holds data and is in no copy: wiki_data is, a copy nothing takes again is a warning in its row and not in the count.
    expect(await screen.findByText("1 volume holds data with no copy")).toBeInTheDocument();
    expect(screen.getByText(/That is 3\.4 GB that could not be recovered if something fails/)).toBeInTheDocument();
  });

  it("offers a copy now where the server gives the command that takes one, an application's volume included, and not on one it has no command for", async () => {
    const nobodys = volume("scratch_data", NOBODY, { backup: { state: "none", via: "" } });
    const { user } = open("/server/docker/volumes", true, { volumes: [covered, bare, nobodys] });
    await user.click(await screen.findByRole("button", { name: "Actions for shop-example-com_uploads" }));
    const menu = await screen.findByRole("menu");
    // What an application owns is changed from the application, and a copy is not changing it.
    expect(within(menu).getByRole("menuitem", { name: /Copy now/ })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /Change from shop.example.com/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(await screen.findByRole("button", { name: "Actions for wiki_data" }));
    await user.click(await screen.findByRole("menuitem", { name: /Copy now/ }));
    const dialog = await screen.findByRole("dialog", { name: "Copy wiki_data now" });
    expect(within(dialog).getByText("noust backup volume create wiki_data")).toBeInTheDocument();
    expect(within(dialog).getByText("It has no copy yet.")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    // A volume the server has no command for (what Noust did not make) is not offered a copy it could not take.
    await user.click(await screen.findByRole("button", { name: "Actions for scratch_data" }));
    expect(screen.queryByRole("menuitem", { name: /Copy now/ })).not.toBeInTheDocument();
  });

  it("leads to where the copies are scheduled", async () => {
    open("/server/docker/volumes", true, { volumes: [covered] });
    expect(await screen.findByRole("link", { name: "Schedule copies" })).toHaveAttribute("href", "/backups/volumes");
  });

  it("has no column and no banner for a server that does not say whether its volumes are copied (a node of 4.2)", async () => {
    const old = { ...bare, backup: undefined } as never;
    open("/server/docker/volumes", true, { volumes: [old] });
    await screen.findByText("wiki_data");
    expect(screen.queryByRole("columnheader", { name: "Backup status" })).not.toBeInTheDocument();
    expect(screen.queryByText(/hold(s)? data with no copy/)).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Copy now/ })).not.toBeInTheDocument();
  });
});

describe("the address range of a network", () => {
  it("says the first subnet, the gateway to whoever points at it and how many more there are, and a dash with the reason for Docker's own", async () => {
    open("/server/docker/networks", true, {
      networks: [
        network("shop_default", SHOP, { ranges: [{ subnet: "172.20.0.0/16", gateway: "172.20.0.1" }, { subnet: "fd00:dead::/64", gateway: "" }] }),
        network("host", NOBODY, { builtin: true, ranges: [], can: { ...CAN_ALL, remove: false, blocked: "builtin" } }),
      ],
    });
    const table = await screen.findByRole("table", { name: "Networks" });
    expect(await within(table).findByRole("columnheader", { name: "Subnet" })).toBeInTheDocument();
    const shop = (await within(table).findByText("shop_default")).closest("tr") as HTMLElement;
    expect(within(shop).getByText("172.20.0.0/16")).toHaveAttribute("title", "Gateway 172.20.0.1");
    expect(within(shop).getByText("and 1 more")).toBeInTheDocument();
    const host = within(table).getByText("host").closest("tr") as HTMLElement;
    expect(within(host).getByText("Docker gives this network no address range")).toBeInTheDocument();
  });
});

describe("where an image comes from", () => {
  it("says built here for an image Noust built and one another hand built, and not looked at for one nobody asked about yet", async () => {
    open("/server/docker/images", true, {
      images: [
        image("sha256:aaaa", ["shop-example-com-web:release-41"], SHOP, { source: "built_noust" }),
        image("sha256:bbbb", ["scratch:dev"], NOBODY, { source: "built_other" }),
        image("sha256:cccc", ["redis:7"], NOBODY, { source: "registry", update: { state: "not_looked", change: "", mode: "none", apply: false, reference: "redis:7", owner: NOBODY } as never }),
        image("sha256:dddd", ["mystery:1"], NOBODY, { source: "unknown" }),
      ],
    });
    const table = await screen.findByRole("table", { name: "Images" });
    const row = async (name: string): Promise<HTMLElement> => (await within(table).findByText(name)).closest("tr") as HTMLElement;
    expect(await within(await row("shop-example-com-web:release-41")).findByText("Built here")).toBeInTheDocument();
    expect(within(await row("scratch:dev")).getByText("Built here")).toBeInTheDocument();
    expect(within(await row("redis:7")).getByText("Not looked at yet")).toBeInTheDocument();
    expect(within(await row("mystery:1")).getByText("Not looked at yet")).toBeInTheDocument();
  });
});
