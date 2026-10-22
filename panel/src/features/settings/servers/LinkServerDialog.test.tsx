import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { FakeBackend, RouteHandler } from "../../../test/fakes";
import { LINK_FIELDS, linkCodeOf } from "../../fleet/link.test";
import { WEB2, centralSession, fleetRoutes } from "../../fleet/testFixtures";
import { defaultCentralHost } from "./LinkServerDialog";

// A dialog of three steps over four requests is slow to settle on a busy machine.
configure({ asyncUtilTimeout: 5000 });

const FINGERPRINT = "SHA256:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU";
const COMMAND =
  "noust fleet link --name nas --as web-5 --host nas.example.com --ssh-port 2222 --host-key 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB' --remote-port 24001 --invite 0123456789abcdef0123456789abcdef";

const READY = { ready: true, problems: [], account: true, ssh_port: 2222, host_key_fingerprint: FINGERPRINT, links: [], setup_command: "noust node link setup" };

const INVITE = {
  node: "web-5",
  central: "nas",
  host: "nas.example.com",
  ssh_port: 2222,
  port: 24001,
  host_key_fingerprint: FINGERPRINT,
  expires_at: "2099-10-14T12:00:00+00:00",
  command: COMMAND,
};

const LINKED = {
  ...WEB2,
  name: "web-5",
  ssh_host: "127.0.0.1",
  ssh_port: 24001,
  ssh_user: "noust-link",
  host_key: "",
  version: "3.4.0",
  link: { port: 24001, state: "active", central_host: "nas.example.com", central_port: 2222, created_at: "2026-10-13T12:00:00+00:00", activated_at: "2026-10-13T12:01:00+00:00" },
  tunnel: { open: true, local_port: 24001, since: null, last_error: null, failures: 0, transport: "link" },
};

function linkBackend(extra: Record<string, RouteHandler> = {}, status: unknown = READY): FakeBackend {
  return fakeBackend({
    ...fleetRoutes(centralSession({}, { totp_enabled: true })),
    "GET /api/fleet/link": () => json(200, status),
    "POST /api/fleet/link/invites": () => json(201, INVITE),
    "DELETE /api/fleet/link/invites/web-5": () => new Response(null, { status: 204 }),
    "POST /api/fleet/link/accept": () => json(201, LINKED),
    ...extra,
  });
}

async function openLink(user: ReturnType<typeof renderConsole>["user"]): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Add a server" }));
  await screen.findByRole("dialog", { name: "Add a server" });
  await user.click(screen.getByRole("button", { name: "It cannot be reached from outside: let it dial this central instead" }));
  return screen.findByRole("dialog", { name: "Add a server behind NAT" });
}

async function invite(user: ReturnType<typeof renderConsole>["user"], dialog: HTMLElement): Promise<void> {
  await user.type(within(dialog).getByLabelText(/^Name/), "web-5");
  await user.type(within(dialog).getByLabelText(/^Address the server dials/), "nas.example.com");
  await user.click(within(dialog).getByRole("button", { name: "Show the command" }));
  await within(dialog).findByTestId("link-command");
}

