import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { FakeEventSource, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { confirmation, retentionOutcome } from "./InstantRollback";
import { planItems } from "./MigrationPlanView";
import { DOMAIN, ELEVATED, IN_PLACE_APP, RELEASE_APP, STATIC_APP, ZERO_DOWNTIME_OFF, accepted, jobOf, part, renderSettings, saveBar } from "./testKit";
import { instanceName, parseDrain } from "./zeroDowntime";

const RELEASES = {
  domain: DOMAIN,
  items: [
    { id: "20260925-184247-2a8b7c4", commit: "2a8b7c4", created_at: "2026-09-25T18:42:47+00:00", activated_at: "2026-09-25T18:43:40+00:00", status: "active", active: true, on_disk: true },
    { id: "20260924-194023-19d3f6e", commit: "19d3f6e", created_at: "2026-09-24T19:40:23+00:00", activated_at: null, status: "superseded", active: false, on_disk: true },
    { id: "20260916-182823-d08e4f7", commit: "d08e4f7", created_at: "2026-09-16T18:28:23+00:00", activated_at: null, status: "failed", active: false, on_disk: false },
  ],
  total: 3,
};

const PLAN = {
  domain: DOMAIN,
  app_path: "/var/www/apps/shop-example-com",
  release_id: "20260925-205239-nogit",
  commit: null,
  persistent: ["uploads", "storage"],
  persistent_source: "common",
  env_files: [".env"],
  unit: "shop-example-com",
  unit_rewrite: true,
  site_rewrite: false,
  untracked_files: [],
  warnings: ["/var/www/apps/shop-example-com is not a git checkout, so what the application wrote for itself cannot be told apart from its code."],
  files: 7,
  bytes: 661,
};

const MIGRATE_JOB = jobOf("m1", "migrate");

const ZERO_DOWNTIME_ON = {
  ...ZERO_DOWNTIME_OFF,
  enabled: true,
  active_color: "blue",
  instances: [
    { color: "blue", unit: "shop-example-com-blue", port: 3001, release: "20260925-184247-2a8b7c4", serving: true, state: "active" },
    { color: "green", unit: "shop-example-com-green", port: 3002, release: "20260924-194023-19d3f6e", serving: false, state: "inactive" },
  ],
  upstream_port: 3001,
};

const ZD_JOB = jobOf("zd1", "zero_downtime");

function deploys(app: object = RELEASE_APP, routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/deploys", "Deploys", app, {
    [`GET /api/apps/${DOMAIN}/releases`]: () => json(200, RELEASES),
    ...routes,
  });
}

/** Confirms "Confirm it's you" with a code, as the operator would. */
async function confirmItsYou(user: Awaited<ReturnType<typeof deploys>>["user"]): Promise<void> {
  const elevate = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(elevate).getByRole("textbox"), "123456");
  await user.click(within(elevate).getByRole("button", { name: "Confirm" }));
}

