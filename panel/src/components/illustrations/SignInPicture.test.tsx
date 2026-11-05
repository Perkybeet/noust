import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { SignInPicture } from "./SignInPicture";

describe("SignInPicture: an operator's picture in the picture's slot", () => {
  it("is the picture, in a box of the proportions of the drawing it replaces, as wide as the slot", () => {
    const { container } = render(<SignInPicture src="/api/auth/sign-in/image?v=abc" fit="cover" alt="Acme" width={640} height={480} />);

    const image = screen.getByRole("img", { name: "Acme" });
    expect(image).toHaveAttribute("src", "/api/auth/sign-in/image?v=abc");
    expect(image).toHaveAttribute("width", "640");
    expect(image).toHaveAttribute("height", "480");
    // The same 400 by 230 the noust is drawn in, so the slab does not change size with the picture.
    const box = container.firstElementChild;
    expect(box).toHaveClass("aspect-[400/230]", "w-full", "overflow-hidden");
  });

  it("fills the space and crops when it is told to cover", () => {
    const { container } = render(<SignInPicture src="/a.png" fit="cover" alt="" />);

    expect(container.querySelector("img")).toHaveClass("object-cover");
    expect(container.firstElementChild).toHaveAttribute("data-fit", "cover");
    expect(container.firstElementChild).not.toHaveClass("p-3");
  });

  it("shows the whole picture, with air round it, when it is told to contain", () => {
    const { container } = render(<SignInPicture src="/a.png" fit="contain" alt="" />);

    expect(container.querySelector("img")).toHaveClass("object-contain");
    expect(container.firstElementChild).toHaveAttribute("data-fit", "contain");
    expect(container.firstElementChild).toHaveClass("p-3");
  });

  it("is decoration when it has no description, and says its description when it has one", () => {
    const { container, rerender } = render(<SignInPicture src="/a.png" fit="contain" alt="" />);
    expect(container.querySelector("img")).toHaveAttribute("alt", "");
    expect(screen.queryByRole("img")).toBeNull();

    rerender(<SignInPicture src="/a.png" fit="contain" alt="Acme Corp, since 1987" />);
    expect(screen.getByRole("img", { name: "Acme Corp, since 1987" })).toBeInTheDocument();
  });

  it("draws the noust when the picture does not load, and says so once", () => {
    const failed = vi.fn();
    const { container } = render(<SignInPicture src="/gone.png" fit="cover" alt="Acme" onFailed={failed} />);

    fireEvent.error(screen.getByRole("img", { name: "Acme" }));

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg[viewBox='0 0 400 230'][aria-hidden='true']")).not.toBeNull();
    expect(failed).toHaveBeenCalledTimes(1);
  });

  it("has no accessibility violations, filling or whole", async () => {
    const { container, rerender } = render(<SignInPicture src="/a.png" fit="cover" alt="Acme" />);
    await expectNoAxeViolations(container);

    rerender(<SignInPicture src="/a.png" fit="contain" alt="" />);
    await expectNoAxeViolations(container);
  });
});
