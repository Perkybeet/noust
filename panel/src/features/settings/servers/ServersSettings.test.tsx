import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { FakeBackend } from "../../../test/fakes";
import { DB1, SSH_TIMEOUT, WEB2, centralSession, fleetRoutes } from "../../fleet/testFixtures";

const KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGk3 noust-central@nas";
const AUTHORIZE = `noust fleet authorize --central-key '${KEY}' --name nas`;
const JOIN_CODE = "noust-join:v1:eyJob3N0X2tleSI6Ii4uLiIsInRva2VuIjoibm91c3RfdG9rX3NlY3JldCJ9";

/** A central with web-2 and db-1 that registers `web-3`, asking for sudo mode first. */
function serversBackend({ totp = true }: { totp?: boolean } = {}): FakeBackend {
  let elevated = false;
  const nodes: unknown[] = [WEB2, DB1];
  const backend = fakeBackend({
    ...fleetRoutes(centralSession({}, { totp_enabled: totp }), nodes),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "GET /api/nodes/web-3/key": () => json(200, { name: "web-3", public_key: KEY, authorize_command: AUTHORIZE }),
    "GET /api/nodes/web-2/key": () => json(200, { name: "web-2", public_key: KEY, authorize_command: AUTHORIZE.replace("--name nas", "--name nas") }),
    "POST /api/nodes": (call) => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      const body = call.body as { name: string; ssh_target: string; join_code: string };
      const node = { ...WEB2, name: body.name, ssh_host: "web3.example.com", version: "2.0.0", status: "reachable" };
      nodes.push(node);
      return json(201, node);
    },
    "DELETE /api/nodes/web-2": () => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      nodes.splice(0, 1);
      return json(200, {
        name: "web-2",
        messages: ["Revoked the fleet token on web-2.", "Closed the tunnel to web-2."],
      });
    },
    "POST /api/nodes/db-1/test": () =>
      json(200, { reachable: false, status: "unreachable", version: "1.9.0", latency_ms: null, error: "The tunnel to db-1 did not open", details: SSH_TIMEOUT }),
    "POST /api/nodes/web-2/test": () =>
      json(200, { reachable: true, status: "reachable", version: "2.0.0", latency_ms: 42, error: null, details: null }),
  });
  return backend;
}

async function confirmItsYou(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
}

async function expectToast(text: string): Promise<void> {
  await waitFor(() => {
    expect([...document.querySelectorAll(".toast")].some((toast) => toast.textContent.includes(text))).toBe(true);
  });
}

/** Steps 1 and 2 of adding web-3, up to pressing "Add server". */
async function enroll(user: ReturnType<typeof renderConsole>["user"], address = "root@web3.example.com"): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Add a server" }));
  const dialog = await screen.findByRole("dialog", { name: "Add a server" });
  expect(dialog).toHaveAccessibleDescription("Step 1 of 3");
  await user.type(within(dialog).getByLabelText(/^Name/), "web-3");
  await user.click(within(dialog).getByRole("button", { name: "Show the command" }));
  expect(await within(dialog).findByTestId("authorize-command")).toHaveTextContent(AUTHORIZE);
  expect(within(dialog).getByRole("button", { name: "Copy command" })).toBeInTheDocument();
  expect(within(dialog).getByText(/It cannot open a shell or run anything on web-3/)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "I ran it: next" }));

  expect(dialog).toHaveAccessibleDescription("Step 2 of 3");
  const code = within(dialog).getByLabelText("Join code");
  // The code carries a token: never shown on screen.
  expect(code).toHaveAttribute("type", "password");
  await user.type(code, JOIN_CODE);
  await user.type(within(dialog).getByLabelText("SSH address"), address);
  await user.click(within(dialog).getByRole("button", { name: "Add server" }));
  return dialog;
}

