import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { certificateOf } from "./certificates";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

const APP = "shop.example.com";
const ADMIN = `admin.${APP}`;
const CREATED = "2026-09-20T10:00:00+00:00";

/** The application's certificate, which names the primary and one alias and not the admin name. */
const SHARED = {
  domain: APP,
  domains: [APP, `www.${APP}`],
  valid_until: "2026-12-01 00:00:00+00:00 (VALID: 67 days)",
  expires_on: "2026-12-01",
  days_remaining: 67,
  auto_renew: true,
  path: `/etc/letsencrypt/live/${APP}/fullchain.pem`,
  key_path: `/etc/letsencrypt/live/${APP}/privkey.pem`,
  issuer: "C = US, O = Let's Encrypt, CN = R11",
  serves: [APP, `www.${APP}`],
};

/** The one the admin name holds on its own, with a different state: it expires sooner. */
const OWN = {
  domain: ADMIN,
  domains: [ADMIN],
  valid_until: "2026-10-12 00:00:00+00:00 (VALID: 9 days)",
  expires_on: "2026-10-12",
  days_remaining: 9,
  auto_renew: true,
  path: `/etc/letsencrypt/live/${ADMIN}/fullchain.pem`,
  key_path: `/etc/letsencrypt/live/${ADMIN}/privkey.pem`,
  issuer: "C = US, O = Let's Encrypt, CN = R11",
  serves: [ADMIN],
};

const JOB = {
  id: "c0ffee09",
  type: "cert_create",
  name: `SSL for ${APP}`,
  description: "Ordering",
  status: "running",
  progress: 20,
  total_steps: 100,
  current_step: "Ordering the certificate of its own",
  created_at: "2026-10-03T19:21:13",
  logs: [],
  metadata: { domain: APP },
};

function entry(name: string, kind: string, certificate: "shared" | "own", pending = false) {
  return { domain: name, kind, created_at: CREATED, enabled: true, certificate, served_by: certificate === "own" && !pending ? name : APP, certificate_pending: pending };
}

