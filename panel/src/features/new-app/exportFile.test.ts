import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/errors";
import { importReportOf } from "../../api/queries/appImport";
import {
  exportFileProblem,
  importBody,
  importProblems,
  importRefusalOf,
  initialImportForm,
  missingValues,
  readExport,
  secretField,
  sourceStripped,
} from "./exportFile";
import { EXPORT } from "./testFixtures";

describe("reading an export file", () => {
  it("reads a wasm-app document", () => {
    expect(readExport(JSON.stringify(EXPORT))).toEqual({ document: EXPORT });
  });

  it("says clearly what is wrong with a file that is not one", () => {
    expect(readExport("not json")).toEqual({ problem: expect.stringMatching(/not JSON/) as string });
    expect(readExport(JSON.stringify({ format: "something-else", version: 1 }))).toEqual({ problem: expect.stringMatching(/not a Noust application export/) as string });
    expect(readExport(JSON.stringify([1, 2]))).toEqual({ problem: expect.stringMatching(/not a Noust application export/) as string });
    expect(readExport(JSON.stringify({ ...EXPORT, version: "1" }))).toEqual({ problem: expect.stringMatching(/not a whole number/) as string });
    expect(readExport(JSON.stringify({ ...EXPORT, version: 2 }))).toEqual({
      problem: "This export is version 2; this console reads version 1. Upgrade Noust on this server to import it.",
    });
    expect(readExport(JSON.stringify({ ...EXPORT, app: { domain: "x.example.com" } }))).toEqual({ problem: expect.stringMatching(/no application in it/) as string });
  });

  it("refuses a file far too large to be an export before reading it", () => {
    expect(exportFileProblem({ size: 4 * 1024 })).toBeNull();
    expect(exportFileProblem({ size: 50 * 1024 * 1024 })).toMatch(/an export is a few kilobytes/);
  });
});

describe("the import form", () => {
  it("starts from the export's domain and source, with an empty value for every secret it left out", () => {
    expect(missingValues(EXPORT)).toEqual(["DATABASE_URL", "STRIPE_KEY"]);
    expect(initialImportForm(EXPORT)).toEqual({
      domain: "shop.example.com",
      source: "https://***@github.com/acme/shop.git",
      secrets: { DATABASE_URL: "", STRIPE_KEY: "" },
    });
  });

  it("requires the source again when the export took its credentials out, and every secret", () => {
    expect(sourceStripped("https://***@github.com/acme/shop.git")).toBe(true);
    expect(sourceStripped("https://github.com/acme/shop.git")).toBe(false);
    const problems = importProblems(initialImportForm(EXPORT), new Set(["shop.example.com"]));
    expect(problems["domain"]).toMatch(/already deployed/);
    expect(problems["source"]).toMatch(/took the credentials out/);
    expect(problems[secretField("STRIPE_KEY")]).toMatch(/left this value out/);
    expect(problems[secretField("DATABASE_URL")]).toMatch(/left this value out/);
  });

  it("accepts a new domain, a source given again and every secret", () => {
    const form = {
      domain: "shop.example.org",
      source: "https://token@github.com/acme/shop.git",
      secrets: { DATABASE_URL: "postgres://db/shop", STRIPE_KEY: "sk_live" },
    };
    expect(importProblems(form, new Set(["shop.example.com"]))).toEqual({});
    expect(importProblems({ ...form, source: "" }, new Set())["source"]).toMatch(/Enter the source/);
    expect(importProblems({ ...form, source: "shop" }, new Set())["source"]).toMatch(/neither a URL nor an absolute path/);
  });
});

describe("the import request", () => {
  it("sends the document, the domain and the source only when they changed, and the secrets", () => {
    const same = { domain: "Shop.Example.com", source: "https://***@github.com/acme/shop.git", secrets: { STRIPE_KEY: "sk" } };
    expect(importBody(EXPORT, same)).toEqual({ document: EXPORT, env: { STRIPE_KEY: "sk" } });
    const changed = { domain: "shop.example.org", source: " https://token@github.com/acme/shop.git ", secrets: {} };
    expect(importBody(EXPORT, changed)).toEqual({
      document: EXPORT,
      domain: "shop.example.org",
      source: "https://token@github.com/acme/shop.git",
      env: {},
    });
  });

  it("sends a taken domain and a refused source back to their fields, and nothing else", () => {
    expect(importRefusalOf(new ApiError(409, "domainconflicterror", "An application is already deployed on shop.example.org"))).toEqual({
      domain: "An application is already deployed on shop.example.org",
    });
    expect(importRefusalOf(new ApiError(400, "validationerror", "The exported source had its credentials taken out", null, { source: "The exported source had its credentials taken out" }))).toEqual({
      source: "The exported source had its credentials taken out",
    });
    expect(importRefusalOf(new ApiError(400, "validationerror", "The export left out the value of 1 variable(s): X", null, { env: "..." }))).toBeNull();
    expect(importRefusalOf(new Error("boom"))).toBeNull();
  });
});

describe("the import's report", () => {
  it("is read off the job's result: every step, and the ones not applied", () => {
    expect(
      importReportOf({
        domain: "shop.example.org",
        steps: [
          { part: "deploy shop.example.org", applied: true, detail: "" },
          { part: "cron nightly", applied: false, detail: "No cron daemon" },
        ],
        not_applied: [{ part: "cron nightly", applied: false, detail: "No cron daemon" }],
      }),
    ).toEqual({
      domain: "shop.example.org",
      steps: [
        { part: "deploy shop.example.org", applied: true, detail: "" },
        { part: "cron nightly", applied: false, detail: "No cron daemon" },
      ],
      notApplied: [{ part: "cron nightly", applied: false, detail: "No cron daemon" }],
    });
    expect(importReportOf({ deployment_id: 4 })).toBeNull();
    expect(importReportOf(null)).toBeNull();
  });
});
