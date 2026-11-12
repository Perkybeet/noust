import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { ChoiceCards } from "./ChoiceCards";
import type { ChoiceCard } from "./ChoiceCards";

type Mode = "releases" | "inplace";

const OPTIONS: readonly ChoiceCard<Mode>[] = [
  { value: "releases", label: "Instant rollback", description: "Each deploy is kept apart; going back takes seconds.", badge: "Recommended" },
  { value: "inplace", label: "Single folder", description: "Every deploy rebuilds the same folder." },
];

function Harness({ onValueChange }: { onValueChange?: (value: Mode) => void }) {
  const [value, setValue] = useState<Mode>("releases");
  return (
    <ChoiceCards
      legend="How deploys work"
      options={OPTIONS}
      value={value}
      onValueChange={(next) => {
        setValue(next);
        onValueChange?.(next);
      }}
    />
  );
}

describe("ChoiceCards", () => {
  it("is one radio group named by its legend, each card named by its label and described by its sentence", async () => {
    const { container } = render(<Harness />);
    const group = screen.getByRole("radiogroup", { name: "How deploys work" });
    const first = within(group).getByRole("radio", { name: "Instant rollback" });
    expect(first).toBeChecked();
    expect(first).toHaveAccessibleDescription("Each deploy is kept apart; going back takes seconds.");
    expect(within(group).getByRole("radio", { name: "Single folder" })).not.toBeChecked();
    expect(within(group).getByText("Recommended")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("chooses with a click anywhere on the card, and with the arrow keys", async () => {
    const onValueChange = vi.fn();
    const user = userEvent.setup();
    render(<Harness onValueChange={onValueChange} />);
    await user.click(screen.getByText("Every deploy rebuilds the same folder."));
    expect(onValueChange).toHaveBeenLastCalledWith("inplace");
    expect(screen.getByRole("radio", { name: "Single folder" })).toBeChecked();
    screen.getByRole("radio", { name: "Single folder" }).focus();
    await user.keyboard("{ArrowUp}");
    expect(onValueChange).toHaveBeenLastCalledWith("releases");
  });

  it("can have nothing chosen, so that no Enter picks for the person", async () => {
    const onValueChange = vi.fn();
    const user = userEvent.setup();
    render(<ChoiceCards legend="Server" options={OPTIONS} value={null} onValueChange={onValueChange} />);
    for (const radio of screen.getAllByRole("radio")) expect(radio).not.toBeChecked();
    await user.tab();
    expect(screen.getByRole("radio", { name: "Instant rollback" })).toHaveFocus();
    expect(onValueChange).not.toHaveBeenCalled();
    await user.click(screen.getByText("Every deploy rebuilds the same folder."));
    expect(onValueChange).toHaveBeenCalledWith("inplace");
  });

  it("turns an option off and keeps what it says readable: its reason", async () => {
    const onValueChange = vi.fn();
    const user = userEvent.setup();
    const { container } = render(
      <ChoiceCards
        legend="Server"
        options={[
          { value: "web-01", label: "web-01", description: "Ubuntu 24.04", untranslated: true },
          { value: "web-02", label: "web-02", description: "No connection for 3h 30m", untranslated: true, disabled: true },
          { value: "db-02", label: "db-02", description: "Debian 12", untranslated: true },
        ]}
        value={null}
        onValueChange={onValueChange}
      />,
    );
    const off = screen.getByRole("radio", { name: "web-02" });
    expect(off).toHaveAttribute("aria-disabled", "true");
    expect(off).toHaveAccessibleDescription("No connection for 3h 30m");
    await user.click(screen.getByText("No connection for 3h 30m"));
    expect(onValueChange).not.toHaveBeenCalled();
    // The arrow keys go past it.
    screen.getByRole("radio", { name: "web-01" }).focus();
    await user.keyboard("{ArrowDown}");
    expect(onValueChange).toHaveBeenLastCalledWith("db-02");
    // It is not raised: nothing says it can be pressed.
    expect(off.className).not.toContain("elev-raised");
    await expectNoAxeViolations(container);
  });

  it("keeps a system value's label out of translation", () => {
    render(
      <ChoiceCards
        legend="Template"
        options={[{ value: "proxy", label: "proxy.conf", description: "Forwards to a port.", untranslated: true }]}
        value="proxy"
        onValueChange={() => undefined}
      />,
    );
    expect(screen.getByText("proxy.conf")).toHaveAttribute("translate", "no");
  });
});
