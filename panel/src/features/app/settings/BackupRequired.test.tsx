import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, renderSettings } from "./testKit";

const ROUTE = `/api/apps/${DOMAIN}/backup-required`;

const DEFAULT_ON = { domain: DOMAIN, required: true, explicit: false, default_required: true, databases: ["postgresql/shop_db"] };
const DEFAULT_OFF = { domain: DOMAIN, required: false, explicit: false, default_required: false, databases: [] };
const CHOSEN_OFF = { domain: DOMAIN, required: false, explicit: true, default_required: true, databases: ["postgresql/shop_db"] };

const ELEVATED_SESSION: Record<string, RouteHandler> = { "GET /api/auth/session": () => json(200, ELEVATED) };

function deploys(read: object, routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/deploys", "Deploys", RELEASE_APP, {
    ...ELEVATED_SESSION,
    [`GET ${ROUTE}`]: () => json(200, read),
    ...routes,
  });
}

describe("the backup an update cannot go without", { timeout: 20_000 }, () => {
  it("says it is required by default because the app has databases, and which", async () => {
    await deploys(DEFAULT_ON);

    const heading = await screen.findByRole("heading", { name: "A backup before each update" });
    const card = heading.closest<HTMLElement>("section, div");
    if (card === null) throw new Error("the heading is in no card");
    expect(await screen.findByRole("switch", { name: "Refuse an update when its backup fails" })).toBeChecked();
    expect(screen.getByTestId("backup-required-note")).toHaveTextContent("required because the app has databases (postgresql/shop_db)");
    expect(screen.queryByRole("button", { name: "Use the default" })).not.toBeInTheDocument();
    await expectNoAxeViolations(card);
  });

  it("says a failed backup is only a warning when the app has no database", async () => {
    await deploys(DEFAULT_OFF);

    expect(await screen.findByRole("switch", { name: "Refuse an update when its backup fails" })).not.toBeChecked();
    expect(screen.getByTestId("backup-required-note")).toHaveTextContent("the app has no database");
  });

  it("turns it on with PUT and announces it", async () => {
    const { user, backend } = await deploys(DEFAULT_OFF, {
      [`PUT ${ROUTE}`]: () => json(200, { ...DEFAULT_OFF, required: true, explicit: true }),
    });

    await user.click(await screen.findByRole("switch", { name: "Refuse an update when its backup fails" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROUTE}`)[0]?.body).toEqual({ required: true });
    });
    expect(await screen.findByRole("button", { name: "Use the default" })).toBeInTheDocument();
  });

  it("asks once more before it goes off, and then says what that costs", async () => {
    const { user, backend } = await deploys(DEFAULT_ON, {
      [`PUT ${ROUTE}`]: () => json(200, CHOSEN_OFF),
    });

    await user.click(await screen.findByRole("switch", { name: "Refuse an update when its backup fails" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Let an update of ${DOMAIN} go on without its backup?` });
    expect(backend.callsTo(`PUT ${ROUTE}`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Let it go on" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROUTE}`)[0]?.body).toEqual({ required: false });
    });
    expect(await screen.findByText(/cannot be undone from a backup that was not taken/)).toBeInTheDocument();
  });

  it("goes back to the default with null", async () => {
    const { user, backend } = await deploys(CHOSEN_OFF, {
      [`PUT ${ROUTE}`]: () => json(200, DEFAULT_ON),
    });

    await user.click(await screen.findByRole("button", { name: "Use the default" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROUTE}`)[0]?.body).toEqual({ required: null });
    });
  });

  it("is not shown by a server that has no such setting", async () => {
    await renderSettings("/deploys", "Deploys", RELEASE_APP, {});

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "A backup before each update" })).not.toBeInTheDocument();
    });
  });

  it("speaks Spanish", async () => {
    await deploys(DEFAULT_ON);
    await screen.findByRole("switch", { name: "Refuse an update when its backup fails" });

    await act(() => setLocale("es"));

    expect(await screen.findByRole("switch", { name: "Rechazar una actualización cuando su copia falla" })).toBeChecked();
    expect(screen.getByTestId("backup-required-note")).toHaveTextContent("obligatoria porque la aplicación tiene bases de datos");
  });
});
