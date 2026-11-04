import { describe, expect, it } from "vitest";

import { loadCatalog } from "../../i18n";
import { stepWords } from "./stepWords";

describe("stepWords", () => {
  it("shows the server's own words in English, whatever the step", () => {
    expect(stepWords("deploy", "Deploying")).toBe("Deploying");
    expect(stepWords("update", "[7/7] 🚀 Activating release...")).toBe("[7/7] 🚀 Activating release...");
    expect(stepWords("deploy", "")).toBeNull();
    expect(stepWords("deploy", null)).toBeNull();
  });

  it("says in Spanish what a deploy is doing: the phases the deployment page draws and the steps around them", async () => {
    await loadCatalog("es");
    expect(stepWords("deploy", "Deploying", "es")).toBe("Desplegando");
    // The deployer's step header, with its counter and its icon, names the phase.
    expect(stepWords("deploy", "[7/7] 🚀 Activating release...", "es")).toBe("Arrancando la versión nueva…");
    expect(stepWords("update", "Activating release 20261030-101500, built from main", "es")).toBe("Arrancando la versión nueva…");
    expect(stepWords("deploy", "[2/7] 📥 Fetching source into a new release...", "es")).toBe("Obteniendo el origen…");
    expect(stepWords("update", "Installing dependencies", "es")).toBe("Instalando dependencias…");
    expect(stepWords("deploy", "[4/7] 🔒 Setting permissions...", "es")).toBe("Ajustando los permisos");
    expect(stepWords("deploy", "[5/7] 🌐 Creating site configuration...", "es")).toBe("Creando la configuración del sitio");
    expect(stepWords("deploy", "Obtaining SSL certificate", "es")).toBe("Obteniendo el certificado");
  });

  it("says a plan of two phases as the plan it is, not as the one it names last", async () => {
    await loadCatalog("es");
    // The first line of an update of an app on releases: it is neither "Arrancando la versión nueva" nor "Compilando".
    expect(stepWords("update", "Building and activating a new release", "es")).toBe("Compilando y activando una nueva release");
  });

  it("leaves a step it does not know as the server said it, and every step of a job that is not a deploy", async () => {
    await loadCatalog("es");
    expect(stepWords("deploy", "Copying the data", "es")).toBe("Copying the data");
    // A database engine's installation is not the deploy's install phase.
    expect(stepWords("database_install", "Installing the postgresql engine", "es")).toBe("Installing the postgresql engine");
  });

  it("says the step of any kind of job in Spanish when the server sent its sentence, with no need to know the job by name", async () => {
    await loadCatalog("es");
    const creating = { code: "jobs.step.backup.creating", params: {} };
    expect(stepWords("backup", "Creating backup", "es", creating)).toBe("Creando la copia de seguridad");
    expect(stepWords("restore", "Uploading to s3-main", "es", { code: "jobs.step.backup.uploading", params: { destination: "s3-main" } })).toBe("Subiendo a s3-main");
    // The deploy's own step is the server's sentence now, and said the same way.
    expect(stepWords("deploy", "Deploying", "es", { code: "jobs.step.deploy.deploying", params: {} })).toBe("Desplegando la aplicación");
    // In English it is the English the server sent, which is the catalog's.
    expect(stepWords("backup", "Creating backup", "en", creating)).toBe("Creating backup");
  });

  it("falls back to the server's English for a code this console does not know, and to the old reading for no code", async () => {
    await loadCatalog("es");
    expect(stepWords("backup", "Something new", "es", { code: "jobs.step.from.a.newer.server", params: {} })).toBe("Something new");
    expect(stepWords("backup", "Creating backup", "es", null)).toBe("Creating backup");
    expect(stepWords("deploy", "Obtaining SSL certificate", "es", null)).toBe("Obteniendo el certificado");
  });
});
