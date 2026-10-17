import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

const NOW = Math.floor(Date.now() / 1000);

const UP_UPTIME = {
  "24h": { checks: 1440, good: 1440, percent: 100, mean_ms: 180, max_ms: 900 },
  "7d": { checks: 10080, good: 10075, percent: 99.95, mean_ms: 181, max_ms: 1200 },
  "30d": { checks: 43200, good: 43100, percent: 99.77, mean_ms: 183, max_ms: 1900 },
  "90d": { checks: 129600, good: 129000, percent: null, mean_ms: null, max_ms: null },
};

function probe(name: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    url: `https://${name}.example.com/health?key=abc123`,
    address: `https://${name}.example.com/health`,
    method: "GET",
    expect_status: "200-399",
    expect_text: "",
    interval_s: 60,
    timeout_s: 10,
    fail_after: 3,
    public: false,
    title: "",
    enabled: true,
    status: { state: "up", since: NOW - 3600, failures: 0, last_checked: NOW - 30, last_status: 200, last_latency_ms: 184, last_error: null },
    uptime: UP_UPTIME,
    ...extra,
  };
}

const DOWN = probe("blog", {
  public: true,
  title: "The blog",
  status: { state: "down", since: NOW - 900, failures: 4, last_checked: NOW - 20, last_status: 503, last_latency_ms: 12, last_error: "HTTP 503 Service Unavailable" },
});

const PAGE = { enabled: true, directory: "/var/lib/noust/status", title: "Status", components: 1, domain: null, site_created: false, certificate_created: false };

const JOB = {
  id: "page-1",
  type: "site_action",
  name: "Publish the status page at status.example.com",
  description: "",
  status: "completed",
  progress: 100,
  total_steps: 100,
  current_step: "",
  created_at: "2026-10-16T10:00:00",
  logs: [{ message: "Writing the site" }, { message: "Obtaining the certificate" }],
  result: { domain: "status.example.com" },
  error: null,
  metadata: { kind: "status_page" },
};
const ACCEPTED = { job_id: JOB.id, status: "completed", message: "Queued", job: JOB };

async function pageAt(items: unknown[] = [probe("shop"), DOWN], page: Record<string, unknown> = PAGE, extra: Record<string, RouteHandler> = {}, heading = "Settings") {
  screenWidth(1440);
  let held = items;
  let status = page;
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/probes": () => json(200, { items: held, down: (held as { status: { state: string } }[]).filter((item) => item.status.state === "down").length, status_page: status }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0 }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "PUT /api/probes/status-page": (call) => {
      status = { ...status, ...(call.body as Record<string, unknown>) };
      return json(200, status);
    },
    "POST /api/probes/status-page/write": () => json(200, { files: ["/var/lib/noust/status/index.html", "/var/lib/noust/status/status.json"] }),
    ...extra,
  });
  const harness = renderConsole("/settings/probes");
  await screen.findByRole("heading", { level: 1, name: heading });
  return {
    ...harness,
    backend,
    replace: (next: unknown[]) => {
      held = next;
    },
  };
}

function row(name: string): HTMLElement {
  const found = within(screen.getByRole("list", { name: "Probes" }))
    .getAllByRole("listitem")
    .find((item) => item.textContent.startsWith(name));
  if (found === undefined) throw new Error(`no row for ${name}`);
  return found;
}

