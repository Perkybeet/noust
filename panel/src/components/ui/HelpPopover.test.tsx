import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { HelpPopover } from "./HelpPopover";

describe("HelpPopover", () => {
  it("is a button named by what it explains, so a screen reader can tell twenty of them apart", () => {
    render(<HelpPopover label="About trusted code hosts">The rule.</HelpPopover>);
    const help = screen.getByRole("button", { name: "About trusted code hosts" });
    expect(help.tagName).toBe("BUTTON");
    expect(help).toHaveAttribute("type", "button");
  });

  it("is drawn as a question mark, not as an information sign", () => {
    render(<HelpPopover label="About it">The rule.</HelpPopover>);
    expect(screen.getByRole("button", { name: "About it" }).querySelector("svg")).toHaveClass("lucide-circle-question-mark");
  });

  it("opens its explanation with a press, and with the keyboard, and closes it with Escape", async () => {
    const user = userEvent.setup();
    render(
      <HelpPopover label="About it" title="Why it is kept">
        So nobody can pose as your code host.
      </HelpPopover>,
    );
    const help = screen.getByRole("button", { name: "About it" });
    await user.click(help);
    expect(await screen.findByRole("dialog", { name: "Why it is kept" })).toHaveTextContent("So nobody can pose as your code host.");
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    help.focus();
    await user.keyboard(" ");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("is a target a finger can hit: a control height, never a bare icon", () => {
    render(<HelpPopover label="About it">The rule.</HelpPopover>);
    expect(screen.getByRole("button", { name: "About it" })).toHaveClass("size-control-sm");
  });

  it("has no accessibility violations, closed and open", async () => {
    const user = userEvent.setup();
    const { container } = render(<HelpPopover label="About it" title="Why">The rule.</HelpPopover>);
    await expectNoAxeViolations(container);
    await user.click(screen.getByRole("button", { name: "About it" }));
    await screen.findByRole("dialog");
    await expectNoAxeViolations(document.body);
  });
});
