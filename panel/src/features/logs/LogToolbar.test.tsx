import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { LogToolbar } from "./LogToolbar";
import type { LogToolbarProps } from "./LogToolbar";

afterEach(() => {
  vi.unstubAllGlobals();
});

type Source = "apps" | "system";
type Range = "live" | "1h" | "24h";

function Harness({ understood, error, hideRange = false }: { understood?: string; error?: string; hideRange?: boolean }) {
  const [q, setQ] = useState("");
  const [source, setSource] = useState<Source>("apps");
  const [range, setRange] = useState<Range>("1h");
  const props: LogToolbarProps<Range, Source> = {
    label: "Filter the logs",
    query: { value: q, onChange: setQ, label: "Search the logs", placeholder: "Search" },
    sources: {
      label: "Where from",
      value: source,
      onChange: setSource,
      options: [
        { value: "apps", label: "Applications" },
        { value: "system", label: "System" },
      ],
    },
    picker: <span data-testid="picker">an application</span>,
    range: {
      label: "How far back",
      value: range,
      onChange: setRange,
      hidden: hideRange,
      options: [
        { value: "live", label: "Live" },
        { value: "1h", label: "1 hour" },
        { value: "24h", label: "24 hours" },
      ],
    },
    ...(understood !== undefined ? { understood } : {}),
    ...(error !== undefined ? { error } : {}),
  };
  return <LogToolbar<Range, Source> {...props} />;
}

describe("LogToolbar", () => {
  it("is one search landmark with the query, the source, the source's own filter, the range and the link", async () => {
    render(<Harness />);
    const bar = screen.getByRole("search", { name: "Filter the logs" });
    const box = within(bar).getByRole("searchbox", { name: "Search the logs" });
    // The `/` shortcut finds the query box of every log surface.
    expect(box).toHaveAttribute("data-page-search");
    expect(within(bar).getByRole("radiogroup", { name: "Where from" })).toBeInTheDocument();
    expect(within(bar).getByTestId("picker")).toBeInTheDocument();
    expect(within(bar).getByRole("combobox", { name: "How far back" })).toHaveTextContent("1 hour");
    expect(within(bar).getByRole("button", { name: "Query syntax" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Copy link to this search" })).toBeInTheDocument();
    await expectNoAxeViolations(bar);
  });

  it("offers Live as one of the ranges, first", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("combobox", { name: "How far back" }));
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(["Live", "1 hour", "24 hours"]);
    await userEvent.click(screen.getByRole("option", { name: "Live" }));
    expect(screen.getByRole("combobox", { name: "How far back" })).toHaveTextContent("Live");
  });

  it("does not draw the range while a stretch picked elsewhere holds it", () => {
    render(<Harness hideRange />);
    expect(screen.queryByRole("combobox", { name: "How far back" })).not.toBeInTheDocument();
  });

  it("copies the address, which is the state of the search", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Copy link to this search" }));
    expect(writeText).toHaveBeenCalledWith(window.location.href);
  });

  it("explains the grammar, with the application field among its examples", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Query syntax" }));
    const popover = await screen.findByRole("dialog");
    expect(within(popover).getByText("@unit:nginx")).toBeInTheDocument();
    expect(within(popover).getByText("@app:shop.example.com")).toBeInTheDocument();
    expect(within(popover).getByText("error OR fatal")).toBeInTheDocument();
    await expectNoAxeViolations(popover);
  });

  it("writes the query back as it was understood, and says nothing when there is nothing to say", () => {
    const { rerender } = render(<Harness understood="timeout AND NOT healthcheck" />);
    expect(screen.getByText("Understood as")).toBeInTheDocument();
    expect(screen.getByText("timeout AND NOT healthcheck")).toBeInTheDocument();
    rerender(<Harness understood="" />);
    expect(screen.queryByText("Understood as")).not.toBeInTheDocument();
  });

  it("says why a query cannot be read instead of how it was understood", () => {
    render(<Harness understood="x" error="A parenthesis is not closed (at character 1)" />);
    expect(screen.getByText(/A parenthesis is not closed/)).toBeInTheDocument();
    expect(screen.queryByText("Understood as")).not.toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<Harness />);
    expect(screen.getByRole("button", { name: "Sintaxis de la búsqueda" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copiar el enlace de esta búsqueda" })).toBeInTheDocument();
    await act(async () => {
      await setLocale("en");
    });
  });
});
