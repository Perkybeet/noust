import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { FakeBackend, RouteHandler } from "../../test/fakes";
import { fleetRoutes } from "./testFixtures";

const PRINT = `SHA256:${"A".repeat(43)}`;
const AUTHORIZED = 'restrict,port-forwarding,permitopen="web2.example.com:22",command="/usr/bin/false" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB noust-central';

const DIRECT = { connect_timeout: null, effective_timeout: 15, default_timeout: 15, bastion: null, authorized_key_line: null };
const THROUGH = {
  connect_timeout: 20,
  effective_timeout: 20,
  default_timeout: 15,
  bastion: { address: "hop@bastion.example.com:22", fingerprint: PRINT, host: "bastion.example.com", port: 22, user: "hop" },
  authorized_key_line: AUTHORIZED,
};

afterEach(() => {
  vi.restoreAllMocks();
});

/** A list's row by a text in it: the tests run at a phone's width, where each table is card rows. */
async function rowOf(text: string): Promise<HTMLElement> {
  const list = await screen.findByRole("list", { name: "Servers of this fleet" });
  return waitFor(() => {
    const row = within(list)
      .queryAllByRole("listitem")
      .find((candidate) => within(candidate).queryAllByText(text).length > 0);
    if (row === undefined) throw new Error(`no row for ${text}`);
    return row;
  });
}

async function openTool(user: ReturnType<typeof renderConsole>["user"], item: string): Promise<void> {
  await user.click(within(await rowOf("web-2")).getByRole("button", { name: "Actions for web-2" }));
  await user.click(await screen.findByRole("menuitem", { name: item }));
}

function backendWith(extra: Record<string, RouteHandler>): FakeBackend {
  return fakeBackend({ ...fleetRoutes(), ...extra });
}

