import { screen, waitFor, within } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { onDesktop, serverRoutes } from "../testing";
import { ITEMS, JOB_ACCEPTED, SOFTWARE, softwareJob, softwareRoutes } from "./testFixtures";

beforeEach(onDesktop);

function page(extra: Record<string, RouteHandler> = {}, options: { session?: typeof SESSION; software?: Parameters<typeof softwareRoutes>[0] } = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(options.session ?? SESSION),
    ...serverRoutes(),
    ...softwareRoutes(options.software),
    "GET /api/jobs/5e1f0c2a": () => json(200, softwareJob("running", { name: "Remove fail2ban" })),
    ...extra,
  });
  const harness = renderConsole("/server/software");
  return { ...harness, backend };
}

function group(name: string): HTMLElement {
  return screen.getByRole("table", { name: `Software: ${name}` });
}

/** The row of a piece by its name, whatever its purpose says: `nginx` is not the row of `nginx stream module`. */
function rowOf(name: string): HTMLElement | null {
  return (
    screen
      .queryAllByRole("row")
      .find((row) => within(row).queryAllByText(name).some((found) => found.className.includes("font-medium"))) ?? null
  );
}

async function findRow(name: string): Promise<HTMLElement> {
  return waitFor(() => {
    const found = rowOf(name);
    if (found === null) throw new Error(`no row for ${name}`);
    return found;
  });
}

describe("the Software section", () => {
  it("lists every piece in its group, with what it is for, its version, who put it there and what uses it", async () => {
    page();
    await screen.findByRole("table", { name: "Software: The machine" });

    const machine = within(group("The machine"));
    expect(machine.getByText("systemd")).toBeInTheDocument();
    // Who put it there: what Noust set up says so, with the person; what was there before says it was not Noust's.
    const web = within(await findRow("nginx"));
    expect(web.getByText("1.24.0")).toBeInTheDocument();
    expect(web.getByText("Installed by Noust")).toBeInTheDocument();
    expect(web.getByText(/yago$/)).toBeInTheDocument();
    expect(web.getByText("4 sites")).toBeInTheDocument();
    const systemd = within(await findRow("systemd"));
    expect(systemd.getByText("Not installed by Noust")).toBeInTheDocument();
    expect(systemd.getByText("Nothing")).toBeInTheDocument();
    // What a piece is for is the server's own sentence, in the console's language.
    expect(systemd.getByText("The service manager every application, site and unit of Noust runs under")).toBeInTheDocument();
  });

  it("says a piece that is not there is not installed, and that it can be installed", async () => {
    page();
    const apache = within(await findRow("Apache"));
    expect(apache.getAllByText("Not installed").length).toBeGreaterThan(0);
    expect(apache.getByText("Does not apply")).toBeInTheDocument();
    expect(apache.getByRole("button", { name: "Install Apache" })).toBeInTheDocument();
  });

  it("says why a piece is never removed, in a help that is a button, and who depends on one that is used", async () => {
    const { user } = page();
    const git = within(await findRow("git"));
    expect(git.getByText("Never")).toBeInTheDocument();
    await user.click(git.getByRole("button", { name: "About removing git" }));
    expect(await screen.findByText("Every deployment from a repository needs it")).toBeInTheDocument();
  });

  it("explains 'when nothing uses it' with what uses it now", async () => {
    const { user } = page();
    const nginx = within(await findRow("nginx"));
    expect(nginx.getByText("When nothing uses it")).toBeInTheDocument();
    await user.click(nginx.getByRole("button", { name: "About removing nginx" }));
    expect(await screen.findByText("Something still depends on it: 4 sites. Stop using it first and it can go.")).toBeInTheDocument();
  });

  it("shows only what is installed, or only what is not, by the filter", async () => {
    const { user } = page();
    await screen.findByRole("table", { name: "Software: The machine" });
    await user.click(screen.getByRole("radio", { name: /^Not installed/ }));
    expect(rowOf("nginx")).toBeNull();
    expect(rowOf("Apache")).not.toBeNull();
    expect(screen.queryByRole("table", { name: "Software: The machine" })).toBeNull();
    await user.click(screen.getByRole("radio", { name: /^Installed/ }));
    expect(rowOf("nginx")).not.toBeNull();
    expect(rowOf("Apache")).toBeNull();
  });

  it("asks the machine again when it is told to check again", async () => {
    const { user, backend } = page();
    await user.click(await screen.findByRole("button", { name: "Check again" }));
    await waitFor(() => {
      expect(backend.callsTo("GET /api/server/software").some((call) => call.search.get("refresh") === "true")).toBe(true);
    });
  });

  it("says the system's software is not Noust's to change, and offers nothing to change it", async () => {
    page({}, { software: { list: { ...SOFTWARE, managed: false, reason: "Noust does not update an image-based system", family: "none" } } });
    expect(await screen.findByText("Noust does not change this system's software.")).toBeInTheDocument();
    expect(screen.getByText("Noust does not update an image-based system")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Install / })).toBeNull();
  });

  it("says why Noust does not change the system's software in the console's language, from the server's code", async () => {
    await act(() => setLocale("es"));
    page(
      {},
      {
        software: {
          list: {
            ...SOFTWARE,
            managed: false,
            family: "none",
            reason: "Noust drives apt, dnf and zypper, and none of them is installed here. Update this system with its own package manager.",
            reason_coded: { code: "host.updates_unsupported.no_manager", params: {} },
          },
        },
      },
    );
    expect(await screen.findByText("Noust no cambia el software de este sistema.")).toBeInTheDocument();
    expect(screen.getByText("Noust maneja apt, dnf y zypper, y aquí no hay ninguno instalado. Actualiza este sistema con su propio gestor de paquetes.")).toBeInTheDocument();
  });

  it("draws no button, and says so once, for an account that can only read", async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => !["server.manage", "server.host_access", "databases.manage"].includes(permission)) };
    page({}, { session: reader });
    expect(await screen.findByText("Your account can see the software but not change it.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Install / })).toBeNull();
  });

  it("has no accessibility violations", async () => {
    page();
    await screen.findByRole("table", { name: "Software: The machine" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says it in Spanish: the groups, the pieces and the server's own sentences", async () => {
    await act(() => setLocale("es"));
    page();
    expect(await screen.findByRole("table", { name: "Software: Sitios y certificados" })).toBeInTheDocument();
    const git = within(await findRow("git"));
    expect(git.getByText("Nunca")).toBeInTheDocument();
    expect(git.getByText("Descarga el código de toda aplicación que se despliega desde un repositorio")).toBeInTheDocument();
    expect(within(await findRow("nginx")).getByText("4 sitios")).toBeInTheDocument();
  });
});

