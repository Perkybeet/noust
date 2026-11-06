import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Building2, Fingerprint, Ticket } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { AuthDoors } from "./AuthDoors";

function Doors({ onSelect = () => undefined, company = true }: { onSelect?: (id: string) => void; company?: boolean }) {
  return (
    <AuthDoors
      label="Other ways to sign in"
      doors={[
        { id: "passkey", label: "Passkey", icon: <Fingerprint />, onSelect: () => onSelect("passkey") },
        ...(company ? [{ id: "company", label: "Your company", icon: <Building2 />, onSelect: () => onSelect("company") }] : []),
        { id: "token", label: "Access token", icon: <Ticket />, onSelect: () => onSelect("token") },
      ]}
    />
  );
}

describe("AuthDoors: the other ways in, as equal doors", () => {
  it("is a named navigation of one button per door, in the order given", () => {
    render(<Doors />);
    const nav = screen.getByRole("navigation", { name: "Other ways to sign in" });
    const buttons = within(nav).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual(["Passkey", "Your company", "Access token"]);
    // Doors open a way in the page's own place: buttons, never links to somewhere else.
    expect(within(nav).queryAllByRole("link")).toHaveLength(0);
    for (const button of buttons) expect(button).toHaveAttribute("type", "button");
  });

  it("gives every door the same room, two or three of them", () => {
    const { rerender } = render(<Doors />);
    for (const button of screen.getAllByRole("button")) expect(button).toHaveClass("flex-1");
    rerender(<Doors company={false} />);
    const two = screen.getAllByRole("button");
    expect(two).toHaveLength(2);
    for (const button of two) expect(button).toHaveClass("flex-1");
  });

  it("draws each icon bare above its label, and says it once to a screen reader", () => {
    render(<Doors />);
    const door = screen.getByRole("button", { name: "Passkey" });
    expect(door.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(door.querySelector("svg")).toHaveClass("size-icon-lg");
  });

  it("opens the door pressed, by pointer", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<Doors onSelect={onSelect} />);
    await user.click(screen.getByRole("button", { name: "Your company" }));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("company");
  });

  it("is walked with Tab, in order, and opened with Enter or Space", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<Doors onSelect={onSelect} />);
    await user.tab();
    expect(screen.getByRole("button", { name: "Passkey" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith("passkey");
    await user.tab();
    expect(screen.getByRole("button", { name: "Your company" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Access token" })).toHaveFocus();
    await user.keyboard(" ");
    expect(onSelect).toHaveBeenLastCalledWith("token");
    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<Doors />);
    await expectNoAxeViolations(container);
  });
});
