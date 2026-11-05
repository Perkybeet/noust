import { describe, expect, it } from "vitest";

import { REPOSITORY } from "../../app/help";
import { FIELD_IDS, MAX_URL_LENGTH, REPORT_TEMPLATE, TITLE_PREFIX, reportLink } from "./githubUrl";
import type { ReportFields } from "./githubUrl";

const FIELDS: ReportFields = {
  title: "La base tienda_prod se queda cargando al abrir Datos",
  version: "4.2.0",
  area: "Databases",
  whatHappened: "Al abrir Datos de tienda_prod la tabla se queda en gris y no carga.\nSale un error en la consola del navegador.",
  reproduce: "1. Abre Bases de datos y entra en tienda_prod.\n2. Pulsa Datos en la columna.",
};

function parse(url: string): { path: string; query: URLSearchParams } {
  const parsed = new URL(url);
  return { path: `${parsed.origin}${parsed.pathname}`, query: parsed.searchParams };
}

describe("the address of the form, filled in", () => {
  it("opens the console's form in the project's repository with every field by its id", () => {
    const { url, cut } = reportLink(FIELDS);
    const { path, query } = parse(url);

    expect(cut).toBe(false);
    expect(path).toBe(`${REPOSITORY}/issues/new`);
    expect(query.get("template")).toBe(REPORT_TEMPLATE);
    expect(query.get("title")).toBe(`${TITLE_PREFIX}${FIELDS.title}`);
    expect(query.get("version")).toBe("4.2.0");
    expect(query.get("area")).toBe("Databases");
    expect(query.get("what-happened")).toBe(FIELDS.whatHappened);
    expect(query.get("reproduce")).toBe(FIELDS.reproduce);
  });

  // The ids, the template and the title are held to the form itself by tests/test_console_report_contract.py.
  it("names the form's fields by their ids and nothing else", () => {
    const { query } = parse(reportLink(FIELDS).url);
    const parameters = [...query.keys()].filter((name) => name !== "template" && name !== "title");

    expect(parameters.sort()).toEqual(Object.values(FIELD_IDS).sort());
    expect(Object.values(FIELD_IDS).sort()).toEqual(["area", "reproduce", "version", "what-happened"]);
  });

  it("leaves the file's own field for the person to fill: it is the one that takes the attachment", () => {
    const { query } = parse(reportLink(FIELDS).url);

    expect(query.has("attachment")).toBe(false);
  });

  it("keeps the characters of any language and the line breaks, encoded", () => {
    const { url } = reportLink({ ...FIELDS, title: "¿Qué pasó? 100% & más = ñ \"x\" #1", reproduce: "a\r\nb" });
    const { query } = parse(url);

    expect(url).not.toMatch(/\s/);
    expect(query.get("title")).toBe(`${TITLE_PREFIX}¿Qué pasó? 100% & más = ñ "x" #1`);
    expect(query.get("reproduce")).toBe("a\r\nb");
  });

  it("leaves out the steps when there are none, since the form does not need them", () => {
    const { query } = parse(reportLink({ ...FIELDS, reproduce: "" }).url);

    expect(query.has("reproduce")).toBe(false);
  });

  it("cuts a long text to fit and says it did, the steps first and the title never", () => {
    const long = { ...FIELDS, whatHappened: "Un error muy largo. ".repeat(900), reproduce: "Paso. ".repeat(900) };

    const link = reportLink(long);
    const { query } = parse(link.url);

    expect(link.cut).toBe(true);
    expect(link.url.length).toBeLessThanOrEqual(MAX_URL_LENGTH);
    expect(query.get("title")).toBe(`${TITLE_PREFIX}${FIELDS.title}`);
    expect(query.get("what-happened")?.endsWith("…")).toBe(true);
    expect(query.get("reproduce")?.endsWith("…")).toBe(true);
    expect(link.fields.whatHappened.length).toBeGreaterThan(link.fields.reproduce.length);
  });

  it("cuts between characters, never inside one", () => {
    const link = reportLink({ ...FIELDS, whatHappened: "😀".repeat(5000) });

    expect(link.cut).toBe(true);
    expect(() => decodeURIComponent(link.url)).not.toThrow();
    expect(link.fields.whatHappened).toMatch(/^(?:😀)+…$/u);
  });

  it("does not cut what fits", () => {
    const link = reportLink({ ...FIELDS, whatHappened: "x".repeat(1500) });

    expect(link.cut).toBe(false);
    expect(link.fields.whatHappened).toHaveLength(1500);
  });
});
