import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApprovalPendingError, ElevationCancelledError } from "../../api/errors";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { toast } from "./toast";

function setup(onConfirm: () => Promise<void>) {
  render(
    <ConfirmDialog
      title="Delete example.com"
      description="Stops the service and removes the site. Backups are kept."
      confirmText="example.com"
      actionLabel="Delete application"
      onConfirm={onConfirm}
      trigger={<Button>Delete</Button>}
    />,
  );
}

async function open() {
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  return screen.findByRole("alertdialog", { name: "Delete example.com" });
}

describe("ConfirmDialog", () => {
  it("keeps the action disabled until the name is typed exactly", async () => {
    const onConfirm = vi.fn(() => Promise.resolve());
    setup(onConfirm);
    await open();
    const action = screen.getByRole("button", { name: "Delete application" });
    const input = screen.getByRole("textbox", { name: /Type example.com to confirm/ });
    expect(action).toBeDisabled();
    await userEvent.type(input, "example.co");
    expect(action).toBeDisabled();
    await userEvent.type(input, "M");
    expect(action).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "example.com");
    expect(action).toBeEnabled();
  });

  it("focuses the name field when it opens", async () => {
    setup(() => Promise.resolve());
    await open();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /to confirm/ })).toHaveFocus();
    });
  });

  it("runs the action on Enter once confirmed and closes when it succeeds", async () => {
    const onConfirm = vi.fn(() => Promise.resolve());
    setup(onConfirm);
    await open();
    await userEvent.type(screen.getByRole("textbox", { name: /to confirm/ }), "example.com{Enter}");
    expect(onConfirm).toHaveBeenCalledOnce();
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  it("stays open and shows the failure verbatim, with the fix above it", async () => {
    const failure = Object.assign(new Error("request failed"), {
      hint: "The unit did not stop. Check its logs, then try again.",
      detail: "Job for noust-example.com.service canceled.",
    });
    setup(() => Promise.reject(failure));
    await open();
    await userEvent.type(screen.getByRole("textbox", { name: /to confirm/ }), "example.com");
    await userEvent.click(screen.getByRole("button", { name: "Delete application" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The unit did not stop. Check its logs, then try again.");
    expect(alert).toHaveTextContent("Job for noust-example.com.service canceled.");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("does not run the action when cancelled", async () => {
    const onConfirm = vi.fn(() => Promise.resolve());
    setup(onConfirm);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("has no accessibility violations when open", async () => {
    setup(() => Promise.resolve());
    await open();
    await expectNoAxeViolations(document.body);
  });

  it("speaks Spanish once the language switches", async () => {
    await act(async () => {
      await setLocale("es");
    });
    setup(() => Promise.resolve());
    await open();
    expect(screen.getByRole("textbox", { name: /Escribe example.com para confirmar/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  describe("friction", () => {
    it("asks once, without typing, for a resource that can be created again (simple)", async () => {
      const onConfirm = vi.fn(() => Promise.resolve());
      render(
        <ConfirmDialog
          friction="simple"
          title="Remove the schedule"
          description="Backups already made are kept."
          actionLabel="Remove schedule"
          onConfirm={onConfirm}
          trigger={<Button>Remove</Button>}
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Remove" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Remove the schedule" });
      expect(dialog.querySelector("input")).toBeNull();
      const action = screen.getByRole("button", { name: "Remove schedule" });
      expect(action).toBeEnabled();
      await userEvent.click(action);
      expect(onConfirm).toHaveBeenCalledOnce();
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      });
    });

    it("focuses Cancel, not the destructive action, when it asks without typing", async () => {
      render(
        <ConfirmDialog
          friction="simple"
          title="Stop shop.example.com"
          description="It stops answering until started again."
          actionLabel="Stop application"
          onConfirm={() => Promise.resolve()}
          trigger={<Button>Stop</Button>}
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Stop" }));
      await screen.findByRole("alertdialog");
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
      });
    });

    it("shows the system's words verbatim when a simple confirmation fails", async () => {
      render(
        <ConfirmDialog
          friction="simple"
          title="Revoke the token"
          description="Scripts using it stop working."
          actionLabel="Revoke token"
          onConfirm={() => Promise.reject(new Error("database is locked"))}
          trigger={<Button>Revoke</Button>}
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
      await userEvent.click(await screen.findByRole("button", { name: "Revoke token" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("database is locked");
    });

    it("runs a reversible action at once, with no dialog at all (none)", async () => {
      const onConfirm = vi.fn(() => Promise.resolve());
      render(
        <ConfirmDialog
          friction="none"
          title="Disable the job"
          description="It can be enabled again."
          actionLabel="Disable"
          onConfirm={onConfirm}
          trigger={<Button>Disable</Button>}
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Disable" }));
      expect(onConfirm).toHaveBeenCalledOnce();
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });

    it("names the server it acts on, so a fleet operator knows where", async () => {
      render(
        <ConfirmDialog
          friction="simple"
          server="web-2"
          title="Stop shop.example.com"
          description="It stops answering."
          actionLabel="Stop application"
          onConfirm={() => Promise.resolve()}
          trigger={<Button>Stop</Button>}
        />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Stop" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent("On web-2");
    });

    it("has no accessibility violations in the simple form", async () => {
      render(
        <ConfirmDialog
          friction="simple"
          title="Remove the schedule"
          description="Backups already made are kept."
          actionLabel="Remove schedule"
          onConfirm={() => Promise.resolve()}
          open
        />,
      );
      await expectNoAxeViolations(await screen.findByRole("alertdialog"));
    });
  });

  it("holds the options that change what the action does, between the question and the name, outside its description", async () => {
    const user = userEvent.setup();
    render(
      <ConfirmDialog
        title="Delete example.com"
        description="Stops the service and removes the site."
        confirmText="example.com"
        actionLabel="Delete application"
        onConfirm={() => Promise.resolve()}
        trigger={<Button>Delete</Button>}
      >
        <label>
          <input type="checkbox" /> Also delete its files
        </label>
      </ConfirmDialog>,
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete example.com" });
    const option = screen.getByRole("checkbox", { name: "Also delete its files" });
    expect(option).not.toBeChecked();
    // Options are not read as part of the question.
    expect(dialog).toHaveAccessibleDescription("Stops the service and removes the site.");
    const name = screen.getByRole("textbox", { name: /to confirm/ });
    expect(option.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await expectNoAxeViolations(dialog);
  });

  it("shows the options of a simple confirmation too", async () => {
    render(
      <ConfirmDialog friction="simple" open title="Stop example.com" description="The site answers 502 until it starts." actionLabel="Stop application" onConfirm={() => Promise.resolve()}>
        <p>Keep the timer running</p>
      </ConfirmDialog>,
    );
    const dialog = await screen.findByRole("alertdialog", { name: "Stop example.com" });
    expect(dialog).toHaveTextContent("Keep the timer running");
  });

  describe("when the action is held rather than failed", () => {
    function simple(onConfirm: () => Promise<void>) {
      render(
        <ConfirmDialog
          friction="simple"
          title="Revoke the token"
          description="Scripts using it stop working."
          actionLabel="Revoke token"
          onConfirm={onConfirm}
          trigger={<Button>Revoke</Button>}
        />,
      );
    }

    it("says nothing went wrong when the operator cancels \"Confirm it's you\", and stays open", async () => {
      const error = vi.spyOn(toast, "error");
      simple(() => Promise.reject(new ElevationCancelledError()));
      await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
      await userEvent.click(await screen.findByRole("button", { name: "Revoke token" }));
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Revoke token" })).not.toHaveAttribute("aria-busy", "true");
      });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByRole("alertdialog", { name: "Revoke the token" })).toBeInTheDocument();
      expect(error).not.toHaveBeenCalled();
      error.mockRestore();
    });

    it("says it waits for approval, with the way to Approvals, and closes", async () => {
      const info = vi.spyOn(toast, "info");
      const error = vi.spyOn(toast, "error");
      simple(() => Promise.reject(new ApprovalPendingError("approval_pending", "7")));
      await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
      await userEvent.click(await screen.findByRole("button", { name: "Revoke token" }));
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      });
      expect(info).toHaveBeenCalledOnce();
      const [title, options] = info.mock.calls[0] ?? [];
      expect(title).toBe("Waiting for approval");
      expect(options?.action?.label).toBe("Open Approvals");
      expect(error).not.toHaveBeenCalled();
      info.mockRestore();
      error.mockRestore();
    });

    it("does not report a cancelled confirmation as a failure when it runs at once (none)", async () => {
      const error = vi.spyOn(toast, "error");
      const onConfirm = vi.fn(() => Promise.reject(new ElevationCancelledError()));
      render(
        <ConfirmDialog friction="none" title="Disable the job" description="It can be enabled again." actionLabel="Disable" onConfirm={onConfirm} trigger={<Button>Disable</Button>} />,
      );
      await userEvent.click(screen.getByRole("button", { name: "Disable" }));
      await waitFor(() => {
        expect(onConfirm).toHaveBeenCalledOnce();
      });
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Disable" })).not.toBeDisabled();
      });
      expect(error).not.toHaveBeenCalled();
      error.mockRestore();
    });
  });
});
