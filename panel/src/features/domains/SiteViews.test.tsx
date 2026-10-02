import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { FakeBackend, RouteHandler } from "../../test/fakes";
import { changedLines } from "../../lib/lineDiff";
import { SMALL_CONFIG, SMALL_EDIT_ADD_HEALTH, SMALL_EDIT_TIMEOUT, SMALL_ROUTE_LOGIN, SMALL_STRUCTURE, SMALL_TOPOLOGY_FACTS } from "./siteTestFixtures";

/** A desktop: the diagram is drawn in columns, not the phone's list. */
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: !query.includes("reduce"),
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

function siteRoutes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    [`GET /api/sites/${SITE}`]: () =>
      json(200, { name: SITE, webserver: "nginx", enabled: true, config_path: PATH, has_ssl: true, server_names: [SITE, `www.${SITE}`] }),
    [`GET /api/sites/${SITE}/config`]: () => json(200, { site: SITE, webserver: "nginx", config: SMALL_CONFIG, path: PATH }),
    [`GET /api/sites/${SITE}/structure`]: () => json(200, { site: SITE, webserver: "nginx", path: PATH, structure: SMALL_STRUCTURE, error: null }),
    [`GET /api/sites/${SITE}/topology`]: () =>
      json(200, { site: SITE, webserver: "nginx", path: PATH, structure: SMALL_STRUCTURE, error: null, ...SMALL_TOPOLOGY_FACTS }),
    ...extra,
  };
}

/** Every location row of the Structure view, in the order shown. */
function locationRows(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>('li > [role="group"]')];
}

/** The card of a server, found by its heading; the shop has two with the same name. */
function serverCard(name: RegExp, index: number): HTMLElement {
  const card = screen.getAllByRole("heading", { name })[index]?.closest("section");
  if (!card) throw new Error("no server card");
  return card;
}

/** The group of one location in the Structure view, named by its match and path. */
async function locationRow(name: string): Promise<HTMLElement> {
  return screen.findByRole("group", { name });
}

