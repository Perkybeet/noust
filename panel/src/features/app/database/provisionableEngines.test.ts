import { describe, expect, it } from "vitest";

import { ENGINES } from "../../databases/testFixtures";
import { provisionableEngines } from "./CreateLinkDialog";

describe("the engines a database can be created on for an application", () => {
  it("leaves out containers: their databases belong to the compose file", () => {
    const host = ENGINES.find((engine) => engine.name === "postgresql");
    if (!host) throw new Error("no postgresql fixture");
    const container = { ...host, name: "postgresql@shop.db", kind: "container", container: "shop-db-1" };

    const names = provisionableEngines([host, container]).map((engine) => engine.name);

    expect(names).toEqual(["postgresql"]);
  });
});
