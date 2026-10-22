import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "./testRoutes";

const JOB = {
  id: "5ca1ab1e",
  type: "update",
  name: `Update ${TAB_DOMAIN}`,
  description: "",
  status: "pending",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-10-16T19:00:00",
  logs: [],
  metadata: { domain: TAB_DOMAIN },
};

const FROZEN = {
  error: "deploys_frozen",
  detail: "Deploys of shop.example.com are frozen until 2026-12-24 18:00 by ana: Black Friday: nothing ships",
  hint: "Lift it with 'noust deploy unfreeze', or deploy anyway with --during-freeze.",
  fields: null,
  output: null,
  freeze: { id: 7, scope: "shop.example.com" },
  automatic: false,
};

const FREEZE = {
  id: 7,
  scope: "server",
  domain: null,
  reason: "Datacentre move",
  starts_at: "2026-10-16T08:00:00+00:00",
  ends_at: "2999-01-01T00:00:00+00:00",
  created_by: "ana",
  created_at: "2026-10-16T08:00:00+00:00",
  lifted_at: null,
  lifted_by: null,
  state: "active",
};

const ROLLOUT = {
  domain: TAB_DOMAIN,
  history_keep: null,
  history_keep_server: 20,
  history_keep_effective: 20,
  stop_timeout: null,
  stop_signal: null,
  stop_supported: true,
  pause_on_rollback: true,
  paused: null,
  held: 0,
  automatic: ["webhook"],
  freeze: null,
};

const WAITING = "20261016-120000-bbbbbbb";
const SERVING = "20261015-120000-aaaaaaa";
const RELEASES = {
  domain: TAB_DOMAIN,
  items: [
    { id: WAITING, commit: "bbbbbbb", created_at: "2026-10-16T12:00:00+00:00", activated_at: null, status: "built", active: false, on_disk: true, waiting: true },
    { id: SERVING, commit: "aaaaaaa", created_at: "2026-10-15T12:00:00+00:00", activated_at: "2026-10-15T12:00:40+00:00", status: "active", active: true, on_disk: true, waiting: false },
  ],
  total: 2,
};

async function open(path: string, app: Record<string, unknown>, extra: Record<string, RouteHandler>) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(app, {
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
      [`GET /api/apps/${TAB_DOMAIN}/rollback-points`]: () => json(200, { items: [], total: 0 }),
      ...extra,
    }),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}${path}`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

/** The row of the build that waits, in the one list of the deployments tab. */
async function waitingRow(): Promise<HTMLElement> {
  const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
  const row = (await within(table).findByText("Built, not serving")).closest("tr");
  if (row === null) throw new Error("no row for the build that waits");
  return row;
}

// Whole-console renders: generous under a loaded machine or a slow CI runner.
describe("a release that was built and is waiting", { timeout: 20_000 }, () => {
  const releases = { [`GET /api/apps/${TAB_DOMAIN}/releases`]: () => json(200, RELEASES) };

  it("is told apart in the one list, and offered to serve, not to go back to", async () => {
    const { user } = await open("/deployments", { layout: "releases" }, releases);

    const row = await waitingRow();
    expect(within(row).getByText("bbbbbbb")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Actions for Built, not serving" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Go live"]);
    await user.keyboard("{Escape}");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("makes it serve after asking, with nothing built again", async () => {
    const { user, backend } = await open("/deployments", { layout: "releases" }, {
      ...releases,
      [`POST /api/apps/${TAB_DOMAIN}/releases/${WAITING}/promote`]: () => json(202, { job_id: JOB.id, status: "pending", message: "queued", job: JOB }),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, { ...JOB, status: "running" }),
    });

    await user.click(within(await waitingRow()).getByRole("button", { name: "Actions for Built, not serving" }));
    await user.click(await screen.findByRole("menuitem", { name: "Go live" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Make release ${WAITING} serve?` });
    expect(within(confirm).getByText(/Commit bbbbbbb was built earlier and is not serving/)).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/releases/${WAITING}/promote`)).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Go live" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/releases/${WAITING}/promote`)[0]?.body).toEqual({ schema_changed_ok: false, frozen_ok: false });
    });
  });

  it("asks again, saying it must go ahead, when a freeze refuses it", async () => {
    const { user, backend } = await open("/deployments", { layout: "releases" }, {
      ...releases,
      [`POST /api/apps/${TAB_DOMAIN}/releases/${WAITING}/promote`]: (call) =>
        (call.body as { frozen_ok?: boolean }).frozen_ok === true ? json(202, { job_id: JOB.id, status: "pending", message: "queued", job: JOB }) : json(409, FROZEN),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, { ...JOB, status: "running" }),
    });

    await user.click(within(await waitingRow()).getByRole("button", { name: "Actions for Built, not serving" }));
    await user.click(await screen.findByRole("menuitem", { name: "Go live" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Make release ${WAITING} serve?` });
    await user.click(within(confirm).getByRole("button", { name: "Go live" }));

    const dialog = await screen.findByRole("dialog", { name: `Deploys of ${TAB_DOMAIN} are frozen` });
    // The freeze's own sentence, verbatim.
    expect(within(dialog).getByText(FROZEN.detail)).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Deploy anyway" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/releases/${WAITING}/promote`).map((call) => call.body)).toEqual([
        { schema_changed_ok: false, frozen_ok: false },
        { schema_changed_ok: false, frozen_ok: true },
      ]);
    });
  });
});

