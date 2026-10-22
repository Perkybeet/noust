import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, renderSettings } from "./testKit";

const ROLLOUT = `/api/apps/${DOMAIN}/rollout`;
const FREEZES = "/api/deploy-freezes";
const SCHEDULE = `/api/apps/${DOMAIN}/update-schedule`;
const BACKUP = `/api/apps/${DOMAIN}/backup-required`;

const RUNNING = {
  domain: DOMAIN,
  history_keep: null,
  history_keep_server: 20,
  history_keep_effective: 20,
  stop_timeout: null,
  stop_signal: null,
  stop_supported: true,
  pause_on_rollback: true,
  paused: null,
  held: 0,
  automatic: ["webhook", "tags"],
  freeze: null,
};

const PAUSED = { ...RUNNING, paused: { at: "2026-10-16T10:00:00+00:00", by: "ana", reason: "Looking into the checkout bug" }, held: 3 };

const ALIASES = { daily: "*-*-* 03:00:00", weekly: "Sun *-*-* 03:00:00", monthly: "*-*-01 03:00:00" };
const SCHEDULE_OFF = {
  domain: DOMAIN,
  scheduled: false,
  calendar: null,
  enabled: true,
  backup_first: true,
  only_if_new: true,
  next_run: null,
  last_run_at: null,
  last_outcome: null,
  last_detail: null,
  last_deployment_id: null,
  aliases: ALIASES,
};
const SCHEDULE_ON = {
  ...SCHEDULE_OFF,
  scheduled: true,
  calendar: ALIASES.weekly,
  next_run: "2999-01-01T03:00:00+00:00",
  last_run_at: "2026-10-12T03:00:00+00:00",
  last_outcome: "backup_failed",
  last_detail: "disk full",
};

const FREEZE = {
  id: 7,
  scope: DOMAIN,
  domain: DOMAIN,
  reason: "Black Friday: nothing ships",
  starts_at: "2026-11-27T00:00:00+00:00",
  ends_at: "2026-11-28T00:00:00+00:00",
  created_by: "ana",
  created_at: "2026-10-16T10:00:00+00:00",
  lifted_at: null,
  lifted_by: null,
  state: "active",
};
const SERVER_FREEZE = { ...FREEZE, id: 8, scope: "server", domain: null, reason: "Datacentre move" };

const SESSION_ROUTES: Record<string, RouteHandler> = { "GET /api/auth/session": () => json(200, ELEVATED) };

function automation(routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/automation", "Automation", RELEASE_APP, { ...SESSION_ROUTES, ...routes });
}

describe("the page", { timeout: 20_000 }, () => {
  it("is three groups of rows, with no save bar: a subsection saves in one way", async () => {
    await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_OFF),
      [`GET ${BACKUP}`]: () => json(200, { domain: DOMAIN, required: true, explicit: false, default_required: true, databases: ["main"] }),
    });

    await screen.findByRole("switch", { name: "Automatic deploys" });
    for (const group of ["Automatic deploys", "Backups before an update", "Holds and schedules"]) {
      expect(screen.getByRole("heading", { name: group })).toBeInTheDocument();
    }
    expect(screen.queryByRole("region", { name: "Unsaved changes" })).not.toBeInTheDocument();
    // The old cards of Deploys are not here, and Compose's rows are only for a stack.
    expect(screen.queryByRole("heading", { name: "Instant rollback" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Back up the databases before each update" })).not.toBeInTheDocument();
  });
});