describe("Add a server behind NAT", () => {
  it("is one step away from adding a server", { timeout: 30_000 }, async () => {
    linkBackend();
    const { user } = renderConsole("/settings/servers");
    const dialog = await openLink(user);
    expect(within(dialog).getByText("Run it on the other server: the one you are adding")).toBeInTheDocument();
  });

  it("says what this central lacks, and the command that fixes it, before it invites anyone", { timeout: 30_000 }, async () => {
    const backend = linkBackend(
      {},
      { ...READY, ready: false, account: false, problems: ["The account noust-link does not exist yet: run 'noust node link setup' as root to create it and restrict it in sshd."] },
    );
    const { user } = renderConsole("/settings/servers");
    const dialog = await openLink(user);

    expect(await within(dialog).findByText("This central is not ready for servers that dial in")).toBeInTheDocument();
    expect(within(dialog).getByText(/The account noust-link does not exist yet/)).toBeInTheDocument();
    expect(within(dialog).getByText("noust node link setup")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Show the command" })).toBeDisabled();
    expect(backend.callsTo("POST /api/fleet/link/invites")).toHaveLength(0);
  });

  it("checks the name and the address before asking the central", { timeout: 30_000 }, async () => {
    const backend = linkBackend();
    const { user } = renderConsole("/settings/servers");
    const dialog = await openLink(user);
    await within(dialog).findByText(/^Address the server dials/);

    await user.type(within(dialog).getByLabelText(/^Name/), "Web 5");
    await user.click(within(dialog).getByRole("button", { name: "Show the command" }));
    expect(await within(dialog).findByText(/Use 1 to 32 lower-case letters/)).toBeInTheDocument();

    await user.clear(within(dialog).getByLabelText(/^Name/));
    await user.type(within(dialog).getByLabelText(/^Name/), "web-5");
    await user.type(within(dialog).getByLabelText(/^Address the server dials/), "x.example.com; reboot");
    await user.click(within(dialog).getByRole("button", { name: "Show the command" }));
    expect(await within(dialog).findByText("Use a host name such as nas.example.com, or an IP address.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/link/invites")).toHaveLength(0);
  });

  it("walks the three steps: the command for the server, its code, the result", { timeout: 60_000 }, async () => {
    const backend = linkBackend();
    const { user, container } = renderConsole("/settings/servers");
    const dialog = await openLink(user);
    await invite(user, dialog);

    expect(backend.callsTo("POST /api/fleet/link/invites")[0]?.body).toEqual({ name: "web-5", host: "nas.example.com" });
    // The command is whole, with the key it pins; the central's own fingerprint is there to compare.
    expect(within(dialog).getByTestId("link-command")).toHaveTextContent(COMMAND);
    expect(within(dialog).getByText(FINGERPRINT)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Copy command" })).toBeInTheDocument();
    expect(within(dialog).getByText("Why this is safe")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "I ran it: continue" }));
    const field = within(dialog).getByLabelText("Link code");
    // The code carries a token: never shown on screen.
    expect(field).toHaveAttribute("type", "password");
    await user.click(field);
    await user.paste(linkCodeOf(LINK_FIELDS));
    expect(await within(dialog).findByText("web-5")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Add server" }));

    await within(dialog).findByText("web-5 is part of the fleet");
    const sent = backend.callsTo("POST /api/fleet/link/accept")[0]?.body as { name: string; link_code: string };
    expect(sent.name).toBe("web-5");
    expect(sent.link_code).toBe(linkCodeOf(LINK_FIELDS));
    // It has no SSH address: what is shown is the port this central listens on for it.
    expect(within(dialog).getByText("Dials in: port 24001 of this central")).toBeInTheDocument();
    expect(container).not.toHaveTextContent(LINK_FIELDS.token);
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
  });

  it("says when a code is for another server or another invitation, before sending it", { timeout: 40_000 }, async () => {
    const backend = linkBackend();
    const { user } = renderConsole("/settings/servers");
    const dialog = await openLink(user);
    await invite(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "I ran it: continue" }));

    await user.click(within(dialog).getByLabelText("Link code"));
    await user.paste(linkCodeOf({ ...LINK_FIELDS, node_name: "web-6", central: "other" }));

    expect(await within(dialog).findByText(/This code is for a server called web-6, not web-5/)).toBeInTheDocument();
    expect(within(dialog).getByText(/This code was made for the central other/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/link/accept")).toHaveLength(0);
  });

  it("tells a join code from a link code, and sends nothing", { timeout: 40_000 }, async () => {
    const backend = linkBackend();
    const { user } = renderConsole("/settings/servers");
    const dialog = await openLink(user);
    await invite(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "I ran it: continue" }));

    await user.click(within(dialog).getByLabelText("Link code"));
    await user.paste("noust-join:v1:eyJ4IjoxfQ");
    await user.click(within(dialog).getByRole("button", { name: "Add server" }));

    expect((await within(dialog).findAllByText(/That is a join code, for a server this central reaches over SSH/)).length).toBeGreaterThan(0);
    expect(backend.callsTo("POST /api/fleet/link/accept")).toHaveLength(0);
  });

  it("shows why a server did not connect, in the central's words, and goes back to the code", { timeout: 60_000 }, async () => {
    const backend = linkBackend({
      "POST /api/fleet/link/accept": () =>
        problem(502, "node_unreachable", "web-5 did not connect within 30s", { output: "web-5 is not connected: nothing listens on 127.0.0.1:24001" }),
    });
    const { user } = renderConsole("/settings/servers");
    const dialog = await openLink(user);
    await invite(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "I ran it: continue" }));
    await user.click(within(dialog).getByLabelText("Link code"));
    await user.paste(linkCodeOf(LINK_FIELDS));
    await user.click(within(dialog).getByRole("button", { name: "Add server" }));

    expect(await within(dialog).findByText("Could not add web-5")).toBeInTheDocument();
    expect(within(dialog).getByText(/web-5 did not connect within 30s/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Back to the link code" }));
    expect(within(dialog).getByLabelText("Link code")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/link/accept")).toHaveLength(1);
  });

  it("gives an invitation back when the name changes after it", { timeout: 40_000 }, async () => {
    const backend = linkBackend();
    const { user } = renderConsole("/settings/servers");
    const dialog = await openLink(user);
    await invite(user, dialog);

    await user.type(within(dialog).getByLabelText(/^Name/), "x");

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/fleet/link/invites/web-5")).toHaveLength(1);
    });
    expect(within(dialog).queryByTestId("link-command")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "I ran it: continue" })).toBeInTheDocument();
  });

  it("says in Spanish what it says in English", { timeout: 40_000 }, async () => {
    await act(async () => {
      await setLocale("es");
    });
    linkBackend();
    const { user } = renderConsole("/settings/servers");
    await user.click(await screen.findByRole("button", { name: "Añadir un servidor" }));
    await user.click(await screen.findByRole("button", { name: "No se puede llegar a él desde fuera: que llame él a esta central" }));
    const dialog = await screen.findByRole("dialog", { name: "Añadir un servidor tras un NAT" });
    expect(await within(dialog).findByText("Dirección a la que llama el servidor")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
  });
});

describe("the address a server is dialled at", () => {
  it("starts from the address the console was reached at, unless it is this machine's own", () => {
    expect(defaultCentralHost("nas.example.com")).toBe("nas.example.com");
    expect(defaultCentralHost("localhost")).toBe("");
    expect(defaultCentralHost("127.0.0.1")).toBe("");
    expect(defaultCentralHost("")).toBe("");
  });
});
