import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "../ui/Button";
import { Setting } from "../ui/Setting";
import { StatusPill } from "../ui/StatusPill";
import { SettingsGroup } from "./SettingsGroup";

describe("SettingsGroup", () => {
  it("is a card whose rows are told apart by a hairline, not a card each", () => {
    render(
      <SettingsGroup title="Notifications">
        <Setting label="Send notifications" checked onCheckedChange={() => undefined} />
        <Setting label="Send a daily summary" checked={false} onCheckedChange={() => undefined} />
      </SettingsGroup>,
    );
    const group = screen.getByRole("heading", { name: "Notifications", level: 3 }).closest("section");
    if (group === null) throw new Error("the group is not a section");
    expect(group).toHaveClass("rounded-card", "border-border", "bg-surface");
    const rows = group.querySelector('[data-slot="settings-group"]');
    expect(rows).toHaveClass("divide-y", "divide-border");
    expect(within(group).getAllByRole("switch")).toHaveLength(2);
    // No padding of its own: the rows draw theirs, edge to edge.
    expect(rows?.parentElement).not.toHaveClass("p-5", "p-4");
  });

  it("puts the state of the feature after the title and the button that changes it at the end", async () => {
    const onTurnOn = vi.fn();
    render(
      <SettingsGroup
        title="Instant rollback"
        status={<StatusPill state="stopped" label="Off" size="sm" />}
        actions={<Button size="sm" onClick={onTurnOn}>Turn on…</Button>}
        description="Each deploy is kept apart; going back takes seconds."
      >
        <Setting label="Versions to keep" action={<span>5</span>} />
      </SettingsGroup>,
    );
    const heading = screen.getByRole("heading", { name: "Instant rollback" });
    expect(heading.nextElementSibling).toHaveTextContent("Off");
    await userEvent.click(screen.getByRole("button", { name: "Turn on…" }));
    expect(onTurnOn).toHaveBeenCalledOnce();
    expect(screen.getByText("Each deploy is kept apart; going back takes seconds.")).toBeInTheDocument();
  });

  it("says the command once, at the foot", () => {
    render(
      <SettingsGroup title="Backups" command="noust app backup-required shop.example.com on">
        <Setting label="Required backup" checked onCheckedChange={() => undefined} />
      </SettingsGroup>,
    );
    expect(screen.getAllByText("noust app backup-required shop.example.com on")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Copy command" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <main>
        <SettingsGroup title="Notifications" status={<StatusPill state="running" label="On" size="sm" />} command="noust notify status">
          <Setting label="Send notifications" help="Nothing is sent until a channel is set up." checked onCheckedChange={() => undefined} />
          <Setting label="Quiet hours" action={<Button size="sm">Set…</Button>} />
        </SettingsGroup>
      </main>,
    );
    await expectNoAxeViolations(container);
  });
});
