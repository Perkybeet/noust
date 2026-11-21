import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { chooseWay } from "../../test/ways";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { translate } from "../../i18n/translate";
import { adoptBody, adoptProblems } from "./AdoptStack";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const DRY_RUN = [" Container proggest-postgres-1  Running", " Container proggest-backend-1  Running", " Container proggest-frontend-1  Running"].join("\n");

const PREVIEW = {
  domain: "proggest.es",
  app_name: "proggest-es",
  app_path: "/srv/proggest",
  compose_file: "docker-compose.prod.yml",
  project: "proggest",
  project_from: "containers",
  containers: ["proggest-postgres-1", "proggest-backend-1", "proggest-frontend-1"],
  running: true,
  source: "git@github.com:proggest/proggest.git",
  branch: "main",
  commit: "4be2c1d",
  site: "/etc/nginx/sites-available/proggest",
  site_name: "proggest",
  ssl: true,
  port: 3000,
  headless: false,
  dry_run: DRY_RUN,
  changes: [],
  warnings: ["The stack's database is not copied before updates until backup.databases is set"],
  adopted: false,
  unit: "proggest-es",
  deployment_id: null,
};

function wizard(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/session": () => json(200, ELEVATED),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [] }),
    "GET /api/recipes": () => json(200, { recipes: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

async function openAdopt(user: ReturnType<typeof renderConsole>["user"], mode = "Already running") {
  await screen.findByRole("heading", { level: 1, name: /New application|Nueva aplicación/ });
  await chooseWay(user, mode);
}

describe("the adoption's form", () => {
  it("asks for the two things it cannot find, and sends only what was given", () => {
    const t = (key: Parameters<typeof adoptProblems>[1] extends (key: infer K) => string ? K : never): string => translate("en", key);
    expect(adoptProblems({ domain: "", path: "srv", composeFile: "", site: "", source: "", branch: "" }, t)).toEqual({
      domain: "Enter the domain the stack serves.",
      path: "Give the absolute path, starting with /.",
    });
    expect(adoptBody({ domain: "Proggest.es", path: "/srv/proggest ", composeFile: "", site: "proggest", source: "", branch: "" }, { preview: true, acceptRecreate: false })).toEqual({
      domain: "proggest.es",
      path: "/srv/proggest",
      site: "proggest",
      preview: true,
      accept_recreate: false,
      accept_host_access: false,
    });
    // What gives the server to a container is a decision of its own, sent only when it was taken.
    expect(adoptBody({ domain: "proggest.es", path: "/srv/proggest", composeFile: "", site: "", source: "", branch: "" }, { preview: false, acceptRecreate: false, acceptHostAccess: true })).toMatchObject({ preview: false, accept_host_access: true });
  });
});

describe("adopting a running stack from New application", { timeout: 20_000 }, () => {
  it("previews first, showing Compose's dry run verbatim, then adopts and opens the app", async () => {
    const { backend, harness } = wizard({
      "POST /api/apps/adopt": (call) => {
        const body = call.body as { preview: boolean };
        return json(200, body.preview ? PREVIEW : { ...PREVIEW, adopted: true, deployment_id: 1 });
      },
      "GET /api/apps/proggest.es": () => json(200, { domain: "proggest.es", name: "proggest.es", status: "running", active: true, enabled: true, app_type: "docker-compose", layout: "inplace" }),
    });
    const { user } = harness;
    await openAdopt(user);
    await user.type(screen.getByRole("textbox", { name: "Domain" }), "proggest.es");
    await user.type(screen.getByRole("textbox", { name: "Directory" }), "/srv/proggest");
    await user.click(screen.getByRole("button", { name: "Preview" }));

    const found = await screen.findByRole("heading", { name: "What Noust found" });
    const card = found.closest("section") ?? document.body;
    // Compose's own words, untouched, before anything is adopted.
    expect(within(card).getByText((_, element) => element?.tagName === "PRE" && element.textContent === DRY_RUN)).toBeInTheDocument();
    expect(within(card).getByText("Its file is named proggest, not after the domain: Noust records the name.")).toBeInTheDocument();
    expect(within(card).getByText(PREVIEW.warnings[0] ?? "")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/adopt").map((call) => (call.body as { preview: boolean }).preview)).toEqual([true]);
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("button", { name: "Adopt" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/adopt").map((call) => (call.body as { preview: boolean }).preview)).toEqual([true, false]);
    });
    expect(await screen.findByRole("heading", { level: 1, name: "proggest.es" })).toBeInTheDocument();
  });

  // What the person is asked to accept was the server's English in a Spanish console: each sentence comes with its code, and is said in the
  // console's language; one that has none yet is said as the server sent it.
  it("says the sentences of the preview in the console's language when they have a code, and as the server said them when they have none", async () => {
    const { harness } = wizard({
      "POST /api/apps/adopt": () =>
        json(200, {
          ...PREVIEW,
          warnings: ["service app is given the host device /dev/dri", "A sentence that has no code yet."],
          warnings_coded: [{ code: "compose_guard.devices", params: { who: "service app", device: "/dev/dri" } }, null],
        }),
    });
    const { user } = harness;
    await openAdopt(user);
    await user.type(screen.getByRole("textbox", { name: "Domain" }), "proggest.es");
    await user.type(screen.getByRole("textbox", { name: "Directory" }), "/srv/proggest");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    const found = await screen.findByRole("heading", { name: "What Noust found" });
    const card = found.closest("section") ?? document.body;
    expect(await within(card).findByText("service app is given the host device /dev/dri")).toBeInTheDocument();
    await act(() => setLocale("es"));
    expect(await within(card).findByText("service app: recibe el dispositivo /dev/dri del servidor")).toBeInTheDocument();
    expect(within(card).getByText("A sentence that has no code yet.")).toBeInTheDocument();
    await act(() => setLocale("en"));
  });

  it("shows Compose's output when adopting would recreate, and adopts only once accepted", async () => {
    const output = " Container proggest-backend-1  Recreate\n Container proggest-backend-1  Recreated";
    const { backend, harness } = wizard({
      "POST /api/apps/adopt": (call) => {
        const body = call.body as { accept_recreate: boolean };
        if (!body.accept_recreate) {
          return json(409, {
            error: "adoptionrefusederror",
            detail: "Starting proggest.es as Noust would recreate containers",
            hint: "Read Compose's output; adopt with accept_recreate to go on.",
            fields: null,
            output,
          });
        }
        return json(200, { ...PREVIEW, changes: ["Container proggest-backend-1  Recreate"] });
      },
    });
    const { user } = harness;
    await openAdopt(user);
    await user.type(screen.getByRole("textbox", { name: "Domain" }), "proggest.es");
    await user.type(screen.getByRole("textbox", { name: "Directory" }), "/srv/proggest");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("Starting the stack as Noust would recreate containers")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent === output)).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /Adopt anyway, recreating what Compose lists/ }));
    await user.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("Starting the stack would change these, as you accepted")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/adopt").at(-1)?.body).toMatchObject({ preview: true, accept_recreate: true });
  });

  it("keeps the refusal and the focus when Adopt anyway is ticked, and forgets the choice when the stack changes", async () => {
    const output = " Container proggest-backend-1  Recreate";
    const { backend, harness } = wizard({
      "POST /api/apps/adopt": (call) => {
        const body = call.body as { accept_recreate: boolean };
        if (!body.accept_recreate) {
          return json(409, { error: "adoptionrefusederror", detail: "Starting proggest.es as Noust would recreate containers", hint: null, fields: null, output });
        }
        return json(200, { ...PREVIEW, changes: ["Container proggest-backend-1  Recreate"] });
      },
    });
    const { user } = harness;
    await openAdopt(user);
    await user.type(screen.getByRole("textbox", { name: "Domain" }), "proggest.es");
    await user.type(screen.getByRole("textbox", { name: "Directory" }), "/srv/proggest");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Starting the stack as Noust would recreate containers");

    const accept = screen.getByRole("checkbox", { name: /Adopt anyway, recreating what Compose lists/ });
    await user.click(accept);
    // What the operator just read is still there to read, and the box they ticked keeps focus.
    const ticked = screen.getByRole("checkbox", { name: /Adopt anyway, recreating what Compose lists/ });
    expect(ticked).toBeChecked();
    expect(ticked).toHaveFocus();
    expect(screen.getByText("Starting the stack as Noust would recreate containers")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent === output)).toBeInTheDocument();

    // Another directory is another stack: the choice made for the first one does not carry over.
    await user.type(screen.getByRole("textbox", { name: "Directory" }), "-two");
    expect(screen.queryByRole("checkbox", { name: /Adopt anyway/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Starting the stack as Noust would recreate containers");
    expect(backend.callsTo("POST /api/apps/adopt").at(-1)?.body).toMatchObject({ path: "/srv/proggest-two", preview: true, accept_recreate: false });
    expect(screen.getByRole("checkbox", { name: /Adopt anyway/ })).not.toBeChecked();
  });

  describe("a stack that runs with what gives this server to its containers", () => {
    const LINES = ["service backend mounts the Docker socket", "service backend is privileged"];
    const refusal = () =>
      json(409, {
        error: "adoptionrefusederror",
        detail: "Adopting proggest.es puts under Noust a stack whose containers have what is on this server",
        hint: "Each line above gives a container something of this server. Go on knowing that: noust app adopt proggest.es --accept-host-access",
        fields: null,
        output: null,
        host_access: LINES,
        coded: { code: "compose_guard.adopt_host_access", params: { domain: "proggest.es", lines: LINES.map((line) => `- ${line}`).join("\n"), command: "noust app adopt proggest.es --path /srv/proggest --accept-host-access" } },
      });
    const answer = (call: { body: unknown }) => {
      const body = call.body as { accept_host_access: boolean; preview: boolean };
      if (!body.accept_host_access) return refusal();
      return json(200, { ...PREVIEW, adopted: !body.preview, host_access: LINES, accepted_by: "admin", warnings: ["Recorded as accepted by admin: the containers of proggest.es keep what they have of this server (2)."] });
    };

    async function previewed(permissions?: string[]) {
      const session = permissions === undefined ? ELEVATED : { ...ELEVATED, permissions };
      const { backend, harness } = wizard({ "GET /api/auth/session": () => json(200, session), "POST /api/apps/adopt": answer });
      const { user } = harness;
      await openAdopt(user);
      await user.type(screen.getByRole("textbox", { name: "Domain" }), "proggest.es");
      await user.type(screen.getByRole("textbox", { name: "Directory" }), "/srv/proggest");
      await user.click(screen.getByRole("button", { name: "Preview" }));
      await screen.findByText("Adopting proggest.es puts under Noust a stack whose containers have what is on this server");
      return { backend, user };
    }

    it("lists each line as what it gives this server, and the box to accept them is not a button", async () => {
      const { backend } = await previewed();
      const list = screen.getByRole("list", { name: "What the containers of this stack have of this server" });
      expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual(LINES);
      // The terminal's command is the server's hint for a client without a console: here the way to go on is the box.
      expect(screen.queryByText(/--accept-host-access/)).not.toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: /I accept that the containers keep what these 2 lines give them of this server/ })).not.toBeChecked();
      expect(backend.callsTo("POST /api/apps/adopt").map((call) => (call.body as { accept_host_access: boolean }).accept_host_access)).toEqual([false]);
      await expectNoAxeViolations(screen.getByRole("main"));
    });

    it("previews again with them accepted and adopts with the acceptance, keeping the lines in sight", async () => {
      const { backend, user } = await previewed();
      await user.click(screen.getByRole("checkbox", { name: /I accept that the containers keep/ }));
      await user.click(screen.getByRole("button", { name: "Preview" }));
      expect(await screen.findByRole("heading", { name: "What Noust found" })).toBeInTheDocument();
      expect(backend.callsTo("POST /api/apps/adopt").at(-1)?.body).toMatchObject({ preview: true, accept_host_access: true });
      expect(screen.getByRole("list", { name: "What the containers of this stack have of this server" })).toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: /I accept that the containers keep/ })).toBeChecked();
      await user.click(screen.getByRole("button", { name: "Adopt" }));
      await waitFor(() => {
        expect(backend.callsTo("POST /api/apps/adopt").at(-1)?.body).toMatchObject({ preview: false, accept_host_access: true });
      });
    });

    it("forgets the acceptance when the stack changes: it was about the last one", async () => {
      const { backend, user } = await previewed();
      await user.click(screen.getByRole("checkbox", { name: /I accept that the containers keep/ }));
      await user.type(screen.getByRole("textbox", { name: "Directory" }), "-two");
      expect(screen.queryByRole("checkbox", { name: /I accept that the containers keep/ })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Preview" }));
      await screen.findByRole("checkbox", { name: /I accept that the containers keep/ });
      expect(backend.callsTo("POST /api/apps/adopt").at(-1)?.body).toMatchObject({ path: "/srv/proggest-two", accept_host_access: false });
      expect(screen.getByRole("checkbox", { name: /I accept that the containers keep/ })).not.toBeChecked();
    });

    it("says who can accept them, with nothing to tick, to an account that does not hold the permission", async () => {
      await previewed(["self", "apps.read", "apps.manage"]);
      expect(screen.getByText(/Only an account that holds the root-equivalent permission can accept this/)).toBeInTheDocument();
      expect(within(screen.getByRole("list", { name: "What the containers of this stack have of this server" })).getAllByRole("listitem")).toHaveLength(2);
      expect(screen.queryByRole("checkbox", { name: /I accept that the containers keep/ })).not.toBeInTheDocument();
    });
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    const { harness } = wizard();
    await openAdopt(harness.user, "Ya en marcha");
    expect(await screen.findByRole("textbox", { name: "Directorio" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previsualizar" })).toBeInTheDocument();
  });
});
