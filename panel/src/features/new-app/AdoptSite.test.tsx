import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { translate } from "../../i18n/translate";
import { siteBody, siteProblems } from "./AdoptSite";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const SITES = {
  sites: [
    {
      site: "legacy.example.com",
      webserver: "nginx",
      config_path: "/etc/nginx/sites-available/legacy.example.com",
      enabled: true,
      domains: ["legacy.example.com"],
      kind: "proxy",
      root: null,
      upstream: "127.0.0.1:8000",
      port: 8000,
      unit: "legacy-api",
      unit_active: true,
      ssl: false,
      adoptable: true,
      reason: null,
    },
    {
      site: "blog",
      webserver: "nginx",
      config_path: "/etc/nginx/sites-available/blog",
      enabled: true,
      domains: ["blog.example.com"],
      kind: "php",
      root: "/var/www/blog",
      upstream: "unix:/run/php/php8.3-fpm.sock",
      port: null,
      unit: null,
      unit_active: null,
      ssl: true,
      adoptable: true,
      reason: null,
    },
    {
      site: "old.example.com",
      webserver: "nginx",
      config_path: "/etc/nginx/sites-available/old.example.com",
      enabled: true,
      domains: ["old.example.com"],
      kind: null,
      root: null,
      upstream: null,
      port: null,
      unit: null,
      unit_active: null,
      ssl: false,
      adoptable: false,
      reason: "it only redirects or answers with a fixed response: nothing runs behind it",
    },
  ],
};

const PLAN = {
  domain: "legacy.example.com",
  app_name: "legacy-example-com",
  kind: "proxy",
  site: "legacy.example.com",
  site_name: null,
  webserver: "nginx",
  enabled: true,
  ssl: false,
  root: "/srv/legacy",
  port: 8000,
  upstream: "127.0.0.1:8000",
  unit: "legacy-api",
  unit_active: true,
  warnings: ["The site also hands requests to 127.0.0.1:8001 (other-api.service), which stays outside this application."],
  adopted: false,
};

