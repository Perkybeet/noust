import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Combobox } from "./Combobox";
import { Field } from "./Field";

interface Zone {
  value: string;
  label: string;
  city: string;
  region: string;
  abbreviation: string;
  offset: string;
}

const ZONES: Zone[] = [
  { value: "Etc/UTC", label: "Etc/UTC", city: "UTC", region: "Etc", abbreviation: "UTC", offset: "UTC+00:00" },
  { value: "Europe/London", label: "Europe/London", city: "London", region: "Europe", abbreviation: "BST", offset: "UTC+01:00" },
  { value: "Europe/Madrid", label: "Europe/Madrid", city: "Madrid", region: "Europe", abbreviation: "CEST", offset: "UTC+02:00" },
  { value: "Africa/Cairo", label: "Africa/Cairo", city: "Cairo", region: "Africa", abbreviation: "EEST", offset: "UTC+03:00" },
  {
    value: "America/Argentina/Buenos_Aires",
    label: "America/Argentina/Buenos_Aires",
    city: "Argentina / Buenos Aires",
    region: "America",
    abbreviation: "-03",
    offset: "UTC-03:00",
  },
];

/** Four hundred zones: enough that only a window of them may be in the document. */
const MANY: Zone[] = Array.from({ length: 400 }, (_, index) => ({
  value: `Zone/City_${String(index).padStart(3, "0")}`,
  label: `Zone/City_${String(index).padStart(3, "0")}`,
  city: `City ${String(index).padStart(3, "0")}`,
  region: index < 200 ? "Zone" : "Other",
  abbreviation: "ZZT",
  offset: index === 321 ? "UTC+09:30" : "UTC+01:00",
}));

const byRegion = (zone: Zone): string => zone.region;

