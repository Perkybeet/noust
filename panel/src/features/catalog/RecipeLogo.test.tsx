import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { RecipeLogo, monogram } from "./RecipeLogo";

describe("a recipe's monogram", () => {
  it("is two letters of its name: the initials of two words, the start of one", () => {
    expect(monogram("Open WebUI")).toBe("OW");
    expect(monogram("Stirling PDF")).toBe("SP");
    expect(monogram("code-server")).toBe("CS");
    expect(monogram("n8n")).toBe("N8");
    expect(monogram("  ")).toBe("?");
    expect(monogram("Ünïcode")).toBe("ÜN");
  });
});

describe("a recipe's logo", () => {
  it("is the image the server serves, as decoration beside the name, never inlined", async () => {
    const { container } = render(<RecipeLogo logo="/api/recipes/vaultwarden/logo" title="Vaultwarden" />);
    const image = container.querySelector("img");
    expect(image).toHaveAttribute("src", "/api/recipes/vaultwarden/logo");
    // The name beside it names the recipe; the logo is not read out a second time.
    expect(image).toHaveAttribute("alt", "");
    expect(image).toHaveAttribute("width", "40");
    await expectNoAxeViolations(container);
  });

  it("is a tile with two letters, in the same box, when there is no logo", () => {
    const { container } = render(<RecipeLogo logo={null} title="Open WebUI" />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("OW")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("OW")).toHaveClass("size-10");
  });

  it("falls back to the tile when the image does not load, and keeps its size", () => {
    const { container } = render(<RecipeLogo logo="/api/recipes/gone/logo" title="Gone Recipe" size="lg" />);
    const image = container.querySelector("img");
    expect(image).not.toBeNull();
    if (image !== null) fireEvent.error(image);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("GR")).toHaveClass("size-16");
  });

  it("has a small size for a table row", () => {
    const { container } = render(<RecipeLogo logo="/api/recipes/x/logo" title="X" size="sm" />);
    expect(container.querySelector("img")).toHaveAttribute("width", "24");
    expect(container.querySelector("img")).toHaveClass("size-6");
  });
});
