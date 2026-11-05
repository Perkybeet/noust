/**
 * Screenshots of the Domains and Network areas for review: every section, the dialogs that add a name and open a port,
 * and Noust DNS in each of its states, both themes (the light and dark projects), a 1440px and a 1920px desktop and a
 * 390px phone, in English and in Spanish
 * (NOUST_SCREENS_LOCALE=es). Tagged @screens, so left out of the default run:
 *
 *     NOUST_SCREENS=1 npx playwright test e2e/domains-screens.spec.ts --workers=1
 *
 * into $NOUST_DOMAINS_SCREENS (default /tmp/console-domains/<theme>/). The sandbox has no Knot, so the
 * states of Noust DNS that need one (set up, a zone with its records, its DNSSEC and its delegation) are
 * answered by the browser from the documented shapes of `GET /api/dns/noust/*`, and the page is the real one.
 * The pages still pass the CSP and console gates of every test, so a screenshot is never of a page that is
 * quietly broken.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, settle, signIn, stillness, test } from "./fixtures";
import { signInSpanish, useSpanish } from "./spanish";

const OUT = process.env.NOUST_DOMAINS_SCREENS ?? "/tmp/console-domains";
const SPANISH = (process.env.NOUST_SCREENS_LOCALE ?? "").startsWith("es");
if (SPANISH) test.use({ locale: "es-ES" });

const DESKTOP = { width: 1440, height: 900 };
const WIDE = { width: 1920, height: 1080 };
const PHONE = { width: 390, height: 844 };

const SERVER = {
  engine: "knot",
  installed: true,
  version: "3.4.6",
  running: true,
  configured: true,
  role: "primary",
  hostname: "ns1.kelmoor.dev",
  listen: ["203.0.113.10"],
  primary_address: null,
  zones: 2,
  secondaries: [{ address: "198.51.100.7", hostname: "ns2.kelmoor.dev", added_at: "2026-10-20T10:00:00+00:00", added_by: "marta" }],
  candidates: ["203.0.113.10"],
  firewall: {},
  foreign_zones: [],
  warnings: ["authdns.warning.availability", "authdns.warning.amplification"],
};

const ZONE = {
  name: "kelmoor.dev",
  nameservers: ["ns1.kelmoor.dev", "ns2.kelmoor.dev"],
  contact: "hostmaster@kelmoor.dev",
  default_ttl: 3600,
  dnssec: true,
  serial: 2026102901,
  records: [
    { id: 1, zone: "kelmoor.dev", name: "@", type: "A", content: "203.0.113.10", ttl: null },
    { id: 2, zone: "kelmoor.dev", name: "@", type: "MX", content: "10 mail.kelmoor.dev.", ttl: null },
    { id: 3, zone: "kelmoor.dev", name: "www", type: "CNAME", content: "kelmoor.dev.", ttl: 300 },
    { id: 4, zone: "kelmoor.dev", name: "automations", type: "A", content: "203.0.113.11", ttl: 300 },
    { id: 5, zone: "kelmoor.dev", name: "@", type: "TXT", content: "v=spf1 mx -all", ttl: null },
  ].map((record) => ({ created_at: "", updated_at: "", ...record })),
  created_at: "2026-10-20T10:00:00+00:00",
  updated_at: "2026-10-20T10:00:00+00:00",
};

const DS = { key_tag: 31337, algorithm: 13, digest_type: 2, digest: "3F5A0C9E21B7D4A8F6C0E1A97B2D5C48E3A1F09B7D6C2E4A8B5F1D3C7E9A0B24", text: "kelmoor.dev. IN DS 31337 13 2 3F5A0C9E21B7D4A8F6C0E1A97B2D5C48E3A1F09B7D6C2E4A8B5F1D3C7E9A0B24" };

const sentence = (code: string, params: Record<string, string>, text: string, hint: string | null = null) => ({ code, params, text, hint });

const DELEGATION = {
  zone: "kelmoor.dev",
  dnssec: true,
  ds: [DS],
  nameservers: [
    { name: "ns1.kelmoor.dev", addresses: ["203.0.113.10"], glue: true },
    { name: "ns2.kelmoor.dev", addresses: ["198.51.100.7"], glue: true },
  ],
  steps: [
    sentence("authdns.delegation.glue", { hosts: "ns1.kelmoor.dev, ns2.kelmoor.dev", addresses: "203.0.113.10, 198.51.100.7" }, "glue"),
    sentence("authdns.delegation.nameservers", { zone: "kelmoor.dev", nameservers: "ns1.kelmoor.dev, ns2.kelmoor.dev" }, "name servers"),
    sentence("authdns.delegation.ds", { zone: "kelmoor.dev", ds: DS.text }, "ds"),
  ],
  warnings: [sentence("authdns.delegation.same_network", { first: "203.0.113.10", second: "203.0.113.11" }, "same network")],
};

const CHECK = {
  zone: "kelmoor.dev",
  delegated: false,
  public_nameservers: ["ns1.old-provider.example", "ns2.old-provider.example"],
  ds_published: null,
  ds_matches: null,
  ready: false,
  servers: [
    { name: "ns1.kelmoor.dev", address: "203.0.113.10", answers: true, authoritative: true, serial: 2026102901, refuses_recursion: true },
    { name: "ns2.kelmoor.dev", address: "198.51.100.7", answers: true, authoritative: true, serial: 2026102900, refuses_recursion: true },
  ],
  problems: [
    sentence("authdns.check.not_delegated", { zone: "kelmoor.dev", found: "ns1.old-provider.example" }, "not delegated"),
    sentence("authdns.check.out_of_sync", { server: "ns2.kelmoor.dev", serial: "2026102900", zone: "kelmoor.dev", primary: "ns1.kelmoor.dev", primary_serial: "2026102901" }, "out of sync"),
  ],
};

/** Answers `GET /api/dns/noust/*` the way a primary with two zones would. */
async function primary(page: Page): Promise<void> {
  await page.route("**/api/dns/noust/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    const tail = pathname.slice(pathname.indexOf("/api/dns/noust/") + "/api/dns/noust/".length);
    const body =
      tail === "server"
        ? SERVER
        : tail === "zones"
          ? { zones: [{ name: "kelmoor.dev", records: 5, serial: 2026102901, dnssec: true, role: "master" }, { name: "brumaria.es", records: 3, serial: 2026102844, dnssec: false, role: "master" }] }
          : tail === "zones/kelmoor.dev"
            ? ZONE
            : tail === "zones/kelmoor.dev/dnssec"
              ? { zone: "kelmoor.dev", enabled: true, algorithm: "ecdsap256sha256", ds: [DS] }
              : tail === "zones/kelmoor.dev/delegation"
                ? DELEGATION
                : tail === "zones/kelmoor.dev/delegation/check"
                  ? CHECK
                  : null;
    if (body === null) await route.continue();
    else await route.fulfill({ json: body });
  });
}

