import { describe, expect, it } from "vitest";

import type { MachineMap } from "../../api/queries/map";
import { bindT } from "../../i18n";
import { countsOf, diagramOf } from "./model";

const MAP: MachineMap = {
  layers: [
    { id: "names", kind: "domain", nodes: [{ id: "domain:shop.example.com", kind: "domain", label: "shop.example.com", domain: "shop.example.com", group: "shop" }] },
    {
      id: "applications",
      kind: "application",
      nodes: [{ id: "app:shop.example.com", kind: "application", label: "shop.example.com", detail: "nextjs", state: "ok", state_word: "running", domain: "shop.example.com", group: "shop" }],
    },
    { id: "databases", kind: "database", nodes: [{ id: "db:postgresql:shop", kind: "database", label: "shop", detail: "postgresql", engine: "postgresql" }] },
    { id: "scheduled", kind: "scheduled", nodes: [{ id: "cron:nightly", kind: "scheduled", label: "nightly", detail: "daily", state: "unknown", state_word: "disabled" }] },
    { id: "future", kind: "mystery", nodes: [{ id: "x:1", kind: "mystery", label: "thing", state: "fine" }] },
  ],
  edges: [
    { source: "domain:shop.example.com", target: "app:shop.example.com", relation: "serves" },
    { source: "app:shop.example.com", target: "db:postgresql:shop", relation: "uses" },
    { source: "app:shop.example.com", target: "cron:nightly", relation: "runs" },
    { source: "app:shop.example.com", target: "x:1", relation: "shares" },
  ],
  truncated: false,
  projects: 1,
};

const t = bindT("en");

describe("the map as the diagram draws it", () => {
  const diagram = diagramOf(MAP, t, (type) => (type === "nextjs" ? "Next.js" : type));

  it("keeps the server's layers and their order, headed in words", () => {
    expect(diagram.layers.map((layer) => layer.title)).toEqual(["Names", "Applications", "Databases", "Scheduled work", "future"]);
  });

  it("says an application's state in the console's own words and its type as a person reads it", () => {
    const app = diagram.layers[1]?.nodes[0];
    expect(app).toMatchObject({ kind: "application", detail: "Next.js", state: "ok", stateLabel: "Running", group: "shop" });
  });

  it("says a disabled job as such, and leaves a database's engine as the server wrote it", () => {
    expect(diagram.layers[3]?.nodes[0]).toMatchObject({ state: "unknown", stateLabel: "Disabled" });
    expect(diagram.layers[2]?.nodes[0]).toMatchObject({ kind: "database", detail: "postgresql" });
  });

  it("draws what a newer server adds as something other, without a state it cannot judge", () => {
    const node = diagram.layers[4]?.nodes[0];
    expect(node?.kind).toBe("other");
    expect(node?.state).toBeUndefined();
  });

  it("names each relation in words, and a new one as the server named it", () => {
    expect(diagram.edges.map((edge) => edge.relation)).toEqual(["answers on", "uses", "runs", "shares"]);
    expect(diagram.edges[0]).toMatchObject({ from: "domain:shop.example.com", to: "app:shop.example.com" });
  });

  it("finds a node by its id, to open the thing it is", () => {
    expect(diagram.nodes.get("db:postgresql:shop")?.engine).toBe("postgresql");
  });

  it("counts what the map holds", () => {
    expect(countsOf(MAP)).toEqual({ applications: 1, databases: 1, scheduled: 1 });
  });
});
