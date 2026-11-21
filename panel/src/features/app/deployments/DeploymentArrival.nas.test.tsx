/**
 * The card a deploy ends on, on a central that deploys on its machine's own Docker (a NAS): the application answers at a port of the
 * machine, and the card says that address, the one the application's page says, and not `http://paperless`, a name that is nothing
 * but what Noust calls it. It also asks for no certificate and offers no domain where the machine has neither.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import { nasSession, serverSession } from "../../../test/nas";
import { TAB_APP, TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

const NAME = "paperless";

const SERVED = {
  id: 20,
  domain: NAME,
  status: "success",
  triggered_by: "manual",
  git_commit: null,
  git_branch: null,
  started_at: "2026-09-25T18:00:00",
  finished_at: "2026-09-25T18:00:40",
  duration_s: 40,
  error: null,
  has_log: true,
  release_id: null,
};

const APP = { ...TAB_APP, name: NAME, domain: NAME, app_type: "container", port: 8000, unit: "", path: "/volume1/docker/noust/apps/paperless" };

async function landed(session: ReturnType<typeof nasSession>, address: Record<string, unknown> | null) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...appRoutes(APP, {
      ...signedInRoutes(session),
      [`GET /api/apps/${NAME}`]: () => json(200, APP),
      [`GET /api/apps/${NAME}/pending`]: () => json(200, { domain: NAME, pending: false, items: [], counts: {}, needs: null, since: null, deployment_id: null, baseline_created: false }),
      [`GET /api/apps/${NAME}/sqlite`]: () => json(200, { domain: NAME, files: [], searched: ["."], truncated: false }),
      [`GET /api/apps/${NAME}/runs`]: () => json(200, { domain: NAME, form: "container", via: "image", image: "paperless-ngx/paperless-ngx:2" }),
      [`GET /api/apps/${NAME}/webhook/deliveries`]: () => json(200, { items: [], total: 0 }),
      ...(address !== null ? { [`GET /api/apps/${NAME}/address`]: () => json(200, address) } : {}),
      "GET /api/deployments": () => json(200, { items: [SERVED], total: 1, next_before_id: null }),
      "GET /api/deployments/20": () => json(200, SERVED),
      "GET /api/deployments/20/log": () => json(200, { content: "Deployed\n", truncated: false, missing_reason: null }),
      "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
    }),
  });
  const harness = renderConsole(`/apps/${NAME}/deployments/20`);
  await screen.findByRole("heading", { level: 1, name: NAME });
  return { ...harness, backend };
}

describe("the card a deploy ends on, on a machine that only has Docker", () => {
  it("says the address of the machine and the port, the one the application's page says, and no name that does not exist", async () => {
    const { backend } = await landed(nasSession(), { domain: NAME, kind: "port", url: "http://nas-casa:8000", port: 8000, proxy_hint: null });
    // The card is what the first deploy of an application ends on: it is there when what to do next is.
    await screen.findByRole("link", { name: "Add a database" });
    // The application's header says where it answers and so does the card: one address, said twice, and the same.
    const links = await screen.findAllByRole("link", { name: /http:\/\/nas-casa:8000/ });
    expect(links.length).toBeGreaterThanOrEqual(2);
    for (const link of links) expect(link).toHaveAttribute("href", "http://nas-casa:8000");
    expect(screen.queryByText(`http://${NAME}`)).not.toBeInTheDocument();
    // The sentence under it is the application's own: where it answers, on your network, without HTTPS.
    expect(screen.getByText(/Answers at/)).toBeInTheDocument();
    // No certificate and no domain are asked of a machine that has neither: not read, not said, not offered.
    expect(screen.queryByText(/no certificate covers this address/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Domains" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Add your own domain" })).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${NAME}/domains`)).toHaveLength(0);
    expect(backend.callsTo("GET /api/certs")).toHaveLength(0);
    // What is worth doing next is still there, for the first deploy.
    expect(screen.getByRole("link", { name: "Add a database" })).toBeInTheDocument();
  });

  it("says there is nothing to open for an application that serves no web, instead of an address", async () => {
    await landed(nasSession(), { domain: NAME, kind: "none", url: null, port: null, proxy_hint: null });
    await screen.findByRole("link", { name: "Add a database" });
    // Said once by the application's page and once by the card: both are the machine's one answer.
    expect(screen.getAllByText("This application serves no web, so there is nothing to open.").length).toBeGreaterThan(0);
    expect(screen.queryByText(`http://${NAME}`)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^http/ })).not.toBeInTheDocument();
  });
});

describe("the same card on a server with Noust's own web server", () => {
  it("is what it was: the name it answers on, and the way to add a domain and a certificate", async () => {
    await landed(serverSession(), null);
    await screen.findByRole("link", { name: "Add a database" });
    expect(await screen.findByText(`http://${NAME}`)).toBeInTheDocument();
    expect(screen.getByText(/Served over HTTP: no certificate covers this address yet\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Domains" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add your own domain" })).toBeInTheDocument();
    expect(TAB_DOMAIN).toBe("shop.example.com");
  });
});
