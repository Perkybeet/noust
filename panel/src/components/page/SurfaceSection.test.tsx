import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KeyRound } from "lucide-react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "../ui/Button";
import { StatusPill } from "../ui/StatusPill";
import { SurfaceSection } from "./SurfaceSection";

describe("SurfaceSection", () => {
  it("is a region named by its title, with the icon of its object decorative before it", () => {
    render(
      <SurfaceSection title="Provider accounts" icon={KeyRound} description="The token Noust makes machines with.">
        <p>rows</p>
      </SurfaceSection>,
    );
    const region = screen.getByRole("region", { name: "Provider accounts" });
    expect(within(region).getByRole("heading", { level: 2, name: "Provider accounts" })).toBeInTheDocument();
    expect(region.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(within(region).getByText("The token Noust makes machines with.")).toBeInTheDocument();
  });

  it("puts its state after the title and its action at the end of the head, over the content", () => {
    const { container } = render(
      <SurfaceSection title="GitHub" status={<StatusPill state="running" label="Connected" size="sm" />} actions={<Button>Add an account</Button>}>
        <p>content</p>
      </SurfaceSection>,
    );
    const head = container.querySelector("header");
    if (head === null) throw new Error("no head drawn");
    const heading = within(head).getByRole("heading", { name: "GitHub" });
    const state = within(head).getByText("Connected");
    const action = within(head).getByRole("button", { name: "Add an account" });
    expect(heading.compareDocumentPosition(state) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(state.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(head).not.toHaveTextContent("content");
    expect(head.compareDocumentPosition(screen.getByText("content")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps the long rule behind a real button that opens it, by touch and by keyboard", async () => {
    const user = userEvent.setup();
    render(
      <SurfaceSection title="Trusted code hosts" help="Nobody can pose as your code host between this server and the real one.">
        <p>rows</p>
      </SurfaceSection>,
    );
    const help = screen.getByRole("button", { name: "About Trusted code hosts" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    help.focus();
    await user.keyboard("{Enter}");
    const popup = await screen.findByRole("dialog", { name: "Trusted code hosts" });
    expect(popup).toHaveTextContent("Nobody can pose as your code host");
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("draws no help button where there is no rule to explain", () => {
    render(
      <SurfaceSection title="Accounts">
        <p>rows</p>
      </SurfaceSection>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("takes the content edge to edge under a hairline when it draws its own edges, and inside the slab otherwise", () => {
    const { rerender } = render(
      <SurfaceSection title="Accounts" flush>
        <p data-testid="body">table</p>
      </SurfaceSection>,
    );
    expect(screen.getByTestId("body").parentElement).toHaveClass("border-t");
    rerender(
      <SurfaceSection title="Accounts">
        <p data-testid="body">form</p>
      </SurfaceSection>,
    );
    expect(screen.getByTestId("body").parentElement).toHaveClass("px-5");
  });

  it("is only its head when it holds nothing (a section that is a link to somewhere else)", () => {
    const { container } = render(<SurfaceSection title="Gitea, Forgejo and Bitbucket" description="They need no connecting here." actions={<a href="#apps">See an app's</a>} />);
    expect(container.querySelector("[data-slot=surface-section]")?.children).toHaveLength(1);
  });

  it("says it is busy while what it holds is read", () => {
    render(
      <SurfaceSection title="Accounts" loading>
        <p>skeleton</p>
      </SurfaceSection>,
    );
    expect(screen.getByRole("region", { name: "Accounts" })).toHaveAttribute("aria-busy", "true");
  });

  it("has no accessibility violations, with its help open", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SurfaceSection title="Accounts" icon={KeyRound} description="A line." help="The rule." status={<StatusPill state="stopped" label="Off" size="sm" />} actions={<Button size="sm">Add</Button>}>
        <p>rows</p>
      </SurfaceSection>,
    );
    await expectNoAxeViolations(container);
    await user.click(screen.getByRole("button", { name: "About Accounts" }));
    await screen.findByRole("dialog");
    await expectNoAxeViolations(document.body);
  });
});
