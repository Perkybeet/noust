import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { CopyValue } from "./CopyValue";

afterEach(() => {
  vi.unstubAllGlobals();
});

const KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPublicHalfOfTheShopKeyThatIsMuchLongerThanTheBoxItSitsIn shop-example-com";

describe("CopyValue", () => {
  it("shows the whole value, never cut, and copies exactly it", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<CopyValue value={KEY} label="Copy the public key" name="Public key of shop-example-com" />);
    const box = screen.getByRole("group", { name: "Public key of shop-example-com" });
    expect(within(box).getByText(KEY)).toBeInTheDocument();
    // A system value: set apart from translation, and wrapped rather than truncated.
    expect(within(box).getByText(KEY)).toHaveAttribute("translate", "no");
    expect(within(box).getByText(KEY).className).toMatch(/break-all/);
    await userEvent.click(within(box).getByRole("button", { name: "Copy the public key" }));
    expect(writeText).toHaveBeenCalledWith(KEY);
    expect(screen.getByRole("status")).toHaveTextContent("Copied to clipboard");
  });

  it("has no accessibility violations, named or not", async () => {
    const { container } = render(
      <>
        <CopyValue value="noust web expose-hooks hooks.example.com" label="Copy the command" />
        <CopyValue value={KEY} label="Copy the public key" name="Public key" />
      </>,
    );
    await expectNoAxeViolations(container);
  });
});