describe("removing a piece", () => {
  it("looks first at what it would do, and asks one question for what Noust put there", async () => {
    const { user, backend } = page({ "POST /api/server/software/fail2ban/remove": () => json(202, JOB_ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: "Remove fail2ban" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Remove fail2ban" });
    expect(backend.callsTo("GET /api/server/software/fail2ban/impact")).toHaveLength(1);
    // The packages, what goes with them, that nothing depends on it, and that it is not purged.
    expect(await within(dialog).findByText("python3-pyinotify")).toBeInTheDocument();
    expect(within(dialog).getByText("whois")).toBeInTheDocument();
    expect(within(dialog).getByText("Nothing: no application, site or backup uses it.")).toBeInTheDocument();
    expect(within(dialog).getByText(/It is removed, not purged/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/server/software/fail2ban/remove")).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Remove fail2ban" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/software/fail2ban/remove")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/software/fail2ban/remove")[0]?.body).toEqual({ confirm_unknown_origin: false });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog", { name: "Remove fail2ban" })).toBeNull();
    });
    // The task is the one in hand of the Server area, said as what it is.
    expect(await screen.findByText("Changing the software")).toBeInTheDocument();
  });

  it("is refused by the server with its reason, what uses the piece and the way to it, and removes nothing", async () => {
    const { user, backend } = page();
    await user.click(await screen.findByRole("button", { name: "Remove nginx" }));

    const dialog = await screen.findByRole("dialog", { name: "nginx cannot be removed now" });
    expect(within(dialog).getByText("nginx cannot be removed while something depends on it: 4 sites")).toBeInTheDocument();
    const list = within(dialog).getByRole("list");
    expect(within(list).getByText("4 sites")).toBeInTheDocument();
    expect(within(list).getByText(/shop\.example\.com, blog\.example\.org/)).toBeInTheDocument();
    expect(within(dialog).getByText("Stop using it first (remove or move what is listed), then remove it.")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "See the sites" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Understood" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/server/software/nginx/remove")).toHaveLength(0);
  });

  it("says the refusal in Spanish when the console is in Spanish, from the code and the values", async () => {
    await act(() => setLocale("es"));
    const { user } = page();
    await user.click(await screen.findByRole("button", { name: "Quitar nginx" }));

    const dialog = await screen.findByRole("dialog", { name: "nginx no se puede quitar ahora" });
    // The server's sentence by its code, with the list of what depends said in Spanish too.
    expect(within(dialog).getByText("nginx no se puede quitar mientras algo dependa de él: 4 sitios")).toBeInTheDocument();
    expect(within(dialog).getByText("Deja de usarlo primero (quita o mueve lo que sale en la lista) y luego quítalo.")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Ver los sitios" })).toBeInTheDocument();
  });

  it("asks for more than a click for what Noust did not install: a yes and the name", async () => {
    const { user, backend } = page({ "POST /api/server/software/chrony/remove": () => json(202, JOB_ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: "Remove chrony" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Remove chrony" });
    expect(await within(dialog).findByText("Noust did not install it: other things on the system may use it without Noust knowing.")).toBeInTheDocument();
    const remove = within(dialog).getByRole("button", { name: "Remove chrony" });
    expect(remove).toBeDisabled();

    await user.click(within(dialog).getByRole("checkbox", { name: /I know Noust did not install it/ }));
    expect(remove).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "chrony");
    expect(remove).toBeEnabled();
    await user.click(remove);

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/software/chrony/remove")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/software/chrony/remove")[0]?.body).toEqual({ confirm_unknown_origin: true });
  });

  it("keeps the dialog open with the server's words when the removal is refused after the question", async () => {
    const { user } = page({
      "POST /api/server/software/fail2ban/remove": () => problem(409, "host_busy", "Another package manager is running", { hint: "Wait for it to finish." }),
    });
    await user.click(await screen.findByRole("button", { name: "Remove fail2ban" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove fail2ban" });
    await user.click(await within(dialog).findByRole("button", { name: "Remove fail2ban" }));

    expect(await within(dialog).findByText("Another package manager is running")).toBeInTheDocument();
    expect(within(dialog).getByText("Wait for it to finish.")).toBeInTheDocument();
  });

  it("says when the server could not tell what removing would do, and offers to try again", async () => {
    const { user } = page({ "GET /api/server/software/fail2ban/impact": () => problem(500, "server_error", "The package manager did not answer") });
    await user.click(await screen.findByRole("button", { name: "Remove fail2ban" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove fail2ban" });
    expect(await within(dialog).findByText("Could not tell what removing fail2ban would do")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("installing a piece", () => {
  it("starts a task at once, with no question, for what comes from the distribution", async () => {
    const { user, backend } = page({ "POST /api/server/software/apache/install": () => json(202, JOB_ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: "Install Apache" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/software/apache/install")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/software/apache/install")[0]?.body).toEqual({ epel: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says what the server refused in a toast, with its own words", async () => {
    const { user } = page({
      "POST /api/server/software/apache/install": () => problem(409, "software_refused", "Noust does not install apache on this system", { hint: "Install it with the system's package manager; Noust manages it from there." }),
    });
    await user.click(await screen.findByRole("button", { name: "Install Apache" }));
    expect(await screen.findByText("Noust does not install apache on this system")).toBeInTheDocument();
  });

  it("asks for EPEL where the piece comes from it, and agrees to it only when told to", async () => {
    const { user, backend } = page(
      { "POST /api/server/software/knot/install": () => json(202, JOB_ACCEPTED) },
      { software: { list: { ...SOFTWARE, family: "dnf" } } },
    );
    await user.click(await screen.findByRole("button", { name: "Install Knot DNS" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Install Knot DNS" });
    expect(within(dialog).getByText("On this system Knot DNS comes from EPEL, a repository that is not the distribution's own.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox", { name: /Enable EPEL/ }));
    await user.click(within(dialog).getByRole("button", { name: "Install Knot DNS" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/software/knot/install")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/software/knot/install")[0]?.body).toEqual({ epel: true });
  });

  it("lists a piece the system's own tools install as one Noust does not install here", async () => {
    page({}, { software: { list: { ...SOFTWARE, items: ITEMS.map((entry) => (entry.id === "apache" ? { ...entry, installable: false } : entry)) } } });
    const apache = within(await findRow("Apache"));
    expect(apache.getByText("Noust does not install it on this system")).toBeInTheDocument();
    expect(apache.queryByRole("button", { name: /^Install/ })).toBeNull();
  });
});
