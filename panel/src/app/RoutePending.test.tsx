import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FramedRoutePending, RoutePending } from "./RoutePending";

describe("RoutePending", () => {
  it("is a busy region that says what it is, with the shape of a page", () => {
    const { container } = render(<RoutePending />);
    const region = screen.getByText("Loading the page").closest('[aria-busy="true"]');
    expect(region).not.toBeNull();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it("brings the page's own margins when no shell is around it", () => {
    const { container: inShell } = render(<RoutePending />);
    expect(inShell.firstElementChild).not.toHaveClass("max-w-page");
    const { container: alone } = render(<FramedRoutePending />);
    expect(alone.firstElementChild).toHaveClass("max-w-page");
  });
});