describe("automatic deploys", { timeout: 20_000 }, () => {
  it("says they run and what could deploy the app by itself", async () => {
    await automation({ [`GET ${ROLLOUT}`]: () => json(200, RUNNING) });

    const toggle = await screen.findByRole("switch", { name: "Automatic deploys" });
    expect(toggle).toBeChecked();
    expect(screen.getByTestId("automatic-sources")).toHaveTextContent("Started by: push webhook, new tags");
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("heading", { name: "Automatic deploys" }).closest("section") ?? document.body);
  });

  it("says when nothing can deploy it by itself", async () => {
    await automation({ [`GET ${ROLLOUT}`]: () => json(200, { ...RUNNING, automatic: [] }) });

    expect(await screen.findByTestId("automatic-sources")).toHaveTextContent("Nothing deploys this app by itself yet");
  });

  it("pauses with the reason given and then says who paused it", async () => {
    let state: object = RUNNING;
    const { user, backend } = await automation({
      [`GET ${ROLLOUT}`]: () => json(200, state),
      [`POST ${ROLLOUT}/pause`]: () => {
        state = PAUSED;
        return json(200, PAUSED);
      },
    });

    await user.click(await screen.findByRole("switch", { name: "Automatic deploys" }));
    const dialog = await screen.findByRole("dialog", { name: `Pause automatic deploys of ${DOMAIN}?` });
    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Looking into the checkout bug");
    await user.click(within(dialog).getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROLLOUT}/pause`)[0]?.body).toEqual({ reason: "Looking into the checkout bug" });
    });
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(screen.getByText(/Paused by ana\./)).toBeInTheDocument();
    expect(screen.getByText(/Reason: Looking into the checkout bug/)).toBeInTheDocument();
    expect(screen.getByText(/3 pushes or tags have been held since\./)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Automatic deploys" })).not.toBeChecked();
  });

  it("pauses without a reason when none is given", async () => {
    const { user, backend } = await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`POST ${ROLLOUT}/pause`]: () => json(200, PAUSED),
    });

    await user.click(await screen.findByRole("switch", { name: "Automatic deploys" }));
    const dialog = await screen.findByRole("dialog", { name: `Pause automatic deploys of ${DOMAIN}?` });
    await user.click(within(dialog).getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROLLOUT}/pause`)[0]?.body).toEqual({ reason: null });
    });
  });

  it("does not pause when the question is closed", async () => {
    const { user, backend } = await automation({ [`GET ${ROLLOUT}`]: () => json(200, RUNNING) });

    await user.click(await screen.findByRole("switch", { name: "Automatic deploys" }));
    const dialog = await screen.findByRole("dialog", { name: `Pause automatic deploys of ${DOMAIN}?` });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(backend.callsTo(`POST ${ROLLOUT}/pause`)).toHaveLength(0);
    expect(screen.getByRole("switch", { name: "Automatic deploys" })).toBeChecked();
  });

  it("resumes with the same switch, and reads the app again", async () => {
    let state: object = PAUSED;
    const { user, backend } = await automation({
      [`GET ${ROLLOUT}`]: () => json(200, state),
      [`POST ${ROLLOUT}/resume`]: () => {
        state = RUNNING;
        return json(200, { domain: DOMAIN, was_paused: true, held: 3 });
      },
    });

    const toggle = await screen.findByRole("switch", { name: "Automatic deploys" });
    expect(toggle).not.toBeChecked();
    await user.click(toggle);

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROLLOUT}/resume`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.getByRole("switch", { name: "Automatic deploys" })).toBeChecked();
    });
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();
  });

  it("turns off pausing after a rollback with PUT", async () => {
    const { user, backend } = await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`PUT ${ROLLOUT}`]: () => json(200, { ...RUNNING, pause_on_rollback: false }),
    });

    const toggle = await screen.findByRole("switch", { name: "Pause automatic deploys after a rollback" });
    expect(toggle).toBeChecked();
    await user.click(toggle);

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ pause_on_rollback: false });
    });
    await waitFor(() => {
      expect(screen.getByRole("switch", { name: "Pause automatic deploys after a rollback" })).not.toBeChecked();
    });
  });

  it("is not shown by a server that has no such setting", async () => {
    await automation({});

    // The rows are read; a server older than the setting answers that it has none, and nothing is drawn.
    await waitFor(() => {
      expect(screen.queryByText("Loading how the app is delivered")).not.toBeInTheDocument();
      expect(screen.queryByText("Loading whether the backup is required")).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("switch", { name: "Automatic deploys" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Refuse an update when its backup fails" })).not.toBeInTheDocument();
    expect(screen.queryByText("Deploy limits")).not.toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await automation({ [`GET ${ROLLOUT}`]: () => json(200, PAUSED) });
    await screen.findByText("Paused");

    await act(() => setLocale("es"));

    expect(await screen.findByText("En pausa")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Despliegues automáticos" })).not.toBeChecked();
    expect(screen.getByText(/Motivo: Looking into the checkout bug/)).toBeInTheDocument();
  });
});

describe("deploy limits", { timeout: 20_000 }, () => {
  async function openLimits(routes: Record<string, RouteHandler>) {
    const harness = await automation(routes);
    await harness.user.click(await screen.findByRole("button", { name: "Edit deploy limits…" }));
    const drawer = await screen.findByRole("dialog", { name: "Deploy limits" });
    return { ...harness, drawer };
  }

  it("says what is kept now without opening the drawer", async () => {
    await automation({ [`GET ${ROLLOUT}`]: () => json(200, RUNNING) });

    expect(await screen.findByTestId("delivery-summary")).toHaveTextContent("History: the server's 20 deploys.");
  });

  it("shows the server's number and sends only what changed", async () => {
    const { user, backend, drawer } = await openLimits({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`PUT ${ROLLOUT}`]: () => json(200, { ...RUNNING, history_keep: 50 }),
    });

    const history = within(drawer).getByRole("textbox", { name: "Deploys kept in history" });
    expect(history).toHaveValue("");
    expect(history).toHaveAttribute("placeholder", "Server default");
    expect(within(drawer).getByText(/Empty uses the server's 20\./)).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Save the limits" })).toBeDisabled();
    await user.type(history, "50");
    await user.click(within(drawer).getByRole("button", { name: "Save the limits" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ history_keep: 50 });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Deploy limits" })).not.toBeInTheDocument();
    });
    expect(await screen.findByTestId("delivery-summary")).toHaveTextContent("History: 50 deploys.");
  });

  it("goes back to the server's number with an empty field", async () => {
    const { user, backend, drawer } = await openLimits({
      [`GET ${ROLLOUT}`]: () => json(200, { ...RUNNING, history_keep: 50 }),
      [`PUT ${ROLLOUT}`]: () => json(200, RUNNING),
    });

    const history = within(drawer).getByRole("textbox", { name: "Deploys kept in history" });
    expect(history).toHaveValue("50");
    await user.clear(history);
    await user.click(within(drawer).getByRole("button", { name: "Save the limits" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ history_keep: null });
    });
  });

  it("refuses a number out of range before asking the backend", async () => {
    const { user, backend, drawer } = await openLimits({ [`GET ${ROLLOUT}`]: () => json(200, RUNNING) });

    await user.type(within(drawer).getByRole("textbox", { name: "Deploys kept in history" }), "0");
    await user.click(within(drawer).getByRole("button", { name: "Save the limits" }));

    expect(await within(drawer).findByText("A number from 1 to 1000, or empty for the server's.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT ${ROLLOUT}`)).toHaveLength(0);
  });

  it("sets the time and the signal a unit stops with", async () => {
    const { user, backend, drawer } = await openLimits({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`PUT ${ROLLOUT}`]: () => json(200, { ...RUNNING, stop_timeout: 30, stop_signal: "SIGINT" }),
    });

    await user.type(within(drawer).getByRole("textbox", { name: "Time to stop" }), "30");
    await user.click(within(drawer).getByRole("combobox", { name: "Stop signal" }));
    await user.click(await screen.findByRole("option", { name: "SIGINT" }));
    await user.click(within(drawer).getByRole("button", { name: "Save the limits" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ stop_timeout: 30, stop_signal: "SIGINT" });
    });
  });

  it("says a unit that Noust does not write has no stop policy here", async () => {
    const { drawer } = await openLimits({ [`GET ${ROLLOUT}`]: () => json(200, { ...RUNNING, stop_supported: false }) });

    expect(within(drawer).getByText(/does not run in a unit Noust writes/)).toBeInTheDocument();
    expect(within(drawer).queryByRole("textbox", { name: "Time to stop" })).not.toBeInTheDocument();
    expect(within(drawer).getByRole("textbox", { name: "Deploys kept in history" })).toBeInTheDocument();
  });
});