describe("Combobox", () => {
  it("is a labelled combobox showing the chosen item", () => {
    render(<Combobox aria-label="Time zone" items={ZONES} value="Europe/Madrid" onValueChange={() => undefined} />);
    expect(screen.getByRole("combobox", { name: "Time zone" })).toHaveValue("Europe/Madrid");
  });

  it("is labelled by an enclosing Field", () => {
    render(
      <Field label="Time zone">
        <Combobox items={ZONES} defaultValue="Etc/UTC" />
      </Field>,
    );
    expect(screen.getByRole("combobox", { name: "Time zone" })).toHaveValue("Etc/UTC");
  });

  it("finds a zone by city, by a short offset and by utc+1", async () => {
    const user = userEvent.setup();
    render(<Combobox aria-label="Time zone" items={ZONES} />);
    const input = screen.getByRole("combobox", { name: "Time zone" });

    await user.type(input, "madrid");
    let options = within(await screen.findByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([expect.stringContaining("Europe/Madrid")]);

    await user.clear(input);
    await user.type(input, "+2");
    options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([expect.stringContaining("Europe/Madrid")]);

    await user.clear(input);
    await user.type(input, "utc+1");
    options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([expect.stringContaining("Europe/London")]);
  });

  it("chooses with the arrow keys and Enter, and closes with Escape", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<Combobox aria-label="Time zone" items={ZONES} onValueChange={onValueChange} />);
    const input = screen.getByRole("combobox", { name: "Time zone" });

    await user.click(input);
    await screen.findByRole("listbox");
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(onValueChange).toHaveBeenLastCalledWith("Europe/London", ZONES[1]);
    expect(input).toHaveValue("Europe/London");

    await user.keyboard("{ArrowDown}");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("selects the chosen text on focus, so typing starts a new search", async () => {
    const user = userEvent.setup();
    render(<Combobox aria-label="Time zone" items={ZONES} defaultValue="Europe/Madrid" />);
    const input = screen.getByRole("combobox", { name: "Time zone" });
    await user.click(input);
    await act(async () => {
      await new Promise((resolve) => requestAnimationFrame(resolve));
    });
    await user.keyboard("+1");
    expect(input).toHaveValue("+1");
    const options = within(await screen.findByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([expect.stringContaining("Europe/London")]);
  });

  it("says when nothing matches", async () => {
    const user = userEvent.setup();
    render(<Combobox aria-label="Time zone" items={ZONES} />);
    await user.type(screen.getByRole("combobox", { name: "Time zone" }), "atlantis");
    expect(await screen.findByText("Nothing matches this search.")).toBeInTheDocument();
  });

  it("groups the items under their group's name", async () => {
    const user = userEvent.setup();
    render(<Combobox aria-label="Time zone" items={ZONES} groupBy={byRegion} />);
    await user.click(screen.getByRole("combobox", { name: "Time zone" }));
    const listbox = await screen.findByRole("listbox");
    const europe = within(listbox).getByRole("group", { name: "Europe" });
    expect(within(europe).getAllByRole("option").map((option) => option.textContent)).toEqual([
      expect.stringContaining("Europe/London"),
      expect.stringContaining("Europe/Madrid"),
    ]);
  });

  it("uses a caller's filter and renders a caller's item", async () => {
    const user = userEvent.setup();
    render(
      <Combobox
        aria-label="Time zone"
        items={ZONES}
        filter={(zone, query) => zone.abbreviation.toLowerCase() === query.toLowerCase()}
        renderItem={(zone) => (
          <span>
            {zone.city} ({zone.abbreviation})
          </span>
        )}
      />,
    );
    await user.type(screen.getByRole("combobox", { name: "Time zone" }), "bst");
    const options = within(await screen.findByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["London (BST)"]);
  });

  describe("virtualized", () => {
    it("renders only a window of a long list, and still finds and chooses any item", async () => {
      vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(288);
      vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(320);
      const user = userEvent.setup();
      const onValueChange = vi.fn();
      render(<Combobox aria-label="Time zone" items={MANY} groupBy={byRegion} virtualized onValueChange={onValueChange} />);
      const input = screen.getByRole("combobox", { name: "Time zone" });

      await user.click(input);
      const listbox = await screen.findByRole("listbox");
      const rendered = await within(listbox).findAllByRole("option");
      expect(rendered.length).toBeGreaterThan(0);
      expect(rendered.length).toBeLessThan(100);

      await user.type(input, "+9");
      const found = within(screen.getByRole("listbox")).getAllByRole("option");
      expect(found.map((option) => option.textContent)).toEqual([expect.stringContaining("Zone/City_321")]);
      await user.keyboard("{ArrowDown}{Enter}");
      expect(onValueChange).toHaveBeenLastCalledWith("Zone/City_321", MANY[321]);
    });

    it("tells every option its place in the whole list", async () => {
      vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(288);
      const user = userEvent.setup();
      render(<Combobox aria-label="Time zone" items={MANY} virtualized />);
      await user.click(screen.getByRole("combobox", { name: "Time zone" }));
      const [first] = await within(await screen.findByRole("listbox")).findAllByRole("option");
      expect(first).toHaveAttribute("aria-setsize", "400");
      expect(first).toHaveAttribute("aria-posinset", "1");
    });
  });

  it("has no accessibility violations, closed or open", async () => {
    const user = userEvent.setup();
    const { container } = render(<Combobox aria-label="Time zone" items={ZONES} groupBy={byRegion} defaultValue="Etc/UTC" />);
    await expectNoAxeViolations(container);
    await user.click(screen.getByRole("combobox", { name: "Time zone" }));
    await screen.findByRole("listbox");
    await expectNoAxeViolations(document.body);
  });

  it("has no accessibility violations when virtualized and open", async () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(288);
    const user = userEvent.setup();
    render(<Combobox aria-label="Time zone" items={MANY} groupBy={byRegion} virtualized />);
    await user.click(screen.getByRole("combobox", { name: "Time zone" }));
    await screen.findByRole("listbox");
    await expectNoAxeViolations(document.body);
  });

  it("speaks Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    const user = userEvent.setup();
    render(<Combobox aria-label="Zona horaria" items={ZONES} />);
    await user.type(screen.getByRole("combobox", { name: "Zona horaria" }), "atlantis");
    expect(await screen.findByText("Nada coincide con esta búsqueda.")).toBeInTheDocument();
  });
});