describe("Settings > Servers", () => {
  it("lists the servers with their address and status, and passes axe", { timeout: 20_000 }, async () => {
    serversBackend();
    const { container } = renderConsole("/settings/servers");
    await screen.findByText("root@web2.example.com");
    const table = screen.getByRole("region", { name: "Servers this central manages" });
    expect(within(table).getByText("root@db1.example.com:2222")).toBeInTheDocument();
    expect(within(table).getByText("Unreachable")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Open web-2" })).toHaveAttribute("href", "/n/web-2");
    await expectNoAxeViolations(container, { page: true });
  });

  it("adds a server in three steps: the command to run on it, the join code, the result", { timeout: 40_000 }, async () => {
    const backend = serversBackend();
    const { user } = renderConsole("/settings/servers");
    const dialog = await enroll(user);
    await expectNoAxeViolations(dialog);
    await confirmItsYou(user);

    await within(dialog).findByText("web-3 is part of the fleet");
    expect(dialog).toHaveAccessibleDescription("Step 3 of 3");
    expect(within(dialog).getByText("Reachable")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Open web-3" })).toHaveAttribute("href", "/n/web-3");
    expect(backend.callsTo("POST /api/nodes").at(-1)?.body).toEqual({ name: "web-3", ssh_target: "root@web3.example.com", join_code: JOIN_CODE });
    await expectToast("Added web-3");
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(await screen.findByText("web-3")).toBeInTheDocument();
  });

  it("checks the name and the SSH address before asking the central", { timeout: 20_000 }, async () => {
    const backend = serversBackend();
    const { user } = renderConsole("/settings/servers");
    await user.click(await screen.findByRole("button", { name: "Add a server" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a server" });
    await user.type(within(dialog).getByLabelText(/^Name/), "Web 3");
    await user.click(within(dialog).getByRole("button", { name: "Show the command" }));
    expect(await within(dialog).findByText(/Use 1 to 32 lower-case letters/)).toBeInTheDocument();
    expect(backend.calls.some((call) => call.path.endsWith("/key"))).toBe(false);
  });

  it("shows a refused registration verbatim, with the way to turn on two-factor sign-in", { timeout: 30_000 }, async () => {
    const backend = serversBackend({ totp: false });
    backend.on("POST /api/nodes", () =>
      problem(400, "nodeerror", "This central may not register nodes yet", {
        hint: "Two-factor sign-in is not enabled on this central, and whoever signs in here reaches every node it manages.",
      }),
    );
    const { user } = renderConsole("/settings/servers");
    // Said before anything is tried, too.
    expect(await screen.findByText("Turn on two-factor sign-in first")).toBeInTheDocument();
    const dialog = await enroll(user);
    await within(dialog).findByText("Could not add web-3");
    expect(within(dialog).getByText("This central may not register nodes yet")).toBeInTheDocument();
    expect(within(dialog).getByText(/Two-factor sign-in is not enabled on this central/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Set up two-factor authentication" })).toHaveAttribute("href", "/settings/security");
    // Back to the code, with what was typed kept.
    await user.click(within(dialog).getByRole("button", { name: "Back to the join code" }));
    expect(within(dialog).getByLabelText("SSH address")).toHaveValue("root@web3.example.com");
  });

  it("shows ssh's own words when the server cannot be reached", { timeout: 30_000 }, async () => {
    const backend = serversBackend();
    backend.on("POST /api/nodes", () =>
      problem(502, "node_unreachable", "The tunnel to web-3 did not open", {
        hint: "Check that the node is up and that its SSH address and host key are the ones the central recorded; the output below is ssh's own.",
        output: "ssh: Could not resolve hostname web3.example.con: Name or service not known",
      }),
    );
    // Sudo mode is already on for this one.
    backend.on("POST /api/auth/elevate", () => json(200, {}));
    const { user } = renderConsole("/settings/servers");
    const dialog = await enroll(user, "root@web3.example.con");
    await within(dialog).findByText("Could not add web-3");
    expect(within(dialog).getByText("ssh: Could not resolve hostname web3.example.con: Name or service not known")).toBeInTheDocument();
    expect(within(dialog).getByText(/Check that the node is up/)).toBeInTheDocument();
  });

  it("removes a server after its name is typed and sudo mode, saying what to run if it is unreachable", { timeout: 30_000 }, async () => {
    const backend = serversBackend();
    const { user } = renderConsole("/settings/servers");
    await user.click(await screen.findByRole("button", { name: "Remove web-2" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Remove web-2?" });
    expect(confirm).toHaveTextContent(/revokes its token there/);
    expect(await within(confirm).findByText("noust fleet deauthorize --name nas")).toBeInTheDocument();
    await expectNoAxeViolations(confirm);
    await user.type(within(confirm).getByRole("textbox"), "web-2");
    await user.click(within(confirm).getByRole("button", { name: "Remove server" }));
    await confirmItsYou(user);

    const result = await screen.findByRole("dialog", { name: "Removed web-2" });
    expect(within(result).getByText(/Revoked the fleet token on web-2\./)).toBeInTheDocument();
    expect(backend.callsTo("DELETE /api/nodes/web-2").at(-1)?.search.get("revoke")).toBe("true");
    await user.click(within(result).getByRole("button", { name: "Done" }));
    const table = screen.getByRole("region", { name: "Servers this central manages" });
    await waitFor(() => {
      expect(within(table).queryByText("web-2")).not.toBeInTheDocument();
    });
  });

  it("tests a server and says how it went, in ssh's words when it failed", { timeout: 20_000 }, async () => {
    serversBackend();
    const { user } = renderConsole("/settings/servers");
    await user.click(await screen.findByRole("button", { name: "Test web-2" }));
    await expectToast("web-2 answered in 42 ms");
    await user.click(screen.getByRole("button", { name: "Test db-1" }));
    await expectToast("db-1 did not answer");
    await expectToast(SSH_TIMEOUT);
  });

  it("speaks Spanish through the whole flow, with no accessibility violations", { timeout: 40_000 }, async () => {
    serversBackend();
    const { user, container } = renderConsole("/settings/servers");
    await screen.findByRole("region", { name: "Servers this central manages" });
    await act(async () => {
      await setLocale("es");
    });
    const table = await screen.findByRole("region", { name: "Servidores que gestiona esta central" });
    expect(within(table).getByText("Dirección SSH")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
    await user.click(screen.getByRole("button", { name: "Añadir un servidor" }));
    const dialog = await screen.findByRole("dialog", { name: "Añadir un servidor" });
    expect(dialog).toHaveAccessibleDescription("Paso 1 de 3");
    await user.type(within(dialog).getByLabelText(/^Nombre/), "web-3");
    await user.click(within(dialog).getByRole("button", { name: "Mostrar la orden" }));
    // The command is the system's: never translated.
    expect(await within(dialog).findByTestId("authorize-command")).toHaveTextContent(AUTHORIZE);
    expect(within(dialog).getByText("Por qué es seguro")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Ya lo ejecuté: siguiente" }));
    expect(within(dialog).getByLabelText("Código de unión")).toHaveAttribute("type", "password");
    await expectNoAxeViolations(dialog);
  });
});
