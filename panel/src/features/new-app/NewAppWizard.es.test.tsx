import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { EXPORT, RECIPES, WORDPRESS } from "./testFixtures";
import type { Inspection } from "./wizard";

/** Fails the test where an element the page should render is not there. */
function missing(what: string): never {
  throw new Error(`No ${what} around the element`);
}

const INSPECTION: Inspection = {
  app_type: "nextjs",
  detected_types: ["nextjs", "nodejs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  default_port: 3000,
  env_keys: [{ name: "DATABASE_URL", default: null, secret: false, required: true }],
  branch: "",
  commit: "",
  platform_proposal: {
    platform: "vercel",
    files: ["vercel.json"],
    app_type: null,
    port: null,
    env: [],
    persistent_paths: [],
    databases: [],
    domains: [],
    warnings: ["Vercel's rewrites have no equivalent; configure them in the site's nginx configuration."],
    start_command: null,
    build_command: "next build",
    install_command: null,
    output_directory: null,
    health_path: null,
    health_timeout: null,
  },
};

function wizard() {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () =>
      json(200, {
        types: [
          { type: "nextjs", name: "Next.js", default_port: 3000 },
          { type: "nodejs", name: "Node.js", default_port: 3000 },
        ],
      }),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "POST /api/apps/inspect": () => json(200, INSPECTION),
    "GET /api/recipes": () => json(200, RECIPES),
    "GET /api/recipes/wordpress": () => json(200, WORDPRESS),
  });
  return { backend, harness: renderConsole("/apps/new") };
}

async function inSpanish() {
  await act(async () => {
    await setLocale("es");
  });
}

describe("the new-app wizard in Spanish", () => {
  it("speaks Spanish on every step of a deploy from code, around the server's own words", { timeout: 20_000 }, async () => {
    await inSpanish();
    const { harness } = wizard();
    const { user } = harness;

    await screen.findByRole("heading", { level: 1, name: "Nueva aplicación" });
    expect(screen.getByRole("heading", { level: 2, name: "Origen" })).toBeInTheDocument();
    const modes = screen.getByRole("radiogroup", { name: "Dónde está el código" });
    expect(within(modes).getByRole("radio", { name: "URL o ruta" })).toBeChecked();
    expect(within(modes).getByRole("radio", { name: "Desde una receta" })).toBeInTheDocument();
    expect(within(modes).getByRole("radio", { name: "Importar una aplicación" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Pasos" });
    expect(within(steps).getByText("Configuración: sin empezar")).toBeInTheDocument();

    // A client-side validation message, in Spanish, said by Continue and on its field.
    await user.click(screen.getByRole("button", { name: "Inspeccionar origen" }));
    expect(await screen.findAllByText("Escribe una URL de Git o una ruta de este servidor.")).toHaveLength(2);
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.type(screen.getByLabelText("Repositorio o directorio"), "/var/www/src/storefront");
    await user.click(screen.getByRole("button", { name: "Inspeccionar origen" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Dirección" })).toHaveFocus();
    await user.type(screen.getByLabelText("Dominio"), "storefront.example.com");
    await user.click(screen.getByRole("button", { name: "Continuar" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Configuración" })).toHaveFocus();
    const found = screen.getByRole("group", { name: "Lo que ha encontrado Noust" });
    expect(within(found).getByText("Compilar")).toBeInTheDocument();
    expect(within(found).getByText(/Parece una aplicación Next\.js que usa/)).toBeInTheDocument();
    expect(within(found).getByText("También coincide con Node.js.", { exact: false })).toBeInTheDocument();
    // The platform's warning stays in the server's words.
    const panel = screen.getByText(/Se ha encontrado una configuración de Vercel/).closest("section") ?? missing("section");
    expect(within(panel).getByText("Vercel's rewrites have no equivalent; configure them in the site's nginx configuration.")).toBeInTheDocument();
    expect(within(panel).getByRole("checkbox", { name: "Usar lo que propone" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Tipo de aplicación" })).toHaveTextContent("Next.js");
    expect(screen.getByLabelText("Puerto")).toHaveValue("3001");
    expect(screen.getByText("Vuelta atrás instantánea, nginx")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(screen.getByRole("button", { name: "Continuar" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(await screen.findAllByText(".env.example no le da ningún valor, así que la aplicación espera uno.")).toHaveLength(2);
    await user.type(screen.getByLabelText(/^DATABASE_URL/), "postgres://db/storefront");
    await user.click(screen.getByRole("button", { name: "Continuar" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Despliegue" })).toHaveFocus();
    expect(screen.getByText("Vuelta atrás instantánea, tras una comprobación de arranque")).toBeInTheDocument();
    expect(screen.getByText("1 variable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Desplegar storefront.example.com" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("offers the recipes in Spanish, their descriptions as the server wrote them", { timeout: 20_000 }, async () => {
    await inSpanish();
    const { harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "Nueva aplicación" });
    await user.click(screen.getByRole("radio", { name: "Desde una receta" }));
    expect(await screen.findByRole("button", { name: "Usar WordPress" })).toBeInTheDocument();
    expect(screen.getByText("Una base de datos mysql")).toBeInTheDocument();
    expect(screen.getByText("No disponible en esta versión")).toBeInTheDocument();
    expect(screen.getByText(/supports only MySQL 8 in production/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /wordpress\.org/ })).toHaveTextContent("wordpress.org (se abre en una pestaña nueva)");
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("button", { name: "Usar WordPress" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Dirección" })).toHaveFocus();
    expect(screen.getByRole("checkbox", { name: "Servirla por HTTPS" })).toBeChecked();
    await user.type(screen.getByLabelText("Dominio"), "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toHaveFocus();
    expect(screen.getByText("Generadas para ti")).toBeInTheDocument();
    expect(screen.getByText("Lo que prepara la receta")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("imports in Spanish: the file, what it defines, and what it left out", { timeout: 20_000 }, async () => {
    await inSpanish();
    const { harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "Nueva aplicación" });
    await user.click(screen.getByRole("radio", { name: "Importar una aplicación" }));
    await user.upload(screen.getByLabelText("Fichero de exportación"), new File(["{"], "x.json", { type: "application/json" }));
    expect(await screen.findByText(/^Este fichero no es JSON\./)).toBeInTheDocument();

    await user.upload(screen.getByLabelText("Fichero de exportación"), new File([JSON.stringify(EXPORT)], "shop.wasm-app.json", { type: "application/json" }));
    expect(await screen.findByText("Una exportación de shop.example.com, una aplicación Next.js.")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(screen.getByRole("button", { name: "Continuar" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Dirección" })).toHaveFocus();
    await user.click(screen.getByText("Lo que define la exportación"));
    const summary = screen.getByText("Lo que define la exportación").closest("details") as HTMLElement;
    expect(within(summary).getByText("1 tarea")).toBeInTheDocument();
    expect(within(summary).getByText("2 valores omitidos")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Dominio"));
    await user.type(screen.getByLabelText("Dominio"), "shop.example.org");
    await user.clear(screen.getByLabelText("Origen"));
    await user.type(screen.getByLabelText("Origen"), "https://github.com/acme/shop.git");
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    await waitFor(() => {
      expect(screen.getAllByText("La exportación omitió este valor. Escribe el valor que tenía la aplicación.")).toHaveLength(2);
      expect(screen.getByText(/^Hay 2 campos que corregir antes de continuar, empezando por (STRIPE_KEY|DATABASE_URL)\.$/)).toBeInTheDocument();
    });
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
