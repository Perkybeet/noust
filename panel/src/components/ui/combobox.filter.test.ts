import { describe, expect, it } from "vitest";

import { matchesComboboxQuery, parseOffsetQuery } from "./combobox.filter";

const MADRID = {
  value: "Europe/Madrid",
  label: "Europe/Madrid",
  city: "Madrid",
  region: "Europe",
  abbreviation: "CEST",
  offset: "UTC+02:00",
};
const LONDON = {
  value: "Europe/London",
  label: "Europe/London",
  city: "London",
  region: "Europe",
  abbreviation: "BST",
  offset: "UTC+01:00",
};
const BUENOS_AIRES = {
  value: "America/Argentina/Buenos_Aires",
  label: "America/Argentina/Buenos_Aires",
  city: "Argentina / Buenos Aires",
  region: "America",
  abbreviation: "-03",
  offset: "UTC-03:00",
};
const KOLKATA = {
  value: "Asia/Kolkata",
  label: "Asia/Kolkata",
  city: "Kolkata",
  region: "Asia",
  abbreviation: "IST",
  offset: "UTC+05:30",
};
const UTC = { value: "Etc/UTC", label: "Etc/UTC", city: "UTC", region: "Etc", abbreviation: "UTC", offset: "UTC+00:00" };
const AUCKLAND = { value: "Pacific/Auckland", label: "Pacific/Auckland", offset: "UTC+12:00", keywords: ["New Zealand"] };

const ALL = [MADRID, LONDON, BUENOS_AIRES, KOLKATA, UTC, AUCKLAND];

function find(query: string): string[] {
  return ALL.filter((item) => matchesComboboxQuery(item, query)).map((item) => item.value);
}

describe("matchesComboboxQuery", () => {
  it("finds an item by its city, ignoring case", () => {
    expect(find("madrid")).toEqual(["Europe/Madrid"]);
    expect(find("MADRID")).toEqual(["Europe/Madrid"]);
  });

  it("finds an item by its abbreviation", () => {
    expect(find("cest")).toEqual(["Europe/Madrid"]);
  });

  it("finds an item by a short offset: +2 means two hours ahead, whatever the minutes", () => {
    expect(find("+2")).toEqual(["Europe/Madrid"]);
    expect(find("+5")).toEqual(["Asia/Kolkata"]);
  });

  it("does not confuse +1 with +12", () => {
    expect(find("+1")).toEqual(["Europe/London"]);
    expect(find("+12")).toEqual(["Pacific/Auckland"]);
  });

  it("reads utc+1, gmt-3 and a full offset", () => {
    expect(find("utc+1")).toEqual(["Europe/London"]);
    expect(find("UTC +1")).toEqual(["Europe/London"]);
    expect(find("gmt-3")).toEqual(["America/Argentina/Buenos_Aires"]);
    expect(find("+05:30")).toEqual(["Asia/Kolkata"]);
    expect(find("+0530")).toEqual(["Asia/Kolkata"]);
    expect(find("+05:00")).toEqual([]);
  });

  it("treats a zero offset as UTC whatever its sign", () => {
    expect(find("+0")).toEqual(["Etc/UTC"]);
    expect(find("-0")).toEqual(["Etc/UTC"]);
  });

  it("matches every word of the query somewhere, across underscores and slashes", () => {
    expect(find("buenos aires")).toEqual(["America/Argentina/Buenos_Aires"]);
    expect(find("america buenos")).toEqual(["America/Argentina/Buenos_Aires"]);
    expect(find("europe london")).toEqual(["Europe/London"]);
  });

  it("searches arrays of strings too, and ignores accents", () => {
    expect(find("zealand")).toEqual(["Pacific/Auckland"]);
    expect(matchesComboboxQuery({ value: "x", label: "Bogotá" }, "bogota")).toBe(true);
  });

  it("matches everything for an empty query", () => {
    expect(find("  ")).toHaveLength(ALL.length);
  });

  it("does not search fields that are not text", () => {
    expect(matchesComboboxQuery({ value: "a", label: "A", disabled: true }, "true")).toBe(false);
  });
});

describe("parseOffsetQuery", () => {
  it("returns minutes and whether they were given", () => {
    expect(parseOffsetQuery("+2")).toEqual({ minutes: 120, exactMinutes: false });
    expect(parseOffsetQuery("utc-03:30")).toEqual({ minutes: -210, exactMinutes: true });
    expect(parseOffsetQuery("madrid")).toBeNull();
    expect(parseOffsetQuery("+")).toBeNull();
  });
});
