import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { EXTRAS_EMPTY, extrasChanged, extrasDraftOf, hasExtras, parseExtras } from "./healthExtras";
import { DOMAIN, ELEVATED, NO_HEALTH_EXTRAS, RELEASE_APP, healthOf, part, renderSettings, saveBar } from "./testKit";

const HEALTH = `GET /api/apps/${DOMAIN}/health`;
const PATCH = `PATCH /api/apps/${DOMAIN}/health`;

function deploys(extras: Record<string, unknown> = {}, app: object = RELEASE_APP, routes: Parameters<typeof renderSettings>[3] = {}) {
  return renderSettings("/deploys", "Releases and rollback", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [HEALTH]: () => json(200, healthOf(app as Record<string, unknown>, extras)),
    ...routes,
  });
}

describe("what the startup check asks and how, beyond the path", () => {
  it("shows a plain request first, with the options folded away", async () => {
    await deploys();
    const check = part("Startup check");

    expect(await within(check).findByRole("radio", { name: "A request" })).toBeChecked();
    expect(within(check).getByRole("textbox", { name: "Path" })).toBeInTheDocument();
    expect(within(check).queryByRole("textbox", { name: "Command" })).not.toBeInTheDocument();
    expect(within(check).getByRole("button", { name: "Request options" })).toHaveAttribute("aria-expanded", "false");
    expect(within(check).getByRole("button", { name: "Timing" })).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the options an application already uses, with what it has", async () => {
    await deploys({ method: "HEAD", scheme: "https", host: "shop.example.com", port: 8443, interval: 5, retries: 20, start_period: 10 });
    const check = part("Startup check");

    expect(await within(check).findByRole("button", { name: "Request options" })).toHaveAttribute("aria-expanded", "true");
    expect(within(check).getByRole("textbox", { name: "Host header" })).toHaveValue("shop.example.com");
    expect(within(check).getByRole("textbox", { name: "Port" })).toHaveValue("8443");
    expect(within(check).getByRole("button", { name: "Timing" })).toHaveAttribute("aria-expanded", "true");
    expect(within(check).getByRole("textbox", { name: "Interval" })).toHaveValue("5");
    expect(within(check).getByRole("textbox", { name: "Attempts" })).toHaveValue("20");
    expect(within(check).getByRole("textbox", { name: "Start period" })).toHaveValue("10");
  });

  it("saves a command check: the command in place of the path, with everything it stores", async () => {
    const { user, backend } = await deploys({}, RELEASE_APP, {
      [PATCH]: ({ body }) => json(200, { ...healthOf(RELEASE_APP), ...(body as object) }),
    });
    const check = part("Startup check");
    await user.click(await within(check).findByRole("radio", { name: "A command" }));
    expect(within(check).queryByRole("textbox", { name: "Path" })).not.toBeInTheDocument();

    await user.type(within(check).getByRole("textbox", { name: "Command" }), "/usr/bin/node scripts/healthcheck.js");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(PATCH)[0]?.body).toEqual({
        path: null,
        expect: null,
        timeout: null,
        ...NO_HEALTH_EXTRAS,
        kind: "command",
        command: "/usr/bin/node scripts/healthcheck.js",
      });
    });
    expect(within(check).getByText(/must exit 0/)).toBeInTheDocument();
  });

  it("asks for a command when the check is one, and says so where it is wrong", async () => {
    const { user, backend } = await deploys();
    const check = part("Startup check");
    await user.click(await within(check).findByRole("radio", { name: "A command" }));

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await within(check).findByText(/Give the command to run/)).toBeInTheDocument();
    expect(within(check).getByRole("textbox", { name: "Command" })).toHaveAttribute("aria-invalid", "true");
    expect(backend.callsTo(PATCH)).toHaveLength(0);
  });

  it("offers the service of a stack, and only for a stack", async () => {
    await deploys({ kind: "command", command: "pg_isready" }, { ...RELEASE_APP, app_type: "docker-compose" });
    expect(await within(part("Startup check")).findByRole("textbox", { name: "Service" })).toBeInTheDocument();
  });

  it("does not offer a service to an application that is not a stack", async () => {
    await deploys({ kind: "command", command: "pg_isready" });
    const check = part("Startup check");
    await within(check).findByRole("textbox", { name: "Command" });
    expect(within(check).queryByRole("textbox", { name: "Service" })).not.toBeInTheDocument();
  });

  it("sends the timing it was given, and the request's method and host", async () => {
    const { user, backend } = await deploys({}, RELEASE_APP, {
      [PATCH]: ({ body }) => json(200, { ...healthOf(RELEASE_APP), ...(body as object) }),
    });
    const check = part("Startup check");
    await user.click(await within(check).findByRole("button", { name: "Request options" }));
    await user.type(within(check).getByRole("textbox", { name: "Host header" }), "shop.example.com");
    await user.click(within(check).getByRole("button", { name: "Timing" }));
    await user.type(within(check).getByRole("textbox", { name: "Interval" }), "5");
    await user.type(within(check).getByRole("textbox", { name: "Attempts" }), "20");
    await user.type(within(check).getByRole("textbox", { name: "Start period" }), "15");

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(PATCH)[0]?.body).toMatchObject({ host: "shop.example.com", interval: 5, retries: 20, start_period: 15, kind: "http" });
    });
  });

  it("puts a refusal under the field it names", async () => {
    const { user } = await deploys({}, RELEASE_APP, {
      [PATCH]: () => problem(422, "validationerror", "An interval of 90 seconds is out of range", { fields: { interval: "Give it from 1 to 60 seconds." } }),
    });
    const check = part("Startup check");
    await user.click(await within(check).findByRole("button", { name: "Timing" }));
    await user.type(within(check).getByRole("textbox", { name: "Interval" }), "50");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await within(check).findByText("Give it from 1 to 60 seconds.")).toBeInTheDocument();
  });

  it("checks the numbers before asking anything", async () => {
    const { user, backend } = await deploys();
    const check = part("Startup check");
    await user.click(await within(check).findByRole("button", { name: "Timing" }));
    await user.type(within(check).getByRole("textbox", { name: "Interval" }), "99");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await within(check).findByText("Give it from 1 to 60 seconds, or leave it empty for the default.")).toBeInTheDocument();
    expect(backend.callsTo(PATCH)).toHaveLength(0);
  });

  it("does not let the rest be saved over what it could not read", async () => {
    const { user, backend } = await deploys({}, RELEASE_APP, { [HEALTH]: () => problem(500, "internal", "The store is busy") });
    const check = part("Startup check");

    expect(await within(check).findByText("Could not read the startup check")).toBeInTheDocument();
    await user.type(within(check).getByRole("textbox", { name: "Path" }), "/healthz");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(backend.callsTo(PATCH)).toHaveLength(0);
  });

  it("has no accessibility violations with the options open", async () => {
    await deploys({ kind: "http", method: "HEAD", interval: 5 });
    await within(part("Startup check")).findByRole("button", { name: "Request options" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("speaks Spanish", async () => {
    await deploys();
    await act(() => setLocale("es"));
    expect(await within(part("Comprobación de arranque")).findByRole("radio", { name: "Una petición" })).toBeInTheDocument();
    expect(within(part("Comprobación de arranque")).getByRole("button", { name: "Opciones de la petición" })).toBeInTheDocument();
  });
});

describe("reading the extra fields", () => {
  it("is a plain request when nothing is set", () => {
    expect(extrasDraftOf(undefined)).toEqual(EXTRAS_EMPTY);
    expect(hasExtras(extrasDraftOf({ kind: "http" }))).toBe(false);
  });

  it("counts what differs from what is stored", () => {
    const stored = extrasDraftOf({ kind: "command", command: "pg_isready" });
    expect(extrasChanged({ ...stored, command: "pg_isready -q", interval: "5" }, stored)).toBe(2);
  });

  it("does not send what the kind does not use", () => {
    const { values } = parseExtras({ ...EXTRAS_EMPTY, kind: "command", command: "pg_isready", method: "HEAD", host: "x.example.com" });
    expect(values).toMatchObject({ kind: "command", command: "pg_isready", method: null, host: null, port: null });
  });

  it("uppercases a method and lowercases a scheme as the server stores them", () => {
    const { values, errors } = parseExtras({ ...EXTRAS_EMPTY, method: "head", scheme: "HTTPS" });
    expect(errors).toEqual({});
    expect(values).toMatchObject({ method: "HEAD", scheme: "https" });
  });

  it.each([
    [{ method: "DELETE" }, "method"],
    [{ scheme: "ftp" }, "scheme"],
    [{ host: "a b" }, "host"],
    [{ port: "70000" }, "port"],
    [{ interval: "0" }, "interval"],
    [{ retries: "101" }, "retries"],
    [{ startPeriod: "601" }, "startPeriod"],
  ])("refuses %j on the field it is about", (change, field) => {
    expect(Object.keys(parseExtras({ ...EXTRAS_EMPTY, ...change }).errors)).toEqual([field]);
  });
});
