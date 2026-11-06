import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { CommandHint } from "./CommandHint";

describe("CommandHint", () => {
  it("shows the command behind a prompt that is not part of it", () => {
    render(<CommandHint command="noust status shop.example.net" label="From a terminal" />);
    const code = screen.getByText("noust status shop.example.net", { exact: false });
    expect(code.tagName).toBe("CODE");
    expect(code.querySelector('[aria-hidden="true"]')).toHaveTextContent("$");
    expect(screen.getByText("From a terminal")).toBeInTheDocument();
  });

  it("copies the command alone", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<CommandHint command="noust update shop.example.net" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy command" }));
    expect(writeText).toHaveBeenCalledWith("noust update shop.example.net");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<CommandHint command="noust list" label="From a terminal" />);
    await expectNoAxeViolations(container);
  });

  it("says why the page shows a command, in the words of its reason, when no label leads it", () => {
    const { rerender } = render(<CommandHint command="noust central seal" reason="solo-terminal" />);
    expect(screen.getByText("Only from a terminal")).toBeInTheDocument();
    rerender(<CommandHint command="noust fleet authorize" reason="otro-servidor" />);
    expect(screen.getByText("On the other server")).toBeInTheDocument();
    rerender(<CommandHint command="nginx -t" reason="salida-del-sistema" />);
    expect(screen.getByText("What the system tells you to run")).toBeInTheDocument();
  });

  it("keeps a label it was given over the words of the reason, and marks the reason on the element for the tests that count them", () => {
    const { container } = render(<CommandHint command="noust central seal" label="Sealing is done on the machine" reason="solo-terminal" />);
    expect(screen.getByText("Sealing is done on the machine")).toBeInTheDocument();
    expect(screen.queryByText("Only from a terminal")).not.toBeInTheDocument();
    expect(container.firstElementChild).toHaveAttribute("data-reason", "solo-terminal");
  });

  it("leads with nothing and marks nothing when it has neither a label nor a reason", () => {
    const { container } = render(<CommandHint command="noust list" />);
    expect(container.firstElementChild).not.toHaveAttribute("data-reason");
    expect(container.querySelector("span.text-fg-faint:not([aria-hidden])")).toBeNull();
  });

  it("says the reason in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<CommandHint command="noust central seal" reason="solo-terminal" />);
    expect(screen.getByText("Solo desde un terminal")).toBeInTheDocument();
  });

  it("labels the copy button in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<CommandHint command="noust list" />);
    expect(screen.getByRole("button", { name: "Copiar comando" })).toBeInTheDocument();
  });
});
