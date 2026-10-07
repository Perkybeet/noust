import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

const JOB = {
  id: "expose-1",
  type: "site_action",
  name: "Publish the console at console.example.com",
  description: "",
  status: "completed",
  progress: 100,
  total_steps: 100,
  current_step: "",
  created_at: "2026-10-06T10:00:00",
  logs: [{ message: "Checking" }, { message: "Obtaining the certificate" }],
  result: { domain: "console.example.com", url: "https://console.example.com", allow_ips: ["203.0.113.0/24"], site_created: true, certificate_created: true, restart: "later", removed: [], notes: ["The console restarts in 4 seconds."] },
  error: null,
  metadata: { kind: "console_expose" },
};
const ACCEPTED = { job_id: JOB.id, status: "completed", message: "Queued", job: JOB };

const PRIVATE = { exposed: false, ens_allows: true, problems: [] };
const PUBLISHED = {
  exposed: true,
  ens_allows: true,
  problems: [],
  domain: "console.example.com",
  url: "https://console.example.com",
  port: 8080,
  scheme: "http",
  allow_ips: ["203.0.113.0/24"],
  site_created: true,
  certificate_created: true,
  proxy_added: "127.0.0.1",
  state: "active",
  created_at: "2026-10-06T09:00:00+00:00",
  created_by: "token",
};

function account(username: string, extra: Record<string, unknown> = {}) {
  return {
    id: 1,
    username,
    display_name: username,
    role: "admin",
    status: "active",
    mfa_enabled: true,
    passkeys: 0,
    backup_codes_remaining: 8,
    created_at: 1_790_000_000,
    created_by: null,
    disabled_at: null,
    disabled_reason: null,
    failures_since_login: 0,
    last_login_at: null,
    last_login_ip: null,
    locked_until: null,
    notice_accepted_at: null,
    password_changed_at: null,
    person_ref: null,
    ...extra,
  };
}

async function pageAt(exposure: unknown, extra: Record<string, RouteHandler> = {}, accounts: unknown[] = [account("ana"), account("dani", { mfa_enabled: false })]) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/console/exposure": () => json(200, exposure),
    "GET /api/auth/accounts": () => json(200, { accounts, conflicts: [] }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0 }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    ...extra,
  });
  const harness = renderConsole("/settings/console");
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  return { ...harness, backend };
}

