import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { APPS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { dockerSession } from "../docker/testing";
import { screenWidth } from "./testRoutes";
import { DOCKER_CONTAINERS_PATH, dockerContainersHref } from "./howItRuns";

const DOMAIN = "shop.example.com";

/** The application the way its page reads it: a service of the system with its account and its folder. */
const SERVICE_APP = {
  ...APPS[0],
  app_type: "nodejs",
  layout: "releases",
  path: "/var/www/apps/shop-example-com",
  unit: "shop-example-com.service",
  run_as: "noust-app-shop-example-com",
};

async function overview(runs: RouteHandler | null, app: Record<string, unknown> = SERVICE_APP, session = SESSION) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(session),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/apps/${DOMAIN}/webhook/deliveries`]: () => json(200, { items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/domains`]: () => json(200, { app: DOMAIN, domains: [] }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...(runs === null ? {} : { [`GET /api/apps/${DOMAIN}/runs`]: runs }),
  });
  const harness = renderConsole(`/apps/${DOMAIN}`);
  await screen.findByRole("heading", { level: 1, name: DOMAIN });
  return { ...harness, backend };
}

async function card(): Promise<HTMLElement> {
  return await screen.findByRole("region", { name: /How it is running|Cómo corre/ });
}

describe("how an application runs, on its page (item 189)", () => {
  it("says a service of the system runs directly on the server, with its unit, its account and its folder", async () => {
    await overview(() => json(200, { domain: DOMAIN, form: "server", via: null, image: null }));
    const section = await card();

    expect(within(section).getByText("Directly on the server, as a system service.")).toBeInTheDocument();
    expect(within(section).getByText("Directly on the server")).toBeInTheDocument();
    expect(within(section).getByText("shop-example-com.service")).toBeInTheDocument();
    expect(within(section).getByText("noust-app-shop-example-com")).toBeInTheDocument();
    expect(within(section).getByText("/var/www/apps/shop-example-com/current")).toBeInTheDocument();
    // The way to its unit, and the way to change it said once, not offered.
    expect(within(section).getByRole("link", { name: "See the service" })).toHaveAttribute("href", "/services/shop-example-com");
    expect(within(section).getByText(/deployed again as a container and the domain is moved\. It is not converted on the fly/)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says a container is built with its Dockerfile and names its image", async () => {
    await overview(
      () => json(200, { domain: DOMAIN, form: "container", via: "dockerfile", image: "noust-shop-example-com:20261112-101500-abc1234" }),
      { ...SERVICE_APP, app_type: "container" },
    );
    const section = await card();

    expect(within(section).getByText("In a container, built with the Dockerfile of its repository.")).toBeInTheDocument();
    expect(within(section).getByText("noust-shop-example-com:20261112-101500-abc1234")).toBeInTheDocument();
    // A container has no unit of its own to show: its containers are the machine's.
    expect(within(section).queryByText("shop-example-com.service")).not.toBeInTheDocument();
    // The way to a service is not drawn for a container: its way is the Docker zone, said below.
    expect(within(section).queryByRole("link", { name: "See the service" })).not.toBeInTheDocument();
    expect(within(section).getByText(/To run it directly on the server: it is deployed again and the domain is moved/)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("leads a container application to its containers in the Docker zone, where the zone is offered and the role may read it", async () => {
    await overview(() => json(200, { domain: DOMAIN, form: "container", via: "dockerfile", image: "noust-shop:1" }), { ...SERVICE_APP, app_type: "container" }, dockerSession(true));
    const link = within(await card()).getByRole("link", { name: "See in Docker" });
    expect(link).toHaveAttribute("href", dockerContainersHref(DOMAIN));
  });

  it("does not lead there for a role that may not read Docker, nor where Docker is not at hand", async () => {
    const withoutRead = { ...dockerSession(true), permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "docker.read") };
    await overview(() => json(200, { domain: DOMAIN, form: "container", via: "dockerfile", image: "noust-shop:1" }), { ...SERVICE_APP, app_type: "container" }, withoutRead);
    expect(within(await card()).queryByRole("link", { name: "See in Docker" })).not.toBeInTheDocument();
  });

  it("does not lead there where the server says it cannot do Docker: what cannot be done is not offered", async () => {
    await overview(() => json(200, { domain: DOMAIN, form: "container", via: "dockerfile", image: "noust-shop:1" }), { ...SERVICE_APP, app_type: "container" }, dockerSession(false));
    expect(await within(await card()).findByText("In a container, built with the Dockerfile of its repository.")).toBeInTheDocument();
    expect(within(await card()).queryByRole("link", { name: "See in Docker" })).not.toBeInTheDocument();
  });

  it("says a container Railpack built is one", async () => {
    await overview(() => json(200, { domain: DOMAIN, form: "container", via: "railpack", image: null }), { ...SERVICE_APP, app_type: "container" });
    const section = await card();

    expect(within(section).getByText("In a container, built by Railpack.")).toBeInTheDocument();
  });

  it("says a Compose stack is containers from a Compose file", async () => {
    await overview(() => json(200, { domain: DOMAIN, form: "container", via: "compose", image: null }), { ...SERVICE_APP, app_type: "docker-compose" });
    const section = await card();

    expect(within(section).getByText("In containers: a Compose stack.")).toBeInTheDocument();
    expect(within(section).getByText("In containers, from a Compose file")).toBeInTheDocument();
  });

  it("says an image of a registry runs in a container too", async () => {
    await overview(() => json(200, { domain: DOMAIN, form: "container", via: "image", image: "ghcr.io/owner/app:1.4" }), { ...SERVICE_APP, app_type: "container" });
    const section = await card();

    expect(within(section).getByText("In a container, from an image of a registry.")).toBeInTheDocument();
    expect(within(section).getByText("ghcr.io/owner/app:1.4")).toBeInTheDocument();
  });

  it("draws nothing against a server that has no such route, as the page always was", async () => {
    await overview(null);

    expect(await screen.findByRole("heading", { level: 2, name: "Health" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "How it is running" })).not.toBeInTheDocument();
  });

  it("says a failure of the server that is not that, in the server's own words", async () => {
    await overview(() => problem(500, "internal_error", "The store is locked"));

    expect(await screen.findByText("Could not read how it runs")).toBeInTheDocument();
    expect(screen.getByText(/The store is locked/)).toBeInTheDocument();
  });

  it("says it in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    await overview(() => json(200, { domain: DOMAIN, form: "server", via: null, image: null }));
    const section = await card();

    expect(within(section).getByText("Directamente en el servidor, como un servicio del sistema.")).toBeInTheDocument();
    expect(within(section).getByRole("link", { name: "Ver el servicio" })).toBeInTheDocument();
    expect(within(section).getByText(/Para ejecutarla en un contenedor: se despliega de nuevo como contenedor y se mueve el dominio/)).toBeInTheDocument();
  });
});

describe("the link to an application's containers", () => {
  it("is the Containers page of the Docker zone, filtered by the application", () => {
    // The zone is built beside this page: the path it will have is fixed here, so a rename on either side fails a test.
    expect(DOCKER_CONTAINERS_PATH).toBe("/server/docker/containers");
    expect(dockerContainersHref("shop.example.com")).toBe("/server/docker/containers?app=shop.example.com");
  });

  it("writes the domain as a value, never as part of the path", () => {
    expect(dockerContainersHref("a&b=c.example.com")).toBe("/server/docker/containers?app=a%26b%3Dc.example.com");
  });
});