/** A server that has Knot and no Noust DNS yet. */
async function notSetUp(page: Page): Promise<void> {
  await page.route("**/api/dns/noust/server", (route) =>
    route.fulfill({ json: { ...SERVER, configured: false, running: false, role: null, hostname: null, listen: [], zones: 0, secondaries: [] } }),
  );
}

const STREAM = {
  protocol: "tcp",
  listen_port: 25565,
  target_host: "127.0.0.1",
  target_port: 25566,
  target: "127.0.0.1:25566",
  sources: [] as string[],
  state: "open",
  app_domain: null as string | null,
  created_at: "2026-10-28T10:00:00Z",
  created_by: "admin",
  timeout_seconds: 3600,
  firewall: { active: true, backend: "ufw", made: [], note: "" },
};

/** The sandbox's nginx has no stream module: the table of published ports is answered by the browser from the documented shape. */
async function published(page: Page): Promise<void> {
  await page.route("**/api/streams", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      json: {
        exposures: [
          { ...STREAM, name: "minecraft" },
          { ...STREAM, name: "git-2222", listen_port: 2222, target: "127.0.0.1:2223", target_port: 2223, sources: ["10.0.0.0/24"], app_domain: "example.com" },
          { ...STREAM, name: "wireguard-51820", protocol: "udp", listen_port: 51820, target: "127.0.0.1:51821", target_port: 51821, state: "opening" },
        ],
        support: { available: true, module: "static", loader: "present", install: null, coded: null, hint: null, problem: null },
      },
    });
  });
}

interface Screen {
  name: string;
  path: string;
  before?: (page: Page) => Promise<void>;
  act?: (page: Page) => Promise<void>;
  /** A console error the state causes on purpose. */
  expect?: RegExp;
}