describe("how the central connects to a server", () => {
  it("reads the options, refuses a wait or a bastion that is not valid, and sends nothing", { timeout: 40_000 }, async () => {
    const backend = backendWith({ "GET /api/nodes/web-2/ssh": () => json(200, DIRECT) });
    const { user, container } = renderConsole("/fleet/servers");

    await openTool(user, "Connection options");
    const dialog = await screen.findByRole("dialog", { name: "How this central connects to web-2" });
    const wait = await within(dialog).findByLabelText(/Wait for the connection/);
    expect(wait).toHaveValue("");
    expect(wait).toHaveAttribute("placeholder", "15");
    expect(within(dialog).getByText("Seconds, from 1 to 300. Empty is the default, 15 seconds.")).toBeInTheDocument();

    await user.type(wait, "301");
    await user.click(within(dialog).getByRole("checkbox", { name: /Connect through a bastion/ }));
    await user.type(within(dialog).getByLabelText(/^Bastion/), "-oProxyCommand=x@host");
    await user.click(within(dialog).getByRole("button", { name: "Save and test" }));

    expect(await within(dialog).findByText("Write a whole number of seconds from 1 to 300.")).toBeInTheDocument();
    expect(within(dialog).getByText("Write the bastion as user@host or user@host:port.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
    await expectNoAxeViolations(container, { page: true });
  });

  it("sets a bastion with its checked host key, then shows the line the bastion's operator adds", { timeout: 40_000 }, async () => {
    const backend = backendWith({
      "GET /api/nodes/web-2/ssh": () => json(200, DIRECT),
      "PUT /api/nodes/web-2/ssh": () => json(200, THROUGH),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Connection options");
    const dialog = await screen.findByRole("dialog", { name: "How this central connects to web-2" });
    await user.type(await within(dialog).findByLabelText(/Wait for the connection/), "20");
    await user.click(within(dialog).getByRole("checkbox", { name: /Connect through a bastion/ }));
    await user.type(within(dialog).getByLabelText(/^Bastion/), "hop@bastion.example.com:22");
    // A bastion without a host key is never trusted on first use: the key is asked for.
    await user.click(within(dialog).getByRole("button", { name: "Save and test" }));
    expect(await within(dialog).findByText("Give the bastion's fingerprint or host key.")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/nodes/web-2/ssh")).toHaveLength(0);

    await user.type(within(dialog).getByLabelText(/^Its host key/), PRINT);
    await user.click(within(dialog).getByRole("button", { name: "Save and test" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/nodes/web-2/ssh")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/nodes/web-2/ssh")[0]?.body).toEqual({
      connect_timeout: 20,
      bastion: { address: "hop@bastion.example.com:22", fingerprint: PRINT },
      test: true,
    });
    const authorize = await within(dialog).findByRole("group", { name: "Authorize this central on the bastion" });
    expect(authorize).toHaveTextContent(`Host key pinned: ${PRINT}`);
    expect(authorize).toHaveTextContent(AUTHORIZED);
    // Saved: the way out is Close (the header's and the footer's), and Save is gone.
    expect(within(dialog).getAllByRole("button", { name: "Close" })).toHaveLength(2);
    expect(within(dialog).queryByRole("button", { name: "Save and test" })).not.toBeInTheDocument();
  });

  it("keeps the key already pinned when the bastion is the same one, and changes only the wait", { timeout: 40_000 }, async () => {
    const backend = backendWith({
      "GET /api/nodes/web-2/ssh": () => json(200, THROUGH),
      "PUT /api/nodes/web-2/ssh": () => json(200, { ...THROUGH, connect_timeout: 30, effective_timeout: 30 }),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Connection options");
    const dialog = await screen.findByRole("dialog", { name: "How this central connects to web-2" });
    const wait = await within(dialog).findByLabelText(/Wait for the connection/);
    expect(wait).toHaveValue("20");
    expect(within(dialog).getByLabelText(/^Bastion/)).toHaveValue("hop@bastion.example.com:22");
    await user.clear(wait);
    await user.type(wait, "30");
    await user.click(within(dialog).getByRole("button", { name: "Save and test" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/nodes/web-2/ssh")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/nodes/web-2/ssh")[0]?.body).toEqual({ connect_timeout: 30, bastion: { address: "hop@bastion.example.com:22" }, test: true });
  });

  it("says why the new options were not kept, in the server's words, and keeps the dialog open", { timeout: 40_000 }, async () => {
    backendWith({
      "GET /api/nodes/web-2/ssh": () => json(200, DIRECT),
      "PUT /api/nodes/web-2/ssh": () => problem(502, "node_unreachable", "web-2 did not answer through the bastion", { output: "ssh: connect to host bastion.example.com port 22: Connection refused" }),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Connection options");
    const dialog = await screen.findByRole("dialog", { name: "How this central connects to web-2" });
    await user.type(await within(dialog).findByLabelText(/Wait for the connection/), "5");
    await user.click(within(dialog).getByRole("button", { name: "Save and test" }));

    expect(await within(dialog).findByText("Could not change how web-2 is connected to")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Connection refused");
    expect(within(dialog).getByRole("button", { name: "Save and test" })).toBeInTheDocument();
  });

  it("shows a failure to read the options with a retry instead of an empty form", { timeout: 40_000 }, async () => {
    backendWith({ "GET /api/nodes/web-2/ssh": () => problem(404, "not_found", "web-2 does not exist") });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Connection options");
    const dialog = await screen.findByRole("dialog", { name: "How this central connects to web-2" });
    expect(await within(dialog).findByText("Could not read how web-2 is connected to")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Save and test" })).not.toBeInTheDocument();
  });
});

describe("a server's inventory", () => {
  it("downloads what the central knows about it, as a file", { timeout: 40_000 }, async () => {
    const created = vi.fn(() => "blob:inventory");
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const backend = backendWith({ "GET /api/nodes/web-2/inventory": () => json(200, { format: "noust-node-inventory", node: "web-2", labels: { env: "prod" } }) });
    const { user } = renderConsole("/fleet/servers");

    await user.click(within(await rowOf("web-2")).getByRole("button", { name: "Actions for web-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Export its inventory" }));

    await waitFor(() => {
      expect(created).toHaveBeenCalledOnce();
    });
    expect(backend.callsTo("GET /api/nodes/web-2/inventory")).toHaveLength(1);
    expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe("web-2.inventory.json");
    expect(await screen.findByText("Inventory of web-2 downloaded")).toBeInTheDocument();
  });

  it("applies an inventory from another central and says what was recorded and what was left out", { timeout: 40_000 }, async () => {
    const backend = backendWith({
      "POST /api/nodes/web-2/inventory": () =>
        json(200, {
          applied: [{ part: "labels" }, { part: "placements" }],
          skipped: [{ part: "ssh", reason: "not asked for" }],
        }),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Apply an inventory");
    const dialog = await screen.findByRole("dialog", { name: "Apply an inventory to web-2" });
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));
    expect(await within(dialog).findByText("Choose the inventory file.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/nodes/web-2/inventory")).toHaveLength(0);

    const field = within(dialog).getByLabelText(/Inventory file/);
    await user.upload(field, new File(["this is not json"], "inventory.json", { type: "application/json" }));
    expect(await within(dialog).findByText("That file could not be read as an inventory.")).toBeInTheDocument();

    const document = { format: "noust-node-inventory", node: "web-2", labels: { env: "prod" } };
    await user.upload(field, new File([JSON.stringify(document)], "inventory.json", { type: "application/json" }));
    await waitFor(() => {
      expect(within(dialog).queryByText("That file could not be read as an inventory.")).not.toBeInTheDocument();
    });
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/nodes/web-2/inventory")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/nodes/web-2/inventory")[0]?.body).toEqual({ document, include_ssh: false });
    const recorded = await within(dialog).findByRole("group", { name: "Recorded" });
    expect(recorded).toHaveTextContent("Labels");
    expect(recorded).toHaveTextContent("Applications on several servers");
    const left = within(dialog).getByRole("group", { name: "Left out" });
    expect(left).toHaveTextContent("Connection options: not asked for");
  });
});

describe("a server's machine at a provider", () => {
  const ACCOUNT = { name: "hetzner-prod", provider: "hetzner", token_hint: "...wxyz", note: "", created_at: "2026-10-01T10:00:00+00:00", created_by: "yago", verified_at: null, last_used_at: null };

  it("links it to the machine the provider knows, with the account and the id", { timeout: 40_000 }, async () => {
    const backend = backendWith({
      "GET /api/fleet/cloud/accounts": () => json(200, [ACCOUNT]),
      "POST /api/fleet/cloud/servers/web-2/link": () => json(200, { node: "web-2", account: "hetzner-prod", provider: "hetzner", provider_id: "4711" }),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Link to a cloud machine");
    const dialog = await screen.findByRole("dialog", { name: "Link web-2 to its machine at a provider" });
    await user.click(within(dialog).getByRole("button", { name: "Link" }));
    expect(await within(dialog).findByText("Write the machine's id.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/cloud/servers/web-2/link")).toHaveLength(0);

    await user.type(within(dialog).getByLabelText(/Machine id/), " 4711 ");
    await user.click(within(dialog).getByRole("button", { name: "Link" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/cloud/servers/web-2/link")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/fleet/cloud/servers/web-2/link")[0]?.body).toEqual({ account: "hetzner-prod", provider_id: "4711", force: false });
    expect(await screen.findByText("web-2 is linked to machine 4711")).toBeInTheDocument();
  });

  it("says a cloud account is needed first, instead of an empty form", { timeout: 40_000 }, async () => {
    backendWith({ "GET /api/fleet/cloud/accounts": () => json(200, []) });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Link to a cloud machine");
    const dialog = await screen.findByRole("dialog", { name: "Link web-2 to its machine at a provider" });
    expect(await within(dialog).findByText("No cloud account yet. Add one in Settings, Cloud.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Link" })).toBeDisabled();
  });
});

const COMMAND = "noust fleet authorize --central-key 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINEW noust-central@nas' --name nas";

describe("a new key for a server", () => {
  it("makes the key, shows the command for the server, and switches only with the code it printed", { timeout: 40_000 }, async () => {
    const backend = backendWith({
      "POST /api/nodes/web-2/rekey": (call) =>
        (call.body as { join_code?: string }).join_code === undefined
          ? json(200, { name: "web-2", step: "authorize", authorize_command: COMMAND, node: null })
          : json(200, { name: "web-2", step: "done", authorize_command: null, node: { name: "web-2" } }),
    });
    const { user, container } = renderConsole("/fleet/servers");

    await openTool(user, "Rotate its key");
    const dialog = await screen.findByRole("dialog", { name: "Give web-2 a new key" });
    // Nothing is asked of the server until the key is made.
    expect(within(dialog).queryByLabelText(/Join code it printed/)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create the new key" }));

    expect(await within(dialog).findByText(COMMAND)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/nodes/web-2/rekey")[0]?.body).toEqual({});
    await expectNoAxeViolations(container, { page: true });

    // The code is needed before the switch.
    await user.click(within(dialog).getByRole("button", { name: "Switch to the new key" }));
    expect(await within(dialog).findByText("Paste the join code the server printed.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/nodes/web-2/rekey")).toHaveLength(1);

    await user.type(within(dialog).getByLabelText(/Join code it printed/), "noust-join:abc");
    await user.click(within(dialog).getByRole("button", { name: "Switch to the new key" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/nodes/web-2/rekey")).toHaveLength(2);
    });
    expect(backend.callsTo("POST /api/nodes/web-2/rekey")[1]?.body).toEqual({ join_code: "noust-join:abc" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Give web-2 a new key" })).not.toBeInTheDocument();
    });
  });

  it("says why the switch did not happen, in the server's words, and keeps the dialog for another try", { timeout: 40_000 }, async () => {
    backendWith({
      "POST /api/nodes/web-2/rekey": (call) =>
        (call.body as { join_code?: string }).join_code === undefined
          ? json(200, { name: "web-2", step: "authorize", authorize_command: COMMAND, node: null })
          : problem(502, "node_unreachable", "web-2 did not answer with the new key", { output: "Permission denied (publickey)." }),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Rotate its key");
    const dialog = await screen.findByRole("dialog", { name: "Give web-2 a new key" });
    await user.click(within(dialog).getByRole("button", { name: "Create the new key" }));
    await user.type(await within(dialog).findByLabelText(/Join code it printed/), "noust-join:abc");
    await user.click(within(dialog).getByRole("button", { name: "Switch to the new key" }));

    expect(await within(dialog).findByText("Could not switch web-2 to the new key")).toBeInTheDocument();
    expect(within(dialog).getByText("web-2 did not answer with the new key")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Join code it printed/)).toHaveValue("noust-join:abc");
  });
});

describe("who has a server", () => {
  const TEAMS = {
    teams: [
      { id: 1, slug: "acme", name: "Acme", role: "owner", active: true },
      { id: 2, slug: "globex", name: "Globex", role: "owner", active: false },
    ],
    active: "acme",
  };

  it("gives the server to a team and says so", { timeout: 40_000 }, async () => {
    const backend = backendWith({
      "GET /api/teams/mine": () => json(200, TEAMS),
      "PUT /api/nodes/web-2/team": () => json(200, { name: "web-2", team: "globex" }),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Give it to a team");
    const dialog = await screen.findByRole("dialog", { name: "Who has web-2" });
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(await within(dialog).findByRole("combobox", { name: /^Team/ }));
    await user.click(await screen.findByRole("option", { name: "Globex" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/nodes/web-2/team")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/nodes/web-2/team")[0]?.body).toEqual({ team: "globex" });
  });

  it("gives it back to the instance with a null", { timeout: 40_000 }, async () => {
    const backend = backendWith({
      "GET /api/teams/mine": () => json(200, TEAMS),
      "GET /api/nodes": () => json(200, { items: [{ name: "web-2", team: "acme" }] }),
      "PUT /api/nodes/web-2/team": () => json(200, { name: "web-2", team: null }),
    });
    const { user } = renderConsole("/fleet/servers");

    await openTool(user, "Give it to a team");
    const dialog = await screen.findByRole("dialog", { name: "Who has web-2" });
    const select = await within(dialog).findByRole("combobox", { name: /^Team/ });
    await waitFor(() => {
      expect(select).toHaveTextContent("Acme");
    });
    await user.click(select);
    await user.click(await screen.findByRole("option", { name: "No team (the instance's)" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/nodes/web-2/team")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/nodes/web-2/team")[0]?.body).toEqual({ team: null });
  });
});