describe("Settings > Probes", () => {
  it("lists each probe with where it stands, what it last answered and its uptime, and says how many are down", { timeout: 20_000 }, async () => {
    await pageAt();
    await screen.findByRole("list", { name: "Probes" });
    expect(screen.getByText("1 address is not answering")).toBeInTheDocument();

    const up = row("shop");
    expect(within(up).getByText("Up")).toHaveAttribute("data-state", "running");
    // The address is shown without its query: it may be a key.
    expect(within(up).getByText("https://shop.example.com/health")).toBeInTheDocument();
    expect(up.textContent).not.toContain("abc123");
    expect(within(up).getByText("200 184 ms")).toBeInTheDocument();
    expect(up.textContent).toContain("24 hours: 100%");
    expect(up.textContent).toContain("7 days: 99.95%");
    expect(up.textContent).toContain("30 days: 99.77%");
    // A window it was never asked over reads as a dash, not as zero.
    expect(up.textContent).toContain("90 days: -");

    const down = row("blog");
    expect(within(down).getByText("Down")).toHaveAttribute("data-state", "failed");
    expect(within(down).getByText("On the status page")).toBeInTheDocument();
    expect(within(down).getByText("503 12 ms: HTTP 503 Service Unavailable")).toBeInTheDocument();
    expect(down.textContent).toContain("Down since");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is in the settings navigation, as the server's own section", { timeout: 20_000 }, async () => {
    await pageAt();
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "Probes" })).toHaveAttribute("aria-current", "page");
  });

  it("explains what a probe is when there is none", { timeout: 20_000 }, async () => {
    await pageAt([]);
    expect(await screen.findByText("No probes yet")).toBeInTheDocument();
    expect(screen.getByText("Everything answers.")).toBeInTheDocument();
  });

  it("asks a probe now and says what it got, verbatim when it was bad", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt([probe("shop"), DOWN], PAGE, {
      "POST /api/probes/shop/check": () => json(200, { ok: true, status: 200, latency_ms: 95, error: null, probe: probe("shop") }),
      "POST /api/probes/blog/check": () => json(200, { ok: false, status: 503, latency_ms: 10, error: "HTTP 503 Service Unavailable", probe: DOWN }),
    });
    await screen.findByRole("list", { name: "Probes" });
    await user.click(within(row("shop")).getByRole("button", { name: "Ask shop now" }));
    expect(await within(row("shop")).findByText("It answered well 200 95 ms")).toBeInTheDocument();
    await user.click(within(row("blog")).getByRole("button", { name: "Ask blog now" }));
    expect(await within(row("blog")).findByText(/It did not answer well 503 10 ms/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/probes/blog/check")).toHaveLength(1);
  });

  it("switches a probe off with the rest of it as it was", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt([probe("shop", { expect_text: "Welcome", interval_s: 120 })], PAGE, {
      "PUT /api/probes/shop": () => json(200, probe("shop", { enabled: false })),
    });
    await screen.findByRole("list", { name: "Probes" });
    await user.click(within(row("shop")).getByRole("switch", { name: "Ask shop" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/probes/shop")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/probes/shop")[0]?.body).toEqual({
      url: "https://shop.example.com/health?key=abc123",
      method: "GET",
      expect_status: "200-399",
      expect_text: "Welcome",
      interval_s: 120,
      timeout_s: 10,
      fail_after: 3,
      public: false,
      title: "",
      enabled: false,
    });
  });

  it("creates a probe from the drawer, and shows the server's refusal beside the field it names", { timeout: 30_000 }, async () => {
    let answers = 0;
    const { user, backend } = await pageAt([probe("blog")], PAGE, {
      "PUT /api/probes/shop": () => {
        answers += 1;
        return answers === 1
          ? problem(422, "validation_error", "The address is not acceptable", { fields: { url: "The address must not carry a user or a password." } })
          : json(200, probe("shop"));
      },
    });
    await screen.findByRole("list", { name: "Probes" });
    await user.click(screen.getByRole("button", { name: "New probe" }));
    const drawer = await screen.findByRole("dialog", { name: "New probe" });
    const save = within(drawer).getByRole("button", { name: "Save probe" });
    // Nothing to send until it has a name and an address.
    expect(save).toBeDisabled();
    await user.type(within(drawer).getByLabelText("Name"), "shop");
    await user.type(within(drawer).getByLabelText("Address"), "https://user:pw@shop.example.com/");
    await user.click(save);
    expect(await within(drawer).findByText("The address must not carry a user or a password.")).toBeInTheDocument();
    // The message is about what was sent: editing the address takes it away.
    await user.clear(within(drawer).getByLabelText("Address"));
    await user.type(within(drawer).getByLabelText("Address"), "https://shop.example.com/health");
    expect(within(drawer).queryByText("The address must not carry a user or a password.")).toBeNull();
    await user.click(within(drawer).getByRole("checkbox", { name: "Show it on the status page" }));
    await user.type(within(drawer).getByLabelText(/^Name on the page/), "Shop");
    await user.click(save);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/probes/shop")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/probes/shop").at(-1)?.body).toEqual({
      url: "https://shop.example.com/health",
      method: "GET",
      expect_status: "200-399",
      expect_text: "",
      interval_s: 60,
      timeout_s: 10,
      fail_after: 3,
      public: true,
      title: "Shop",
      enabled: true,
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "New probe" })).toBeNull();
    });
  });

  it("reads a probe's incidents only when they are asked for", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt([DOWN], PAGE, {
      "GET /api/probes/blog/history": () =>
        json(200, {
          name: "blog",
          incidents: [
            { started_at: NOW - 900, ended_at: null, reason: "HTTP 503 Service Unavailable" },
            { started_at: NOW - 86_400, ended_at: NOW - 86_400 + 462, reason: "timed out" },
          ],
          answers: [],
        }),
    });
    await screen.findByRole("list", { name: "Probes" });
    expect(backend.callsTo("GET /api/probes/blog/history")).toHaveLength(0);
    await user.click(within(row("blog")).getByRole("button", { name: "Incidents" }));
    expect(await within(row("blog")).findByText(/^Down for 1[5-9]m \d\ds and counting$/)).toBeInTheDocument();
    expect(within(row("blog")).getByText("It was down for 7m 42s")).toBeInTheDocument();
    expect(within(row("blog")).getByText("timed out")).toBeInTheDocument();
  });

  it("removes a probe after one question", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt([probe("shop")], PAGE, {
      "DELETE /api/probes/shop": () => new Response(null, { status: 204 }),
    });
    await screen.findByRole("list", { name: "Probes" });
    await user.click(within(row("shop")).getByRole("button", { name: "Remove shop" }));
    const question = await screen.findByRole("alertdialog", { name: "Remove shop?" });
    await user.click(within(question).getByRole("button", { name: "Remove" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/probes/shop")).toHaveLength(1);
    });
  });
});

