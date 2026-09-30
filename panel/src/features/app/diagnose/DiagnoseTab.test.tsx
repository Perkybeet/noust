import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";

const DOMAIN = "shop.example.com";

const DOWN = {
  domain: DOMAIN,
  verdict: "down",
  probable_cause: "The app listens on 3001, not the recorded port 3000.",
  checks: [
    { name: "unit", status: "ok", summary: "shop-example-com.service is active (running)", evidence: "ActiveState=active\nSubState=running" },
    {
      name: "port",
      status: "fail",
      summary: "The app listens on 3001, not the recorded port 3000",
      evidence: 'LISTEN 0 511 127.0.0.1:3001 0.0.0.0:* users:(("node",pid=4242,fd=19))',
    },
    { name: "journal", status: "warn", summary: "Last 2 journal line(s)", evidence: "Error: listen EADDRINUSE :::3000" },
    { name: "oom", status: "ok", summary: "No OOM kills in the kernel log in the last 7 days", evidence: "" },
    { name: "certificate", status: "skip", summary: "No certificate found for shop.example.com", evidence: "" },
  ],
};

const HEALTHY = {
  domain: DOMAIN,
  verdict: "healthy",
  probable_cause: null,
  checks: [{ name: "unit", status: "ok", summary: "shop-example-com.service is active (running)", evidence: "ActiveState=active" }],
};

function at(rows: readonly HTMLElement[], index: number): HTMLElement {
  const row = rows[index];
  if (!row) throw new Error(`No row ${String(index)}`);
  return row;
}

async function diagnoseTab(diagnose: RouteHandler) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/apps/${DOMAIN}/diagnose`]: diagnose,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/diagnose`);
  await screen.findByRole("heading", { level: 1, name: DOMAIN });
  return { ...harness, backend };
}

describe("the Diagnose tab", () => {
  it("puts the verdict first, in the product's words, with the system's own sentence under it", async () => {
    await diagnoseTab(() => json(200, DOWN));
    const verdict = await screen.findByRole("heading", { level: 2, name: "Verdict: Down" });
    expect(verdict.querySelector("[data-verdict]")).toHaveAttribute("data-verdict", "down");
    expect(screen.getByText("Most likely")).toBeInTheDocument();
    // The port failed and the service is up: the headline is the port's, not systemd's text.
    expect(screen.getByText("Nothing answers on the app's port")).toBeInTheDocument();
    const said = screen.getByText(DOWN.probable_cause);
    expect(said.tagName).toBe("PRE");
    expect(screen.getByText(/^5 checks:/)).toBeInTheDocument();
    // What to do about it, beside it.
    expect(screen.getByRole("link", { name: "View logs" })).toHaveAttribute("href", `/apps/${DOMAIN}/logs`);
    expect(screen.getByRole("button", { name: "Roll back…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart" })).toBeInTheDocument();
  });

  it("lists what did not pass first with its output verbatim, and folds what passed into one line", async () => {
    const { user } = await diagnoseTab(() => json(200, DOWN));
    const checks = await screen.findByRole("region", { name: "Checks" });
    const rows = within(within(checks).getByRole("list", { name: "Checks to look at" })).getAllByRole("listitem");
    // The two problems, then one line for the rest.
    expect(rows).toHaveLength(2);
    expect(within(at(rows, 0)).getByText("Failed")).toBeInTheDocument();
    expect(within(at(rows, 0)).getByText("Listening port")).toBeInTheDocument();
    expect(within(at(rows, 1)).getByText("Warning")).toBeInTheDocument();

    // The check the verdict hangs on is open, and so are the logs it cites; output is verbatim, in mono.
    const evidence = within(at(rows, 0)).getByText(DOWN.checks[1]?.evidence ?? "");
    expect(evidence.tagName).toBe("PRE");
    expect(evidence).toBeVisible();
    expect(rows[0]?.querySelector("details")).toHaveAttribute("open");
    expect(rows[1]?.querySelector("details")).toHaveAttribute("open");

    // What passed or was skipped is folded under one line, and opens on request.
    const rest = checks.querySelector<HTMLElement>("details[data-rest]");
    if (rest === null) throw new Error("nothing folded");
    expect(rest).not.toHaveAttribute("open");
    await user.click(within(rest).getByText("3 checks passed or were skipped"));
    expect(rest).toHaveAttribute("open");
    const passed = within(rest).getByRole("list", { name: "Checks that passed or were skipped" });
    expect(within(passed).getAllByRole("listitem")).toHaveLength(3);
    expect(within(passed).getAllByText("No output")).toHaveLength(2);
    expect(within(passed).getByText("Skipped")).toBeInTheDocument();
  });

  it("links a journal finding to the live log", async () => {
    await diagnoseTab(() => json(200, DOWN));
    const link = await screen.findByRole("link", { name: "Follow the live log" });
    expect(link).toHaveAttribute("href", `/apps/${DOMAIN}/logs`);
  });

  it("runs the probes again on request and announces the new verdict", async () => {
    let calls = 0;
    const { user, backend } = await diagnoseTab(() => {
      calls += 1;
      return json(200, calls === 1 ? DOWN : HEALTHY);
    });
    await screen.findByText(DOWN.probable_cause);
    await user.click(screen.getByRole("button", { name: "Run again" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Verdict: Healthy" })).toBeInTheDocument();
    expect(screen.getByText("All good")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart" })).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/diagnose`)).toHaveLength(2);
    await waitFor(() => {
      expect(screen.getByTestId("announcer-polite")).toHaveTextContent(`${DOMAIN}: Healthy.`);
    });
  });

  it("shows a failure to diagnose verbatim, with a way to try again", async () => {
    await diagnoseTab(() => problem(500, "internal", "ss: command not found", { hint: "Install iproute2." }));
    expect(await screen.findByText("Could not load the diagnosis of shop.example.com")).toBeInTheDocument();
    expect(screen.getByText("ss: command not found")).toBeInTheDocument();
    expect(screen.getByText("Install iproute2.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    await diagnoseTab(() => json(200, DOWN));
    await screen.findByText(DOWN.probable_cause);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("puts the verdict and the checks in Spanish", async () => {
    await act(() => setLocale("es"));
    await diagnoseTab(() => json(200, DOWN));
    const verdict = await screen.findByRole("heading", { level: 2, name: "Veredicto: Caída" });
    expect(verdict.querySelector("[data-verdict]")).toHaveAttribute("data-verdict", "down");
    expect(screen.getByText("Lo más probable")).toBeInTheDocument();
    expect(screen.getByText("Nada responde en el puerto de la aplicación")).toBeInTheDocument();
    expect(screen.getByText(/^5 comprobaciones:/)).toBeInTheDocument();
    const checks = await screen.findByRole("region", { name: "Comprobaciones" });
    const rows = within(within(checks).getByRole("list", { name: "Comprobaciones que revisar" })).getAllByRole("listitem");
    expect(within(at(rows, 0)).getByText("Fallida")).toBeInTheDocument();
    expect(within(at(rows, 0)).getByText("Puerto a la escucha")).toBeInTheDocument();
    expect(within(checks).getByText("3 comprobaciones superadas u omitidas")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
