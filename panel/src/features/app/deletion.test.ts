import { describe, expect, it } from "vitest";

import type { DeletionPlan } from "../../api/queries/apps";
import { NOTHING_CHOSEN, deleteOptionsOf, dropsOf, keptOf } from "./deletion";

const PLAN: DeletionPlan = {
  domain: "shop.example.com",
  path: "/var/www/apps/shop-example-com",
  keeps_directory: false,
  has_volumes: false,
  databases: [],
};

const ADOPTED_STACK: DeletionPlan = { ...PLAN, path: "/opt/shop", keeps_directory: true, has_volumes: true };

const DATABASES: DeletionPlan["databases"] = [
  { engine: "postgresql", name: "shop", owned: true, shared_with: [], will_drop: true },
  { engine: "postgresql", name: "reports", owned: false, shared_with: ["analytics.example.com"], will_drop: false },
];

describe("what the page of deleting an application sends", () => {
  it("starts with nothing chosen, so nothing that cannot be made again is destroyed unless it is ticked", () => {
    expect(deleteOptionsOf(NOTHING_CHOSEN, ADOPTED_STACK)).toEqual({
      removeFiles: false,
      removeSsl: false,
      removeVolumes: false,
      removeDatabases: false,
      removeAdoptedDirectory: null,
    });
  });

  it("deletes the files of an application Noust made with the box alone", () => {
    expect(deleteOptionsOf({ ...NOTHING_CHOSEN, files: true }, PLAN)).toMatchObject({ removeFiles: true, removeAdoptedDirectory: null });
  });

  it("names the directory of an adopted stack when its box is ticked, because the server removes it only then", () => {
    // The server keeps a directory outside its apps directory whatever remove_files says, unless it is named.
    expect(deleteOptionsOf({ ...NOTHING_CHOSEN, files: true }, ADOPTED_STACK)).toMatchObject({ removeFiles: true, removeAdoptedDirectory: "/opt/shop" });
  });

  it("does not name a directory that was not asked to go", () => {
    expect(deleteOptionsOf({ ...NOTHING_CHOSEN, volumes: true }, ADOPTED_STACK)).toMatchObject({ removeFiles: false, removeAdoptedDirectory: null, removeVolumes: true });
  });

  it("cannot ask for volumes or databases the application does not have", () => {
    const options = deleteOptionsOf({ files: false, certificate: true, volumes: true, databases: true }, { ...PLAN, databases: DATABASES.slice(1) });
    expect(options).toMatchObject({ removeSsl: true, removeVolumes: false, removeDatabases: false });
  });

  it("asks for the databases to go only when one of them is the application's own and nothing else uses it", () => {
    expect(deleteOptionsOf({ ...NOTHING_CHOSEN, databases: true }, { ...PLAN, databases: DATABASES })).toMatchObject({ removeDatabases: true });
  });
});

describe("which databases go and which stay", () => {
  it("drops the one that is only the application's and keeps the one other applications use", () => {
    expect(dropsOf({ ...PLAN, databases: DATABASES }).map((database) => database.name)).toEqual(["shop"]);
    expect(keptOf({ ...PLAN, databases: DATABASES }).map((database) => database.name)).toEqual(["reports"]);
  });
});
