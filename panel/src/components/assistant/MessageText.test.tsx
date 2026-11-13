import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useT } from "../../i18n";
import { expectNoAxeViolations } from "../../test/axe";
import { AiMark, locationWord } from "./AiMark";
import { LiteralLines, MessageText } from "./MessageText";

function Place({ location }: { location: string | null }) {
  const t = useT();
  return <span data-testid="place">{locationWord(t, location)}</span>;
}

const HOSTILE = [
  'Restart it. <img src=x onerror="alert(1)"> <script>steal()</script>',
  "",
  "![tracking pixel](https://tracker.example.net/p.gif)",
  "",
  "[https://noust.example/docs](https://evil.example.net/login) and [run me](javascript:alert(1)).",
].join("\n");

describe("MessageText", () => {
  it("draws what a hostile answer says as text: no tag, no script, no image comes out of it", () => {
    const { container } = render(<MessageText text={HOSTILE} />);
    expect(container.querySelector("img, script, iframe, object, embed, svg image")).toBeNull();
    // The tag is on screen as the characters it is.
    expect(container).toHaveTextContent('<img src=x onerror="alert(1)">');
    expect(container).toHaveTextContent("<script>steal()</script>");
    // Nothing in it can run: no attribute that is a handler.
    expect(container.querySelector("[onerror], [onclick], [onload]")).toBeNull();
  });

  it("says an image was not shown instead of asking for it", () => {
    render(<MessageText text="![tracking pixel](https://tracker.example.net/p.gif)" />);
    expect(screen.getByText("[image not shown: tracking pixel]")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows where a link really goes, whatever its label says, and opens it only when pressed", () => {
    render(<MessageText text="[https://noust.example/docs](https://evil.example.net/login)" />);
    const link = screen.getByRole("link", { name: /https:\/\/noust\.example\/docs/ });
    expect(link).toHaveAttribute("href", "https://evil.example.net/login");
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(link).toHaveAttribute("target", "_blank");
    // The host it goes to is drawn beside it, in view, not in a tooltip.
    expect(screen.getByText("(evil.example.net)")).toBeVisible();
  });

  it("does not make a link of anything that is not a web address", () => {
    render(<MessageText text="[run me](javascript:alert(1)) and [here](/apps/shop.example.com)" />);
    expect(screen.queryByRole("link")).toBeNull();
    // The destination stays in view so nothing hides what the text meant.
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
    expect(screen.getByText("/apps/shop.example.com")).toBeInTheDocument();
  });

  it("draws lists, bold, code and a block of code with a copy button and a warning that it is the assistant's", async () => {
    const { container } = render(<MessageText text={["Do this:", "", "1. Add **PORT**", "2. Run `npm start`", "", "```", "npm start", "```"].join("\n")} />);
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Code the assistant wrote. Read it before you run it.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("has no violations with the hostile answer on screen", async () => {
    const { container } = render(<MessageText text={HOSTILE} />);
    await expectNoAxeViolations(container);
  });
});

describe("LiteralLines", () => {
  it("draws the system's lines with their numbers, verbatim and not translated", () => {
    render(<LiteralLines caption="Log of deployment 14, lines 41 to 42" lines={[{ n: 41, text: "Error: listen EADDRINUSE :::3100" }, { n: 42, text: "    at Server.setup" }]} />);
    const pre = screen.getByLabelText("The system's words, with their line numbers");
    expect(pre).toHaveAttribute("translate", "no");
    expect(pre.textContent).toContain(" 41  Error: listen EADDRINUSE :::3100");
    expect(pre.textContent).toContain(" 42      at Server.setup");
  });
});

describe("AiMark", () => {
  it("says an AI wrote it, with the model, where it runs and for a screen reader one sentence", async () => {
    const { container } = render(<AiMark model="qwen3-14b" where="On your network" at="10:42" />);
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByText("Reply written by an AI, model qwen3-14b")).toBeInTheDocument();
    expect(screen.getByText("qwen3-14b")).toBeInTheDocument();
    expect(screen.getByText("On your network")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says a place it does not know is not stated, never one it cannot stand behind", () => {
    const { rerender } = render(<Place location="mars" />);
    expect(screen.getByTestId("place")).toHaveTextContent("Place not stated");
    rerender(<Place location={null} />);
    expect(screen.getByTestId("place")).toHaveTextContent("Place not stated");
    rerender(<Place location="eu" />);
    expect(screen.getByTestId("place")).toHaveTextContent("In the European Union");
  });
});