describe("Settings > Console access", () => {
  it("says the console is private, and what is checked before it can be published, naming the accounts that stop it", async () => {
    await pageAt(PRIVATE);
    expect(await screen.findByText("The console is private")).toBeInTheDocument();
    expect(screen.getByText("1 account signs in with a password alone")).toBeInTheDocument();
    expect(screen.getByText("dani")).toBeInTheDocument();
    expect(screen.queryByText("ana")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Accounts" })).toHaveAttribute("href", "/settings/accounts");
    expect(screen.getByText("It allows publishing the console.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish the console…" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is in the settings navigation, as the server's own section", async () => {
    await pageAt(PRIVATE);
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "Console access" })).toHaveAttribute("aria-current", "page");
  });

  it("says every account is covered when they are, and leaves the check to the server when the accounts cannot be read", async () => {
    await pageAt(PRIVATE, {}, [account("ana")]);
    expect(await screen.findByText("Every account that can sign in has an authenticator or a passkey.")).toBeInTheDocument();
  });

  it("publishes in steps: the name and who may reach it, what Noust will do, then the job", { timeout: 30_000 }, async () => {
    const { user, backend } = await pageAt(PRIVATE, {
      "POST /api/console/exposure/preview": () => json(200, { domain: "console.example.com", steps: ["write the nginx site of console.example.com, proxying to http://127.0.0.1:8080, reachable by only 203.0.113.0/24", "obtain a Let's Encrypt certificate for console.example.com and serve only HTTPS"] }),
      "POST /api/console/exposure": () => json(202, ACCEPTED),
    });
    await user.click(await screen.findByRole("button", { name: "Publish the console…" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish the console" });
    await user.click(within(dialog).getByRole("button", { name: "Preview" }));
    expect(await within(dialog).findByText("Enter the name the console is published at.")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/^Name/), "console.example.com");
    await user.type(within(dialog).getByLabelText(/^Reachable from/), "203.0.113.0/24");
    await user.click(within(dialog).getByRole("button", { name: "Preview" }));
    expect(await within(dialog).findByText(/write the nginx site of console\.example\.com/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/console/exposure/preview")[0]?.body).toEqual({ domain: "console.example.com", allow_ips: ["203.0.113.0/24"], force_ens: false, skip_dns_check: false });
    expect(backend.callsTo("POST /api/console/exposure")).toHaveLength(0);
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Publish the console" }));
    await waitFor(() => expect(backend.callsTo("POST /api/console/exposure")).toHaveLength(1));
    expect(await within(dialog).findByText("The console is published at console.example.com")).toBeInTheDocument();
    expect(within(dialog).getByText("The console restarts in 4 seconds.")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /Open https:\/\/console\.example\.com/ })).toHaveAttribute("href", "https://console.example.com");
  });

  it("shows what the server refuses with, verbatim, and goes no further", async () => {
    const { user, backend } = await pageAt(PRIVATE, {
      "POST /api/console/exposure/preview": () =>
        problem(400, "security_error", "The console is not published while an account can sign in without a second factor", {
          hint: "Enrol a second factor on every account.",
          output: "The account dani signs in with a password alone. Its owner enrols an authenticator or a passkey (Security, after signing in), or the account is disabled (noust user disable dani); then publish the console.",
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Publish the console…" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish the console" });
    await user.type(within(dialog).getByLabelText(/^Name/), "console.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Preview" }));
    expect(await within(dialog).findByText("The console cannot be published")).toBeInTheDocument();
    expect(within(dialog).getByText("The console is not published while an account can sign in without a second factor")).toBeInTheDocument();
    expect(within(dialog).getByText(/The account dani signs in with a password alone/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Preview" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/console/exposure")).toHaveLength(0);
  });

  it("offers to force the ENS profile only where it blocks, and asks for an allow-list with it", async () => {
    const { user } = await pageAt({ ...PRIVATE, ens_allows: false });
    expect(await screen.findByText("The ENS profile keeps the console on loopback. Publishing needs the override, an allow-list and a confirmation, and is audited.")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Publish the console…" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish the console" });
    await user.type(within(dialog).getByLabelText(/^Name/), "console.example.com");
    await user.click(within(dialog).getByRole("checkbox", { name: /Publish although the ENS profile/ }));
    await user.click(within(dialog).getByRole("button", { name: "Preview" }));
    expect(await within(dialog).findByText("Forcing the ENS profile needs an allow-list: name who may reach it.")).toBeInTheDocument();
  });

  it("shows what is published and what Noust made, and takes it down after asking", async () => {
    const { user, backend } = await pageAt(PUBLISHED, {
      "DELETE /api/console/exposure": () => json(202, { job_id: "unexpose-1", status: "pending", message: "Queued", job: { ...JOB, id: "unexpose-1", status: "running", result: null } }),
      "GET /api/jobs/unexpose-1": () => json(200, { ...JOB, id: "unexpose-1", status: "running", result: null }),
    });
    expect(await screen.findByText("The console is published")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /https:\/\/console\.example\.com/ })).toBeInTheDocument();
    expect(screen.getAllByText("Written by Noust: it is removed when the console is unpublished")).toHaveLength(2);
    expect(screen.getByText("Added by Noust to the console's service: 127.0.0.1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unpublish the console" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Unpublish the console" });
    expect(within(confirm).getByText(/removes exactly what publishing made/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Unpublish" }));
    await waitFor(() => expect(backend.callsTo("DELETE /api/console/exposure")).toHaveLength(1));
    expect(await screen.findByText("Unpublishing the console")).toBeInTheDocument();
  });

  it("says when what it made is no longer there, in the system's own words", async () => {
    await pageAt({ ...PUBLISHED, problems: ["The nginx site of console.example.com is gone."] });
    expect(await screen.findByText("Published, with a problem")).toBeInTheDocument();
    expect(screen.getByText("The nginx site of console.example.com is gone.")).toBeInTheDocument();
  });

  it("is read in Spanish", async () => {
    await pageAt(PRIVATE);
    await act(() => setLocale("es"));
    expect(await screen.findByText("La consola es privada")).toBeInTheDocument();
    expect(screen.getByText("Un segundo factor en cada cuenta que pueda entrar")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