describe("building without serving", { timeout: 20_000 }, () => {
  it("is offered to an app with releases, and queues a build that waits", async () => {
    const { user, backend } = await open("", { layout: "releases" }, {
      [`POST /api/apps/${TAB_DOMAIN}/releases/stage`]: () => json(202, { job_id: JOB.id, status: "pending", message: "queued", job: { ...JOB, type: "stage" } }),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, { ...JOB, status: "running" }),
    });

    await user.click(await screen.findByRole("button", { name: "More options for Update" }));
    const menu = await screen.findByRole("menu");
    await user.click(within(menu).getByRole("menuitem", { name: /Build without serving/ }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/releases/stage`)[0]?.body).toEqual({ no_cache: false });
    });
  });

  it("is not offered to an app kept in one folder", async () => {
    const { user } = await open("", { layout: "inplace" }, {});

    await user.click(await screen.findByRole("button", { name: "More options for Update" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).queryByRole("menuitem", { name: /Build without serving/ })).not.toBeInTheDocument();
  });
});

describe("an update during a freeze", { timeout: 20_000 }, () => {
  it("says the freeze in its own words and goes ahead only when told to", async () => {
    const { user, backend } = await open("", {}, {
      "POST /api/jobs/update": (call) =>
        (call.body as { frozen_ok?: boolean }).frozen_ok === true ? json(202, { message: "queued", job: JOB }) : json(409, FROZEN),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, { ...JOB, status: "running" }),
    });

    await user.click(await screen.findByRole("button", { name: "Update" }));
    const dialog = await screen.findByRole("dialog", { name: `Deploys of ${TAB_DOMAIN} are frozen` });
    expect(within(dialog).getByText(FROZEN.detail)).toBeInTheDocument();
    expect(within(dialog).getByText(FROZEN.hint)).toBeInTheDocument();
    // Said once, before anything is queued over the freeze.
    expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(1);
    await user.click(within(dialog).getByRole("button", { name: "Deploy anyway" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/update").map((call) => call.body)).toEqual([
        { domain: TAB_DOMAIN, force: false },
        { domain: TAB_DOMAIN, force: false, frozen_ok: true },
      ]);
    });
  });

  it("leaves it alone when the answer is to leave it", async () => {
    const { user, backend } = await open("", {}, { "POST /api/jobs/update": () => json(409, FROZEN) });

    await user.click(await screen.findByRole("button", { name: "Update" }));
    const dialog = await screen.findByRole("dialog", { name: `Deploys of ${TAB_DOMAIN} are frozen` });
    await user.click(within(dialog).getByRole("button", { name: "Leave it" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: `Deploys of ${TAB_DOMAIN} are frozen` })).not.toBeInTheDocument();
    });
    expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(1);
  });
});

describe("what holds the deploys of an app", { timeout: 20_000 }, () => {
  it("says a freeze and a pause on the deployments page", async () => {
    await open("/deployments", {}, {
      [`GET /api/apps/${TAB_DOMAIN}/rollout`]: () =>
        json(200, { ...ROLLOUT, freeze: FREEZE, paused: { at: "2026-10-16T10:00:00+00:00", by: "ana", reason: "Looking into the checkout bug" }, held: 2 }),
    });

    expect(await screen.findByText("Deploys are frozen on this server")).toBeInTheDocument();
    expect(screen.getByText(/Datacentre move/)).toBeInTheDocument();
    expect(screen.getByText("Automatic deploys are paused")).toBeInTheDocument();
    expect(screen.getByText("Reason: Looking into the checkout bug")).toBeInTheDocument();
    expect(screen.getByText("2 pushes or tags have been held since.")).toBeInTheDocument();
  });

  it("says nothing when nothing holds them", async () => {
    await open("/deployments", {}, { [`GET /api/apps/${TAB_DOMAIN}/rollout`]: () => json(200, ROLLOUT) });

    await screen.findByText("No deploys recorded yet");
    expect(screen.queryByText("Automatic deploys are paused")).not.toBeInTheDocument();
    expect(screen.queryByText(/are frozen/)).not.toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await open("/deployments", {}, { [`GET /api/apps/${TAB_DOMAIN}/rollout`]: () => json(200, { ...ROLLOUT, freeze: { ...FREEZE, scope: TAB_DOMAIN, domain: TAB_DOMAIN } }) });
    await screen.findByText(`Deploys of ${TAB_DOMAIN} are frozen`);

    await act(() => setLocale("es"));

    expect(await screen.findByText(`Los despliegues de ${TAB_DOMAIN} están congelados`)).toBeInTheDocument();
  });
});
