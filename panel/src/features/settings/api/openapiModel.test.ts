import { describe, expect, it } from "vitest";

import { addressOf, curlOf, exampleOf, filterOperations, proseOf, readDocument, typeOf } from "./openapiModel";

const DOC = {
  info: { title: "Noust", version: "1" },
  paths: {
    "/api/apps/{domain}/restart": {
      parameters: [{ in: "path", name: "domain", required: true, schema: { type: "string" } }],
      post: {
        summary: "Restart App",
        description: "Restart an application.\n\nArgs:\n    domain: Domain of the application.\n\nReturns:\n    The action outcome.",
        tags: ["Applications"],
        "x-noust-permission": "apps.operate",
        responses: { "200": { description: "Successful Response" }, "422": { description: "Validation Error" } },
      },
    },
    "/api/jobs/update": {
      post: {
        summary: "Create Update Job",
        tags: ["Jobs"],
        "x-noust-permission": "apps.deploy",
        "x-noust-requires-elevation": true,
        requestBody: { content: { "application/json": { schema: { $ref: "#/components/schemas/UpdateRequest" } } } },
        responses: { "202": { description: "Accepted" } },
      },
    },
    "/api/apps": {
      get: {
        summary: "List Apps",
        tags: ["Applications"],
        "x-noust-permission": "apps.read",
        parameters: [{ in: "query", name: "team", schema: { anyOf: [{ type: "string" }, { type: "null" }] } }],
        responses: { "200": { description: "Successful Response" } },
      },
    },
  },
  components: {
    schemas: {
      UpdateRequest: {
        type: "object",
        required: ["domain"],
        properties: {
          domain: { type: "string", description: "Domain of the application\nand more" },
          force: { type: "boolean", default: false },
          next: { $ref: "#/components/schemas/UpdateRequest" },
        },
      },
    },
  },
};

describe("the API document as operations", () => {
  const read = readDocument(DOC);

  it("lists every operation by tag and path, with what it needs", () => {
    expect(read.operations.map((operation) => operation.id)).toEqual(["GET /api/apps", "POST /api/apps/{domain}/restart", "POST /api/jobs/update"]);
    expect(read.tags).toEqual(["Applications", "Jobs"]);
    const restart = read.operations[1];
    expect(restart).toMatchObject({ permission: "apps.operate", needsSudoMode: false, summary: "Restart App" });
    expect(read.operations[2]).toMatchObject({ needsSudoMode: true, permission: "apps.deploy" });
  });

  it("keeps a route's prose and drops the developers' Args and Returns", () => {
    expect(read.operations[1]?.description).toBe("Restart an application.");
    expect(proseOf("One line")).toBe("One line");
  });

  it("takes the path parameters a path item shares and the query ones of the operation", () => {
    expect(read.operations[1]?.parameters).toEqual([{ name: "domain", where: "path", required: true, description: "", type: "string" }]);
    expect(read.operations[0]?.parameters).toEqual([{ name: "team", where: "query", required: false, description: "", type: "string" }]);
  });

  it("reads a body's fields and builds an example that does not run away on a recursive model", () => {
    const update = read.operations[2];
    expect(update?.body).toEqual([
      { name: "domain", type: "string", required: true, description: "Domain of the application" },
      { name: "force", type: "boolean", required: false, description: "" },
      { name: "next", type: "UpdateRequest", required: false, description: "" },
    ]);
    expect(update?.example).toEqual({ domain: "string", force: false, next: null });
  });

  it("says a type in a word", () => {
    expect(typeOf(DOC, { type: "array", items: { type: "integer" } })).toBe("integer[]");
    expect(typeOf(DOC, { anyOf: [{ type: "string" }, { type: "null" }] })).toBe("string");
    expect(typeOf(DOC, { enum: ["a", "b"] })).toBe('"a" | "b"');
    expect(typeOf(DOC, undefined)).toBe("any");
    expect(exampleOf(DOC, { type: "string", format: "date-time" })).toBe("2026-01-01T00:00:00Z");
  });

  it("filters by text, tag and method", () => {
    expect(filterOperations(read.operations, { q: "restart" }).map((operation) => operation.id)).toEqual(["POST /api/apps/{domain}/restart"]);
    expect(filterOperations(read.operations, { q: "apps.deploy" }).map((operation) => operation.id)).toEqual(["POST /api/jobs/update"]);
    expect(filterOperations(read.operations, { tag: "Applications", method: "GET" }).map((operation) => operation.id)).toEqual(["GET /api/apps"]);
    expect(filterOperations(read.operations, {})).toHaveLength(3);
  });

  it("writes the address with the values given and a curl that never holds the token", () => {
    const restart = read.operations[1];
    const list = read.operations[0];
    const update = read.operations[2];
    if (!restart || !list || !update) throw new Error("missing operations");
    expect(addressOf(restart, { domain: "shop.example.com" })).toBe("/api/apps/shop.example.com/restart");
    expect(addressOf(restart, {})).toBe("/api/apps/{domain}/restart");
    expect(addressOf(list, { team: "acme" })).toBe("/api/apps?team=acme");
    expect(curlOf(list, "https://noust.acme.test", {})).toBe('curl -sS -X GET -H "Authorization: Bearer $NOUST_TOKEN" "https://noust.acme.test/api/apps"');
    const withBody = curlOf(update, "https://noust.acme.test", {});
    expect(withBody).toContain('-H "Content-Type: application/json"');
    expect(withBody).toContain("-d '{");
    expect(withBody).not.toContain("noust_tok_");
  });

  it("reads a document that is not one as empty", () => {
    expect(readDocument(null).operations).toEqual([]);
    expect(readDocument({ paths: { "/x": null } }).operations).toEqual([]);
  });
});

describe("a route that takes a file", () => {
  it("says its body is not JSON, so the page does not pretend to compose one", () => {
    const document = readDocument({
      paths: {
        "/api/uploads/{id}": {
          put: {
            requestBody: { content: { "application/octet-stream": { schema: { type: "string", format: "binary" } } } },
            responses: { "200": { description: "ok" } },
          },
        },
        "/api/jobs": {
          post: {
            requestBody: { content: { "application/json": { schema: { type: "object", properties: { name: { type: "string" } } } } } },
            responses: { "202": { description: "ok" } },
          },
          get: { responses: { "200": { description: "ok" } } },
        },
      },
    });

    const byId = Object.fromEntries(document.operations.map((operation) => [operation.id, operation]));
    expect(byId["PUT /api/uploads/{id}"]).toMatchObject({ rawBody: true, body: null });
    expect(byId["POST /api/jobs"]).toMatchObject({ rawBody: false });
    expect(byId["GET /api/jobs"]).toMatchObject({ rawBody: false });
  });
});
