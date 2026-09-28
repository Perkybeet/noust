import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { CommandHint } from "./CommandHint";

describe("CommandHint", () => {
  it("shows the command behind a prompt that is not part of it", () => {
    render(<CommandHint command="wasm status shop.example.net" label="From a terminal" />);
    const code = screen.getByText("wasm status shop.example.net", { exact: false });
    expect(code.tagName).toBe("CODE");
    expect(code.querySelector('[aria-hidden="true"]')).toHaveTextContent("$");
    expect(screen.getByText("From a terminal")).toBeInTheDocument();
  });

  it("copies the command alone", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<CommandHint command="wasm update shop.example.net" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy command" }));
    expect(writeText).toHaveBeenCalledWith("wasm update shop.example.net");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<CommandHint command="wasm list" label="From a terminal" />);
    await expectNoAxeViolations(container);
  });

  it("labels the copy button in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<CommandHint command="wasm list" />);
    expect(screen.getByRole("button", { name: "Copiar comando" })).toBeInTheDocument();
  });
});
