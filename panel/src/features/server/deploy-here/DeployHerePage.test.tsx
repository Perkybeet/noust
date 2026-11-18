import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import { hubSession, nasSession } from "../../../test/nas";

const check = (key: string, status: string, code: string, text: string, params: Record<string, string | number> = {}, hint: string | null = null) => ({ key, status, code, params, text, hint });

const GREEN = {
  ready: true,
  role: "hub",
  deploy_here: "containers",
  checks: [
    check("setting", "ok", "deploy_here.setting.on", "This central deploys containers on this machine's Docker"),
    check("socket", "ok", "deploy_here.socket.found", "Docker's socket is mounted at /var/run/docker.sock", { socket: "/var/run/docker.sock" }),
    check("engine", "ok", "deploy_here.engine.ok", "Docker 24.0.2 answers, speaking API 1.43, with Compose 2.20.2", { version: "24.0.2", api: "1.43", compose: "2.20.2" }),
    check("group", "ok", "deploy_here.group.ok", "Noust may use Docker's socket, which belongs to the group administrators", { group: "administrators" }),
    check("apps_folder", "ok", "deploy_here.folder.ok", "/volume1/docker/noust/apps is the same folder inside this container and on the machine", { folder: "/volume1/docker/noust/apps" }),
  ],
};

const WRONG_FOLDER = {
  ready: false,
  role: "hub",
  deploy_here: "containers",
  checks: [
    ...GREEN.checks.slice(0, 4),
    check(
      "apps_folder",
      "fail",
      "deploy_here.folder.other",
      "/data/apps is another folder on the machine than inside this container",
      { folder: "/data/apps" },
      "Something else is at /data/apps on the machine. Mount the folder you mean at the same path inside the container, so that both sides name one folder.",
    ),
  ],
};

const NOT_TOLD = {
  ready: false,
  role: "hub",
  deploy_here: "",
  checks: [
    check(
      "setting",
      "fail",
      "deploy_here.setting.off",
      "This central is not set to deploy on this machine",
      { variable: "NOUST_DEPLOY_HERE=containers" },
      "Add NOUST_DEPLOY_HERE=containers to the environment of the central's container, beside Docker's socket, and recreate it. It is asked for there on purpose, and the console cannot do it for you.",
    ),
    check("socket", "fail", "deploy_here.socket.missing", "There is no Docker socket at /var/run/docker.sock", { socket: "/var/run/docker.sock" }, "Mount the machine's socket into the central: add /var/run/docker.sock:/var/run/docker.sock to the volumes of its compose file and recreate the container."),
    check("engine", "skipped", "deploy_here.unchecked.socket", "Not checked yet: Docker's socket cannot be used"),
  ],
};

function page(session: ReturnType<typeof nasSession>, report: unknown) {
  const backend = fakeBackend({
    ...signedInRoutes(session),
    "POST /api/central/container-host/check": () => json(200, report),
  });
  return { backend, harness: renderConsole("/server/deploy-here") };
}

