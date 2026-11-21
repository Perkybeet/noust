import { beforeAll, describe, expect, it } from "vitest";

import { loadCatalog } from "./catalogs";
import { translate } from "./translate";

// A language a test asks for is not here until it is loaded: asked cold, Spanish answers with the English it falls back to.
beforeAll(() => loadCatalog("es"));

/** A line that counts containers says "1 container" and "1 contenedor": the number the noun agrees with is the `count`. */
describe("the counts the Docker zone says", () => {
  it("says a server's containers in the singular when it has one, and in the plural otherwise", () => {
    expect(translate("en", "docker.fleet.serverLine", { running: 1, count: 1 })).toBe("1 of 1 container running");
    expect(translate("en", "docker.fleet.serverLine", { running: 2, count: 3 })).toBe("2 of 3 containers running");
    expect(translate("es", "docker.fleet.serverLine", { running: 1, count: 1 })).toBe("1 de 1 contenedor en ejecución");
    expect(translate("es", "docker.fleet.serverLine", { running: 2, count: 3 })).toBe("2 de 3 contenedores en ejecución");
  });
});
