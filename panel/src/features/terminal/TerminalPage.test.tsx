import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionInfo } from "../../api/queries/auth";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../app/testRoutes";
import { onDesktop, serverRoutes } from "../server/testing";
import { installFakeCanvas } from "../../components/ui/terminal/testing";
import { FakeTerminalSocket, STATUS_BODY, readyFrame, terminalRoutes, ticketBody } from "./testing";

const APP_PATH = `/apps/${TAB_DOMAIN}/terminal`;

const WITH_TERMINAL: SessionInfo = { ...SESSION, permissions: [...(SESSION.permissions ?? []), "apps.terminal", "server.terminal"] };
const WITHOUT_TERMINAL: SessionInfo = SESSION;

let sockets: FakeTerminalSocket[];

beforeEach(() => {
  sockets = [];
  installFakeCanvas();
  // jsdom lays nothing out: a box for the screen and for the log viewer's window.
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(340);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
  class Made extends FakeTerminalSocket {
    constructor(url: string) {
      super(url);
      sockets.push(this);
    }
  }
  vi.stubGlobal("WebSocket", Made);
});

function routes(extra: Record<string, RouteHandler> = {}, session: SessionInfo = WITH_TERMINAL): Record<string, RouteHandler> {
  return {
    ...appRoutes({}, {}),
    ...onNodeless(session),
    ...terminalRoutes(),
    "POST /api/auth/ws-ticket": () => json(200, { ticket: "platform-ticket", expires_in: 30 }),
    ...extra,
  };
}

function onNodeless(session: SessionInfo): Record<string, RouteHandler> {
  return { "GET /api/auth/session": () => json(200, session) };
}

