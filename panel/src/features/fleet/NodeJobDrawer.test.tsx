import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { FakeWebSocket, fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { JOB, PLAN, fleetRoutes } from "./testFixtures";
import { NOUST_PENDING_STEP, PlanView } from "./BulkActionDialog";
import { outcomeLines } from "./NodeJobDrawer";
import { useT } from "../../i18n";
import { bindT } from "../../i18n/useT";
import { render } from "@testing-library/react";

const NOW = new Date().toISOString();
const STARTED = new Date(Date.now() - 125_000).toISOString();

function node(state: string, extra: Record<string, unknown> = {}) {
  return {
    node: "web-2",
    position: 0,
    batch: 0,
    state,
    reason: null,
    step: "Updated; a reboot is due (not done)",
    node_jobs: ["nj-1"],
    items: [{ scope: "security", packages: 3, reboot_required: true, state: "succeeded" }],
    error: null,
    output: null,
    started_at: STARTED,
    ended_at: NOW,
    requires_elevation: false,
    href: "/n/web-2/activity",
    ...extra,
  };
}

const OS_JOB = {
  ...JOB,
  job_id: "fj-9",
  action: "os_updates",
  title: "Install operating system updates",
  request: { action: "os_updates", nodes: ["web-2"], strategy: { serial: 1 } },
  status: "succeeded",
  summary: { queued: 0, running: 0, succeeded: 1, failed: 0, skipped: 0, unreachable: 0, refused: 0, interrupted: 0 },
  nodes: [node("succeeded")],
};

const NODE_JOB = {
  id: "nj-1",
  type: "os_update",
  name: "Apply security updates",
  description: "",
  status: "completed",
  progress: 100,
  total_steps: 100,
  current_step: "Update finished",
  created_at: STARTED,
  logs: [],
  metadata: {},
  result: { packages: ["openssl", "libssl3", "nginx"], reboot_required: true, stale_services: ["nginx.service"] },
};

const LOG = "[INFO] apt-get -y upgrade openssl libssl3 nginx\nSetting up openssl (3.0.13-0ubuntu3.4) ...\n[INFO] Update finished";

function page(job: object, extra: Record<string, RouteHandler> = {}) {
  // The log viewer sizes its window from offsetHeight, which jsdom reports as 0.
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  const backend = fakeBackend({
    ...fleetRoutes(),
    "GET /api/fleet/jobs/fj-9": () => json(200, job),
    "GET /api/nodes/web-2/api/jobs/nj-1": () => json(200, NODE_JOB),
    "GET /api/nodes/web-2/api/jobs/nj-1/log": () => json(200, { content: LOG, truncated: false }),
    "POST /api/auth/ws-ticket": () => json(200, { ticket: "t1" }),
    ...extra,
  });
  const harness = renderConsole("/fleet/jobs/fj-9");
  return { ...harness, backend };
}

async function openDrawer(user: ReturnType<typeof renderConsole>["user"]) {
  const list = await screen.findByRole("list", { name: "Every server of this action" });
  await user.click(await within(list).findByRole("button", { name: "Details for web-2" }));
  return screen.findByRole("dialog", { name: "This action on web-2" });
}

describe("one server's part of a fleet action (item 61)", { timeout: 30_000 }, () => {
  it("says what a system update did, shows the node's log and leads to the job on the server", async () => {
    const { user } = page(OS_JOB);
    const drawer = await openDrawer(user);
    const outcome = await within(drawer).findByRole("list", { name: "What it did" });
    expect(await within(outcome).findByText("3 packages updated.")).toBeInTheDocument();
    expect(within(outcome).getByText("Updated: openssl, libssl3, nginx.")).toBeInTheDocument();
    expect(within(outcome).getByText("A reboot is due to finish it. Nothing was rebooted.")).toBeInTheDocument();
    // Not an action per application: no list of one nameless item.
    expect(within(drawer).queryByText("Each application")).not.toBeInTheDocument();
    const log = await within(drawer).findByRole("region", { name: "Log of job nj-1 on web-2" });
    expect(await within(log).findByText("Setting up openssl (3.0.13-0ubuntu3.4) ...")).toBeInTheDocument();
    expect(within(drawer).getByText("2m 05s")).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: "Open on web-2" })).toHaveAttribute("href", "/n/web-2/activity?q=nj-1");
    await expectNoAxeViolations(drawer);
  });

  it("says a Noust update from which version to which, and a failure first, verbatim with its fix", async () => {
    const failed = node("failed", {
      items: [],
      node_jobs: ["nj-1"],
      error: { code: "node_error", message: "The update failed on web-2", hint: "Read the output below." },
      output: "E: dpkg was interrupted, you must manually run 'dpkg --configure -a'",
    });
    const { user } = page({ ...OS_JOB, action: "noust_update", status: "failed", nodes: [failed] });
    const drawer = await openDrawer(user);
    const error = within(drawer).getByText("It failed on web-2").closest("[role='alert'], div");
    expect(error).not.toBeNull();
    expect(within(drawer).getByText("The update failed on web-2")).toBeInTheDocument();
    expect(within(drawer).getByText("Read the output below.")).toBeInTheDocument();
    expect(within(drawer).getByText("E: dpkg was interrupted, you must manually run 'dpkg --configure -a'")).toBeInTheDocument();
  });

  it("lists each application only for an action per application, with a way to its deployment", async () => {
    const perApp = node("succeeded", {
      items: [{ domain: "shop.example.com", state: "succeeded", deployment_id: 41 }],
      node_jobs: ["nj-1"],
    });
    const { user } = page({ ...OS_JOB, action: "apps_update", nodes: [perApp] });
    const drawer = await openDrawer(user);
    const apps = within(drawer).getByRole("list", { name: "Each application" });
    expect(within(apps).getByText("shop.example.com")).toBeInTheDocument();
    expect(within(apps).getByRole("link", { name: "Deployment 41" })).toBeInTheDocument();
  });

  it("follows a running job live, over the node's job WebSocket the central relays", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const running = { ...NODE_JOB, status: "running", result: null, logs: [{ timestamp: "2026-10-02T10:00:01", message: "Starting the update in its own unit" }] };
    const { user } = page(
      { ...OS_JOB, status: "running", nodes: [node("running", { ended_at: null, items: [] })] },
      { "GET /api/nodes/web-2/api/jobs/nj-1": () => json(200, running) },
    );
    const drawer = await openDrawer(user);
    await waitFor(() => {
      expect(FakeWebSocket.instances.some((socket) => socket.url.includes("/ws/nodes/web-2/jobs/nj-1"))).toBe(true);
    });
    const socket = FakeWebSocket.instances.find((candidate) => candidate.url.includes("/ws/nodes/web-2/jobs/nj-1"));
    act(() => {
      socket?.open();
      socket?.frame({ type: "update", job: { ...running, logs: [...running.logs, { timestamp: "2026-10-02T10:00:09", message: "Unpacking openssl" }] } });
    });
    const log = await within(drawer).findByRole("region", { name: "Log of job nj-1 on web-2" });
    expect(await within(log).findByText("Unpacking openssl")).toBeInTheDocument();
    expect(within(drawer).getByText("Live")).toBeInTheDocument();
  });

  it("lists each certificate a renewal renewed, with its names and new expiry", async () => {
    const certs = node("succeeded", { items: [], step: "Certificates renewed" });
    const renewed = {
      ...NODE_JOB,
      type: "cert_renew",
      result: {
        domain: null,
        status: "renewed",
        renewed: [
          { name: "shop.example.com", domains: ["shop.example.com", "www.shop.example.com"], expiry: "2027-01-29" },
          { name: "blog.example.com", domains: ["blog.example.com"], expiry: "2027-01-30" },
        ],
      },
    };
    const { user } = page({ ...OS_JOB, action: "certs_renew", nodes: [certs] }, { "GET /api/nodes/web-2/api/jobs/nj-1": () => json(200, renewed) });
    const drawer = await openDrawer(user);
    const outcome = await within(drawer).findByRole("list", { name: "What it did" });
    expect(await within(outcome).findByText("2 certificates renewed.")).toBeInTheDocument();
    expect(within(outcome).getByText(/^shop\.example\.com, www\.shop\.example\.com: valid until /)).toHaveTextContent("2027");
    expect(within(outcome).getByText(/^blog\.example\.com: valid until /)).toBeInTheDocument();
    await expectNoAxeViolations(drawer);
  });

  it("says when no certificate was due, and nothing when an older server does not say", () => {
    expect(outcomeLines(bindT("en"), "certs_renew", [], { status: "renewed", renewed: [] })).toEqual(["No certificate was due for renewal."]);
    expect(outcomeLines(bindT("en"), "certs_renew", [], { status: "renewed" })).toEqual([]);
    expect(outcomeLines(bindT("en"), "certs_renew", [], null)).toEqual([]);
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    const { user } = page(OS_JOB);
    const list = await screen.findByRole("list", { name: "Todos los servidores de esta acción" });
    await user.click(await within(list).findByRole("button", { name: "Detalles de web-2" }));
    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByText("3 paquetes actualizados.")).toBeInTheDocument();
  });
});

function Plan() {
  const t = useT();
  const plan = { ...PLAN, action: "os_updates", nodes: [{ ...PLAN.nodes[0], step: NOUST_PENDING_STEP }] };
  return <PlanView t={t} plan={plan as never} action="os_updates" />;
}

describe("the plan of a system update that includes Noust", () => {
  it("says the server also updates Noust, in the operator's words", () => {
    render(<Plan />);
    expect(screen.getByText("Also updates Noust: its console restarts, and the job waits for it")).toBeInTheDocument();
    expect(screen.getByText(/1 server also updates Noust with its system/)).toBeInTheDocument();
  });
});