describe("Settings > Probes: the status page", () => {
  it("keeps the page's files, titles it and writes it now", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt();
    const section = (await screen.findByRole("heading", { name: "Status page" })).closest("section") ?? document.body;
    expect(section.textContent).toContain("Written to /var/lib/noust/status: 1 component.");
    expect(within(section).getByText("It is not on a name of its own: serve the folder above yourself, or put it on one.")).toBeInTheDocument();

    await user.click(within(section).getByRole("switch", { name: "Keep the page's files up to date" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/probes/status-page")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/probes/status-page")[0]?.body).toEqual({ enabled: false });

    const title = within(section).getByLabelText("Page title");
    await user.clear(title);
    await user.type(title, "Acme status");
    await user.click(within(section).getByRole("button", { name: "Save title" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/probes/status-page")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/probes/status-page")[1]?.body).toEqual({ title: "Acme status" });

    await user.click(within(section).getByRole("button", { name: "Write now" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/probes/status-page/write")).toHaveLength(1);
    });
    expect(await screen.findByText("Wrote 2 files")).toBeInTheDocument();
  });

  it("puts the page on a name through a job and shows its output", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt([probe("shop", { public: true })], PAGE, {
      "POST /api/probes/status-page/publish": () => json(202, ACCEPTED),
    });
    await user.click(await screen.findByRole("button", { name: "Publish on a name" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish the status page" });
    // Nothing to send until it has a name.
    expect(within(dialog).getByRole("button", { name: "Publish" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Name"), "status.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Publish" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/probes/status-page/publish")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/probes/status-page/publish")[0]?.body).toEqual({ domain: "status.example.com", skip_dns_check: false });
    expect(await within(dialog).findByText("status.example.com shows the status page")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /Open status\.example\.com/ })).toHaveAttribute("href", "https://status.example.com");
  });

  it("shows the server's refusal to publish verbatim and stays on the name", { timeout: 30_000 }, async () => {
    const { user } = await pageAt([probe("shop", { public: true })], PAGE, {
      "POST /api/probes/status-page/publish": () => problem(400, "validation_error", "status.example.com is an application's name", { hint: "Choose a name of its own." }),
    });
    await user.click(await screen.findByRole("button", { name: "Publish on a name" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish the status page" });
    await user.type(within(dialog).getByLabelText("Name"), "status.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Publish" }));
    expect(await within(dialog).findByText("status.example.com is an application's name")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Name")).toHaveValue("status.example.com");
  });

  it("takes a published page off its name, removing only what publishing made", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt([probe("shop", { public: true })], { ...PAGE, domain: "status.example.com", site_created: true, certificate_created: true }, {
      "DELETE /api/probes/status-page/site": () => json(202, { ...ACCEPTED, job: { ...JOB, name: "Take the status page off status.example.com" } }),
    });
    expect(await screen.findByText(/Published on/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open status\.example\.com/ })).toHaveAttribute("href", "https://status.example.com");
    await user.click(screen.getByRole("button", { name: "Take it off the name" }));
    const dialog = await screen.findByRole("dialog", { name: "Take the status page off its name" });
    expect(within(dialog).getByText(/only those/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Take it off" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/probes/status-page/site")).toHaveLength(1);
    });
    expect(await within(dialog).findByText("status.example.com no longer shows the status page")).toBeInTheDocument();
  });
});

describe("Settings > Probes in Spanish", () => {
  it("reads in Spanish, with no accessibility violations", { timeout: 20_000 }, async () => {
    await act(async () => {
      await setLocale("es");
    });
    await pageAt(undefined, undefined, undefined, "Ajustes");
    expect(await screen.findByText("1 dirección no responde")).toBeInTheDocument();
    expect(screen.getByText("Página de estado")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