async function rowOf(table: HTMLElement, name: string): Promise<HTMLElement> {
  const link = await within(table).findByRole("link", { name: (accessible) => accessible.startsWith(name) });
  const row = link.closest("tr");
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

describe("a name that holds a certificate of its own", () => {
  it("is shown with the state of its own certificate, not as uncovered", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/certs": () => json(200, { certificates: [SHARED, OWN], total: 2 }),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      [`GET /api/apps/${APP}/domains`]: () =>
        json(200, { app: APP, domains: [entry(APP, "primary", "shared"), entry(ADMIN, "alias", "own")], canonical: "" }),
    });
    renderConsole(`/apps/${APP}/domains`);
    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });

    const admin = await rowOf(table, ADMIN);
    expect(within(admin).getByText("Own certificate")).toBeInTheDocument();
    // Its own certificate expires in 9 days: that is what the row says, and not "Not covered".
    expect(within(admin).getByText("Expires in 9 days")).toBeInTheDocument();
    expect(within(admin).queryByText("No HTTPS")).not.toBeInTheDocument();
    // The link goes to HTTPS because the name has a certificate that covers it.
    expect(within(admin).getByRole("link", { name: (name) => name.startsWith(ADMIN) })).toHaveAttribute("href", `https://${ADMIN}`);

    const primary = await rowOf(table, APP);
    expect(within(primary).queryByText("Own certificate")).not.toBeInTheDocument();
    expect(within(primary).getByText("HTTPS")).toBeInTheDocument();
    await expectNoAxeViolations(table);
  });

  it("says when its certificate is not issued yet", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/certs": () => json(200, { certificates: [SHARED], total: 1 }),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      [`GET /api/apps/${APP}/domains`]: () =>
        json(200, { app: APP, domains: [entry(APP, "primary", "shared"), entry(ADMIN, "alias", "own", true)], canonical: "" }),
    });
    renderConsole(`/apps/${APP}/domains`);
    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });

    const admin = await rowOf(table, ADMIN);
    expect(within(admin).getByText("Own certificate pending")).toBeInTheDocument();
    expect(within(admin).getByText("No HTTPS")).toBeInTheDocument();
  });

  it("is given its certificate, and handed back, from its row, as a job", { timeout: 25_000 }, async () => {
    let domains = [entry(APP, "primary", "shared"), entry(ADMIN, "alias", "shared")];
    const changed = (certificate: "shared" | "own") => {
      domains = domains.map((item) => (item.domain === ADMIN ? entry(ADMIN, "alias", certificate, true) : item));
      return json(200, { app: APP, domains, tls: true, adopted: [], certificate_job_id: JOB.id, canonical: "" });
    };
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/certs": () => json(200, { certificates: [SHARED], total: 1 }),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      [`GET /api/apps/${APP}/domains`]: () => json(200, { app: APP, domains, canonical: "" }),
      [`PUT /api/apps/${APP}/domains/${ADMIN}/certificate`]: (request) => changed((request.body as { certificate: "own" | "shared" }).certificate),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    });
    const { user } = renderConsole(`/apps/${APP}/domains`);
    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });
    let row = await rowOf(table, ADMIN);

    await user.click(within(row).getByRole("button", { name: `Actions for ${ADMIN}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Give it its own certificate" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${APP}/domains/${ADMIN}/certificate`)[0]?.body).toEqual({ certificate: "own" });
    });
    expect((await screen.findAllByText(`Ordering a certificate of its own for ${ADMIN}`)).length).toBeGreaterThan(0);
    row = await rowOf(table, ADMIN);
    expect(await within(row).findByText("Own certificate pending")).toBeInTheDocument();

    // And back: the menu now offers the way.
    await user.click(within(row).getByRole("button", { name: `Actions for ${ADMIN}` }));
    expect(await screen.findByRole("menuitem", { name: "Use the app's certificate" })).toBeInTheDocument();
  });

  it("offers to hand the certificate back, and not the primary any change", { timeout: 20_000 }, async () => {
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/certs": () => json(200, { certificates: [SHARED, OWN], total: 2 }),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      [`GET /api/apps/${APP}/domains`]: () =>
        json(200, { app: APP, domains: [entry(APP, "primary", "shared"), entry(ADMIN, "alias", "own")], canonical: "" }),
      [`PUT /api/apps/${APP}/domains/${ADMIN}/certificate`]: () =>
        json(200, { app: APP, domains: [entry(APP, "primary", "shared"), entry(ADMIN, "alias", "shared")], tls: true, adopted: [], certificate_job_id: JOB.id, canonical: "" }),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    });
    const { user } = renderConsole(`/apps/${APP}/domains`);
    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });

    const primary = await rowOf(table, APP);
    await user.click(within(primary).getByRole("button", { name: `Actions for ${APP}` }));
    expect(await screen.findByRole("menuitem", { name: "Check DNS" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Give it its own certificate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Use the app's certificate" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    const admin = await rowOf(table, ADMIN);
    await user.click(within(admin).getByRole("button", { name: `Actions for ${ADMIN}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Use the app's certificate" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${APP}/domains/${ADMIN}/certificate`)[0]?.body).toEqual({ certificate: "shared" });
    });
    expect((await screen.findAllByText(`Covering ${ADMIN} with the certificate of ${APP}`)).length).toBeGreaterThan(0);
  });

  it("speaks Spanish", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/certs": () => json(200, { certificates: [SHARED, OWN], total: 2 }),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      [`GET /api/apps/${APP}/domains`]: () =>
        json(200, { app: APP, domains: [entry(APP, "primary", "shared"), entry(ADMIN, "alias", "own")], canonical: "" }),
    });
    await act(() => setLocale("es"));
    const { user } = renderConsole(`/apps/${APP}/domains`);
    const table = await screen.findByRole("region", { name: `Dominios de ${APP}` });
    const admin = await rowOf(table, ADMIN);

    expect(within(admin).getByText("Certificado propio")).toBeInTheDocument();
    await user.click(within(admin).getByRole("button", { name: `Acciones de ${ADMIN}` }));
    expect(await screen.findByRole("menuitem", { name: "Usar el certificado de la aplicación" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});

describe("which certificate presents a name", () => {
  const app = { domain: APP };
  const own = { domain: ADMIN };
  const find = (name: string) => (name === ADMIN ? own : null);

  it("is the application's for a name that shares", () => {
    expect(certificateOf({ domain: `www.${APP}`, certificate: "shared" }, app, find)).toBe(app);
    expect(certificateOf({ domain: `www.${APP}` }, app, find)).toBe(app);
  });

  it("is the name's own for one that holds it", () => {
    expect(certificateOf({ domain: ADMIN, certificate: "own" }, app, find)).toBe(own);
  });

  it("is the application's, which serves it meanwhile, for a name whose own is not issued yet", () => {
    expect(certificateOf({ domain: "other.example.com", certificate: "own" }, app, find)).toBe(app);
  });

  it("stays unknown while the application's is", () => {
    expect(certificateOf({ domain: ADMIN, certificate: "shared" }, undefined, find)).toBeUndefined();
  });
});