describe("Deploy on this machine, on a hub that was not told to (item 188)", () => {
  it("says what it means, how it is turned on and that the console does not turn it on, and asks nothing of the machine until it is asked", { timeout: 60_000 }, async () => {
    const { backend } = page(hubSession(), NOT_TOLD);

    expect(await screen.findByRole("heading", { level: 1, name: "Deploy on this machine" })).toBeInTheDocument();
    expect(screen.getByText("Off")).toBeInTheDocument();
    expect(screen.getByText("Docker access is root access on this machine")).toBeInTheDocument();
    // It is said where it is turned on, and that it is not here.
    expect(screen.getByText(/not from this console/)).toBeInTheDocument();
    // The server refuses to write it from the console or the API (`config.deploy_here_not_from_console`), so nothing here offers to: no
    // switch, and no button that turns it on.
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Turn on/ })).not.toBeInTheDocument();
    const lines = screen.getByRole("group", { name: "Lines to add to Noust's compose.yaml" });
    expect(within(lines).getByText("NOUST_DEPLOY_HERE: containers")).toBeInTheDocument();
    expect(within(lines).getByText(`- /var/run/docker.sock:/var/run/docker.sock`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the lines" })).toBeInTheDocument();
    // What it will and will not do, before it is on.
    expect(screen.getByRole("heading", { level: 2, name: "What Noust will be able to do here, and what not" })).toBeInTheDocument();
    // Nothing was asked of the machine: the check starts a container, so a person asks for it.
    expect(backend.callsTo("POST /api/central/container-host/check")).toHaveLength(0);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("runs the check when asked and says, for each thing that fails, the change that fixes it, and what could not be checked as not checked", { timeout: 60_000 }, async () => {
    const { backend, harness } = page(hubSession(), NOT_TOLD);
    await screen.findByRole("heading", { level: 1, name: "Deploy on this machine" });

    await harness.user.click(screen.getByRole("button", { name: "Run again" }));

    expect(await screen.findByText("This central is not set to deploy on this machine")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/central/container-host/check")).toHaveLength(1);
    // The fix is the server's sentence, with the line to add.
    expect(screen.getByText(/Add NOUST_DEPLOY_HERE=containers to the environment/)).toBeInTheDocument();
    expect(screen.getByText("Not checked yet: Docker's socket cannot be used")).toBeInTheDocument();
    expect(screen.getByText("With a problem")).toBeInTheDocument();
    expect(screen.getByText("Noust does not deploy here until this is fixed")).toBeInTheDocument();
    // Nothing is promised while something fails; the way to turn it on is still there.
    expect(screen.getByRole("group", { name: "Lines to add to Noust's compose.yaml" })).toBeInTheDocument();
  });

  it("does not give Run again to a role that may not ask the machine, and says why", { timeout: 60_000 }, async () => {
    const session = { ...hubSession(), permissions: ["self", "apps.read", "server.read"] };
    page(session, NOT_TOLD);
    await screen.findByRole("heading", { level: 1, name: "Deploy on this machine" });
    // The page is for people who may manage Docker: the one action is not given to anyone else.
    expect(screen.getByRole("button", { name: "Run again" })).toBeDisabled();
  });
});

describe("Deploy on this machine, on a central that deploys containers", () => {
  it("checks the machine's Docker when it is opened, once, and says what it found and what Noust does and does not do here", { timeout: 60_000 }, async () => {
    const { backend } = page(nasSession(), GREEN);

    expect(await screen.findByText("Docker 24.0.2 answers, speaking API 1.43, with Compose 2.20.2")).toBeInTheDocument();
    expect(screen.getByText("On")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/central/container-host/check")).toHaveLength(1);
    // It is on: what it means and how to turn it on are not repeated.
    expect(screen.queryByRole("group", { name: "Lines to add to Noust's compose.yaml" })).not.toBeInTheDocument();
    expect(screen.queryByText("Docker access is root access on this machine")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "What Noust does here, and what it does not" })).toBeInTheDocument();
    expect(screen.getByText(/Applications directly on the machine/)).toBeInTheDocument();
    // The way on is the Docker zone of the console, and the check stays one press away.
    expect(screen.getByRole("link", { name: "Go to Docker" })).toHaveAttribute("href", "/server/docker");
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("finds a folder that is not the same inside and outside, says the change, and promises nothing meanwhile", { timeout: 60_000 }, async () => {
    page(nasSession(), WRONG_FOLDER);

    expect(await screen.findByText("/data/apps is another folder on the machine than inside this container")).toBeInTheDocument();
    expect(screen.getByText("With a problem")).toBeInTheDocument();
    expect(screen.getByText(/Mount the folder you mean at the same path inside the container/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "What Noust does here, and what it does not" })).not.toBeInTheDocument();
    // Nothing leads on while something fails: the one action is to run the check again.
    expect(screen.queryByRole("link", { name: "Go to Docker" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
  });

  it("says it in Spanish, in the words of the machine", { timeout: 60_000 }, async () => {
    page(nasSession(), GREEN);
    await screen.findByText("Docker 24.0.2 answers, speaking API 1.43, with Compose 2.20.2");
    await act(() => setLocale("es"));

    expect(await screen.findByRole("heading", { level: 1, name: "Desplegar en esta máquina" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Activado")).toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 2, name: "Qué hace Noust aquí y qué no" })).toBeInTheDocument();
  });
});
