import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { SMALL_CONFIG } from "./siteTestFixtures";

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

const SITE = "shop.example.com";
const PATH = `/etc/nginx/sites-available/${SITE}`;

function open(config: RouteHandler) {
  fakeBackend({
    ...signedInRoutes(),
    [`GET /api/sites/${SITE}`]: () =>
      json(200, { name: SITE, webserver: "nginx", enabled: true, config_path: PATH, has_ssl: true, server_names: [SITE], pending_reload: false, edited_outside: false }),
    [`GET /api/sites/${SITE}/config`]: config,
    [`GET /api/sites/${SITE}/config/versions`]: () => json(200, { site: SITE, webserver: "nginx", edited_outside: false, pending_reload: false, versions: [] }),
    [`GET /api/sites/${SITE}/structure`]: () => json(200, { site: SITE, webserver: "nginx", path: PATH, structure: null, error: null }),
  });
  return renderConsole(`/domains/sites/${SITE}`);
}

/** The line of facts under the title and the notice: what the site serves and the file it is in. */
async function factsLine(): Promise<Element> {
  const line = (await screen.findByRole("main")).querySelector('[data-slot="meta"]');
  if (line === null) throw new Error("the page draws no line of facts");
  return line;
}

describe("the line of facts of a site's file", () => {
  it("holds its place with a skeleton the screen reader is told is busy, while the file is read", async () => {
    open(() => new Promise<Response>(() => undefined));
    const line = await factsLine();
    expect(line.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(line).toHaveAttribute("aria-busy", "true");
  });

  it("is the path of the file, and not busy, once it is read", async () => {
    open(() => json(200, { site: SITE, webserver: "nginx", config: SMALL_CONFIG, path: PATH }));
    const line = await factsLine();
    await waitFor(() => {
      expect(line).toHaveTextContent(PATH);
    });
    expect(line.querySelector('[data-slot="skeleton"]')).toBeNull();
    expect(line).not.toHaveAttribute("aria-busy");
  });

  it("keeps its place but stops breathing when the file cannot be read: nothing is on its way", async () => {
    open(() => json(500, { error: "internal_error", message: "could not read the file" }));
    expect(await screen.findByText(`Could not read the configuration of ${SITE}`, { exact: false })).toBeInTheDocument();
    const line = await factsLine();
    expect(line.querySelector('[data-slot="skeleton"]')).toBeNull();
    expect(line).not.toHaveAttribute("aria-busy");
  });
});
