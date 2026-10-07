import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DB1, WEB2, centralSession, fleetRoutes } from "../testFixtures";

const FINGERPRINT = "SHA256:Zk3w0xV8mUq1yJdq9Pj2o8mI1d1d2c0eE5jK3fT7QwA";
const PASSWORD = "pa55word-for-root";
const KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----\n";

const JOB = {
  id: "enroll-1",
  type: "node_enroll",
  name: "Enroll web-3",
  description: "",
  status: "completed",
  progress: 100,
  total_steps: 100,
  current_step: "",
  created_at: "2026-10-06T10:00:00",
  logs: [{ message: "Connecting to 203.0.113.9" }, { message: "Installing Noust" }],
  result: { name: "web-3" },
  error: null,
  metadata: { kind: "node_enroll", node: "web-3" },
};

const WEB3 = { ...WEB2, name: "web-3", ssh_host: "203.0.113.9", version: "3.4.0", status: "reachable" };

function enrollRoutes(extra: Record<string, RouteHandler> = {}, nodes: unknown[] = [WEB2, DB1]): Record<string, RouteHandler> {
  return {
    ...fleetRoutes(centralSession({}, {}), nodes),
    "POST /api/fleet/enroll/host-key": () => json(200, { host: "203.0.113.9", port: 22, key_type: "ssh-ed25519", fingerprint: FINGERPRINT }),
    "POST /api/fleet/enroll": () => json(202, { job_id: JOB.id, status: "completed", message: "Queued", job: JOB }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0 }),
    ...extra,
  };
}

async function openGuided(extra: Record<string, RouteHandler> = {}, nodes: unknown[] = [WEB2, DB1]) {
  const backend = fakeBackend(enrollRoutes(extra, nodes));
  const harness = renderConsole("/settings/servers");
  await harness.user.click(await screen.findByRole("button", { name: "Add a server" }));
  const dialog = await screen.findByRole("dialog", { name: "Add a server" });
  return { ...harness, backend, dialog };
}

/** Fills the server's details with a password, up to "Check the host key". */
async function fill(user: ReturnType<typeof renderConsole>["user"], dialog: HTMLElement) {
  await user.type(within(dialog).getByLabelText(/^Name/), "web-3");
  await user.type(within(dialog).getByLabelText(/^Address/), "203.0.113.9");
  await user.type(within(dialog).getByLabelText("Password"), PASSWORD);
}

