import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { WhichServerDialog } from "./WhichServerDialog";
import type { WhichServerOption } from "./WhichServerDialog";

const SERVERS: readonly WhichServerOption[] = [
  { name: "central-01", central: true, detail: "Debian 12" },
  { name: "web-01", detail: "Ubuntu 24.04" },
  { name: "web-02", detail: "Ubuntu 22.04", answer: { kind: "stale", since: new Date(Date.now() - 3 * 3_600_000 - 30 * 60_000 - 20_000) } },
  { name: "db-02", detail: "Debian 12" },
  { name: "vps-03", detail: "Ubuntu 20.04", unfit: "Docker is not installed" },
  { name: "nas-01", detail: "Synology DSM 7", answer: { kind: "unsupported", version: "4.2.1" } },
];

interface Setup {
  servers?: readonly WhichServerOption[];
  open?: boolean;
  onChoose?: (server: string) => void;
  onOpenChange?: (open: boolean) => void;
}

function dialog({ servers = SERVERS, open = true, onChoose = () => undefined, onOpenChange = () => undefined }: Setup = {}) {
  return <WhichServerDialog open={open} onOpenChange={onOpenChange} description="Choose where Uptime Kuma is installed." servers={servers} onChoose={onChoose} />;
}

function radio(name: string): HTMLElement {
  return screen.getByRole("radio", { name });
}