async function appPage(extra: Record<string, RouteHandler> = {}, session: SessionInfo = WITH_TERMINAL) {
  screenWidth(1440);
  const backend = fakeBackend(routes(extra, session));
  const harness = renderConsole(APP_PATH);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

async function openIt(harness: Awaited<ReturnType<typeof appPage>>): Promise<FakeTerminalSocket> {
  const open = await screen.findByRole("button", { name: "Open terminal" });
  await waitFor(() => {
    expect(open).toBeEnabled();
  });
  await harness.user.click(open);
  await waitFor(() => {
    expect(sockets).toHaveLength(1);
  });
  const socket = sockets[0];
  if (socket === undefined) throw new Error("no socket");
  act(() => {
    socket.open();
    socket.frame(readyFrame());
  });
  return socket;
}

describe("an application's terminal page", () => {
  describe("before it opens", () => {
    it("says in a line what the shell is, keeps the rest behind one button, and offers to open it", async () => {
      const harness = await appPage();
      expect(await screen.findByRole("heading", { name: `A shell as ${TAB_DOMAIN}` })).toBeInTheDocument();
      expect(screen.getByText("As the account the application runs as, in its sandbox.")).toBeInTheDocument();
      expect(screen.getByText("Not open", { selector: "[data-state]" })).toBeInTheDocument();
      // Three paragraphs were above the button; now they are one press away.
      expect(screen.queryByText(/the same files and the same secrets it has/)).not.toBeInTheDocument();
      await harness.user.click(screen.getByRole("button", { name: "How this session works" }));
      expect(await screen.findByText(/the same files and the same secrets it has/)).toBeInTheDocument();
      // The limits are the server's own, in minutes.
      expect(screen.getByText(/ends after 10 min without typing and after 120 min in all/)).toBeInTheDocument();
      expect(screen.getByText("Opening it asks you to confirm it's you.")).toBeInTheDocument();
      await harness.user.keyboard("{Escape}");
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Open terminal" })).toBeEnabled();
      });
    });

    it("says which permission an account lacks, instead of a button that would only fail", async () => {
      await appPage({}, WITHOUT_TERMINAL);
      expect(await screen.findByText("Your account cannot open a terminal here. It needs the apps.terminal permission.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Open terminal" })).toBeDisabled();
    });

    it("says that terminals are off, with the command that turns them on, and still offers a command to run once", async () => {
      await appPage({ "GET /api/terminal/status": () => json(200, { ...STATUS_BODY, enabled: false }) });
      expect(await screen.findByText("Terminals are off on this server")).toBeInTheDocument();
      expect(screen.getByText("noust config set terminal.enabled true")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Open terminal" })).not.toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: /Command/ })).toBeInTheDocument();
    });

    it("lets the shell and the service be chosen", async () => {
      const harness = await appPage();
      await harness.user.click(await screen.findByRole("button", { name: "Options" }));
      expect((await screen.findAllByText("The server's default")).length).toBeGreaterThan(0);
      await harness.user.type(screen.getByRole("textbox", { name: /Service/ }), "web");
      await openIt(harness);
      expect(harness.backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/terminal`)[0]?.body).toMatchObject({ service: "web" });
    });
  });

  describe("a session", () => {
    it("opens with a ticket and shows where, as whom and until when", async () => {
      const harness = await appPage();
      await openIt(harness);
      expect(await screen.findByText("Connected")).toBeInTheDocument();
      const header = screen.getByRole("group", { name: "Terminal session" });
      expect(within(header).getByText(TAB_DOMAIN)).toBeInTheDocument();
      expect(within(header).getByText("noust-app-shop")).toBeInTheDocument();
      expect(within(header).getByText(/Ends in \d+:\d\d:\d\d/)).toBeInTheDocument();
      expect(within(header).getByText("Closes after 10 min without typing")).toBeInTheDocument();
      expect(within(header).getByText("Recorded in the audit trail")).toBeInTheDocument();
      const url = new URL(sockets[0]?.url ?? "");
      expect(url.searchParams.get("terminal_ticket")).toBe("ticket-s-1-first");
    });

    it("draws what the shell prints and sends what is typed", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      await harness.user.click(screen.getByRole("button", { name: "Display" }));
      await harness.user.click(await screen.findByRole("checkbox", { name: /Text view/ }));
      await harness.user.keyboard("{Escape}");
      act(() => {
        socket.frame({ type: "output", data: "deploy@web-01:~$ " });
      });
      await waitFor(() => {
        expect(screen.getByRole("region", { name: "Terminal contents as text" })).toHaveTextContent("deploy@web-01:~$");
      });
      const box = screen.getByRole("textbox", { name: `Terminal - ${TAB_DOMAIN}` });
      fireEvent.keyDown(box, { key: "l" });
      fireEvent.keyDown(box, { key: "s" });
      fireEvent.keyDown(box, { key: "Enter" });
      expect(socket.typed()).toBe("ls\r");
    });

    it("warns that a limit is near, in a notice that is said once", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      act(() => {
        socket.frame({ type: "notice", kind: "idle", seconds: 55 });
      });
      expect(await screen.findByText(/Nothing has been typed for a while\. This terminal closes in \d+ s unless you press a key\./)).toBeInTheDocument();
      fireEvent.keyDown(screen.getByRole("textbox", { name: `Terminal - ${TAB_DOMAIN}` }), { key: "x" });
      await waitFor(() => {
        expect(screen.queryByText(/Nothing has been typed for a while/)).not.toBeInTheDocument();
      });
    });

    it("says how it ended, and opens another one with the same choices", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      act(() => {
        socket.frame({ type: "ended", reason: "exited", exit_code: 3 });
      });
      expect(await screen.findByText("The shell exited with status 3.")).toBeInTheDocument();
      expect(screen.getByText("Ended")).toBeInTheDocument();
      // Nothing typed after it ended goes anywhere.
      fireEvent.keyDown(screen.getByRole("textbox", { name: `Terminal - ${TAB_DOMAIN}` }), { key: "x" });
      expect(socket.typed()).toBe("");

      await harness.user.click(screen.getByRole("button", { name: "Open a new terminal" }));
      await waitFor(() => {
        expect(sockets).toHaveLength(2);
      });
      expect(harness.backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/terminal`)).toHaveLength(2);
    });

    it("says why a session ended when the server ended it", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      act(() => {
        socket.frame({ type: "ended", reason: "idle", exit_code: null });
      });
      expect(await screen.findByText("Closed: nothing was typed for 10 min.")).toBeInTheDocument();
    });

    it("ends the session when asked", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      await harness.user.click(screen.getByRole("button", { name: "End session" }));
      expect(socket.frames().at(-1)).toEqual({ type: "close" });
    });

    it("keeps the screen and says it is reconnecting when the connection drops", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      act(() => {
        socket.frame({ type: "output", data: "kept on screen" });
        socket.drop(1006);
      });
      expect(await screen.findByText("Reconnecting")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "End session" })).toBeInTheDocument();
    });

    it("shows the refusal in the server's own words, and starts over", async () => {
      const harness = await appPage({
        [`POST /api/apps/${TAB_DOMAIN}/terminal`]: () =>
          problem(429, "terminal_limit", "8 terminals are already open on this server", { hint: "End one, or raise terminal.max_sessions" }),
      });
      const open = await screen.findByRole("button", { name: "Open terminal" });
      await waitFor(() => {
        expect(open).toBeEnabled();
      });
      await harness.user.click(open);
      expect(await screen.findByText("The terminal did not open")).toBeInTheDocument();
      expect(screen.getByText("8 terminals are already open on this server")).toBeInTheDocument();
      expect(screen.getByText("End one, or raise terminal.max_sessions")).toBeInTheDocument();
      await harness.user.click(screen.getByRole("button", { name: "Open terminal" }));
      expect(await screen.findByRole("heading", { name: `A shell as ${TAB_DOMAIN}` })).toBeInTheDocument();
    });
  });

  describe("the command to run once", () => {
    async function typeCommand(harness: Awaited<ReturnType<typeof appPage>>, text: string) {
      const input = await screen.findByRole("textbox", { name: /Command/ });
      await harness.user.clear(input);
      await harness.user.click(input);
      await harness.user.paste(text);
    }

    it("shows how the line will be split, and that nothing interprets it", async () => {
      const harness = await appPage();
      expect(await screen.findByText("Type a command to see how it will run.")).toBeInTheDocument();
      await typeCommand(harness, "rails runner 'puts User.count' | wc -l");
      const list = await screen.findByRole("list", { name: "Run as" });
      expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["rails", "runner", "puts User.count", "|", "wc", "-l"]);
      expect(screen.getByText("These are passed as text, not interpreted: |")).toBeInTheDocument();
      // The same thing, for a terminal.
      expect(screen.getByText(/noust app exec shop\.example\.com -- rails runner 'puts User\.count' '\|' wc -l/)).toBeInTheDocument();
    });

    it("will not run a line that ends inside a quote", async () => {
      const harness = await appPage();
      await typeCommand(harness, "echo 'unfinished");
      expect(await screen.findByText("A quote is open: the command ends inside it.")).toBeInTheDocument();
      // The button is never off for a wrong value: pressing it sends nothing and goes to the field.
      await harness.user.click(screen.getByRole("button", { name: "Run command" }));
      expect(harness.backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/exec`)).toHaveLength(0);
      expect(screen.getByLabelText("Command")).toHaveFocus();
    });

    it("says what is missing instead of running nothing", async () => {
      const harness = await appPage();
      const run = await screen.findByRole("button", { name: "Run command" });
      expect(run).toBeEnabled();
      await harness.user.click(run);
      expect(await screen.findByText("Type the command to run.")).toBeInTheDocument();
      expect(screen.getByLabelText("Command")).toHaveFocus();
      expect(harness.backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/exec`)).toHaveLength(0);
    });

    it("rejects a time limit that is not a number in range", async () => {
      const harness = await appPage();
      await typeCommand(harness, "true");
      await harness.user.click(screen.getByRole("button", { name: "Service and time limit" }));
      const limits = await screen.findAllByRole("textbox");
      const timeout = limits.find((field) => field.getAttribute("placeholder") === "Up to 1800");
      if (timeout === undefined) throw new Error("no time limit field");
      await harness.user.type(timeout, "5000");
      expect(await screen.findByText("Whole seconds, from 1 to 1800.")).toBeInTheDocument();
      await harness.user.click(screen.getByRole("button", { name: "Run command" }));
      expect(await screen.findByText("The time limit is not valid: whole seconds, within the limit.")).toBeInTheDocument();
      expect(harness.backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/exec`)).toHaveLength(0);
    });

    it("runs it as a job and shows its output as the program printed it", async () => {
      let finished = false;
      const job = (status: string) => ({
        id: "j1",
        type: "app_exec",
        name: `Run in ${TAB_DOMAIN}`,
        description: "",
        status,
        state: status,
        progress: finished ? 100 : 5,
        total_steps: 100,
        current_step: "",
        created_at: "2026-10-06T09:00:00",
        logs: [],
        metadata: { domain: TAB_DOMAIN },
        result: finished ? { exit_code: 0 } : null,
      });
      const harness = await appPage({
        [`POST /api/apps/${TAB_DOMAIN}/exec`]: () => json(202, { job_id: "j1", status: "pending", message: "Command queued", job: job("pending") }),
        "GET /api/jobs/j1": () => json(200, job(finished ? "completed" : "running")),
        "GET /api/jobs/j1/log": () =>
          json(200, {
            content: "[2026-10-06 09:00:00] [INFO] Running a command in shop.example.com\n[2026-10-06 09:00:01] [INFO] 42 users\n",
            truncated: false,
          }),
      });
      await typeCommand(harness, "rails runner 'puts User.count'");
      await harness.user.click(screen.getByRole("button", { name: "Run command" }));
      expect(harness.backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/exec`)[0]?.body).toEqual({ argv: ["rails", "runner", "puts User.count"] });
      // The title says it, and so does the job's own first line in its output.
      expect(await screen.findAllByText(`Running a command in ${TAB_DOMAIN}`)).not.toHaveLength(0);
      // What the program printed, without the queue's stamp and level.
      expect(await screen.findByText("42 users")).toBeInTheDocument();
      expect(screen.queryByText(/\[INFO\]/)).not.toBeInTheDocument();

      finished = true;
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 3_200));
      });
      expect(await screen.findByText("The command finished")).toBeInTheDocument();
      expect(screen.getByText("Exit status 0.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Run another" })).toBeEnabled();
    }, 15_000);

    it("shows a command that failed in the system's own words", async () => {
      const failed = {
        id: "j2",
        type: "app_exec",
        name: "Run",
        description: "",
        status: "failed",
        state: "failed",
        progress: 5,
        total_steps: 100,
        current_step: "",
        created_at: "2026-10-06T09:00:00",
        error: "The command exited with status 3",
        logs: [],
        metadata: { domain: TAB_DOMAIN },
        result: { exit_code: 3 },
      };
      const harness = await appPage({
        [`POST /api/apps/${TAB_DOMAIN}/exec`]: () => json(202, { job_id: "j2", status: "pending", message: "Command queued", job: failed }),
        "GET /api/jobs/j2": () => json(200, failed),
        "GET /api/jobs/j2/log": () => json(200, { content: "[2026-10-06 09:00:01] [ERROR] boom\n", truncated: false }),
      });
      await typeCommand(harness, "false");
      await harness.user.click(screen.getByRole("button", { name: "Run command" }));
      expect(await screen.findByText(`The command in ${TAB_DOMAIN} failed`)).toBeInTheDocument();
      expect(screen.getByText("The command exited with status 3")).toBeInTheDocument();
      expect(await screen.findByText("boom")).toBeInTheDocument();
    });

    it("does not offer the form to an account without the permission", async () => {
      await appPage({}, WITHOUT_TERMINAL);
      expect(await screen.findAllByText(/needs the apps\.terminal permission|It needs the apps\.terminal permission/)).not.toHaveLength(0);
      expect(screen.queryByRole("textbox", { name: /Command/ })).not.toBeInTheDocument();
    });

    it("says it could not be started when the server refuses", async () => {
      const harness = await appPage({
        [`POST /api/apps/${TAB_DOMAIN}/exec`]: () => problem(400, "validation_error", "The command is empty"),
      });
      await typeCommand(harness, "x");
      await harness.user.click(screen.getByRole("button", { name: "Run command" }));
      expect(await screen.findByText("The command could not be started")).toBeInTheDocument();
      expect(screen.getByText("The command is empty")).toBeInTheDocument();
    });
  });

  describe("reaching it", () => {
    it("is under More actions for an account that may open it, and not for one that may not", async () => {
      screenWidth(1440);
      fakeBackend(routes({}, WITH_TERMINAL));
      const first = renderConsole(`/apps/${TAB_DOMAIN}`);
      const top = await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
      const header = top.closest("[data-slot='header']");
      if (!(header instanceof HTMLElement)) throw new Error("no header");
      await first.user.click(await within(header).findByRole("button", { name: "More actions" }));
      await first.user.click(await screen.findByRole("menuitem", { name: "Terminal" }));
      await waitFor(() => {
        expect(first.location().pathname).toBe(APP_PATH);
      });
    });

    it("is not in the menu of an account that may not open it", async () => {
      screenWidth(1440);
      fakeBackend(routes({}, WITHOUT_TERMINAL));
      const { user } = renderConsole(`/apps/${TAB_DOMAIN}`);
      const top = await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
      const header = top.closest("[data-slot='header']");
      if (!(header instanceof HTMLElement)) throw new Error("no header");
      await user.click(await within(header).findByRole("button", { name: "More actions" }));
      const menu = await screen.findByRole("menu");
      expect(within(menu).queryByRole("menuitem", { name: "Terminal" })).not.toBeInTheDocument();
    });
  });

  describe("in Spanish", () => {
    it("speaks Spanish, from the start to the end of a session", async () => {
      await setLocale("es");
      const harness = await appPage();
      expect(await screen.findByRole("heading", { name: `Una shell como ${TAB_DOMAIN}` })).toBeInTheDocument();
      const open = await screen.findByRole("button", { name: "Abrir terminal" });
      await waitFor(() => {
        expect(open).toBeEnabled();
      });
      await harness.user.click(open);
      await waitFor(() => {
        expect(sockets).toHaveLength(1);
      });
      act(() => {
        sockets[0]?.open();
        sockets[0]?.frame(readyFrame());
        sockets[0]?.frame({ type: "ended", reason: "limit", exit_code: null });
      });
      expect(await screen.findByText("Cerrada: la sesión llegó a su límite de tiempo.")).toBeInTheDocument();
    });
  });

  describe("accessibility", () => {
    it("has no violations before it opens", async () => {
      const harness = await appPage();
      await screen.findByRole("heading", { name: `A shell as ${TAB_DOMAIN}` });
      await expectNoAxeViolations(harness.container);
    });

    it("has none with a session open", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      await screen.findByText("Connected");
      act(() => {
        socket.frame({ type: "notice", kind: "idle", seconds: 55 });
      });
      await screen.findByText(/closes in/);
      await expectNoAxeViolations(harness.container);
    });

    it("has none once it ended", async () => {
      const harness = await appPage();
      const socket = await openIt(harness);
      act(() => {
        socket.frame({ type: "ended", reason: "exited", exit_code: 0 });
      });
      await screen.findByText("The shell exited.");
      await expectNoAxeViolations(harness.container);
    });
  });
});