describe("freezing the deploys of an app", { timeout: 20_000 }, () => {
  it("lists the app's freeze with its reason, and not the server's", async () => {
    await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes: [FREEZE, SERVER_FREEZE] }),
    });

    const list = await screen.findByRole("list", { name: "Freeze deploys" });
    expect(within(list).getByText("Black Friday: nothing ships")).toBeInTheDocument();
    expect(within(list).getByText("Frozen now")).toBeInTheDocument();
    expect(within(list).getByText(/Set by ana/)).toBeInTheDocument();
    expect(screen.queryByText("Datacentre move")).not.toBeInTheDocument();
  });

  it("says in the row that deploys are frozen now", async () => {
    await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes: [FREEZE] }),
    });

    const row = (await screen.findByText("Holds deploys for a stretch: an incident, a sale, a holiday.")).closest<HTMLElement>("[data-slot=setting]");
    if (row === null) throw new Error("the freeze row is not on the page");
    expect(within(row).getAllByText("Frozen now").length).toBeGreaterThan(0);
  });

  it("freezes with a reason and a length", async () => {
    const { user, backend } = await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes: [] }),
      [`POST ${FREEZES}`]: () => json(201, FREEZE),
    });

    await user.click(await screen.findByRole("button", { name: "Freeze deploys…" }));
    const dialog = await screen.findByRole("dialog", { name: `Freeze deploys of ${DOMAIN}` });
    await user.click(within(dialog).getByRole("button", { name: "Freeze" }));
    // A person who is refused is told why, so the reason is not optional.
    expect(await within(dialog).findByText("Say why: a person who is refused is told.")).toBeInTheDocument();
    expect(backend.callsTo(`POST ${FREEZES}`)).toHaveLength(0);

    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Black Friday: nothing ships");
    await user.click(within(dialog).getByRole("combobox", { name: "For how long" }));
    await user.click(await screen.findByRole("option", { name: "1 day" }));
    await user.click(within(dialog).getByRole("button", { name: "Freeze" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${FREEZES}`)[0]?.body).toEqual({ reason: "Black Friday: nothing ships", domain: DOMAIN, hours: 24 });
    });
  });

  it("freezes until somebody lifts it, from a time given", async () => {
    const { user, backend } = await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes: [] }),
      [`POST ${FREEZES}`]: () => json(201, FREEZE),
    });

    await user.click(await screen.findByRole("button", { name: "Freeze deploys…" }));
    const dialog = await screen.findByRole("dialog", { name: `Freeze deploys of ${DOMAIN}` });
    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Holiday");
    await user.click(within(dialog).getByRole("combobox", { name: "Starts" }));
    await user.click(await screen.findByRole("option", { name: "At a time" }));
    await user.click(within(dialog).getByRole("combobox", { name: "For how long" }));
    await user.click(await screen.findByRole("option", { name: "Until I lift it" }));
    const at = within(dialog).getByLabelText("Starts at");
    // A date-time control takes its whole value at once, as the browser's picker hands it over.
    fireEvent.change(at, { target: { value: "2026-12-24T18:00" } });
    await user.click(within(dialog).getByRole("button", { name: "Freeze" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${FREEZES}`)[0]?.body).toEqual({ reason: "Holiday", domain: DOMAIN, starts_at: "2026-12-24T18:00" });
    });
  });

  it("lifts a freeze after asking", async () => {
    let freezes = [FREEZE];
    const { user, backend } = await automation({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes }),
      [`DELETE ${FREEZES}/7`]: () => {
        freezes = [];
        return json(200, { lifted: [{ ...FREEZE, state: "lifted" }] });
      },
    });

    await user.click(await screen.findByRole("button", { name: "Lift the freeze: Black Friday: nothing ships" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Lift the freeze?" });
    expect(backend.callsTo(`DELETE ${FREEZES}/7`)).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Lift the freeze" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE ${FREEZES}/7`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByText("Black Friday: nothing ships")).not.toBeInTheDocument();
    });
  });
});

