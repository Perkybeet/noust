import { configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

// The wizard is lazy-loaded and the import form is read in two steps, which a busy machine
// stretches past the second a lookup waits by default.
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

const APPLICATIONS = {
  platform: "coolify",
  applications: [
    { ref: "u1", name: "storefront", build: "nixpacks", source: "https://github.com/acme/storefront", domains: ["shop.example.com"], project: "acme" },
    { ref: "u2", name: "worker", build: "dockerfile", source: "", domains: [], project: "acme" },
  ],
};

const PROPOSAL = {
  platform: "coolify",
  files: [],
  app_type: "nextjs",
  warnings: ["Coolify's preview deployments have no equivalent here: use Previews in Noust."],
  domains: ["shop.example.com"],
  equivalent: null,
};

const DOCUMENT = {
  format: "noust-app",
  version: 1,
  secrets_included: false,
  app: { domain: "shop.example.com", app_type: "nextjs" },
  env: {},
};

function importer(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes({ ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "POST /api/import/applications": () => json(200, APPLICATIONS),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new?start=import") };
}

/**
 * The list's description. The announcer says it again, for assistive technology, a moment after the
 * list is drawn, so a bare lookup finds one element or two depending on how fast the machine is.
 */
function listedOn(sentence: RegExp): Promise<HTMLElement> {
  return screen.findByText(sentence, { ignore: 'script, style, [role="status"]' });
}

async function listThem(user: ReturnType<typeof renderConsole>["user"]) {
  await user.click(await screen.findByRole("button", { name: "Read it from Coolify, Dokploy or CapRover" }));
  await user.type(await screen.findByLabelText(/^Address of the server/), "https://coolify.example.com");
  await user.type(screen.getByLabelText(/^API token/), "ro-token");
  await user.click(screen.getByRole("button", { name: "List the applications" }));
}

describe("importing from Coolify, Dokploy or CapRover", () => {
  it("lists what the other server has, with the token sent once and in the body only", { timeout: 40_000 }, async () => {
    const { backend, harness } = importer();
    await listThem(harness.user);

    await waitFor(() => expect(backend.callsTo("POST /api/import/applications")).toHaveLength(1));
    expect(backend.callsTo("POST /api/import/applications")[0]?.body).toEqual({ platform: "coolify", url: "https://coolify.example.com", token: "ro-token" });
    expect(backend.callsTo("POST /api/import/applications")[0]?.search.toString()).toBe("");
    expect(await listedOn(/2 found on https:\/\/coolify\.example\.com/)).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("reads one application and hands it to the review as if it were an export file", { timeout: 40_000 }, async () => {
    const { backend, harness } = importer({
      "POST /api/import/proposal": () => json(200, { proposal: PROPOSAL, document: DOCUMENT, deployable: true }),
    });
    await listThem(harness.user);
    await listedOn(/2 found on/);
    await harness.user.click(screen.getByRole("button", { name: "Read it" }));

    expect(await screen.findByText("storefront was read")).toBeInTheDocument();
    // What has no equivalent here, as the server wrote it.
    expect(screen.getByText("Coolify's preview deployments have no equivalent here: use Previews in Noust.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/import/proposal")[0]?.body).toEqual({
      platform: "coolify",
      url: "https://coolify.example.com",
      token: "ro-token",
      ref: "u1",
    });
    // The export card now names what was read.
    expect(await screen.findByText(/Coolify: storefront/)).toBeInTheDocument();
    expect(screen.getByText(/An export of shop\.example\.com, a Next\.js app\./)).toBeInTheDocument();
  });

  it("gives the command that creates the equivalent when the application cannot be an export", { timeout: 40_000 }, async () => {
    const { harness } = importer({
      "POST /api/import/proposal": () =>
        json(200, {
          proposal: { ...PROPOSAL, app_type: "container", equivalent: "noust create --domain shop.example.com --image ghcr.io/acme/worker:1" },
          document: DOCUMENT,
          deployable: false,
        }),
    });
    await listThem(harness.user);
    await listedOn(/2 found on/);
    await harness.user.click(screen.getByRole("button", { name: "Read it" }));

    expect(await screen.findByText("storefront cannot be imported as it is")).toBeInTheDocument();
    expect(screen.getByText("noust create --domain shop.example.com --image ghcr.io/acme/worker:1")).toBeInTheDocument();
    // Nothing was loaded for review.
    expect(screen.queryByText(/An export of shop\.example\.com/)).not.toBeInTheDocument();
  });

  it("asks for the domain when the application answered on none there", { timeout: 40_000 }, async () => {
    let attempts = 0;
    const { backend, harness } = importer({
      "POST /api/import/proposal": () => {
        attempts += 1;
        return attempts === 1
          ? json(400, { error: "validation_error", detail: "It answered on no domain there, so there is none to propose", hint: "Give the domain to create it on here.", fields: { domain: "It answered on no domain there, so there is none to propose" } })
          : json(200, { proposal: PROPOSAL, document: DOCUMENT, deployable: true });
      },
    });
    await listThem(harness.user);
    await listedOn(/2 found on/);
    await harness.user.click(screen.getByRole("button", { name: "Read it" }));
    expect(await screen.findByText(/answered on no domain there/)).toBeInTheDocument();

    await harness.user.type(screen.getByLabelText(/^Domain to create it on here/), "shop.example.com");
    await harness.user.click(screen.getByRole("button", { name: "Read it" }));
    await waitFor(() => expect(backend.callsTo("POST /api/import/proposal")).toHaveLength(2));
    expect(backend.callsTo("POST /api/import/proposal")[1]?.body).toMatchObject({ domain: "shop.example.com" });
  });

  it("asks CapRover for its dashboard password, because it has no token that only reads", { timeout: 40_000 }, async () => {
    const { backend, harness } = importer({
      "POST /api/import/applications": () => json(200, { ...APPLICATIONS, platform: "caprover" }),
    });
    await harness.user.click(await screen.findByRole("button", { name: "Read it from Coolify, Dokploy or CapRover" }));
    expect(await screen.findByLabelText(/^API token/)).toBeInTheDocument();

    await harness.user.click(screen.getByRole("combobox", { name: "Platform" }));
    await harness.user.click(await screen.findByRole("option", { name: "CapRover" }));

    expect(await screen.findByLabelText(/^Dashboard password/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^API token/)).not.toBeInTheDocument();
    expect(screen.getByText(/CapRover has no token that only reads/)).toBeInTheDocument();
    await harness.user.type(screen.getByLabelText(/^Address of the server/), "https://captain.apps.example.com");
    await harness.user.type(screen.getByLabelText(/^Dashboard password/), "captain42");
    await harness.user.click(screen.getByRole("button", { name: "List the applications" }));

    await waitFor(() => expect(backend.callsTo("POST /api/import/applications")).toHaveLength(1));
    expect(backend.callsTo("POST /api/import/applications")[0]?.body).toEqual({
      platform: "caprover",
      url: "https://captain.apps.example.com",
      token: "captain42",
    });
  });

  it("shows why the other server could not be read, verbatim", { timeout: 40_000 }, async () => {
    const { harness } = importer({
      "POST /api/import/applications": () => problem(502, "integrationerror", "coolify.example.com answered 401 Unauthorized"),
    });
    await listThem(harness.user);

    const message = await screen.findByText(/coolify\.example\.com answered 401 Unauthorized/);
    expect(within(message.closest("div") ?? document.body).getByText("The applications could not be listed")).toBeInTheDocument();
  });
});
