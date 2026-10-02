import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { CLOCK, onDesktop, serverRoutes } from "./testing";
import { timeInZone } from "./TimeZoneField";

beforeEach(onDesktop);

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const ZONES = {
  generated_at: "2026-09-29T10:00:00+00:00",
  timezones: [
    { name: "Etc/UTC", city: "UTC", region: "Etc", offset: "UTC+00:00", offset_minutes: 0, abbreviation: "UTC" },
    { name: "Europe/London", city: "London", region: "Europe", offset: "UTC+01:00", offset_minutes: 60, abbreviation: "BST" },
    { name: "Europe/Madrid", city: "Madrid", region: "Europe", offset: "UTC+02:00", offset_minutes: 120, abbreviation: "CEST" },
    { name: "America/Argentina/Buenos_Aires", city: "Argentina / Buenos Aires", region: "America", offset: "UTC-03:00", offset_minutes: -180, abbreviation: "-03" },
  ],
};

function system(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...serverRoutes(),
    "GET /api/auth/session": () => json(200, ELEVATED),
    "GET /api/server/clock/timezones": () => json(200, ZONES),
    ...extra,
  });
  const harness = renderConsole("/server/system");
  return { ...harness, backend };
}

async function openZoneDialog(user: ReturnType<typeof renderConsole>["user"]) {
  await user.click(await screen.findByRole("button", { name: "Change time zone" }));
  return screen.findByRole("dialog", { name: "Change the time zone" });
}

describe("timeInZone", () => {
  it("reads the time from the server's offset, not the browser's zone database", () => {
    const now = Date.parse("2026-09-29T10:00:00Z");
    expect(timeInZone(now, 120, "en")).toContain("12:00");
    expect(timeInZone(now, -180, "en")).toContain("07:00");
  });
});

describe("changing the server's time zone (item 54)", { timeout: 20_000 }, () => {
  it("chooses from the server's zones, each with its offset and abbreviation, and says the time it will read", async () => {
    const { user, backend } = system({
      "PUT /api/server/time": () => json(200, { time: { ...CLOCK, timezone: "America/Argentina/Buenos_Aires" }, moved_timers: [] }),
    });
    const dialog = await openZoneDialog(user);
    const input = await within(dialog).findByRole("combobox", { name: "Time zone" });
    expect(input).toHaveValue("Europe/Madrid");

    await user.clear(input);
    await user.type(input, "buenos");
    const options = within(await screen.findByRole("listbox")).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("America/Argentina/Buenos_Aires");
    expect(options[0]).toHaveTextContent("UTC-03:00 · -03");
    await user.keyboard("{ArrowDown}{Enter}");
    // The live region says only what the choice changed; the time ticks outside it, so a screen
    // reader is not read the clock again every half minute.
    const status = within(dialog).getByRole("status");
    expect(status.textContent).toBe("With this zone the server runs on UTC-03:00 · -03.");
    expect(within(dialog).getByText((_, element) => element?.tagName === "P" && /^Its clock would read .*\d{2}:\d{2}.* now\.$/.test(element.textContent))).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Change time zone" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/server/time")[0]?.body).toEqual({ timezone: "America/Argentina/Buenos_Aires", install_ntp: false });
    });
  });

  it("finds a zone by its offset, and marks the one in use", async () => {
    const { user } = system();
    const dialog = await openZoneDialog(user);
    const input = await within(dialog).findByRole("combobox", { name: "Time zone" });
    await user.clear(input);
    await user.type(input, "+2");
    const options = within(await screen.findByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([expect.stringContaining("Europe/Madrid")]);
    expect(options[0]).toHaveTextContent("In use");
    await expectNoAxeViolations(dialog);
  });

  it("says an older server does not list its zones, and takes a typed name", async () => {
    const { user, backend } = system({
      "GET /api/server/clock/timezones": () => problem(404, "not_found", "Not Found"),
      "PUT /api/server/time": () => json(200, { time: { ...CLOCK, timezone: "Europe/Lisbon" }, moved_timers: [] }),
    });
    const dialog = await openZoneDialog(user);
    expect(await within(dialog).findByText(/This server has an older version of Noust and does not list its zones/)).toBeInTheDocument();
    const field = within(dialog).getByRole("textbox", { name: "Time zone" });
    await user.clear(field);
    await user.type(field, "Europe/Lisbon");
    await user.click(within(dialog).getByRole("button", { name: "Change time zone" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/server/time")[0]?.body).toEqual({ timezone: "Europe/Lisbon", install_ntp: false });
    });
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    const { user } = system();
    await user.click(await screen.findByRole("button", { name: "Cambiar la zona horaria" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("combobox"));
    const options = within(await screen.findByRole("listbox")).getAllByRole("option");
    expect(options.some((option) => option.textContent.includes("En uso"))).toBe(true);
  });
});