const SCREENS: readonly Screen[] = [
  { name: "domains", path: "/domains" },
  { name: "domains-filtered", path: "/domains?q=automations" },
  {
    name: "domains-add",
    path: "/domains",
    act: async (page) => {
      await page.getByRole("button", { name: /Add a name|Añadir un nombre/ }).first().click();
      await expect(page.getByRole("dialog")).toBeVisible();
    },
  },
  {
    name: "domains-add-site",
    path: "/domains",
    act: async (page) => {
      await page.getByRole("button", { name: /Add a name|Añadir un nombre/ }).first().click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("radio", { name: /A site of its own|Un sitio propio/ }).check();
      await expect(dialog.getByRole("radio", { name: /Reverse proxy|Proxy inverso/ })).toBeVisible();
    },
  },
  { name: "sites", path: "/domains/sites" },
  { name: "certificates", path: "/domains/certificates" },
  { name: "dns", path: "/domains/dns" },
  {
    name: "dns-add-ionos",
    path: "/domains/dns",
    act: async (page) => {
      await page.getByRole("button", { name: /Add a provider|Añadir un proveedor/ }).first().click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await dialog.getByRole("combobox").first().click();
      await page.getByRole("option", { name: /IONOS/ }).click();
      await expect(dialog.getByText(/IONOS/).first()).toBeVisible();
    },
  },
  { name: "noust-dns-install", path: "/domains/dns/noust" },
  {
    name: "noust-dns-install-missing",
    path: "/domains/dns/noust",
    act: async (page) => {
      await page.getByRole("button", { name: /Install Noust DNS|Instalar Noust DNS/ }).click();
      await expect(page.getByText(/Accept what it takes on|Acepta lo que supone/)).toBeVisible();
    },
  },
  { name: "noust-dns-setup", path: "/domains/dns/noust", before: notSetUp },
  { name: "noust-dns", path: "/domains/dns/noust", before: primary },
  { name: "dns-with-noust", path: "/domains/dns", before: primary },
  { name: "zone-records", path: "/domains/dns/zones/kelmoor.dev", before: primary },
  {
    name: "zone-record-add",
    path: "/domains/dns/zones/kelmoor.dev",
    before: primary,
    act: async (page) => {
      await page.getByRole("button", { name: /Add record|Añadir registro/ }).first().click();
      await expect(page.getByRole("dialog")).toBeVisible();
    },
  },
  { name: "zone-dnssec", path: "/domains/dns/zones/kelmoor.dev?view=dnssec", before: primary },
  { name: "zone-delegation", path: "/domains/dns/zones/kelmoor.dev?view=delegation", before: primary },
  {
    name: "zone-delegation-checked",
    path: "/domains/dns/zones/kelmoor.dev?view=delegation",
    before: primary,
    act: async (page) => {
      await page.getByRole("button", { name: /Check now|Comprobar ahora/ }).click();
      await expect(page.getByText(/Not ready|No está listo/)).toBeVisible();
    },
  },
  { name: "tunnels", path: "/network/tunnels" },
  { name: "ports", path: "/network" },
  { name: "ports-published", path: "/network", before: published },
  {
    name: "open-port",
    path: "/network",
    act: async (page) => {
      await page.getByRole("button", { name: /Open a port|Abrir un puerto/ }).first().click();
      const dialog = page.getByRole("alertdialog");
      await dialog.getByRole("textbox", { name: /^(Port|Puerto)$/ }).fill("25565");
      await expect(dialog.getByText(/Nothing listens on 25565|Ahora no escucha nada en el 25565/)).toBeVisible();
    },
  },
  {
    name: "open-port-inside",
    path: "/network/listening?q=3306",
    act: async (page) => {
      await page.getByRole("button", { name: /Open to the outside|Abrir hacia fuera/ }).first().click();
      await expect(page.getByRole("alertdialog").getByRole("textbox", { name: /Port to answer on|Puerto desde fuera/ })).toBeVisible();
    },
  },
  { name: "listening", path: "/network/listening" },
  { name: "listening-5432", path: "/network/listening?q=5432" },
  { name: "firewall", path: "/network/firewall" },
  { name: "interfaces", path: "/network/interfaces" },
];

test.describe("domains screens @screens", () => {
  for (const screen of SCREENS) {
    test(screen.name, async ({ page, consoleServer, problems }, testInfo) => {
      if (screen.expect) problems.expect(screen.expect);
      const dir = path.join(OUT, SPANISH ? "es" : "en", testInfo.project.name);
      if (screen.before) await screen.before(page);
      for (const [label, size] of [
        ["desktop", DESKTOP],
        ["wide", WIDE],
        ["mobile", PHONE],
      ] as const) {
        await page.setViewportSize(size);
        if (SPANISH) {
          await useSpanish(page);
          if (label === "desktop") await signInSpanish(page, consoleServer, screen.path);
          else await page.goto(screen.path);
        } else if (label === "desktop") {
          await signIn(page, consoleServer, screen.path);
        } else {
          await page.goto(screen.path);
        }
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await settle(page);
        await screen.act?.(page);
        await settle(page);
        await stillness(page);
        // L-4: a page never scrolls sideways at 390px; only a table, a log or a tab strip scrolls inside itself.
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${screen.name} at ${label}`).toBeLessThanOrEqual(0);
        await page.screenshot({ path: path.join(dir, `${screen.name}-${label}.png`), fullPage: true });
      }
    });
  }
});