function wizard(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/session": () => json(200, ELEVATED),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [] }),
    "GET /api/recipes": () => json(200, { recipes: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/apps/native-sites": () => json(200, SITES),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

async function openSiteAdoption(user: ReturnType<typeof renderConsole>["user"], mode = "Already running", site = "A site on nginx or Apache") {
  await screen.findByRole("heading", { level: 1, name: /New application|Nueva aplicación/ });
  await user.click(await screen.findByRole("radio", { name: mode }));
  await user.click(await screen.findByRole("radio", { name: new RegExp(site) }));
}

describe("the site adoption's form", () => {
  it("asks for the one thing it cannot find, and sends only what was given", () => {
    const t = (key: Parameters<typeof siteProblems>[1] extends (key: infer K) => string ? K : never): string => translate("en", key);
    expect(siteProblems({ domain: "", site: "", unit: "", path: "srv" }, t)).toEqual({
      domain: "Enter the domain the site answers on.",
      path: "Give the absolute path, starting with /.",
    });
    expect(siteProblems({ domain: "blog.example.com", site: "", unit: "", path: "" }, t)).toEqual({});
    expect(siteBody({ domain: "Legacy.Example.com", site: "", unit: " legacy-api ", path: "" }, true)).toEqual({
      domain: "legacy.example.com",
      unit: "legacy-api",
      preview: true,
    });
  });
});

describe("adopting a site from New application", { timeout: 20_000 }, () => {
  it("lists what the server has, previews what is kept as it is, then adopts and opens the app", async () => {
    const { backend, harness } = wizard({
      "POST /api/apps/adopt-site": (call) => {
        const body = call.body as { preview: boolean };
        return json(200, body.preview ? PLAN : { ...PLAN, adopted: true });
      },
      "GET /api/apps/legacy.example.com": () => json(200, { domain: "legacy.example.com", name: "legacy.example.com", status: "running", active: true, enabled: true, app_type: "adopted", layout: "inplace" }),
    });
    const { user } = harness;
    await openSiteAdoption(user);

    // What is on the server, with the reason for what cannot be adopted.
    const found = await screen.findByRole("list", { name: "On this server" });
    expect(within(found).getByText("Behind the web server: legacy-api")).toBeInTheDocument();
    expect(within(found).getByText("it only redirects or answers with a fixed response: nothing runs behind it")).toBeInTheDocument();
    expect(within(found).getByRole("button", { name: "Choose old.example.com" })).toBeDisabled();

    await user.click(within(found).getByRole("button", { name: "Choose legacy.example.com" }));
    expect(screen.getByRole("textbox", { name: "Domain" })).toHaveValue("legacy.example.com");
    await user.click(screen.getByRole("button", { name: "Preview" }));

    const plan = await screen.findByRole("heading", { name: "What Noust found" });
    const card = plan.closest("section") ?? document.body;
    expect(within(card).getByText("legacy-api.service")).toBeInTheDocument();
    expect(within(card).getByText("Started, stopped and restarted from here; its file is never rewritten.")).toBeInTheDocument();
    expect(within(card).getByText(PLAN.warnings[0] ?? "")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/adopt-site").map((call) => (call.body as { preview: boolean }).preview)).toEqual([true]);
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("button", { name: "Adopt" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/adopt-site").map((call) => (call.body as { preview: boolean }).preview)).toEqual([true, false]);
    });
    expect(await screen.findByRole("heading", { level: 1, name: "legacy.example.com" })).toBeInTheDocument();
  });

  it("names the site file only when it is not the domain's, and asks again when the form changes", async () => {
    const { backend, harness } = wizard({ "POST /api/apps/adopt-site": () => json(200, PLAN) });
    const { user } = harness;
    await openSiteAdoption(user);

    await user.click(await screen.findByRole("button", { name: "Choose blog.example.com" }));
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByRole("heading", { name: "What Noust found" });
    expect(backend.callsTo("POST /api/apps/adopt-site").at(-1)?.body).toEqual({ domain: "blog.example.com", site: "blog", preview: true });

    // Another unit is another adoption: the preview that was read does not stand for it.
    await user.click(screen.getByText("More options"));
    await user.type(screen.getByRole("textbox", { name: /Service/ }), "other-api");
    expect(screen.queryByRole("heading", { name: "What Noust found" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeInTheDocument();
  });

  it("says why the server refused, in the server's words", async () => {
    const { harness } = wizard({
      "POST /api/apps/adopt-site": () =>
        json(400, {
          error: "validation_error",
          detail: "No site in nginx or Apache answers on missing.example.com",
          hint: "List the ones that could be adopted with: noust app discover",
          fields: { domain: "No site in nginx or Apache answers on missing.example.com" },
        }),
    });
    const { user } = harness;
    await openSiteAdoption(user);
    await user.type(await screen.findByRole("textbox", { name: "Domain" }), "missing.example.com");
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByText("No site in nginx or Apache answers on missing.example.com")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "What Noust found" })).not.toBeInTheDocument();
  });

  it("says there is nothing to adopt when every site is accounted for", async () => {
    const { harness } = wizard({ "GET /api/apps/native-sites": () => json(200, { sites: [] }) });
    await openSiteAdoption(harness.user);

    expect(await screen.findByText("Nothing to adopt")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Domain" })).toBeInTheDocument();
  });

  it("keeps the stack adoption one choice away", async () => {
    const { harness } = wizard();
    const { user } = harness;
    await openSiteAdoption(user);
    await user.click(await screen.findByRole("radio", { name: /A Docker Compose stack/ }));

    expect(await screen.findByRole("textbox", { name: "Directory" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "On this server" })).not.toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    const { harness } = wizard();
    await openSiteAdoption(harness.user, "Ya en marcha", "Un sitio en nginx o Apache");
    expect(await screen.findByRole("list", { name: "En este servidor" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Dominio" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previsualizar" })).toBeInTheDocument();
  });
});
