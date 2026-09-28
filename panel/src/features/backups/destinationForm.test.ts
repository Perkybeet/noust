import { describe, expect, it } from "vitest";

import type { BackendField } from "../../api/queries/backupDestinations";
import { fieldsPayload, hasRequiredValues } from "./destinationForm";

function field(overrides: Partial<BackendField> = {}): BackendField {
  return { key: "host", label: "Host", secret: false, required: true, placeholder: "", help: "", choices: [], ...overrides };
}

describe("hasRequiredValues", () => {
  it("is true once every required field has a value", () => {
    const fields = [field({ key: "host" }), field({ key: "port", required: false })];
    expect(hasRequiredValues(fields, {})).toBe(false);
    expect(hasRequiredValues(fields, { host: "  " })).toBe(false);
    expect(hasRequiredValues(fields, { host: "example.com" })).toBe(true);
  });

  it("treats a required secret already stored as satisfied when left blank", () => {
    const fields = [field({ key: "pass", secret: true, required: true })];
    expect(hasRequiredValues(fields, {})).toBe(false);
    expect(hasRequiredValues(fields, {}, new Set(["pass"]))).toBe(true);
    expect(hasRequiredValues(fields, { pass: "typed" }, new Set())).toBe(true);
  });
});

describe("fieldsPayload", () => {
  it("sends every non-secret field as typed, blank or not", () => {
    const fields = [field({ key: "host" }), field({ key: "port", required: false })];
    expect(fieldsPayload(fields, { host: "example.com", port: "" })).toEqual({ host: "example.com", port: "" });
  });

  it("omits a blank secret field, so an update keeps its stored value", () => {
    const fields = [field({ key: "host" }), field({ key: "pass", secret: true })];
    expect(fieldsPayload(fields, { host: "example.com", pass: "" })).toEqual({ host: "example.com" });
    expect(fieldsPayload(fields, { host: "example.com", pass: "s3cr3t" })).toEqual({ host: "example.com", pass: "s3cr3t" });
  });
});
