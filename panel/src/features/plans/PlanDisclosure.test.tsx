import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fakeBackend, json, problem } from "../../test/fakes";
import { PlanDisclosure } from "./PlanDisclosure";

const PLAN = {
  operation: "app.stop",
  subject: "shop.example.com",
  effect: { code: "test.plan.effect", params: {}, text: "Stops shop.example.com: it answers nothing until it is started again.", hint: null },
  commands: [{ argv: ["systemctl", "stop", "shop.service"], text: "systemctl stop shop.service", probe: false }],
  files: [],
  steps: [],
  findings: [],
  unknown: [{ code: "test.plan.unknown", params: {}, text: "Whether anything depends on it.", hint: null }],
  needs: { elevation: false, approval: false, confirmations: [] },
  blocked: null,
  asked_remote: false,
  omitted: 0,
};

function mount() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <div data-testid="host">
        <PlanDisclosure operation="stop" params={{ domain: "shop.example.com" }} />
      </div>
    </QueryClientProvider>,
  );
}

describe("PlanDisclosure", () => {
  it("asks for the simulation of the operation when it opens and draws it folded, with what it does not know counted", async () => {
    const backend = fakeBackend({ "POST /api/apps/shop.example.com/stop/plan": () => json(200, PLAN) });
    mount();
    const toggle = await screen.findByRole("button", { name: /See what it will do/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("1 command");
    expect(toggle).toHaveTextContent("1 thing it does not know");
    expect(backend.callsTo("POST /api/apps/shop.example.com/stop/plan")).toHaveLength(1);
  });

  it("is not drawn at all where the server has no such plan: it is not offered, not shown empty", async () => {
    const backend = fakeBackend({ "POST /api/apps/shop.example.com/stop/plan": () => problem(404, "not_found", "No such route") });
    mount();
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/shop.example.com/stop/plan")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.getByTestId("host")).toBeEmptyDOMElement();
    });
  });

  it("says where the simulation failed, with the system's words, and never blocks what the dialog is for", async () => {
    fakeBackend({ "POST /api/apps/shop.example.com/stop/plan": () => problem(500, "internal_error", "The simulation could not read the unit", { output: "Unit shop.service not found." }) });
    mount();
    expect(await screen.findByText("The simulation could not read the unit")).toBeInTheDocument();
    expect(screen.getByText("Unit shop.service not found.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });
});