describe("the server's terminal page", () => {
  beforeEach(onDesktop);

  async function serverPage(status: Record<string, unknown>, session: SessionInfo = WITH_TERMINAL) {
    const backend = fakeBackend({
      ...routes({}, session),
      ...serverRoutes(),
      "GET /api/terminal/status": () => json(200, status),
      "POST /api/server/terminal": () => json(200, ticketBody({ domain: null })),
    });
    const harness = renderConsole("/server/terminal");
    await screen.findByRole("heading", { level: 1, name: "Server" });
    return { ...harness, backend };
  }

  it("says the shell is off, and how to turn it on", async () => {
    await serverPage(STATUS_BODY);
    expect(await screen.findByText("The terminal on the server is off")).toBeInTheDocument();
    expect(screen.getByText("noust config set terminal.server.enabled true")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open terminal" })).not.toBeInTheDocument();
  });

  it("says the profile keeps it off even when it is switched on, and how to force it", async () => {
    await serverPage({ ...STATUS_BODY, ens: true, server: { allowed: false, enabled: true, reason: "The ENS profile keeps it off", how: "force" } });
    expect(await screen.findByText(/The ENS profile keeps it off even when it is turned on/)).toBeInTheDocument();
    expect(screen.getByText("noust config set terminal.server.force_ens true")).toBeInTheDocument();
  });

  it("opens a root shell when it is on, and says it is root", async () => {
    const harness = await serverPage({ ...STATUS_BODY, server: { allowed: true, enabled: true, reason: null, how: null } });
    expect(await screen.findByRole("heading", { name: "A root shell on the server" })).toBeInTheDocument();
    const open = screen.getByRole("button", { name: "Open terminal" });
    await waitFor(() => {
      expect(open).toBeEnabled();
    });
    await harness.user.click(open);
    await waitFor(() => {
      expect(sockets).toHaveLength(1);
    });
    act(() => {
      sockets[0]?.open();
      sockets[0]?.frame(readyFrame({ domain: null }));
    });
    const header = await screen.findByRole("group", { name: "Terminal session" });
    expect(within(header).getByText("root")).toBeInTheDocument();
    expect(harness.backend.callsTo("POST /api/server/terminal")).toHaveLength(1);
  });

  it("is offered in the server's menu to an account that holds the permission", async () => {
    const harness = await serverPage(STATUS_BODY);
    await harness.user.click(await screen.findByRole("button", { name: "More actions" }));
    expect(await screen.findByRole("menuitem", { name: "Open a terminal" })).toBeInTheDocument();
  });

  it("is not offered to one that does not", async () => {
    const harness = await serverPage(STATUS_BODY, WITHOUT_TERMINAL);
    await harness.user.click(await screen.findByRole("button", { name: "More actions" }));
    await screen.findByRole("menu");
    expect(screen.queryByRole("menuitem", { name: "Open a terminal" })).not.toBeInTheDocument();
  });
});
