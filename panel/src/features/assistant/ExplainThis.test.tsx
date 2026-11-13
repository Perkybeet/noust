import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { authKeys } from "../../api/queries/auth";
import { AssistantButton } from "./AssistantButton";
import { ExplainThis } from "./ExplainThis";
import { resetPanel, usePanel } from "./store";

const ON = { enabled: true, can_use: true, tools: true, reason: null, model: "tiny-model", location: "machine", mask_personal: true };

function mount(assistant: object | null, ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  queryClient.setQueryData(authKeys.session, { authenticated: true, assistant });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function Open() {
  const panel = usePanel();
  return <p data-testid="panel">{panel.open ? `open:${JSON.stringify(panel.request?.page)}:${panel.request?.question ?? ""}` : "closed"}</p>;
}

afterEach(() => {
  resetPanel();
});

describe("the assistant, where the console draws it", () => {
  it("offers 'Explain this' on the page that has the error, and opens the panel with that page and a question already asked", async () => {
    mount(ON, (
      <>
        <ExplainThis page={{ kind: "deployment", id: 14, domain: "shop.example.com" }} />
        <Open />
      </>
    ));
    await userEvent.click(screen.getByRole("button", { name: "Explain this" }));
    expect(screen.getByTestId("panel").textContent).toContain('open:{"kind":"deployment","id":14,"domain":"shop.example.com"}:Explain this to me');
  });

  it("draws nothing at all while it is off: not a disabled button, nothing", () => {
    const { container } = mount({ ...ON, enabled: false, can_use: false }, (
      <>
        <ExplainThis page={{ kind: "diagnosis", domain: "shop.example.com" }} />
        <AssistantButton />
      </>
    ));
    expect(container).toBeEmptyDOMElement();
  });

  it("draws nothing for a person who may not use it, though it is on", () => {
    const { container } = mount({ ...ON, can_use: false, reason: { code: "assistant.unavailable.permission", params: {}, text: "x", hint: null } }, (
      <>
        <ExplainThis page={{ kind: "diagnosis", domain: "shop.example.com" }} />
        <AssistantButton />
      </>
    ));
    expect(container).toBeEmptyDOMElement();
  });

  it("draws nothing before the session is known, and for a server that does not have the assistant", () => {
    const { container } = mount(null, (
      <>
        <ExplainThis page={{ kind: "alert", rule: "disk-full" }} />
        <AssistantButton />
      </>
    ));
    expect(container).toBeEmptyDOMElement();
  });

  it("has a button in the bar that says whether the panel is open and opens and closes it", async () => {
    mount(ON, (
      <>
        <AssistantButton />
        <Open />
      </>
    ));
    const button = screen.getByRole("button", { name: "AI assistant" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(button);
    expect(screen.getByTestId("panel").textContent).toMatch(/^open/);
    expect(screen.getByRole("button", { name: "AI assistant" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "AI assistant" }));
    expect(screen.getByTestId("panel").textContent).toBe("closed");
  });
});
