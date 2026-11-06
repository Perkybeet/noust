import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { FirstUse } from "./FirstUse";

describe("FirstUse", () => {
  it("says what it gives you in one sentence, beside the button that starts it", () => {
    render(<FirstUse action={<Button variant="primary">Add an account</Button>}>Add the token of Hetzner Cloud and you can create servers without leaving here.</FirstUse>);
    expect(screen.getByText(/Add the token of Hetzner Cloud/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add an account" })).toBeInTheDocument();
  });

  it("draws its drawing as decoration: the sentence is what is read", () => {
    const { container } = render(<FirstUse>Only private images need an account.</FirstUse>);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("draws no button where the page's header holds it, and a second way in beside the first when there is one", () => {
    const { container, rerender } = render(<FirstUse>Only private images need an account.</FirstUse>);
    expect(container.querySelector("button")).toBeNull();
    rerender(
      <FirstUse action={<Button variant="primary">Go to Applications</Button>} secondary={<a href="#docs">Read how</a>}>
        When you deploy from a private repository its key appears here.
      </FirstUse>,
    );
    expect(screen.getByRole("link", { name: "Read how" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to Applications" })).toBeInTheDocument();
  });

  it("is carved, as a place that waits to be filled", () => {
    render(<FirstUse>One sentence.</FirstUse>);
    expect(document.querySelector("[data-slot=first-use]")).toHaveClass("elev-carved-fine");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<FirstUse action={<Button variant="primary">Connect GitLab</Button>}>Connect gitlab.com or your own GitLab.</FirstUse>);
    await expectNoAxeViolations(container);
  });
});
