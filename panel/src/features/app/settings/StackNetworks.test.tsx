import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, part, renderSettings } from "./testKit";

const COMPOSE_APP = { ...RELEASE_APP, app_type: "docker-compose", layout: "inplace", follow_tags: null };

const OWN = {
  name: "shop_default",
  id: "ffffffffffff",
  logical: "default",
  driver: "bridge",
  scope: "local",
  internal: false,
  ipv6: false,
  subnets: ["172.18.0.0/16"],
  owner: "shop",
  shared: false,
  members: [
    { name: "shop-web-1", address: "172.18.0.2", own: true },
    { name: "shop-db-1", address: "172.18.0.3", own: true },
  ],
  findings: [],
};

const PROXY = {
  ...OWN,
  name: "proxy",
  logical: "",
  owner: "",
  shared: true,
  subnets: ["172.19.0.0/16"],
  members: [
    { name: "shop-web-1", address: "172.19.0.5", own: true },
    { name: "traefik", address: "172.19.0.2", own: false },
  ],
  findings: ["It is shared: traefik is on it too.", "Compose did not make it: it was made by hand or by another tool."],
};

function networks(body: object, routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/compose", "Compose", COMPOSE_APP, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/compose`]: () =>
      json(200, { domain: DOMAIN, compose_file: null, profiles: [], build_args: [], up_args: [], services: [], customised: false, allowed_build_args: [], allowed_up_args: [] }),
    [`GET /api/apps/${DOMAIN}/networks`]: () => json(200, body),
    ...routes,
  });
}

describe("the Docker networks of a stack", () => {
  it("says a stack alone on its own network is isolated", { timeout: 20_000 }, async () => {
    await networks({ app: DOMAIN, applicable: true, reason: "", containers: ["shop-db-1", "shop-web-1"], isolated: true, networks: [OWN] });

    await screen.findByRole("heading", { name: "Docker networks" });
    const card = part("Docker networks");
    expect(await within(card).findByText(/Isolated: every network is the stack's own/)).toBeInTheDocument();
    const list = within(card).getByRole("list", { name: "Networks of the stack" });
    expect(within(list).getByText("shop_default")).toBeInTheDocument();
    expect(within(list).getByText("Address ranges: 172.18.0.0/16")).toBeInTheDocument();
    expect(screen.getByText("noust app compose show " + DOMAIN)).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("names who else is on a shared network and what Docker reports about it", { timeout: 20_000 }, async () => {
    await networks({ app: DOMAIN, applicable: true, reason: "", containers: ["shop-web-1"], isolated: false, networks: [OWN, PROXY] });

    await screen.findByRole("heading", { name: "Docker networks" });
    const card = part("Docker networks");
    expect(await within(card).findByText(/Not isolated: a network is shared/)).toBeInTheDocument();
    const members = within(card).getByRole("list", { name: "Containers on proxy" });
    expect(within(members).getByText("traefik")).toBeInTheDocument();
    expect(within(members).getByText("Other stack")).toBeInTheDocument();
    expect(within(card).getByText("It is shared: traefik is on it too.")).toBeInTheDocument();
    expect(within(card).getByText("Shared")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("says so when the containers are not running", async () => {
    await networks({ app: DOMAIN, applicable: true, reason: "", containers: [], isolated: true, networks: [] });

    await screen.findByRole("heading", { name: "Docker networks" });
    expect(await within(part("Docker networks")).findByText(/The stack is on no Docker network now/)).toBeInTheDocument();
  });

  it("shows Docker's own words when it cannot be asked", async () => {
    await networks({}, {
      [`GET /api/apps/${DOMAIN}/networks`]: () => problem(500, "deployment_error", "Docker could not list its networks", { hint: "Is Docker running? Check: systemctl status docker" }),
    });

    await screen.findByRole("heading", { name: "Docker networks" });
    expect(await within(part("Docker networks")).findByText("Docker could not list its networks")).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await networks({ app: DOMAIN, applicable: true, reason: "", containers: ["shop-web-1"], isolated: true, networks: [OWN] });
    await act(() => setLocale("es"));

    const card = part("Redes de Docker");
    expect(await within(card).findByText(/Aislada: todas las redes son de la pila/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Redes de la pila" })).toBeInTheDocument();
  });
});
