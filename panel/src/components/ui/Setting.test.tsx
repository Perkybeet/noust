import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { Setting } from "./Setting";
import { StatusPill } from "./StatusPill";

describe("Setting", () => {
  it("is a switch named by its label and described by its help", () => {
    render(<Setting label="Zero-downtime deploys" help="The new version starts beside the old one." checked={false} onCheckedChange={() => undefined} />);
    const control = screen.getByRole("switch", { name: "Zero-downtime deploys" });
    expect(control).toHaveAttribute("aria-checked", "false");
    expect(control).toHaveAccessibleDescription("The new version starts beside the old one.");
  });

  it("applies the change at once, with no question, when turning on", async () => {
    const onCheckedChange = vi.fn();
    render(<Setting label="Send notifications" checked={false} onCheckedChange={onCheckedChange} />);
    await userEvent.click(screen.getByRole("switch", { name: "Send notifications" }));
    expect(onCheckedChange).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("keeps the label when the state changes: the control says the state, the name never does", () => {
    const { rerender } = render(<Setting label="Send notifications" checked={false} onCheckedChange={() => undefined} />);
    expect(screen.getByText("Send notifications")).toBeInTheDocument();
    rerender(<Setting label="Send notifications" checked onCheckedChange={() => undefined} />);
    expect(screen.getByRole("switch", { name: "Send notifications" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getAllByText("Send notifications")).toHaveLength(1);
  });

  describe("its size", () => {
    it("is 44px without help: 12px, one 20px line (13px type), 12px", () => {
      const { container } = render(<Setting label="Send notifications" checked onCheckedChange={() => undefined} />);
      const row = container.querySelector('[data-slot="setting"]');
      expect(row).toHaveClass("px-5", "py-3");
      expect(screen.getByText("Send notifications")).toHaveClass("text-13");
      // The switch is 20px: it never makes the row taller than its text.
      expect(screen.getByRole("switch")).toHaveClass("h-5");
    });

    it("is 60px with one line of help: the help is a 12px line (16px) under the 20px one", () => {
      render(<Setting label="Zero-downtime deploys" help="The new version starts beside the old one." checked onCheckedChange={() => undefined} />);
      const help = screen.getByText("The new version starts beside the old one.");
      expect(help).toHaveClass("text-12", "max-w-measure-help");
      expect(help).not.toHaveClass("mt-1", "mt-0.5", "mt-2");
    });

    it("keeps a small button from making its row taller", () => {
      const { container } = render(<Setting label="Instant rollback" help="Going back takes seconds." action={<Button size="sm">Turn on…</Button>} />);
      expect(screen.getByRole("button", { name: "Turn on…" }).parentElement).toHaveClass("-my-1");
      expect(container.querySelector('[data-slot="setting"]')).toHaveClass("py-3");
    });
  });

  describe("busy", () => {
    it("keeps the value it had, says it is busy and ignores a second press", async () => {
      const onCheckedChange = vi.fn();
      render(<Setting label="Send notifications" checked={false} busy onCheckedChange={onCheckedChange} />);
      const control = screen.getByRole("switch", { name: "Send notifications" });
      expect(control).toHaveAttribute("aria-busy", "true");
      expect(control).toHaveAttribute("aria-checked", "false");
      expect(control.querySelector('[data-slot="switch-arc"]')).not.toBeNull();
      await userEvent.click(control);
      expect(onCheckedChange).not.toHaveBeenCalled();
    });

    it("is not announced: it adds no live region", () => {
      const { container } = render(<Setting label="Send notifications" checked busy onCheckedChange={() => undefined} />);
      expect(container.querySelector('[role="status"], [role="alert"], [aria-live]')).toBeNull();
    });
  });

  describe("risk", () => {
    it("is one line under the help, with a glyph and a word for screen readers", () => {
      render(
        <Setting
          label="Zero-downtime deploys"
          help="The new version starts beside the old one."
          risk="Two copies run at once for a few seconds."
          checked
          onCheckedChange={() => undefined}
        />,
      );
      const risk = screen.getByText("Two copies run at once for a few seconds.");
      expect(risk.closest("p")).toHaveClass("text-12", "text-fg");
      expect(screen.getByText("Warning:")).toHaveClass("sr-only");
      expect(screen.getByRole("switch")).toHaveAccessibleDescription(/Two copies run at once/);
    });

    it("opens the detail behind What changes", async () => {
      render(
        <Setting
          label="Zero-downtime deploys"
          risk="Two copies run at once for a few seconds."
          riskDetail="Does not tolerate a SQLite database both copies write to."
          checked
          onCheckedChange={() => undefined}
        />,
      );
      expect(screen.queryByText(/SQLite/)).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "What changes" }));
      expect(await screen.findByText(/SQLite/)).toBeInTheDocument();
    });

    it("draws no link when there is no detail", () => {
      render(<Setting label="Zero-downtime deploys" risk="Two copies run at once." checked onCheckedChange={() => undefined} />);
      expect(screen.queryByRole("button", { name: "What changes" })).not.toBeInTheDocument();
    });
  });

  describe("confirmOff", () => {
    function guarded(onCheckedChange: (checked: boolean) => void | Promise<void>) {
      render(
        <Setting
          label="Required backup"
          help="Updates wait for a backup."
          checked
          onCheckedChange={onCheckedChange}
          confirmOff={{ title: "Turn off the required backup?", description: "Updates will run without a backup first.", actionLabel: "Turn off the backup" }}
        />,
      );
    }

    it("asks one question before taking a safeguard away, and does nothing until it is answered", async () => {
      const onCheckedChange = vi.fn(() => Promise.resolve());
      guarded(onCheckedChange);
      await userEvent.click(screen.getByRole("switch", { name: "Required backup" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Turn off the required backup?" });
      expect(dialog.querySelector("input")).toBeNull();
      expect(onCheckedChange).not.toHaveBeenCalled();
      expect(screen.getByRole("switch", { name: "Required backup", hidden: true })).toHaveAttribute("aria-checked", "true");
      await userEvent.click(screen.getByRole("button", { name: "Turn off the backup" }));
      expect(onCheckedChange).toHaveBeenCalledExactlyOnceWith(false);
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      });
    });

    it("leaves the switch on when the question is declined", async () => {
      const onCheckedChange = vi.fn();
      guarded(onCheckedChange);
      await userEvent.click(screen.getByRole("switch", { name: "Required backup" }));
      await screen.findByRole("alertdialog");
      await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      });
      expect(onCheckedChange).not.toHaveBeenCalled();
    });

    it("keeps the question open and shows the failure when turning it off fails", async () => {
      const failure = Object.assign(new Error("request failed"), { hint: "The schedule could not be removed.", detail: "unit not found" });
      guarded(() => Promise.reject(failure));
      await userEvent.click(screen.getByRole("switch", { name: "Required backup" }));
      await screen.findByRole("alertdialog");
      await userEvent.click(screen.getByRole("button", { name: "Turn off the backup" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("unit not found");
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    });

    it("asks nothing when turning it on", async () => {
      const onCheckedChange = vi.fn();
      render(
        <Setting
          label="Required backup"
          checked={false}
          onCheckedChange={onCheckedChange}
          confirmOff={{ title: "Turn off the required backup?", description: "x", actionLabel: "Turn off" }}
        />,
      );
      await userEvent.click(screen.getByRole("switch", { name: "Required backup" }));
      expect(onCheckedChange).toHaveBeenCalledExactlyOnceWith(true);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  describe("disabled", () => {
    it("says why, in the place of the help, and cannot be pressed", async () => {
      const onCheckedChange = vi.fn();
      render(
        <Setting
          label="Zero-downtime deploys"
          help="The new version starts beside the old one."
          disabled
          reason="Needs instant rollback first."
          checked={false}
          onCheckedChange={onCheckedChange}
        />,
      );
      const control = screen.getByRole("switch", { name: "Zero-downtime deploys" });
      expect(control).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByText("Needs instant rollback first.")).toBeInTheDocument();
      expect(screen.queryByText("The new version starts beside the old one.")).not.toBeInTheDocument();
      expect(control).toHaveAccessibleDescription("Needs instant rollback first.");
      await userEvent.click(control);
      expect(onCheckedChange).not.toHaveBeenCalled();
    });

    it("requires a reason, which the type checks", () => {
      // @ts-expect-error A disabled switch without a reason does not compile: it would be mute.
      const mute = <Setting label="Zero-downtime deploys" disabled checked={false} onCheckedChange={() => undefined} />;
      expect(mute).toBeDefined();
    });
  });

  describe("locked by policy", () => {
    it("shows the lock and who sets it, and cannot be pressed", () => {
      const { container } = render(
        <Setting label="Require two-factor sign-in" help="Every account signs in with a second factor." lockedBy="Set by the ENS profile." checked onCheckedChange={() => undefined} />,
      );
      expect(screen.getByRole("switch")).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByText("Set by the ENS profile.")).toBeInTheDocument();
      expect(screen.queryByText("Every account signs in with a second factor.")).not.toBeInTheDocument();
      expect(container.querySelector("svg.lucide-lock-keyhole")).not.toBeNull();
    });
  });

  describe("elevated", () => {
    it("shows a key with a name before the press", () => {
      render(<Setting label="Allow SSH passwords" elevated checked={false} onCheckedChange={() => undefined} />);
      expect(screen.getByRole("img", { name: "Asks you to confirm it's you first" })).toBeInTheDocument();
    });

    it("shows none when the window is open", () => {
      render(<Setting label="Allow SSH passwords" checked={false} onCheckedChange={() => undefined} />);
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
    });
  });

  describe("status and fields", () => {
    it("shows a state next to the name, and the problem in the help", () => {
      render(
        <Setting
          label="Send notifications"
          status={<StatusPill state="warning" label="On, no channel" size="sm" />}
          help="Nothing is sent until a channel is set up."
          checked
          onCheckedChange={() => undefined}
        />,
      );
      expect(screen.getByText("On, no channel")).toBeInTheDocument();
      expect(screen.getByRole("switch", { name: "Send notifications" })).toHaveAccessibleDescription("Nothing is sent until a channel is set up.");
    });

    it("shows its fields only while it is on", () => {
      const { rerender } = render(
        <Setting label="Zero-downtime deploys" checked={false} onCheckedChange={() => undefined}>
          <p>Keep the old version for 30 seconds.</p>
        </Setting>,
      );
      expect(screen.queryByText("Keep the old version for 30 seconds.")).not.toBeInTheDocument();
      rerender(
        <Setting label="Zero-downtime deploys" checked onCheckedChange={() => undefined}>
          <p>Keep the old version for 30 seconds.</p>
        </Setting>,
      );
      expect(screen.getByText("Keep the old version for 30 seconds.")).toBeInTheDocument();
    });

    it("always shows the fields of a row whose control is a button", () => {
      render(
        <Setting label="Instant rollback" action={<Button size="sm">Turn on…</Button>}>
          <p>Versions to keep</p>
        </Setting>,
      );
      expect(screen.getByText("Versions to keep")).toBeInTheDocument();
    });
  });

  it("speaks Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<Setting label="Cero cortes" risk="Dos copias a la vez." riskDetail="x" elevated checked onCheckedChange={() => undefined} />);
    expect(screen.getByRole("img", { name: "Pide confirmar que eres tú antes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Qué cambia" })).toBeInTheDocument();
    expect(screen.getByText("Aviso:")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <Setting label="Send notifications" help="Nothing is sent until a channel is set up." checked onCheckedChange={() => undefined} />
        <Setting label="Zero-downtime deploys" help="The new version starts beside the old one." risk="Two copies run at once." riskDetail="Detail." elevated checked={false} onCheckedChange={() => undefined} />
        <Setting label="Busy" checked busy onCheckedChange={() => undefined} />
        <Setting label="Needs a plan" help="A dry run first." action={<Button size="sm">Turn on…</Button>} />
        <Setting label="Locked" lockedBy="Set by the ENS profile." checked onCheckedChange={() => undefined} />
        <Setting label="Unavailable" disabled reason="Needs instant rollback first." checked={false} onCheckedChange={() => undefined} />
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
