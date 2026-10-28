import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { SegmentedControl } from "./SegmentedControl";

const WINDOWS = [
  { value: "1h", label: "1h" },
  { value: "24h", label: "24h" },
  { value: "30d", label: "30d" },
] as const;

type Window = (typeof WINDOWS)[number]["value"];

function Picker({ onChange }: { onChange?: (value: Window) => void }) {
  const [value, setValue] = useState<Window>("1h");
  return (
    <SegmentedControl
      label="Time range"
      options={WINDOWS}
      value={value}
      onValueChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

describe("SegmentedControl", () => {
  it("is a named radio group with the current choice checked", () => {
    render(<Picker />);
    expect(screen.getByRole("radiogroup", { name: "Time range" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "1h" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "24h" })).toHaveAttribute("aria-checked", "false");
  });

  it("changes on click and with the arrow keys", async () => {
    const onChange = vi.fn();
    render(<Picker onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "24h" }));
    expect(onChange).toHaveBeenLastCalledWith("24h");
    await userEvent.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenLastCalledWith("30d");
    expect(screen.getByRole("radio", { name: "30d" })).toHaveAttribute("aria-checked", "true");
  });

  it("never asks for more room than its container has: its options wrap instead of overflowing the page", () => {
    // jsdom does no layout, so what is held here is the rule that does it (the E2E suite measures
    // a phone): an unbounded no-shrink pill of six languages was 372px wide on a 390px screen.
    render(<Picker />);
    const group = screen.getByRole("radiogroup", { name: "Time range" });
    expect(group).toHaveClass("max-w-full", "flex-wrap");
  });

  it("draws a picture before each option's name when it has one, as decoration, and fills its container on request", async () => {
    render(
      <SegmentedControl
        label="Navigation"
        value="double"
        onValueChange={() => undefined}
        fill
        options={[
          { value: "double", label: "Double bar", icon: <svg data-testid="two" /> },
          { value: "single", label: "One bar", icon: <svg data-testid="one" /> },
        ]}
      />,
    );
    const group = screen.getByRole("radiogroup", { name: "Navigation" });
    expect(group).toHaveClass("w-full", "flex-nowrap");
    // The name is the option's: the picture beside it is decoration and adds nothing to it.
    expect(screen.getByRole("radio", { name: "Double bar" })).toContainElement(screen.getByTestId("two"));
    expect(screen.getByTestId("two").parentElement).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("radio", { name: "One bar" })).toContainElement(screen.getByTestId("one"));
    await expectNoAxeViolations(group);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<Picker />);
    await expectNoAxeViolations(container);
  });
});
