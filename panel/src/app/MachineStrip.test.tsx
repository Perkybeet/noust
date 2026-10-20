import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { bindT, loadCatalog } from "../i18n";
import { setLocale } from "./locale";
import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { MACHINE, fakeBackend, json, signedInRoutes } from "../test/fakes";
import { StreamBadge, unitTallySummary } from "./MachineStrip";

async function stripAt(units: typeof MACHINE.units) {
  fakeBackend({ ...signedInRoutes(), "GET /api/system/machine": () => json(200, { ...MACHINE, units }) });
  const harness = renderConsole("/apps");
  await screen.findByRole("heading", { level: 1 });
  const strip = screen.getByRole("group", { name: "This machine" });
  await within(strip).findByText("web-01");
  return { ...harness, strip };
}

describe("unitTallySummary", () => {
  it("says what each count means, in one sentence", () => {
    expect(unitTallySummary({ running: 15, failed: 0, stopped: 1 }, bindT("en"))).toBe(
      "Noust's services: 15 running, 0 failed, 1 stopped",
    );
  });

  it("says it in Spanish too", async () => {
    await loadCatalog("es");
    expect(unitTallySummary({ running: 15, failed: 0, stopped: 1 }, bindT("es"))).toBe(
      "Servicios de Noust: 15 en marcha, 0 con fallo, 1 detenidos",
    );
  });
});

describe("the unit tally", () => {
  it("names itself the same sentence it shows as a tooltip, not two different ones", async () => {
    const { strip } = await stripAt({ running: 9, failed: 1, stopped: 2 });
    const link = within(strip).getByRole("link", { name: "Noust's services: 9 running, 1 failed, 2 stopped" });
    expect(link).toHaveAttribute("href", "/server/services");
  });

  it("says each count in words, with its shape", async () => {
    const { strip } = await stripAt({ running: 9, failed: 1, stopped: 2 });
    const link = within(strip).getByRole("link", { name: "Noust's services: 9 running, 1 failed, 2 stopped" });
    // Both forms are rendered; the strip's own width (a container query) shows one of them:
    // the words, or on a narrower strip the counts alone, whose meaning the link's name keeps.
    expect(link.textContent).toBe("Services9 running91 failed12 stopped2");
    expect(within(link).getByText("9 running")).toHaveClass("hidden");
  });

  it("keeps the machine's name whole: the counts and meters give way first", async () => {
    const { strip } = await stripAt({ running: 9, failed: 1, stopped: 2 });
    const name = within(strip).getByText("web-01");
    expect(name.closest("a")).toHaveClass("shrink-0");
    expect(name).not.toHaveClass("truncate");
  });

  it("shows the same sentence as a tooltip on keyboard focus", async () => {
    const { strip } = await stripAt({ running: 9, failed: 1, stopped: 2 });
    const link = within(strip).getByRole("link", { name: "Noust's services: 9 running, 1 failed, 2 stopped" });
    link.focus();
    expect(await screen.findByText("Noust's services: 9 running, 1 failed, 2 stopped", {}, { timeout: 2000 })).toBeInTheDocument();
  });

  it("has no accessibility violations, with a failure to show", async () => {
    const { strip } = await stripAt({ running: 9, failed: 1, stopped: 2 });
    await expectNoAxeViolations(strip);
  });
});

describe("the live stream's badge", () => {
  it("says Live, with the running shape, while the stream keeps the readings current", () => {
    const { container } = render(<StreamBadge stream="live" />);
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(container.querySelector("[data-state='running']")).not.toBeNull();
  });

  it("says Reconnecting, with the in-progress shape, when the stream is down", () => {
    const { container } = render(<StreamBadge stream="reconnecting" />);
    expect(screen.getByText("Reconnecting")).toBeInTheDocument();
    expect(container.querySelector("[data-state='deploying']")).not.toBeNull();
    expect(screen.queryByText("Live")).toBeNull();
  });

  it("says nothing before the first connection", () => {
    const { container } = render(<StreamBadge stream="connecting" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("gives the word way on a narrow strip but never the reconnecting state", () => {
    const live = render(<StreamBadge stream="live" />);
    expect(live.container.firstElementChild).toHaveClass("hidden");
    live.unmount();
    const down = render(<StreamBadge stream="reconnecting" />);
    expect(down.container.firstElementChild).not.toHaveClass("hidden");
  });

  it("stays at the end of the strip, so the readings that arrive after it do not push it across the bar", () => {
    const live = render(<StreamBadge stream="live" />);
    expect(live.container.firstElementChild).toHaveClass("ml-auto");
    live.unmount();
    const down = render(<StreamBadge stream="reconnecting" />);
    expect(down.container.firstElementChild).toHaveClass("ml-auto");
  });

  it("says it in Spanish too", async () => {
    await act(() => setLocale("es"));
    render(<StreamBadge stream="live" />);
    expect(screen.getByText("En vivo")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<StreamBadge stream="live" />);
    await expectNoAxeViolations(container);
  });
});