describe("a site's three views", () => {
  it("share one draft: a read timeout set in Structure is in the text and in the count", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(
      siteRoutes({
        [`POST /api/sites/${SITE}/config/edit`]: () => json(200, SMALL_EDIT_TIMEOUT),
      }),
    );
    const { user } = renderConsole(`/domains/sites/${SITE}?view=structure`);
    const row = await locationRow("/api/v1/auth/login");
    // Locations in the order nginx tries them: the exact match before the prefixes.
    const names = locationRows().map((group) => document.getElementById(group.getAttribute("aria-labelledby") ?? "")?.textContent);
    expect(names).toEqual(["/", "= /old", "/api/v1/auth/login", "/assets/", "/api/", "~ /\\."]);
    // The comment above it in the file, as a note.
    expect(within(row).getByText("Sign-in is rate limited before it reaches the app.")).toBeInTheDocument();
    expect(within(row).getByRole("textbox", { name: /^Rate limit/ })).toHaveValue("zone=auth_limit burst=20 nodelay");
    // The upstream it goes to, one click away.
    expect(within(row).getByRole("button", { name: "Show the upstream shop_backend" })).toBeInTheDocument();
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();

    const timeout = within(row).getByRole("textbox", { name: /^Read timeout/ });
    await user.type(timeout, "90s{Enter}");
    await waitFor(() => expect(backend.callsTo(`POST /api/sites/${SITE}/config/edit`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/sites/${SITE}/config/edit`)[0]?.body).toEqual({
      config: SMALL_CONFIG,
      ops: [{ op: "set_directive", parent: "s1/l0", name: "proxy_read_timeout", args: ["90s"] }],
    });
    expect(await screen.findByText("1 unsaved change")).toBeInTheDocument();
    const changed = await locationRow("/api/v1/auth/login");
    expect(within(changed).getByText("Changed")).toBeInTheDocument();
    expect(within(changed).getByRole("textbox", { name: /^Read timeout/ })).toHaveValue("90s");
    // Nothing else is marked: the other locations kept their text.
    expect(screen.getAllByText("Changed")).toHaveLength(1);
    await expectNoAxeViolations(document.body);

    // Switching views is not leaving: the draft goes with it.
    await user.click(screen.getByRole("radio", { name: "Text" }));
    expect(screen.queryByRole("dialog", { name: "Leave without saving?" })).not.toBeInTheDocument();
    const editor = await screen.findByRole("textbox", { name: `Configuration of ${SITE}` });
    expect(editor).toHaveValue(SMALL_EDIT_TIMEOUT.config);
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    // Nothing was written: only Test and save does that.
    expect(backend.callsTo(`PUT /api/sites/${SITE}/config`)).toHaveLength(0);
  });

  it("adds a location from a template and marks only it", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(siteRoutes({ [`POST /api/sites/${SITE}/config/edit`]: () => json(200, SMALL_EDIT_ADD_HEALTH) }));
    const { user } = renderConsole(`/domains/sites/${SITE}?view=structure`);
    await locationRow("/api/v1/auth/login");
    const server = serverCard(/^Server shop\.example\.com/, 1);
    await user.click(within(server).getByRole("button", { name: "Add location" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a location to shop.example.com" });
    await user.type(within(dialog).getByRole("textbox", { name: "Path" }), "/api/");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    // The same match and path twice is refused before nginx would.
    expect(await within(dialog).findByText("Another location of this server already has this match and path.")).toBeInTheDocument();
    await user.clear(within(dialog).getByRole("textbox", { name: "Path" }));
    await user.type(within(dialog).getByRole("textbox", { name: "Path" }), "/health");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/sites/${SITE}/config/edit`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/sites/${SITE}/config/edit`)[0]?.body).toEqual({
      config: SMALL_CONFIG,
      ops: [{ op: "add_block", parent: "s1", name: "location", args: ["/health"], template: "proxy", to: "http://shop_backend" }],
    });
    const added = await locationRow("/health");
    expect(within(added).getByText("Changed")).toBeInTheDocument();
    expect(screen.getAllByText("Changed")).toHaveLength(1);
  });

  it("shows raw directives it does not model, as written", async () => {
    fakeBackend(siteRoutes());
    renderConsole(`/domains/sites/${SITE}?view=structure`);
    await locationRow("/api/v1/auth/login");
    // A map outside any server, and an if inside one: the structure models neither, and both
    // are on screen with the way to their line.
    expect(screen.getByRole("heading", { name: "Other directives" })).toBeInTheDocument();
    expect(screen.getByText(/map \$http_upgrade \$connection_upgrade \{/)).toBeInTheDocument();
    expect(screen.getByText(/if \(\$host = www\.shop\.example\.com\) \{/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to line 1" })).toBeInTheDocument();
    // Listens, names and certificate with its expiry from the topology.
    const server = serverCard(/^Server shop\.example\.com/, 1);
    expect(within(server).getAllByText("443 · TLS · HTTP/2").length).toBeGreaterThan(0);
    expect(screen.getByText("Expires in 60 days")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Upstream shop_backend" })).toBeInTheDocument();
  });

  it("does not draw half a structure from a draft that does not parse, and goes to its line", { timeout: 20_000 }, async () => {
    fakeBackend(
      siteRoutes({
        [`POST /api/sites/${SITE}/structure`]: () =>
          json(200, { site: SITE, webserver: "nginx", path: PATH, structure: null, error: { line: 4, column: 1, message: 'unexpected "}"' } }),
      }),
    );
    const { user } = renderConsole(`/domains/sites/${SITE}`);
    const editor = await screen.findByRole("textbox", { name: `Configuration of ${SITE}` });
    await user.clear(editor);
    await user.type(editor, "server {{\n    listen 80;\n    server_name x;\n}}\n}}");
    await user.click(screen.getByRole("radio", { name: "Structure" }));
    expect(await screen.findByText("This draft cannot be shown as a structure")).toBeInTheDocument();
    expect(screen.getByText('unexpected "}"')).toBeInTheDocument();
    expect(screen.getByText(/Line 4, column 1 is not valid nginx syntax/)).toBeInTheDocument();
    expect(locationRows()).toHaveLength(0);
    await expectNoAxeViolations(document.body);

    await user.click(screen.getByRole("button", { name: "Go to text" }));
    const back = await screen.findByRole("textbox", { name: `Configuration of ${SITE}` });
    await waitFor(() => expect(back).toHaveFocus());
    const value = (back as HTMLTextAreaElement).value;
    expect((back as HTMLTextAreaElement).selectionStart).toBe(value.split("\n").slice(0, 3).join("\n").length + 1);
  });

  it("draws the diagram with a summary and a table, and lights only the route a URL takes", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(siteRoutes({ [`POST /api/sites/${SITE}/route`]: () => json(200, SMALL_ROUTE_LOGIN) }));
    const { user } = renderConsole(`/domains/sites/${SITE}?view=diagram`);
    const diagram = await screen.findByRole("figure", { name: `How ${SITE} answers` });
    expect(within(diagram).getByRole("button", { name: /^Backend 127\.0\.0\.1:3000, Noust application shop\.example\.com: Not responding$/ })).toBeInTheDocument();
    expect(within(diagram).getByRole("button", { name: /^Server shop\.example\.com, Also www\.shop\.example\.com: Expires in 60 days$/ })).toBeInTheDocument();
    expect(within(diagram).getByText(/1 element is not responding: 127\.0\.0\.1:3000\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show the connections as a table" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);

    await user.type(screen.getByRole("textbox", { name: "Try a URL" }), "/api/v1/auth/login");
    await user.click(screen.getByRole("button", { name: "Try" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/sites/${SITE}/route`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/sites/${SITE}/route`)[0]?.body).toEqual({ host: SITE, path: "/api/v1/auth/login", scheme: "https" });
    const answer = await screen.findByText((_, element) => element?.tagName === "P" && element.textContent.includes(" answers https://"));
    expect(answer.textContent).toBe("/api/v1/auth/login of shop.example.com answers https://shop.example.com/api/v1/auth/login.");
    // The trace in words, a server named rather than its id.
    const steps = screen.getByRole("list", { name: "How nginx chooses" });
    expect(within(steps).getByText(/is the exact name/).textContent).toBe("shop.example.com is the exact name shop.example.com of shop.example.com.");
    expect(within(steps).getByText(/the longest wins/).textContent).toBe("/api/v1/auth/login starts with the prefixes /api/, /api/v1/auth/login: the longest wins, /api/v1/auth/login.");
    // Only that path is lit: its location, not the catch-all /api/.
    const lit = (id: string) => diagram.querySelector(`[data-node="${id}"]`)?.hasAttribute("data-active");
    expect(lit("s1/l0")).toBe(true);
    expect(lit("u:shop_backend")).toBe(true);
    expect(lit("b:127.0.0.1:3000")).toBe(true);
    expect(lit("s1/l1")).toBe(false);
    expect(within(diagram).getByText(/Highlighted path: 443 · TLS · HTTP\/2 → shop\.example\.com → \/api\/v1\/auth\/login → shop_backend → 127\.0\.0\.1:3000\./)).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("says an older Noust has no Structure or Diagram, and keeps the text", async () => {
    fakeBackend(
      siteRoutes({
        [`GET /api/sites/${SITE}/structure`]: () => problem(404, "not_found", "Not Found"),
        [`GET /api/sites/${SITE}/topology`]: () => problem(404, "not_found", "Not Found"),
      }),
    );
    const { user } = renderConsole(`/domains/sites/${SITE}?view=structure`);
    expect(await screen.findByText("This server runs an older Noust")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
    await user.click(screen.getByRole("radio", { name: "Diagram" }));
    expect(await screen.findByText("This server runs an older Noust")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit as text" }));
    expect(await screen.findByRole("textbox", { name: `Configuration of ${SITE}` })).toHaveValue(SMALL_CONFIG);
  });

  it("explains the route in Spanish", { timeout: 20_000 }, async () => {
    fakeBackend(siteRoutes({ [`POST /api/sites/${SITE}/route`]: () => json(200, SMALL_ROUTE_LOGIN) }));
    await act(() => setLocale("es"));
    const { user } = renderConsole(`/domains/sites/${SITE}?view=diagram`);
    await screen.findByRole("figure", { name: `Cómo responde ${SITE}` });
    expect(screen.getByRole("radio", { name: "Diagrama" })).toBeChecked();
    await user.type(screen.getByRole("textbox", { name: "Probar una URL" }), "/api/v1/auth/login");
    await user.click(screen.getByRole("button", { name: "Trazar" }));
    const steps = await screen.findByRole("list", { name: "Cómo elige nginx" });
    expect(within(steps).getByText(/gana el más largo/).textContent).toBe(
      "/api/v1/auth/login empieza por los prefijos /api/, /api/v1/auth/login: gana el más largo, /api/v1/auth/login.",
    );
  });
});

/** A response the test hands over when it decides: the race is in the order of the answers. */
function later(): { handler: RouteHandler; answer: (response: Response) => Promise<void> } {
  let release: (response: Response) => void = () => undefined;
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  return {
    handler: () => pending,
    answer: async (response) => {
      await act(async () => {
        release(response);
        await pending;
      });
    },
  };
}

/** The draft after a second Structure edit: the rate limit's burst lowered. */
const SECOND_EDIT = { ...SMALL_EDIT_TIMEOUT, config: SMALL_EDIT_TIMEOUT.config.replace("burst=20", "burst=5") };

describe("a Structure edit racing the save bar", () => {
  /**
   * One edit already in the draft (the read timeout), and a second one typed in the rate limit
   * whose `/config/edit` answer the test holds back: leaving the field sends it, and the click
   * that left it lands on the bar before the console has re-rendered.
   */
  async function secondEditInFlight(extra: Record<string, RouteHandler> = {}): Promise<{
    backend: FakeBackend;
    second: ReturnType<typeof later>;
    rate: HTMLElement;
    user: ReturnType<typeof renderConsole>["user"];
  }> {
    const second = later();
    let calls = 0;
    const backend = fakeBackend(
      siteRoutes({
        [`POST /api/sites/${SITE}/config/edit`]: (call) => {
          calls += 1;
          return calls === 1 ? json(200, SMALL_EDIT_TIMEOUT) : second.handler(call);
        },
        ...extra,
      }),
    );
    const { user } = renderConsole(`/domains/sites/${SITE}?view=structure`);
    const row = await locationRow("/api/v1/auth/login");
    await user.type(within(row).getByRole("textbox", { name: /^Read timeout/ }), "90s{Enter}");
    expect(await screen.findByText("1 unsaved change")).toBeInTheDocument();
    const rate = within(await locationRow("/api/v1/auth/login")).getByRole("textbox", { name: /^Rate limit/ });
    await waitFor(() => expect(rate).toBeEnabled());
    await user.clear(rate);
    await user.type(rate, "zone=auth_limit burst=5 nodelay");
    return { backend, second, rate, user };
  }

  it("keeps a discard: the answer to an edit sent before it does not bring the draft back", { timeout: 20_000 }, async () => {
    const { backend, second, rate, user } = await secondEditInFlight();
    fireEvent.blur(rate);
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/sites/${SITE}/config/edit`)).toHaveLength(2));
    await second.answer(json(200, SECOND_EDIT));
    await waitFor(() => expect(screen.getByText("No unsaved changes")).toBeInTheDocument());
    await user.click(screen.getByRole("radio", { name: "Text" }));
    expect(await screen.findByRole("textbox", { name: `Configuration of ${SITE}` })).toHaveValue(SMALL_CONFIG);
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
  });

  it("does not drop an edit whose answer arrives while Test and save is writing the text before it", { timeout: 20_000 }, async () => {
    const put = later();
    // The file on disk, as the save leaves it: the page reads it again once saved.
    let onDisk = SMALL_CONFIG;
    const { backend, second, rate, user } = await secondEditInFlight({
      [`GET /api/sites/${SITE}/config`]: () => json(200, { site: SITE, webserver: "nginx", config: onDisk, path: PATH }),
      [`PUT /api/sites/${SITE}/config`]: async (call) => {
        const response = await put.handler(call);
        onDisk = (call.body as { config: string }).config;
        return response;
      },
    });
    fireEvent.blur(rate);
    fireEvent.click(screen.getByRole("button", { name: "Test and save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/sites/${SITE}/config`)).toHaveLength(1));
    // What was saved is the draft the click saw; the burst was still on its way.
    expect(backend.callsTo(`PUT /api/sites/${SITE}/config`)[0]?.body).toEqual({ config: SMALL_EDIT_TIMEOUT.config });
    await second.answer(json(200, SECOND_EDIT));
    await put.answer(json(200, { success: true, message: "Configuration updated.", site: SITE }));
    expect(await screen.findByText(/^Saved\. It passed nginx's configuration test/)).toBeInTheDocument();
    // The burst is not lost: it is the one change left to save.
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Text" }));
    expect(await screen.findByRole("textbox", { name: `Configuration of ${SITE}` })).toHaveValue(SECOND_EDIT.config);
  });

  it("holds the save bar and the text while an edit is on its way", { timeout: 20_000 }, async () => {
    const { second, user } = await secondEditInFlight();
    await user.tab();
    const bar = screen.getByRole("region", { name: /save/i });
    await waitFor(() => expect(within(bar).getByRole("button", { name: "Test and save" })).toBeDisabled());
    expect(within(bar).getByRole("button", { name: "Test" })).toBeDisabled();
    expect(within(bar).getByRole("button", { name: "Discard" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: "Text" }));
    expect(await screen.findByRole("textbox", { name: `Configuration of ${SITE}` })).toBeDisabled();
    await second.answer(json(200, SECOND_EDIT));
    await waitFor(() => expect(within(bar).getByRole("button", { name: "Test and save" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: `Configuration of ${SITE}` })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: `Configuration of ${SITE}` })).toHaveValue(SECOND_EDIT.config);
  });
});

describe("counting changed lines", () => {
  it("counts an insertion as its own lines, and a replaced line once", () => {
    expect(changedLines("a\nb\nc", "a\nb\nc")).toBe(0);
    expect(changedLines("a\nb\nc", "a\nB\nc")).toBe(1);
    expect(changedLines("a\nb\nc\nd\ne", "a\nx\ny\nb\nc\nd\ne")).toBe(2);
    expect(changedLines("a\nb\nc\nd", "a\nd")).toBe(2);
    expect(changedLines(SMALL_CONFIG, SMALL_EDIT_ADD_HEALTH.config)).toBe(SMALL_EDIT_ADD_HEALTH.changed_lines);
  });
});
