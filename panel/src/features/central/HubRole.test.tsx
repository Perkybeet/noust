import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { setLocale } from "../../app/locale";
import { ErrorBlock } from "../../components/page/QueryState";
import { expectNoAxeViolations } from "../../test/axe";

const HUB_ROLE = new ApiError(409, "hub_role", "'noust create' is not available on this central, which is a hub");
const LOCKED = new ApiError(423, "central_locked", "The secret 'nodes/web-2/token' is sealed and the store is locked");

describe("the central's refusals, wherever a page shows an error", () => {
  it("says a hub does not do this, and where to do it, above the central's own words", async () => {
    const { container } = render(<ErrorBlock error={HUB_ROLE} title="Could not create the application" />);
    expect(screen.getByText("Not available on a hub")).toBeInTheDocument();
    expect(screen.getByText(/Open a server of the fleet and do it there/)).toBeInTheDocument();
    expect(screen.getByText(HUB_ROLE.detail)).toBeInTheDocument();
    expect(screen.queryByText("Could not create the application")).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("keeps the backend's own fix when it sends one", () => {
    const withHint = new ApiError(409, "hub_role", HUB_ROLE.detail, "Run it on the server instead.");
    render(<ErrorBlock error={withHint} title="Could not create the application" />);
    expect(screen.getByText("Run it on the server instead.")).toBeInTheDocument();
  });

  it("says a server is out of reach because the central is locked", () => {
    render(<ErrorBlock error={LOCKED} title="Could not load applications" />);
    expect(screen.getByText("This central is locked")).toBeInTheDocument();
    expect(screen.getByText("Its servers are out of reach until you unlock its sealed secrets.")).toBeInTheDocument();
    expect(screen.getByText(LOCKED.detail)).toBeInTheDocument();
  });

  it("says both in Spanish, never translating the system's words", async () => {
    await act(async () => {
      await setLocale("es");
    });
    const { container } = render(
      <>
        <ErrorBlock error={HUB_ROLE} title="No se pudo crear la aplicación" />
        <ErrorBlock error={LOCKED} title="No se pudieron cargar las aplicaciones" />
      </>,
    );
    expect(screen.getByText("No disponible en un hub")).toBeInTheDocument();
    expect(screen.getByText("Esta central está bloqueada")).toBeInTheDocument();
    expect(screen.getByText(HUB_ROLE.detail)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
