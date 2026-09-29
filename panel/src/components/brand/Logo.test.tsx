import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import iconMono from "../../assets/brand/noust-icon-mono.svg?raw";
import icon from "../../assets/brand/noust-icon.svg?raw";
import markMono from "../../assets/brand/noust-mark-mono.svg?raw";
import mark from "../../assets/brand/noust-mark.svg?raw";
import wordmarkMono from "../../assets/brand/noust-wordmark-mono.svg?raw";
import wordmark from "../../assets/brand/noust-wordmark.svg?raw";
import { Logo, type LogoVariant } from "./Logo";

const SOURCES: Record<LogoVariant, [string, string]> = {
  mark: [mark, markMono],
  icon: [icon, iconMono],
  wordmark: [wordmark, wordmarkMono],
};

/** The file and fragment a `<use>` href names, ignoring the query Vite adds in development. */
const target = (file: string, id: string) => new RegExp(`/${file}\\.svg(\\?[^#]*)?#${id}$`);

const VARIANTS: [LogoVariant, string, string, number][] = [
  ["mark", "noust-mark", "0 0 140 36", 140],
  ["icon", "noust-icon", "0 0 32 32", 36],
  ["wordmark", "noust-wordmark", "0 0 254 36", 254],
];

describe("Logo", () => {
  it.each(VARIANTS)("draws the %s from its file, at the file's proportions", (variant, id, viewBox, width) => {
    const { container } = render(<Logo variant={variant} height={36} />);
    // At 36 px tall the width is the drawing's own, since the mark and wordmark are 36 units tall.
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("viewBox", viewBox);
    expect(svg).toHaveAttribute("width", String(width));
    expect(container.querySelector("use")?.getAttribute("href")).toMatch(target(id, id));
    // A data: URL would render nothing: <use> refuses them.
    expect(container.querySelector("use")?.getAttribute("href")).not.toMatch(/^data:/);
  });

  it("points <use> at an id and a viewBox the file really has", () => {
    // A renamed group or a redrawn canvas would leave an empty or cropped logo with no error.
    for (const [variant, id, viewBox] of VARIANTS) {
      for (const source of SOURCES[variant]) {
        expect(source).toContain(`id="${id}"`);
        expect(source).toContain(`viewBox="${viewBox}"`);
        // The ink has to be currentColor, or the logo cannot follow the theme.
        expect(source).toContain('stroke="currentColor"');
      }
    }
  });

  it("switches to the one-colour files", () => {
    const { container } = render(<Logo variant="wordmark" mono />);
    expect(container.querySelector("use")?.getAttribute("href")).toMatch(target("noust-wordmark-mono", "noust-wordmark"));
  });

  it("is named Noust when it stands alone", () => {
    render(<Logo variant="icon" />);
    expect(screen.getByRole("img", { name: "Noust" })).toBeInTheDocument();
  });

  it("is hidden when the text beside it already names it", () => {
    const { container } = render(<Logo decorative />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("paints its ink in the text colour, so it follows the theme", () => {
    const { container } = render(<Logo className="h-8" />);
    expect(container.querySelector("svg")).toHaveClass("text-fg", "h-8");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <Logo variant="mark" />
        <Logo variant="icon" height={32} />
        <a href="/">
          <Logo variant="wordmark" decorative />
          <span>Console</span>
        </a>
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
