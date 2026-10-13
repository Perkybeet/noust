import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { CLEANUP_RUN, CLEANUP_SCHEDULE, onDesktop, serverRoutes } from "../testing";

beforeEach(onDesktop);

function storage(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({ ...signedInRoutes(), ...serverRoutes(), ...extra });
  const harness = renderConsole("/server/storage");
  return { ...harness, backend };
}

/** The card under a heading: a section with no name is not a region, so it is found by its title. */
async function cardTitled(name: string): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name });
  const card = heading.closest("section");
  if (card === null) throw new Error(`no card titled ${name}`);
  return card;
}

const ON = {
  ...CLEANUP_SCHEDULE,
  policy: { ...CLEANUP_SCHEDULE.policy, enabled: true, threshold_percent: 75, actions: ["journal", "releases"] },
  timer: { installed: true, next_run: "2026-09-30T02:00:00+00:00", last_run: "2026-09-29T02:00:00+00:00" },
};

describe("the automatic cleanup card", () => {
  it("says nothing is cleaned until a policy is set, and offers to set one", async () => {
    storage();
    const card = await cardTitled("Automatic cleanup");
    expect(await within(card).findByText("Off")).toBeInTheDocument();
    expect(within(card).getByText(/Nothing is cleaned unless you ask/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Set a policy" })).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Run now" })).not.toBeInTheDocument();
  });

  it("reads a policy back in words: when, at what fullness, what, and the timer's next run", async () => {
    storage({ "GET /api/server/storage/schedule": () => json(200, ON) });
    const card = await cardTitled("Automatic cleanup");
    await waitFor(() => {
      expect(within(card).getByText("On")).toBeInTheDocument();
    });
    expect(within(card).getByText("Every day at 02:00")).toBeInTheDocument();
    expect(within(card).getByText("When a disk is 75% full or more")).toBeInTheDocument();
    expect(within(card).getByText("System logs, Old deploys kept for rollback")).toBeInTheDocument();
    expect(within(card).getByText("Next run")).toBeInTheDocument();
  });

  it("warns when the policy is on but its timer is not installed", async () => {
    storage({ "GET /api/server/storage/schedule": () => json(200, { ...ON, timer: { installed: false, next_run: null, last_run: null } }) });
    expect(await screen.findByText("The timer is missing")).toBeInTheDocument();
  });

  it("saves a policy from the defaults, switched on, with the threshold the operator typed", async () => {
    const { user, backend } = storage({
      "PUT /api/server/storage/schedule": () => json(200, ON),
    });
    await user.click(await screen.findByRole("button", { name: "Set a policy" }));
    const threshold = await screen.findByRole("textbox", { name: /Run when a disk is this full/ });
    await user.clear(threshold);
    await user.type(threshold, "75");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/server/storage/schedule")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/server/storage/schedule")[0]?.body).toEqual({
      enabled: true,
      schedule: "*-*-* 02:00:00",
      threshold_percent: 75,
      actions: ["journal", "pkg-cache", "releases"],
      journal_mb: null,
      allow_docker: false,
    });
  });

  it("does not send a threshold that is not a percentage", async () => {
    const { user, backend } = storage();
    await user.click(await screen.findByRole("button", { name: "Set a policy" }));
    const threshold = await screen.findByRole("textbox", { name: /Run when a disk is this full/ });
    await user.clear(threshold);
    await user.type(threshold, "150");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText("Use a whole number from 0 to 99.")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/server/storage/schedule")).toHaveLength(0);
  });

  it("will not schedule a Docker action until the operator accepts what it deletes", async () => {
    const { user, backend } = storage({ "PUT /api/server/storage/schedule": () => json(200, ON) });
    await user.click(await screen.findByRole("button", { name: "Set a policy" }));
    await user.click(await screen.findByRole("checkbox", { name: /Docker build cache/ }));
    expect(await screen.findByText("These delete something you may want back")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText(/Accept it to schedule a Docker action/)).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/server/storage/schedule")).toHaveLength(0);

    await user.click(screen.getByRole("checkbox", { name: "I accept that this policy deletes them" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/server/storage/schedule")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/server/storage/schedule")[0]?.body).toMatchObject({
      actions: ["journal", "pkg-cache", "releases", "docker-build-cache"],
      allow_docker: true,
    });
  });

  it("runs the policy now as a job, after listing what it cleans", async () => {
    const { user, backend } = storage({
      "GET /api/server/storage/schedule": () => json(200, ON),
      "POST /api/server/storage/schedule/run": () => json(202, { job_id: "j9", message: "Cleanup queued", status: "pending" }),
    });
    await user.click(await screen.findByRole("button", { name: "Run now" }));
    const dialog = await screen.findByRole("dialog", { name: "Run the cleanup policy now?" });
    expect(within(within(dialog).getByRole("list", { name: "What the policy cleans" })).getAllByRole("listitem")).toHaveLength(2);
    await user.click(within(dialog).getByRole("button", { name: "Run the policy" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/storage/schedule/run")).toHaveLength(1);
    });
  });
});

describe("the cleanup history", () => {
  it("says no cleanup has run yet", async () => {
    storage();
    expect(await screen.findByText("No cleanup has run yet.")).toBeInTheDocument();
  });

  it("lists the runs and opens one to show what each action did and why one failed, verbatim", async () => {
    const { user } = storage({ "GET /api/server/storage/history": () => json(200, [CLEANUP_RUN]) });
    const table = await screen.findByRole("table", { name: "Cleanups that ran on this server" });
    const row = await within(table).findByRole("row", { name: /The timer/ });
    expect(within(row).getByText("Partly done")).toBeInTheDocument();
    expect(within(row).getByText("81% to 62%")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Details of cleanup 7" }));

    const drawer = await screen.findByRole("dialog", { name: "Cleanup 7" });
    const actions = within(drawer).getByRole("list", { name: "What each action did" });
    expect(within(actions).getAllByRole("listitem").length).toBeGreaterThanOrEqual(3);
    // What was removed is named, and the failure is the tool's own output.
    expect(within(drawer).getByText("sha256:ab12cd34ef56")).toBeInTheDocument();
    expect(within(drawer).getByText("rm: cannot remove '/var/www/apps/shop/releases/20260101': Device or resource busy")).toBeInTheDocument();
  });

  it("says a skipped run did nothing, and why", async () => {
    storage({
      "GET /api/server/storage/history": () =>
        json(200, [{ id: 8, trigger: "schedule", status: "skipped", started_at: "2026-09-30T02:00:00+00:00", mount: "/", percent_before: 41.2, actions: [], reason: "The fullest disk is at 41%, under the 80% threshold." }]),
    });
    const table = await screen.findByRole("table", { name: "Cleanups that ran on this server" });
    const row = await within(table).findByRole("row", { name: /The timer/ });
    expect(within(row).getByText("Skipped")).toBeInTheDocument();
  });

  it("says in Spanish what it says in English, and passes axe", async () => {
    await act(async () => {
      await setLocale("es");
    });
    storage({
      "GET /api/server/storage/schedule": () => json(200, ON),
      "GET /api/server/storage/history": () => json(200, [CLEANUP_RUN]),
    });
    const card = await cardTitled("Limpieza automática");
    expect(await within(card).findByText("Cuando un disco está al 75% o más")).toBeInTheDocument();
    expect(await cardTitled("Historial de limpiezas")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