describe("instant rollback, on", () => {
  it("says what is live, what can be gone back to, and how many versions are kept", async () => {
    await deploys();
    const card = part("Instant rollback");
    expect(within(card).getByText("On")).toBeInTheDocument();
    expect(within(card).getByText("Each deploy is kept apart; going back takes seconds.")).toBeInTheDocument();
    expect(await within(card).findByText("20260925-184247-2a8b7c4")).toBeInTheDocument();
    expect(within(card).getByText("2 versions")).toBeInTheDocument();
    expect(within(card).getByText("1 more listed whose build failed and was removed")).toBeInTheDocument();
    expect(within(card).getByRole("textbox", { name: "Versions to keep" })).toHaveValue("5");
    expect(within(card).getByRole("link", { name: "Go back to an earlier version" })).toHaveAttribute("href", `/apps/${DOMAIN}/deployments`);
  });

  it("saves the startup check and how many versions to keep from one save bar, asking who it is once", async () => {
    let app = RELEASE_APP;
    const { user, backend } = await deploys(RELEASE_APP, {
      [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
      "POST /api/auth/elevate": () => json(200, { elevated_until: "2999-01-01T00:00:00+00:00" }),
      [`PATCH /api/apps/${DOMAIN}/health`]: () => {
        app = { ...app, health_path: "/healthz" } as typeof app;
        return json(200, { domain: DOMAIN, path: "/healthz", expect: null, timeout: null, effective_path: "/healthz", effective_expect: "any status below 500", effective_timeout: 30 });
      },
      [`PATCH /api/apps/${DOMAIN}/releases/retention`]: () => {
        app = { ...app, keep_releases: 7 };
        return json(200, { domain: DOMAIN, keep_releases: 7, pruned: [] });
      },
    });
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    expect(within(saveBar()).getByRole("button", { name: "Save" })).toBeDisabled();

    const check = part("Startup check");
    await user.type(within(check).getByRole("textbox", { name: "Path" }), "/healthz");
    const keep = within(part("Instant rollback")).getByRole("textbox", { name: "Versions to keep" });
    await user.clear(keep);
    await user.type(keep, "7");
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/health`)[0]?.body).toEqual({ path: "/healthz", expect: null, timeout: null });
    });
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/releases/retention`)[0]?.body).toEqual({ keep: 7 });
    });
    expect(backend.callsTo("POST /api/auth/elevate")).toHaveLength(1);
    expect(await screen.findByText("Saved. It keeps 7 versions; nothing was removed.")).toBeInTheDocument();
    expect(await within(saveBar()).findByText("No unsaved changes")).toBeInTheDocument();
  });

  it("checks every field before asking anything, and says each mistake where it is", async () => {
    const { user, backend } = await deploys();
    const check = part("Startup check");
    const path = within(check).getByRole("textbox", { name: "Path" });
    await user.type(path, "https://example.com/health");
    const keep = within(part("Instant rollback")).getByRole("textbox", { name: "Versions to keep" });
    await user.clear(keep);
    await user.type(keep, "0");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await within(check).findByText(/A scheme or a host is not accepted/)).toBeInTheDocument();
    expect(path).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Keep from 1 to 50 versions.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Confirm it's you" })).not.toBeInTheDocument();
    expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/health`)).toHaveLength(0);
  });

  it("puts a refusal under the field it names, and still saves the rest", async () => {
    const { user, backend } = await deploys(RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`PATCH /api/apps/${DOMAIN}/health`]: () => problem(400, "validationerror", "A timeout of 90 seconds is longer than the gate allows", { hint: "Use 5 to 60." }),
      [`PATCH /api/apps/${DOMAIN}/releases/retention`]: () => json(200, { domain: DOMAIN, keep_releases: 3, pruned: ["20260920-100000-aaaaaaa"] }),
    });
    await user.type(within(part("Startup check")).getByRole("textbox", { name: "Timeout" }), "90");
    const keep = within(part("Instant rollback")).getByRole("textbox", { name: "Versions to keep" });
    await user.clear(keep);
    await user.type(keep, "3");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("A timeout of 90 seconds is longer than the gate allows. Use 5 to 60.")).toBeInTheDocument();
    expect(await screen.findByText("Saved. It keeps 3 versions; removed 1 version: 20260920-100000-aaaaaaa.")).toBeInTheDocument();
    expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/releases/retention`)).toHaveLength(1);
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
  });

  it("discards what was typed, and puts the startup check back to its defaults", async () => {
    const withCheck = { ...RELEASE_APP, health_path: "/up", health_timeout: 60 };
    const { user, backend } = await deploys(withCheck, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`PATCH /api/apps/${DOMAIN}/health`]: () =>
        json(200, { domain: DOMAIN, path: null, expect: null, timeout: null, effective_path: "/", effective_expect: "any status below 500", effective_timeout: 30 }),
    });
    const check = part("Startup check");
    expect(within(check).getByText("GET /up")).toBeInTheDocument();
    const path = within(check).getByRole("textbox", { name: "Path" });
    await user.clear(path);
    await user.type(path, "/other");
    await user.click(within(saveBar()).getByRole("button", { name: "Discard" }));
    expect(path).toHaveValue("/up");

    await user.click(within(check).getByRole("button", { name: "Use defaults" }));
    expect(path).toHaveValue("");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/health`)[0]?.body).toEqual({ path: null, expect: null, timeout: null });
    });
  });

  it("has no accessibility violations", async () => {
    await deploys();
    await within(part("Instant rollback")).findByText("20260925-184247-2a8b7c4");
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("turning on instant rollback", () => {
  function inPlace(routes: Record<string, RouteHandler> = {}) {
    return deploys(IN_PLACE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}/migrate/plan`]: () => json(200, PLAN),
      [`POST /api/apps/${DOMAIN}/migrate`]: () => json(202, accepted(MIGRATE_JOB)),
      [`GET /api/jobs/${MIGRATE_JOB.id}`]: () => json(200, MIGRATE_JOB),
      ...routes,
    });
  }

  it("says the benefit first, what changes on disk, the restart and the undo, before anything is read", async () => {
    const { backend } = await inPlace();
    const card = part("Instant rollback");
    expect(within(card).getByText("Off")).toBeInTheDocument();
    expect(within(card).getByText(/^Safe updates: if a new version fails/)).toBeInTheDocument();
    expect(within(card).getByText(/each deploy gets a folder of its own under/)).toBeInTheDocument();
    expect(within(card).getByText("Turning it on restarts the app once, for a moment.")).toBeInTheDocument();
    expect(within(card).getByText(/everything is put back as it was\. Nothing is deleted\./)).toBeInTheDocument();
    expect(within(card).getByText(/The first step is a dry run/)).toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/migrate/plan`)).toHaveLength(0);
  });

  it("shows the dry run, confirms, and follows the move to its end", async () => {
    const { user, backend } = await inPlace();
    await user.click(within(part("Instant rollback")).getByRole("button", { name: "Turn on instant rollback…" }));
    const dialog = await screen.findByRole("dialog", { name: `Turn on instant rollback for ${DOMAIN}` });
    expect(within(dialog).getByRole("list", { name: "Steps" })).toBeInTheDocument();
    expect(within(dialog).getByText("What changes: current step")).toBeInTheDocument();
    expect(await within(dialog).findByText(PLAN.warnings[0] ?? "")).toBeInTheDocument();
    expect(within(dialog).getByText("uploads, storage")).toBeInTheDocument();
    expect(within(dialog).getByText("shop-example-com, rewritten to run the live version")).toBeInTheDocument();
    expect(within(dialog).getByText("7 files, 661 B")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/migrate`)).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(within(dialog).getByText(/becomes its first version, uploads, storage move to shared\//)).toBeInTheDocument();
    expect(within(dialog).getByText("The app restarts once")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Turn it on" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/migrate`)[0]?.body).toEqual({ persist: ["uploads", "storage"] });
    });
    expect(await within(dialog).findByText(/Moving the app onto its first version/)).toBeInTheDocument();

    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", {
        ...MIGRATE_JOB,
        status: "completed",
        progress: 100,
        result: { domain: DOMAIN, status: "migrated", release_id: "20260925-205300-nogit", persistent: ["uploads", "storage"], env_files: [".env"], files_before: 7, files_after: 7, bytes_before: 661, bytes_after: 661, unit_rewritten: true, site_rewritten: false, deployment_id: 40 },
      });
    });
    expect(await within(dialog).findByText(/7 files before, 7 after/)).toBeInTheDocument();
    expect(within(dialog).getByText("20260925-205300-nogit")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: `Turn on instant rollback for ${DOMAIN}` })).not.toBeInTheDocument();
    });
  });

  it("says what is missing when Continue is pressed before the dry run is read", async () => {
    const { user } = await inPlace({ [`GET /api/apps/${DOMAIN}/migrate/plan`]: () => new Promise<Response>(() => undefined) });
    await user.click(within(part("Instant rollback")).getByRole("button", { name: "Turn on instant rollback…" }));
    const dialog = await screen.findByRole("dialog", { name: `Turn on instant rollback for ${DOMAIN}` });
    const next = within(dialog).getByRole("button", { name: "Continue" });
    expect(next).toBeEnabled();
    await user.click(next);
    expect(await within(dialog).findByText("Wait for the dry run to finish, or check again if it failed.")).toBeInTheDocument();
    expect(within(dialog).getByText("What changes: current step")).toBeInTheDocument();
  });

  it("shows why the move failed, and that it was undone", async () => {
    const { user } = await inPlace();
    await user.click(within(part("Instant rollback")).getByRole("button", { name: "Turn on instant rollback…" }));
    const dialog = await screen.findByRole("dialog", { name: `Turn on instant rollback for ${DOMAIN}` });
    await within(dialog).findByText("uploads, storage");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    await user.click(within(dialog).getByRole("button", { name: "Turn it on" }));
    await within(dialog).findByText(/Moving the app onto its first version/);
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...MIGRATE_JOB, status: "failed", error: "shop.example.com did not answer on the new layout: http://127.0.0.1:3000/ refused the connection" });
    });
    expect(await within(dialog).findByText(/did not answer on the new layout/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Everything was put back as it was/)).toBeInTheDocument();
  });

  it("has no accessibility violations in the dry run", async () => {
    const { user } = await inPlace();
    await user.click(within(part("Instant rollback")).getByRole("button", { name: "Turn on instant rollback…" }));
    const dialog = await screen.findByRole("dialog", { name: `Turn on instant rollback for ${DOMAIN}` });
    await within(dialog).findByText("uploads, storage");
    await expectNoAxeViolations(dialog);
  });
});