describe("scheduled updates", { timeout: 20_000 }, () => {
  async function openSchedule(data: object, routes: Record<string, RouteHandler> = {}, button = "Set an update schedule…") {
    const harness = await automation({ [`GET ${SCHEDULE}`]: () => json(200, data), ...routes });
    await harness.user.click(await screen.findByRole("button", { name: button }));
    const drawer = await screen.findByRole("dialog", { name: `Update schedule of ${DOMAIN}` });
    return { ...harness, drawer };
  }

  it("offers to schedule an app that does not update itself", async () => {
    const { user, backend, drawer } = await openSchedule(SCHEDULE_OFF, { [`PUT ${SCHEDULE}`]: () => json(200, SCHEDULE_ON) });

    expect(within(drawer).getByRole("combobox", { name: "When" })).toHaveTextContent("Every Sunday at 03:00");
    expect(within(drawer).getByRole("checkbox", { name: "Refuse the update when its backup does not happen" })).toBeChecked();
    expect(within(drawer).getByRole("checkbox", { name: "Do nothing when there is nothing new" })).toBeChecked();
    await user.click(within(drawer).getByRole("button", { name: "Save the schedule" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${SCHEDULE}`)[0]?.body).toEqual({ calendar: "weekly", enabled: true, backup_first: true, only_if_new: true });
    });
    expect(await screen.findByRole("button", { name: "Edit schedule…" })).toBeInTheDocument();
  });

  it("says when it runs next and how the last run ended", async () => {
    await automation({ [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_ON) });

    expect(await screen.findByText("Scheduled updates")).toBeInTheDocument();
    expect(await screen.findByTestId("schedule-last-run")).toHaveTextContent("refused, its backup failed");
    expect(screen.getByText("disk full")).toBeInTheDocument();
    expect(screen.getByText("On")).toBeInTheDocument();
  });

  it("writes its own calendar expression and keeps what was written", async () => {
    const { user, backend, drawer } = await openSchedule(
      SCHEDULE_ON,
      { [`PUT ${SCHEDULE}`]: () => json(200, { ...SCHEDULE_ON, calendar: "Sun *-*-* 04:30:00" }) },
      "Edit schedule…",
    );

    await user.click(within(drawer).getByRole("combobox", { name: "When" }));
    await user.click(await screen.findByRole("option", { name: "At a time I write" }));
    await user.click(within(drawer).getByRole("button", { name: "Save the schedule" }));
    expect(await within(drawer).findByText("Write when it should run.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT ${SCHEDULE}`)).toHaveLength(0);

    await user.type(within(drawer).getByRole("textbox", { name: /Calendar expression/ }), "Sun *-*-* 04:30");
    await user.click(within(drawer).getByRole("button", { name: "Save the schedule" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${SCHEDULE}`)[0]?.body).toEqual({ calendar: "Sun *-*-* 04:30", enabled: true, backup_first: true, only_if_new: true });
    });
  });

  it("puts the backend's refusal of an expression beside the field", async () => {
    const { user, drawer } = await openSchedule(
      SCHEDULE_ON,
      {
        [`PUT ${SCHEDULE}`]: () =>
          json(400, { error: "validation_error", detail: "Not a calendar expression: 'whenever'", hint: "Write a systemd calendar expression.", fields: { calendar: "Not a calendar expression: 'whenever'" }, output: null }),
      },
      "Edit schedule…",
    );

    await user.click(within(drawer).getByRole("combobox", { name: "When" }));
    await user.click(await screen.findByRole("option", { name: "At a time I write" }));
    await user.type(within(drawer).getByRole("textbox", { name: /Calendar expression/ }), "whenever");
    await user.click(within(drawer).getByRole("button", { name: "Save the schedule" }));

    expect(await within(drawer).findByText("Not a calendar expression: 'whenever'")).toBeInTheDocument();
    expect(within(drawer).getByRole("textbox", { name: /Calendar expression/ })).toHaveAttribute("aria-invalid", "true");
  });

  it("stops the app updating itself after asking", async () => {
    const { user, backend, drawer } = await openSchedule(SCHEDULE_ON, { [`DELETE ${SCHEDULE}`]: () => json(200, SCHEDULE_OFF) }, "Edit schedule…");

    await user.click(within(drawer).getByRole("button", { name: "Stop updating by itself" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Stop ${DOMAIN} updating itself?` });
    expect(backend.callsTo(`DELETE ${SCHEDULE}`)).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Stop updating" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE ${SCHEDULE}`)).toHaveLength(1);
    });
    expect(await screen.findByRole("button", { name: "Set an update schedule…" })).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await automation({ [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_ON) });
    await screen.findByTestId("schedule-last-run");

    await act(() => setLocale("es"));

    expect(await screen.findByText("Actualizaciones programadas")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-last-run")).toHaveTextContent("rechazada, falló su copia de seguridad");
  });
});
