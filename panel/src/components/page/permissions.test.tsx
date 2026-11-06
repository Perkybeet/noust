import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { authKeys } from "../../api/queries/auth";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Can, GuardedButton, ReadOnlyLine, useCan } from "./permissions";

function clientWith(permissions: readonly string[] | null, role: string | null = "viewer"): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (permissions !== null) client.setQueryData(authKeys.session, { authenticated: true, permissions: [...permissions], role });
  return client;
}

function withSession(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderWith(ui: ReactNode, permissions: readonly string[] | null, role: string | null = "viewer") {
  const Wrapper = withSession(clientWith(permissions, role));
  return render(<Wrapper>{ui}</Wrapper>);
}

describe("useCan", () => {
  it("is true for a permission the session holds and false for one it does not", () => {
    const wrapper = withSession(clientWith(["apps.read"]));
    expect(renderHook(() => useCan("apps.read"), { wrapper }).result.current).toBe(true);
    expect(renderHook(() => useCan("apps.manage"), { wrapper }).result.current).toBe(false);
  });

  it("is false while the session is not known: nothing that writes is drawn for a moment it should not be", () => {
    const wrapper = withSession(clientWith(null));
    expect(renderHook(() => useCan("apps.manage"), { wrapper }).result.current).toBe(false);
  });

  it("without a permission is the question itself, for what asks about many (a list of sections, the panels a role may see)", () => {
    const wrapper = withSession(clientWith(["apps.read", "server.read"]));
    const { result } = renderHook(() => useCan(), { wrapper });
    expect(result.current("apps.read")).toBe(true);
    expect(["server.read", "fleet.manage"].map(result.current)).toEqual([true, false]);
  });

  it("without a permission answers no to everything while the session is not known", () => {
    const { result } = renderHook(() => useCan(), { wrapper: withSession(clientWith(null)) });
    expect(result.current("apps.read")).toBe(false);
  });
});

describe("Can", () => {
  it("draws what a permission allows, and nothing otherwise: a row's action is not drawn for a role that cannot do it", () => {
    const ui = (
      <Can permission="apps.manage">
        <button type="button">Remove</button>
      </Can>
    );
    const held = renderWith(ui, ["apps.manage"]);
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
    held.unmount();
    renderWith(ui, ["apps.read"]);
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });
});

describe("GuardedButton", () => {
  it("is the button itself for a role that may", () => {
    renderWith(
      <GuardedButton permission="apps.manage" variant="primary">
        Add an account
      </GuardedButton>,
      ["apps.manage"],
    );
    const button = screen.getByRole("button", { name: "Add an account" });
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute("data-variant", "primary");
    expect(screen.queryByRole("button", { name: "Why you cannot do this" })).not.toBeInTheDocument();
  });

  it("stays on screen for a role that may not, disabled, with a lock and the reason one press away", async () => {
    const user = userEvent.setup();
    const { container } = renderWith(
      <GuardedButton permission="apps.manage" variant="primary">
        Add an account
      </GuardedButton>,
      ["apps.read"],
    );
    const button = screen.getByRole("button", { name: "Add an account" });
    expect(button).toBeDisabled();
    // A disabled primary is not a primary: the page keeps one per view for whoever can press it.
    expect(button).toHaveAttribute("data-variant", "secondary");
    expect(container.querySelector("[data-slot=guarded-action] svg")).toHaveAttribute("aria-hidden", "true");
    await user.click(screen.getByRole("button", { name: "Why you cannot do this" }));
    const why = await screen.findByRole("dialog", { name: "Why you cannot" });
    expect(why).toHaveTextContent("apps.manage");
  });

  it("says the page's own reason where it has one", async () => {
    const user = userEvent.setup();
    renderWith(
      <GuardedButton permission="fleet.manage" reason="First add a provider account: it is the one the machine is paid with.">
        Create a server
      </GuardedButton>,
      [],
    );
    await user.click(screen.getByRole("button", { name: "Why you cannot do this" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("First add a provider account");
  });

  it("stays disabled, with no lock, for a role that may when the page itself cannot yet, and says what to do first", async () => {
    const user = userEvent.setup();
    const { container } = renderWith(
      <GuardedButton permission="fleet.manage" variant="primary" icon={<span data-testid="plus" />} unavailable="First add a provider account: it is the one the machine is paid with.">
        Create a server
      </GuardedButton>,
      ["fleet.manage"],
    );
    expect(screen.getByRole("button", { name: "Create a server" })).toBeDisabled();
    // Nothing is denied to this person, so no lock: the icon the button was given is the one it keeps.
    expect(screen.getByTestId("plus")).toBeInTheDocument();
    expect(container.querySelector("svg.lucide-lock-keyhole")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Why you cannot do this yet" }));
    expect(await screen.findByRole("dialog", { name: "Why not yet" })).toHaveTextContent("First add a provider account");
  });

  it("gives the role's reason before the page's: what a role may not do is not fixed by adding an account", () => {
    const { container } = renderWith(
      <GuardedButton permission="fleet.manage" unavailable="Add an account first.">
        Create a server
      </GuardedButton>,
      [],
    );
    expect(container.querySelector("svg.lucide-lock-keyhole")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Why you cannot do this" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Why you cannot do this yet" })).not.toBeInTheDocument();
  });

  it("has no accessibility violations, held or not", async () => {
    const view = renderWith(<GuardedButton permission="apps.manage">Add</GuardedButton>, ["apps.manage"]);
    await expectNoAxeViolations(view.container);
    view.unmount();
    const locked = renderWith(<GuardedButton permission="apps.manage">Add</GuardedButton>, []);
    await expectNoAxeViolations(locked.container);
  });
});

describe("ReadOnlyLine", () => {
  it("says once, at the top, that the page can be read and not changed, with the role and the permission", () => {
    renderWith(<ReadOnlyLine permission="apps.manage" />, ["apps.read"], "viewer");
    const line = document.querySelector("[data-slot=read-only]");
    expect(line).toHaveTextContent("Your role, viewer, can see this page and not change it. Changing it takes the permission apps.manage.");
  });

  it("names no role for an access that has none", () => {
    renderWith(<ReadOnlyLine permission="apps.manage" />, ["apps.read"], null);
    expect(document.querySelector("[data-slot=read-only]")).toHaveTextContent("This access can see this page and not change it.");
  });

  it("draws nothing for a role that may, nor while the session is not known", () => {
    const held = renderWith(<ReadOnlyLine permission="apps.manage" />, ["apps.manage"]);
    expect(document.querySelector("[data-slot=read-only]")).toBeNull();
    held.unmount();
    renderWith(<ReadOnlyLine permission="apps.manage" />, null);
    expect(document.querySelector("[data-slot=read-only]")).toBeNull();
  });

  it("speaks Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    renderWith(<ReadOnlyLine permission="apps.manage" />, [], "viewer");
    const line = document.querySelector("[data-slot=read-only]");
    if (line === null) throw new Error("no line drawn");
    expect(within(line as HTMLElement).getByText(/puede ver esta página, no cambiarla/)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = renderWith(<ReadOnlyLine permission="apps.manage" />, [], "viewer");
    await expectNoAxeViolations(container);
  });
});
