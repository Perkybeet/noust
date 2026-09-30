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
