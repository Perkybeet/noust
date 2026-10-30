import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { onDesktop, serverRoutes } from "../testing";
import { BLOCKED, JOB, NOT_INSTALLED, PLAN, STATUS, webserverRoutes } from "./testFixtures";

beforeEach(onDesktop);

function page(extra: Record<string, RouteHandler> = {}, overrides: Parameters<typeof webserverRoutes>[0] = {}) {
  const backend = fakeBackend({ ...signedInRoutes(), ...serverRoutes(), ...webserverRoutes(overrides), ...extra });
  const harness = renderConsole("/server/webserver");
  return { ...harness, backend };
}

describe("the Web server section", () => {
  it("says which web server answers on ports 80 and 443, and what is behind each", async () => {
    page();
    expect(await screen.findByText(/answers on ports 80 and 443\.$/)).toBeInTheDocument();

    const nginx = screen.getByRole("heading", { name: "nginx" }).closest("section");
    if (nginx === null) throw new Error("no nginx card");
    expect(within(nginx).getByText("Serving")).toBeInTheDocument();
    expect(within(nginx).getByText("Answers on ports 80 and 443")).toBeInTheDocument();
    expect(within(nginx).getByText("9")).toBeInTheDocument();
    const apache = screen.getByRole("heading", { name: "Apache" }).closest("section");
    if (apache === null) throw new Error("no Apache card");
    expect(within(apache).getByText("Stopped")).toBeInTheDocument();
  });

  it("says a web server that is not installed is not installed", async () => {
    page({}, { status: NOT_INSTALLED });

    expect(await screen.findByText("Apache is not installed on this server.")).toBeInTheDocument();
    expect(screen.getByText("Not installed")).toBeInTheDocument();
  });

  it("offers one primary action, the move to the other one, and none when nothing is behind the serving one", async () => {
    page();
    expect(await screen.findByRole("button", { name: "Move to Apache" })).toBeInTheDocument();
  });

  it("offers no move when nothing is behind the web server that serves", async () => {
    page({}, { status: { ...STATUS, servers: { ...STATUS.servers, nginx: { installed: true, running: true, starts_at_boot: true, applications: 0, sites: 0, version: "1.24.0" } } } });

    await screen.findByText(/answers on ports 80 and 443\.$/);
    expect(screen.queryByRole("button", { name: /^Move to/ })).toBeNull();
  });

  it("says the setting is for new applications and changes only that", async () => {
    const { user, backend } = page({ "PUT /api/config/webserver": () => json(200, { webserver: "apache" }) });
    expect(await screen.findByText("Which web server a new application is put behind. It moves none of the ones already here.")).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "New applications go behind" }));
    await user.click(await screen.findByRole("option", { name: "Apache" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/config/webserver")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/config/webserver")[0]?.body).toEqual({ webserver: "apache" });
    expect(backend.callsTo("POST /api/webserver/migrate")).toHaveLength(0);
  });

  it("says what refused the setting, in the server's words", async () => {
    const { user } = page({
      "PUT /api/config/webserver": () => problem(409, "webserver_in_use", "9 applications are still behind nginx", { hint: "Move them first: noust webserver migrate --to apache --all" }),
    });
    await user.click(await screen.findByRole("combobox", { name: "New applications go behind" }));
    await user.click(await screen.findByRole("option", { name: "Apache" }));

    expect(await screen.findByText("9 applications are still behind nginx")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    page();
    await screen.findByRole("button", { name: "Move to Apache" });

    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("moving to the other web server", () => {
  it("reads the plan before asking for anything: what happens, what stops it, what the new site will not do", async () => {
    const { user, backend } = page();
    await user.click(await screen.findByRole("button", { name: "Move to Apache" }));

    const dialog = await screen.findByRole("dialog", { name: "Move to Apache" });
    expect(await within(dialog).findByText("Apache will take over ports 80 and 443 from nginx for 2 site(s)")).toBeInTheDocument();
    expect(within(dialog).getByText(/^Apache is not running, so it takes over ports 80 and 443 from nginx\./)).toBeInTheDocument();
    expect(within(dialog).getByText("1 protection lost")).toBeInTheDocument();
    // Looking changes nothing.
    expect(backend.callsTo("POST /api/webserver/migrate")).toHaveLength(0);
    const asked = backend.callsTo("GET /api/webserver/migration/plan")[0]?.search;
    expect([asked?.get("to"), asked?.get("all")]).toEqual(["apache", "true"]);
  });

  it("puts a protection that would be lost before anything else about the site", async () => {
    const { user } = page();
    await user.click(await screen.findByRole("button", { name: "Move to Apache" }));
    const dialog = await screen.findByRole("dialog", { name: "Move to Apache" });

    expect(await within(dialog).findByText("Protections the new site will not have")).toBeInTheDocument();
    expect(within(dialog).getByText("limit_req (line 12) protects the site, and Apache has nothing equivalent")).toBeInTheDocument();
    expect(within(dialog).getByText(/Put another protection in front of it first/)).toBeInTheDocument();
    expect(within(dialog).getByText("One of them protected a site. It is the first thing listed under that site.")).toBeInTheDocument();
  });

  it("does not move until what the new sites will not do is accepted, and says so instead of a dead button", async () => {
    const { user, backend } = page({ "POST /api/webserver/migrate": () => json(202, { job_id: JOB.id, status: "completed", message: "Queued", job: JOB }) });
    await user.click(await screen.findByRole("button", { name: "Move to Apache" }));
    const dialog = await screen.findByRole("dialog", { name: "Move to Apache" });
    await within(dialog).findByText("Apache will take over ports 80 and 443 from nginx for 2 site(s)");

    await user.click(within(dialog).getByRole("button", { name: "Move to Apache" }));
    expect(await within(dialog).findByText("Accept what the new sites will not do, or choose to leave them where they are.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/webserver/migrate")).toHaveLength(0);

    await user.click(within(dialog).getByRole("checkbox", { name: /I accept that the new site will not do 1 thing the old one did/ }));
    await user.click(within(dialog).getByRole("button", { name: "Move to Apache" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/webserver/migrate")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/webserver/migrate")[0]?.body).toEqual({ to: "apache", all: true, accept_losses: true, confirm: true });
    expect(await within(dialog).findByText("The sites now answer through Apache")).toBeInTheDocument();
    // The output is virtualised: jsdom has no height to draw lines in, so the region is what is asserted.
    expect(within(dialog).getByLabelText("Output of the move to Apache")).toBeInTheDocument();
  });

  it("asks for no acceptance when nothing is lost", async () => {
    const clean = { ...PLAN, losses: 0, sites: PLAN.sites.map((site) => ({ ...site, findings: [] })) };
    const { user, backend } = page({ "POST /api/webserver/migrate": () => json(202, { job_id: JOB.id, status: "completed", message: "Queued", job: JOB }) }, { plan: clean });
    await user.click(await screen.findByRole("button", { name: "Move to Apache" }));
    const dialog = await screen.findByRole("dialog", { name: "Move to Apache" });
    await within(dialog).findByText("Apache will take over ports 80 and 443 from nginx for 2 site(s)");

    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Move to Apache" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/webserver/migrate")[0]?.body).toEqual({ to: "apache", all: true, accept_losses: false, confirm: true });
    });
  });

  it("says what stops the move and the command that installs the other web server, and offers no move", async () => {
    const { user, backend } = page({}, { plan: BLOCKED, status: NOT_INSTALLED });
    await user.click(await screen.findByRole("button", { name: "Move to Apache" }));
    const dialog = await screen.findByRole("dialog", { name: "Move to Apache" });

    expect(await within(dialog).findByText("It cannot move yet")).toBeInTheDocument();
    expect(within(dialog).getByText(/Apache is not installed on this server/)).toBeInTheDocument();
    expect(within(dialog).getByText("apt-get install -y apache2")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Move to Apache" })).toBeNull();
    // The dialog's own corner button and the footer's say the same: there is a way out and no way on.
    expect(within(dialog).getAllByRole("button", { name: "Close" }).length).toBeGreaterThan(0);
    expect(backend.callsTo("POST /api/webserver/migrate")).toHaveLength(0);
  });

  it("says the move did not finish, that everything was put back, and shows the web server's own output", async () => {
    const failed = { ...JOB, status: "failed", error: "Apache did not accept shop.example.net: AH00526: Syntax error", logs: [{ message: "AH00526: Syntax error on line 14" }] };
    const { user } = page({
      "POST /api/webserver/migrate": () => json(202, { job_id: failed.id, status: "failed", message: "Queued", job: failed }),
      [`GET /api/jobs/${failed.id}`]: () => json(200, failed),
    });
    await user.click(await screen.findByRole("button", { name: "Move to Apache" }));
    const dialog = await screen.findByRole("dialog", { name: "Move to Apache" });
    await within(dialog).findByText("Apache will take over ports 80 and 443 from nginx for 2 site(s)");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Move to Apache" }));

    expect(await within(dialog).findByText("The move did not finish")).toBeInTheDocument();
    expect(within(dialog).getByText(/Everything that was changed was put back/)).toBeInTheDocument();
    expect(within(dialog).getAllByText(/AH00526/).length).toBeGreaterThan(0);
  });
});