describe("zero-downtime deploys", () => {
  it("warns about two copies before turning it on, sends the seconds, and waits for the job", async () => {
    let state: object = ZERO_DOWNTIME_OFF;
    let polls = 0;
    const { user, backend } = await deploys(RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}/zero-downtime`]: () => json(200, state),
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () => json(202, accepted(ZD_JOB)),
      [`GET /api/jobs/${ZD_JOB.id}`]: () => {
        polls += 1;
        state = { ...ZERO_DOWNTIME_ON, drain_seconds: 20 };
        return json(200, { ...ZD_JOB, status: "completed", progress: 100 });
      },
    });
    await screen.findByRole("button", { name: "Turn on…" });
    const card = part("Zero-downtime deploys");
    expect(within(card).getByText("Off")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Turn on…" }));

    const dialog = await screen.findByRole("dialog", { name: `Turn on zero-downtime deploys for ${DOMAIN}?` });
    expect(within(dialog).getByText("Two copies of the app run at once for a few seconds")).toBeInTheDocument();
    expect(within(dialog).getByText(/a SQLite database both copies write to/)).toBeInTheDocument();
    const drain = within(dialog).getByRole("textbox", { name: "Keep the old version for" });
    expect(drain).toHaveValue("10");
    await user.clear(drain);
    await user.type(drain, "20");
    await user.click(within(dialog).getByRole("button", { name: "Turn on" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)[0]?.body).toEqual({ enabled: true, drain_seconds: 20 });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: `Turn on zero-downtime deploys for ${DOMAIN}?` })).not.toBeInTheDocument();
    });
    expect(polls).toBeGreaterThan(0);
    expect(await within(part("Zero-downtime deploys")).findByText("On")).toBeInTheDocument();
    expect(within(part("Zero-downtime deploys")).getByRole("list", { name: "Copies of the app" })).toBeInTheDocument();
  });

  it("names both copies, the live one, and saves how long the old one stays from the save bar", async () => {
    const { user, backend } = await deploys(RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}/zero-downtime`]: () => json(200, ZERO_DOWNTIME_ON),
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () => json(202, accepted(ZD_JOB)),
      [`GET /api/jobs/${ZD_JOB.id}`]: () => json(200, { ...ZD_JOB, status: "completed", progress: 100 }),
    });
    const copies = await screen.findByRole("list", { name: "Copies of the app" });
    const card = part("Zero-downtime deploys");
    const [blue, green] = within(copies).getAllByRole("listitem");
    if (!blue || !green) throw new Error("two copies expected");
    expect(within(blue).getByText("Blue")).toBeInTheDocument();
    expect(within(blue).getByText("Live")).toBeInTheDocument();
    expect(within(green).getByText("Idle")).toBeInTheDocument();
    expect(within(green).getByText("3002")).toBeInTheDocument();

    const drain = within(card).getByRole("textbox", { name: "Keep the old version for" });
    await user.clear(drain);
    await user.type(drain, "30");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)[0]?.body).toEqual({ enabled: true, drain_seconds: 30 });
    });
  });

  it("refuses seconds out of range before asking", async () => {
    const { user, backend } = await deploys(RELEASE_APP, {
      [`GET /api/apps/${DOMAIN}/zero-downtime`]: () => json(200, ZERO_DOWNTIME_ON),
    });
    const drain = await screen.findByRole("textbox", { name: "Keep the old version for" });
    await user.clear(drain);
    await user.type(drain, "400");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("From 0 to 300 seconds, not 400.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)).toHaveLength(0);
  });

  it("asks once before turning it off", async () => {
    const { user, backend } = await deploys(RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}/zero-downtime`]: () => json(200, ZERO_DOWNTIME_ON),
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () => json(202, accepted(ZD_JOB)),
      [`GET /api/jobs/${ZD_JOB.id}`]: () => json(200, { ...ZD_JOB, status: "completed", progress: 100 }),
    });
    await user.click(await screen.findByRole("button", { name: "Turn off" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Turn off zero-downtime deploys for ${DOMAIN}?` });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Turn off" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)[0]?.body).toEqual({ enabled: false });
    });
  });

  it("tells a single-folder app it needs instant rollback first, and says the backend's reason otherwise", async () => {
    const notEligible = { ...ZERO_DOWNTIME_OFF, eligible: false, reason: "shop.example.com is deployed in place; blue/green runs two releases side by side", hint: null };
    const inPlace = await deploys(IN_PLACE_APP, { [`GET /api/apps/${DOMAIN}/zero-downtime`]: () => json(200, notEligible) });
    expect(await screen.findByText("They need instant rollback, above: turn that on first.")).toBeInTheDocument();
    expect(screen.queryByText(/blue\/green runs two releases/)).not.toBeInTheDocument();
    inPlace.unmount();

    await deploys(RELEASE_APP, {
      [`GET /api/apps/${DOMAIN}/zero-downtime`]: () =>
        json(200, { ...notEligible, reason: "The unit name of shop.example.com is too long for its instances", hint: "Its instances' names would be longer than 256 characters." }),
    });
    await screen.findByText("The unit name of shop.example.com is too long for its instances");
    const card = part("Zero-downtime deploys");
    expect(within(card).getByText("Its instances' names would be longer than 256 characters.")).toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("a static site", () => {
  it("has nothing to check or run twice, and nothing to save", async () => {
    await deploys(STATIC_APP);
    expect(within(part("Startup check")).getByText(/A static site is served as files by the web server/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Zero-downtime deploys" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });
});

describe("what the dry run and the save say", () => {
  it("says a save that pruned nothing removed nothing, and names what it removed", () => {
    expect(retentionOutcome({ keep_releases: 5, pruned: [] })).toBe("Saved. It keeps 5 versions; nothing was removed.");
    expect(retentionOutcome({ keep_releases: 2, pruned: ["a", "b"] })).toBe("Saved. It keeps 2 versions; removed 2 versions: a, b.");
  });

  it("describes the move from its plan", () => {
    expect(confirmation(PLAN)).toBe(
      "The folder the app runs from now becomes its first version, uploads, storage move to shared/, and the service is rewritten to run the live version. Nothing is deleted or copied.",
    );
    expect(planItems(PLAN).map((item) => item.label)).toEqual(["First version", "Commit", "Kept in shared/", "Environment", "Service", "Site", "Files"]);
  });

  it("reads the seconds as the backend will and names the copies", () => {
    expect(parseDrain("0")).toEqual({ seconds: 0, error: null });
    expect(parseDrain(" 300 ")).toEqual({ seconds: 300, error: null });
    expect(parseDrain("301").error).toBe("From 0 to 300 seconds, not 301.");
    expect(parseDrain("1.5").error).toBe("Give a whole number of seconds from 0 to 300.");
    expect(instanceName("blue")).toBe("Blue");
    expect(instanceName("")).toBe("Copy");
  });

  it("says the same in Spanish", async () => {
    await setLocale("es");
    expect(retentionOutcome({ keep_releases: 5, pruned: [] }, "es")).toBe("Guardado. Mantiene 5 versiones; no se eliminó nada.");
    expect(confirmation(PLAN, "es")).toMatch(/^La carpeta desde la que se ejecuta ahora la aplicación pasa a ser su primera versión/);
    expect(parseDrain("301", "es").error).toBe("De 0 a 300 segundos, no 301.");
    expect(instanceName("green", "es")).toBe("Verde");
  });
});

describe("the Deploys subsection in Spanish", () => {
  it("speaks of the benefit, with no accessibility violations", async () => {
    await deploys(IN_PLACE_APP);
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Despliegues" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Vuelta atrás instantánea" })).toBeInTheDocument();
    expect(screen.getByText(/^Actualizaciones seguras/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activar la vuelta atrás instantánea…" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Comprobación de arranque" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