describe("WhichServerDialog", () => {
  it("asks which server, says what is placed, and says that the server chosen above stays as it is", async () => {
    render(dialog());
    const box = await screen.findByRole("dialog", { name: "On which server?" });
    expect(within(box).getByText(/Choose where Uptime Kuma is installed\./)).toBeInTheDocument();
    expect(within(box).getByText(/The server chosen above stays as it is\./)).toBeInTheDocument();
  });

  it("is closed until it is asked", () => {
    render(dialog({ open: false }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lists every server and preselects none, so no Enter deploys to the wrong one", async () => {
    render(dialog());
    await screen.findByRole("dialog");
    expect(screen.getAllByRole("radio")).toHaveLength(6);
    for (const each of screen.getAllByRole("radio")) expect(each).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("says what each server is when it can be chosen, and marks the central", async () => {
    render(dialog());
    await screen.findByRole("dialog");
    expect(radio("web-01")).toHaveAccessibleDescription("Ubuntu 24.04");
    expect(radio("web-01")).not.toHaveAttribute("aria-disabled", "true");
    expect(within(radio("central-01")).getByText("central")).toBeInTheDocument();
  });

  it("turns off each server that does not do, with its reason, in the order of the list", async () => {
    render(dialog());
    await screen.findByRole("dialog");
    // No connection: the one that answers late and the one that says so by its age.
    expect(radio("web-02")).toHaveAttribute("aria-disabled", "true");
    expect(radio("web-02")).toHaveAccessibleDescription("No connection for 3h 30m");
    // Its own reason, from what it lacks.
    expect(radio("vps-03")).toHaveAttribute("aria-disabled", "true");
    expect(radio("vps-03")).toHaveAccessibleDescription("Docker is not installed");
    // Its Noust does not know it.
    expect(radio("nas-01")).toHaveAttribute("aria-disabled", "true");
    expect(radio("nas-01")).toHaveAccessibleDescription("Its Noust, 4.2.1, does not support this");
  });

  it("says no connection first when a server cannot be reached and also lacks something", async () => {
    render(dialog({ servers: [{ name: "web-02", unfit: "Docker is not installed", answer: { kind: "unreachable" } }, { name: "web-01" }] }));
    await screen.findByRole("dialog");
    expect(radio("web-02")).toHaveAccessibleDescription("No connection");
  });

  it("preselects the one server that does, because there is nothing to choose between", async () => {
    render(dialog({ servers: SERVERS.filter((server) => server.name === "web-01" || server.name === "vps-03" || server.name === "web-02") }));
    await screen.findByRole("dialog");
    expect(radio("web-01")).toBeChecked();
  });

  it("continues with the one server when it is the only one, and with Enter", async () => {
    const onChoose = vi.fn();
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(dialog({ servers: [{ name: "web-01" }, { name: "web-02", answer: { kind: "unreachable" } }], onChoose, onOpenChange }));
    await screen.findByRole("dialog");
    screen.getByRole("button", { name: "Continue" }).focus();
    await user.keyboard("{Enter}");
    expect(onChoose).toHaveBeenCalledWith("web-01");
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("does not continue with nothing chosen: it says what is missing and takes the keyboard to the choice", async () => {
    const onChoose = vi.fn();
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(dialog({ onChoose, onOpenChange }));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(onChoose).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByText("Choose a server.")).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Server" })).toHaveAttribute("aria-invalid", "true");
    expect(radio("central-01")).toHaveFocus();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("continues with the server chosen, and closes", async () => {
    const onChoose = vi.fn();
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(dialog({ onChoose, onOpenChange }));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Choose a server.")).toBeInTheDocument();
    await user.click(radio("db-02"));
    expect(screen.queryByText("Choose a server.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(onChoose).toHaveBeenCalledWith("db-02");
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("does not pick a server that is off, with a click or with the arrows", async () => {
    const onChoose = vi.fn();
    const user = userEvent.setup();
    render(dialog({ onChoose }));
    await screen.findByRole("dialog");
    await user.click(screen.getByText("Docker is not installed"));
    expect(radio("vps-03")).not.toBeChecked();
    radio("db-02").focus();
    await user.keyboard("{ArrowDown}");
    // Past the two that are off, back to the first that is not.
    expect(radio("central-01")).toBeChecked();
  });

  it("reaches the first server that does with the keyboard when the ones before it are off", async () => {
    const user = userEvent.setup();
    render(dialog({ servers: [{ name: "web-02", answer: { kind: "unreachable" } }, { name: "vps-03", unfit: "Docker is not installed" }, { name: "web-01" }, { name: "db-02" }] }));
    await screen.findByRole("dialog");
    radio("web-01").focus();
    await user.keyboard("{ArrowDown}");
    expect(radio("db-02")).toBeChecked();
    // And back: it goes past the off ones at the top, to the last.
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(radio("db-02")).toBeChecked();
  });

  it("says that no server can take it, each with why, and offers no way to continue", async () => {
    render(dialog({ servers: [{ name: "web-02", answer: { kind: "unreachable" } }, { name: "nas-01", answer: { kind: "unsupported" } }] }));
    await screen.findByRole("dialog");
    expect(screen.getByText("No server can take this now: each one says why.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
    expect(radio("nas-01")).toHaveAccessibleDescription("Its Noust does not support this");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("forgets what was chosen when it is closed and asked again", async () => {
    const user = userEvent.setup();
    const { rerender } = render(dialog());
    await screen.findByRole("dialog");
    await user.click(radio("web-01"));
    expect(radio("web-01")).toBeChecked();
    rerender(dialog({ open: false }));
    rerender(dialog());
    await screen.findByRole("dialog");
    expect(radio("web-01")).not.toBeChecked();
  });

  it("drops the choice of a server that stops answering while the question is open", async () => {
    const user = userEvent.setup();
    const { rerender } = render(dialog());
    await screen.findByRole("dialog");
    await user.click(radio("web-01"));
    rerender(dialog({ servers: SERVERS.map((server) => (server.name === "web-01" ? { ...server, answer: { kind: "unreachable" as const } } : server)) }));
    expect(radio("web-01")).not.toBeChecked();
    expect(radio("web-01")).toHaveAttribute("aria-disabled", "true");
  });

  it("closes from Cancel without choosing", async () => {
    const onChoose = vi.fn();
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(dialog({ onChoose, onOpenChange }));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("says it in Spanish", async () => {
    await act(() => setLocale("es"));
    render(dialog());
    await screen.findByRole("dialog", { name: "¿En qué servidor?" });
    expect(screen.getByText(/El servidor elegido arriba se queda como está\./)).toBeInTheDocument();
    expect(radio("web-02")).toHaveAccessibleDescription(/Sin conexión desde hace 3\sh 30\smin/);
    expect(radio("nas-01")).toHaveAccessibleDescription("Su Noust, 4.2.1, no lo admite");
    expect(screen.getByRole("button", { name: "Continuar" })).toBeInTheDocument();
  });

  it("has no accessibility violations, with and without a choice", async () => {
    const user = userEvent.setup();
    render(dialog());
    await screen.findByRole("dialog");
    await expectNoAxeViolations(document.body);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await expectNoAxeViolations(document.body);
  });
});
