/**
 * What Home says happened lately, in the console's language and with the people by name: a fleet
 * job's title is one of the server's sentences (not an English string in a Spanish console), and a
 * line says "Marta Ruiz", not "Session marta".
 */

import { act, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { metricsReadFixture, overviewFixture } from "./testFixtures";

// uPlot draws on a canvas jsdom does not have; the charts' contract is their summary.
vi.mock("uplot", () => ({
  default: vi.fn(function () {
    return {
      destroy: vi.fn(),
      setData: vi.fn(),
      setScale: vi.fn(),
      setSize: vi.fn(),
      setCursor: vi.fn(),
      redraw: vi.fn(),
      bbox: { left: 0, top: 0, width: 400, height: 140 },
      valToPos: () => 0,
    };
  }),
}));

const NOW = Math.floor(Date.now() / 1000);
const AGO = (seconds: number): string => new Date(Date.now() - seconds * 1000).toISOString();

const ACTIVITY = [
  {
    id: "job:1",
    type: "fleet",
    title: "Apply the fleet's settings on 2 servers",
    title_coded: { code: "fleet_job.settings_apply.several", params: { count: 2 } },
    status: "completed",
    domain: null,
    actor: "marta",
    actor_name: "Marta Ruiz",
    at: AGO(60),
  },
  { id: "job:2", type: "deploy", title: "Deploy tidewick.io", title_coded: null, status: "completed", domain: "tidewick.io", actor: "marta", actor_name: "Marta Ruiz", at: AGO(120) },
  { id: "job:3", type: "update", title: "Update shop.example.com", title_coded: null, status: "completed", domain: "shop.example.com", actor: "webhook", actor_name: null, at: AGO(180) },
  // A node older than this console sends neither field: the English title and the old way of naming the actor.
  { id: "job:4", type: "fleet", title: "Renew certificates on 1 server", status: "completed", domain: null, actor: "iker", at: AGO(240) },
];

function home() {
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/overview": () => json(200, overviewFixture({ activity: ACTIVITY })),
    "GET /api/apps": () => json(200, { total: 0, apps: [] }),
    "GET /api/metrics/query": (call) => json(200, metricsReadFixture(call.search.getAll("metric"), { now: NOW, cells: 1440, recordedFrom: 1430 })),
    "GET /api/config": () => json(200, { config: { notifications: { enabled: false } }, path: "/etc/noust/config.yaml" }),
  });
  return renderConsole("/");
}

describe("Home's recent activity", () => {
  it("names a person by the name of their account and a fleet job by its sentence", { timeout: 20_000 }, async () => {
    home();
    const activity = await screen.findByRole("list", { name: "Recent activity, newest first" });

    expect(within(activity).getByText("Apply the fleet's settings on 2 servers")).toBeInTheDocument();
    expect(within(activity).getAllByText("Marta Ruiz")).toHaveLength(2);
    // Not a session: marta is an account.
    expect(within(activity).queryByText(/Session marta/)).not.toBeInTheDocument();
    // A webhook is not a person, and says what it is.
    expect(within(activity).getByText("A webhook delivery")).toBeInTheDocument();
    // What an older node sent is shown as it came.
    expect(within(activity).getByText("Renew certificates on 1 server")).toBeInTheDocument();
  });

  it("says a fleet job's title in Spanish, and keeps the English of one that comes without a code", { timeout: 20_000 }, async () => {
    home();
    await screen.findByRole("list", { name: "Recent activity, newest first" });
    await act(() => setLocale("es"));
    const activity = await screen.findByRole("list", { name: "Actividad reciente, lo más reciente primero" });

    expect(within(activity).getByText("Aplicar los ajustes de la flota en 2 servidores")).toBeInTheDocument();
    expect(within(activity).queryByText("Apply the fleet's settings on 2 servers")).not.toBeInTheDocument();
    expect(within(activity).getAllByText("Marta Ruiz")).toHaveLength(2);
    // A node that sent no code for its title: the server's English, true if not translated.
    expect(within(activity).getByText("Renew certificates on 1 server")).toBeInTheDocument();
  });
});
