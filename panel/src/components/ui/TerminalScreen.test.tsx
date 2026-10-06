import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { TerminalScreen } from "./TerminalScreen";
import type { TerminalScreenProps } from "./TerminalScreen";
import { installFakeCanvas, makeSource } from "./terminal/testing";

function lines(count: number): string {
  return Array.from({ length: count }, (_, index) => `line ${String(index)}`).join("\r\n");
}

function mount(overrides: Partial<TerminalScreenProps> = {}) {
  const source = makeSource();
  const onInput = vi.fn();
  const onResize = vi.fn();
  const view = render(<TerminalScreen source={source} onInput={onInput} onResize={onResize} label="Terminal - shop.example.com" {...overrides} />);
  return { source, onInput, onResize, view };
}

describe("TerminalScreen", () => {
  beforeEach(() => {
    installFakeCanvas();
    // jsdom lays nothing out: the box is 800 by 340, which holds a grid of 100 by 20.
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(340);
  });

  it("is a text box named for what it is a terminal of, with its keys explained", () => {
    mount();
    const box = screen.getByRole("textbox", { name: "Terminal - shop.example.com" });
    expect(box).toHaveAccessibleDescription(/Ctrl\+Shift\+C/);
    expect(box).toHaveAccessibleDescription(/Escape and then Tab/);
  });

  it("hides the canvas, which says nothing a reader could use", () => {
    const { view } = mount();
    expect(view.container.querySelector("canvas")).toHaveAttribute("aria-hidden", "true");
  });

  it("tells the program how big the box is", () => {
    const { onResize, source } = mount();
    expect(onResize).toHaveBeenCalledWith(100, 20);
    expect(source.terminal.screen.cols).toBe(100);
  });

  it("sends what is typed", () => {
    const { onInput } = mount();
    const box = screen.getByRole("textbox");
    fireEvent.keyDown(box, { key: "l" });
    fireEvent.keyDown(box, { key: "s" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onInput.mock.calls).toEqual([["l"], ["s"], ["\r"]]);
  });

  it("sends nothing once the session is over", () => {
    const { onInput } = mount({ disabled: true });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "x" });
    expect(onInput).not.toHaveBeenCalled();
  });

  it("takes the keyboard when asked", () => {
    mount({ focusOnMount: true });
    expect(screen.getByRole("textbox")).toHaveFocus();
  });

  it("stops listening to a screen it was told to leave, and listens to the new one", () => {
    const first = makeSource();
    const onInput = vi.fn();
    const { rerender } = render(<TerminalScreen source={first} onInput={onInput} onResize={vi.fn()} label="t" />);
    const next = makeSource();
    rerender(<TerminalScreen source={next} onInput={onInput} onResize={vi.fn()} label="t" />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "a" });
    expect(onInput).toHaveBeenCalledTimes(1);
  });

  describe("reading it without sight", () => {
    it("shows the screen as text, which follows the output", async () => {
      const { source } = mount({ textView: true });
      const region = screen.getByRole("region", { name: "Terminal contents as text" });
      act(() => {
        source.write("hello\r\nworld");
      });
      await waitFor(() => {
        expect(region).toHaveTextContent("hello");
      });
      expect(region).toHaveTextContent("world");
      expect(region).toHaveAttribute("tabindex", "0");
    });

    it("has no text view unless asked for it", () => {
      mount();
      expect(screen.queryByRole("region", { name: "Terminal contents as text" })).not.toBeInTheDocument();
    });

    it("reads new lines aloud when asked, and only the new ones", async () => {
      const { source } = mount({ announce: true });
      const log = screen.getByRole("log", { name: "New terminal output" });
      expect(log).toHaveAttribute("aria-live", "polite");
      act(() => {
        source.write("first\r\n");
      });
      await waitFor(() => {
        expect(within(log).getByText("first")).toBeInTheDocument();
      });
      act(() => {
        source.write("second\r\n");
      });
      await waitFor(() => {
        expect(within(log).getByText("second")).toBeInTheDocument();
      });
      expect(within(log).getAllByText("first")).toHaveLength(1);
    });

    it("does not read anything aloud by default: a full-screen program redraws all the time", async () => {
      const { source } = mount();
      const log = screen.getByRole("log", { name: "New terminal output" });
      expect(log).toHaveAttribute("aria-live", "off");
      act(() => {
        source.write("redrawn\r\n");
      });
      await new Promise((resolve) => setTimeout(resolve, 350));
      expect(log).toBeEmptyDOMElement();
    });
  });

  describe("scrolling back", () => {
    it("offers the way back to the newest output, and takes it", async () => {
      const user = userEvent.setup();
      const { source, view } = mount();
      act(() => {
        source.write(lines(60));
      });
      expect(screen.queryByRole("button", { name: "Jump to the latest output" })).not.toBeInTheDocument();
      const box = view.container.firstElementChild?.firstElementChild;
      if (!(box instanceof HTMLElement)) throw new Error("no box");
      fireEvent.wheel(box, { deltaY: -3, deltaMode: 1 });
      const jump = await screen.findByRole("button", { name: "Jump to the latest output" });
      await user.click(jump);
      expect(screen.queryByRole("button", { name: "Jump to the latest output" })).not.toBeInTheDocument();
      expect(screen.getByRole("textbox")).toHaveFocus();
    });
  });

  describe("accessibility", () => {
    it("has no violations", async () => {
      const { view } = mount();
      await expectNoAxeViolations(view.container);
    });

    it("has none with the text view and the announcements on", async () => {
      const { view, source } = mount({ textView: true, announce: true });
      act(() => {
        source.write("hello\r\n");
      });
      await waitFor(() => {
        expect(screen.getByRole("region", { name: "Terminal contents as text" })).toHaveTextContent("hello");
      });
      await expectNoAxeViolations(view.container);
    });

    it("has none while scrolled back, with its button showing", async () => {
      const { view, source } = mount();
      act(() => {
        source.write(lines(60));
      });
      const box = view.container.firstElementChild?.firstElementChild;
      if (!(box instanceof HTMLElement)) throw new Error("no box");
      fireEvent.wheel(box, { deltaY: -3, deltaMode: 1 });
      await screen.findByRole("button", { name: "Jump to the latest output" });
      await expectNoAxeViolations(view.container);
    });
  });
});