describe("adding a server from an address and a credential", () => {
  it("asks for the server and a way in, and says the credential is used once and kept nowhere", { timeout: 30_000 }, async () => {
    const { dialog } = await openGuided();
    expect(within(dialog).getByText("Used once, kept nowhere")).toBeInTheDocument();
    expect(within(dialog).getByText(/This central does not store it/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(within(dialog).getByLabelText(/^Account/)).toHaveValue("root");
    expect(within(dialog).getByLabelText(/^SSH port/)).toHaveValue("22");
    await expectNoAxeViolations(dialog);
  });

  it("checks the details before asking the server anything", { timeout: 30_000 }, async () => {
    const { user, dialog, backend } = await openGuided();
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    expect(await within(dialog).findByText(/Use 1 to 32 lower-case letters/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/^Name/), "web-3");
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    expect(await within(dialog).findByText("Enter the server's address.")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/^Address/), "203.0.113.9");
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    expect(await within(dialog).findByText("Enter the account's password.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/enroll/host-key")).toHaveLength(0);
  });

  it("shows the host key's fingerprint and sends nothing more until it is confirmed", { timeout: 30_000 }, async () => {
    const { user, dialog, backend } = await openGuided();
    await fill(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    expect(await within(dialog).findByText(FINGERPRINT)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/enroll/host-key")[0]?.body).toEqual({ host: "203.0.113.9", port: 22 });
    // The credential has not left the page yet: only the address was sent.
    expect(JSON.stringify(backend.callsTo("POST /api/fleet/enroll/host-key")[0]?.body)).not.toContain(PASSWORD);
    expect(within(dialog).getByText("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add the server" }));
    expect(await within(dialog).findByText("Compare the fingerprint with the server's own, then tick the box.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/enroll")).toHaveLength(0);
  });

  it("enrolls with the confirmed fingerprint and the password, follows the job, and forgets the password", { timeout: 40_000 }, async () => {
    const nodes: unknown[] = [WEB2, DB1];
    const { user, dialog, backend } = await openGuided(
      {
        "POST /api/fleet/enroll": () => {
          nodes.push(WEB3);
          return json(202, { job_id: JOB.id, status: "completed", message: "Queued", job: JOB });
        },
      },
      nodes,
    );
    await fill(user, dialog);
    await user.click(within(dialog).getByRole("combobox", { name: /This central may/ }));
    await user.click(await screen.findByRole("option", { name: /Operate and update/ }));
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    await within(dialog).findByText(FINGERPRINT);
    await user.click(within(dialog).getByRole("checkbox", { name: /The fingerprint is the server's own/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add the server" }));
    await waitFor(() => expect(backend.callsTo("POST /api/fleet/enroll")).toHaveLength(1));
    expect(backend.callsTo("POST /api/fleet/enroll")[0]?.body).toEqual({
      name: "web-3",
      host: "203.0.113.9",
      port: 22,
      ssh_user: "root",
      host_key_fingerprint: FINGERPRINT,
      access: "deploy",
      allow_host_access: false,
      install: true,
      replace_token: false,
      password: PASSWORD,
      private_key: null,
      passphrase: null,
    });
    expect(await within(dialog).findByText("Added web-3")).toBeInTheDocument();
    expect(await within(dialog).findByText("web-3 is part of the fleet")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Output of adding web-3")).toBeInTheDocument();
    expect(await within(dialog).findByText("Open web-3")).toBeInTheDocument();
    // Nothing of the credential is left on the page.
    expect(document.body.textContent).not.toContain(PASSWORD);
    expect(screen.queryByDisplayValue(PASSWORD)).not.toBeInTheDocument();
    expect(within(dialog).getByText("The credential left this page when it was sent.")).toBeInTheDocument();
  });

  it("enrolls with a private key and its passphrase, and no password", { timeout: 40_000 }, async () => {
    const { user, dialog, backend } = await openGuided();
    await user.type(within(dialog).getByLabelText(/^Name/), "web-3");
    await user.type(within(dialog).getByLabelText(/^Address/), "203.0.113.9");
    await user.click(within(dialog).getByRole("radio", { name: /^A private key/ }));
    await user.click(within(dialog).getByLabelText(/^Private key/));
    await user.paste(KEY);
    await user.type(within(dialog).getByLabelText(/^Key passphrase/), "open sesame");
    await user.click(within(dialog).getByRole("button", { name: "More options" }));
    await user.click(within(dialog).getByRole("checkbox", { name: /Also manage the server itself/ }));
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    await within(dialog).findByText(FINGERPRINT);
    await user.click(within(dialog).getByRole("checkbox", { name: /The fingerprint is the server's own/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add the server" }));
    await waitFor(() => expect(backend.callsTo("POST /api/fleet/enroll")).toHaveLength(1));
    expect(backend.callsTo("POST /api/fleet/enroll")[0]?.body).toMatchObject({ password: null, private_key: KEY, passphrase: "open sesame", allow_host_access: true });
  });

  it("refuses a key that does not start like one", { timeout: 30_000 }, async () => {
    const { user, dialog, backend } = await openGuided();
    await user.type(within(dialog).getByLabelText(/^Name/), "web-3");
    await user.type(within(dialog).getByLabelText(/^Address/), "203.0.113.9");
    await user.click(within(dialog).getByRole("radio", { name: /^A private key/ }));
    await user.click(within(dialog).getByLabelText(/^Private key/));
    await user.paste("ssh-ed25519 AAAA this-is-a-public-key");
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    expect(await within(dialog).findByText("That does not look like a private key: it starts with -----BEGIN.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/enroll/host-key")).toHaveLength(0);
  });

  it("shows why the server could not be reached, in ssh's own words", { timeout: 30_000 }, async () => {
    const { user, dialog } = await openGuided({
      "POST /api/fleet/enroll/host-key": () => problem(502, "node_unreachable", "Could not read a host key from 203.0.113.9:22", { hint: "Check the address and that sshd answers.", output: "ssh-keyscan: connect: Connection refused" }),
    });
    await fill(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    expect(await within(dialog).findByText("Could not ask the server for its host key")).toBeInTheDocument();
    expect(within(dialog).getByText("Could not read a host key from 203.0.113.9:22")).toBeInTheDocument();
    expect(within(dialog).getByText(/Connection refused/)).toBeInTheDocument();
  });

  it("says what failed, verbatim, when the job fails, and goes back to the details with the credential gone", { timeout: 40_000 }, async () => {
    const failed = { ...JOB, status: "failed", result: null, error: "Permission denied (publickey,password).", logs: [{ message: "Connecting to 203.0.113.9" }, { message: "Permission denied (publickey,password)." }] };
    const { user, dialog } = await openGuided({
      "POST /api/fleet/enroll": () => json(202, { job_id: JOB.id, status: "failed", message: "Queued", job: failed }),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, failed),
    });
    await fill(user, dialog);
    await user.click(within(dialog).getByRole("button", { name: "Check the host key" }));
    await within(dialog).findByText(FINGERPRINT);
    await user.click(within(dialog).getByRole("checkbox", { name: /The fingerprint is the server's own/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add the server" }));
    expect(await within(dialog).findByText("Adding web-3 failed")).toBeInTheDocument();
    expect(within(dialog).getAllByText("Permission denied (publickey,password).").length).toBeGreaterThan(0);
    await user.click(within(dialog).getByRole("button", { name: "Back to the server's details" }));
    expect(within(dialog).getByLabelText(/^Name/)).toHaveValue("web-3");
    expect(within(dialog).getByLabelText("Password")).toHaveValue("");
  });

  it("leads to the way that gives the central no credential at all", { timeout: 30_000 }, async () => {
    const { user, dialog } = await openGuided({ "GET /api/nodes/web-3/key": () => json(200, { name: "web-3", public_key: "ssh-ed25519 AAAA noust-central@nas", authorize_command: "noust fleet authorize --name nas" }) });
    await user.click(within(dialog).getByRole("button", { name: "Authorize it with a command on the server instead" }));
    expect(await screen.findByText("Run it on the other server: the one you are adding")).toBeInTheDocument();
  });

  it("is read in Spanish", { timeout: 30_000 }, async () => {
    const { dialog } = await openGuided();
    await act(() => setLocale("es"));
    expect(within(dialog).getByText("Se usa una vez y no se guarda en ningún sitio")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
  });
});
