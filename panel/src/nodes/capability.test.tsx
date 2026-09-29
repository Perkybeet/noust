import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { installNodeSource } from "../api/nodeScope";
import { ApiError } from "../api/errors";
import { createQueryClient } from "../app/App";
import { setLocale } from "../app/locale";
import { ErrorBlock } from "../components/page/QueryState";
import { expectNoAxeViolations } from "../test/axe";
import { NODES, fakeBackend, json, problem } from "../test/fakes";
import { NodeCapabilityGate, compileOperations, offers, useNodeCapability } from "./capability";
import { ProvideNode } from "./useNode";

const SCHEMA = {
  openapi: "3.1.0",
  paths: {
    "/api/apps": { get: { operationId: "list_apps_api_apps_get" } },
    "/api/apps/{domain}": { get: { operationId: "get_app_api_apps__domain__get" }, delete: { operationId: "delete_app_api_apps__domain__delete" } },
  },
};

function onServer(node: string | null) {
  const client = createQueryClient();
  client.setDefaultOptions({ ...client.getDefaultOptions(), queries: { ...client.getDefaultOptions().queries, retry: false } });
  installNodeSource(() => node);
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ProvideNode node={node}>{children}</ProvideNode>
    </QueryClientProvider>
  );
}

describe("what a node offers", () => {
  it("is read from an OpenAPI document by operationId, path, or method and path", () => {
    const operations = compileOperations(SCHEMA);
    expect(offers(operations, "list_apps_api_apps_get")).toBe(true);
    expect(offers(operations, "/api/apps/{domain}")).toBe(true);
    expect(offers(operations, "DELETE /api/apps/{domain}")).toBe(true);
    expect(offers(operations, "delete /api/apps/{domain}")).toBe(true);
    expect(offers(operations, "POST /api/apps/{domain}")).toBe(false);
    expect(offers(operations, "/api/apps/{domain}/previews")).toBe(false);
    expect(offers(compileOperations("not a schema"), "/api/apps")).toBe(false);
  });

  it("is everything on this server, without reading anything", () => {
    const backend = fakeBackend({ "GET /api/nodes": () => json(200, { items: [] }) });
    const { result } = renderHook(() => useNodeCapability("/api/anything"), { wrapper: onServer(null) });
    expect(result.current).toEqual({ status: "available", node: null, version: null });
    expect(backend.calls.some((call) => call.path.endsWith("/openapi.json"))).toBe(false);
  });

  it("reads the node's schema through the central, once per node and version", async () => {
    const backend = fakeBackend({
      "GET /api/nodes": () => json(200, { items: NODES }),
      "GET /api/nodes/web-2/api/openapi.json": () => json(200, SCHEMA),
    });
    const wrapper = onServer("web-2");
    const { result } = renderHook(
      () => [useNodeCapability("DELETE /api/apps/{domain}"), useNodeCapability("/api/apps/{domain}/previews")],
      { wrapper },
    );
    expect(result.current[0]?.status).toBe("checking");
    await waitFor(() => {
      expect(result.current[0]?.status).toBe("available");
    });
    expect(result.current[1]).toEqual({ status: "missing", node: "web-2", version: "2.0.0" });
    expect(backend.callsTo("GET /api/nodes/web-2/api/openapi.json")).toHaveLength(1);
  });

  it("lets the page run when the schema cannot be read: the node's answer decides", async () => {
    fakeBackend({
      "GET /api/nodes": () => json(200, { items: NODES }),
      "GET /api/nodes/web-2/api/openapi.json": () => problem(404, "not_found", "Not Found"),
    });
    const { result } = renderHook(() => useNodeCapability("/api/apps"), { wrapper: onServer("web-2") });
    await waitFor(() => {
      expect(result.current.status).toBe("unknown");
    });
  });

  it("says a page is not available on the node, with its version, in English and Spanish", async () => {
    fakeBackend({
      "GET /api/nodes": () => json(200, { items: NODES }),
      "GET /api/nodes/db-1/api/openapi.json": () => json(200, SCHEMA),
    });
    const Wrapper = onServer("db-1");
    const view = render(
      <Wrapper>
        <NodeCapabilityGate capability="/api/apps/{domain}/previews">
          <p>Previews</p>
        </NodeCapabilityGate>
      </Wrapper>,
    );
    expect(await screen.findByRole("heading", { name: "Not available on db-1 (Noust 1.9.0)" })).toBeInTheDocument();
    expect(screen.getByText("The Noust on db-1 does not offer this. Update Noust on db-1 to use it here.")).toBeInTheDocument();
    expect(screen.queryByText("Previews")).toBeNull();
    await expectNoAxeViolations(view.container);
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { name: "No disponible en db-1 (Noust 1.9.0)" })).toBeInTheDocument();
  });

  it("renders the page when the node offers it", async () => {
    fakeBackend({
      "GET /api/nodes": () => json(200, { items: NODES }),
      "GET /api/nodes/web-2/api/openapi.json": () => json(200, SCHEMA),
    });
    const Wrapper = onServer("web-2");
    render(
      <Wrapper>
        <NodeCapabilityGate capability="GET /api/apps">
          <p>Applications</p>
        </NodeCapabilityGate>
      </Wrapper>,
    );
    expect(await screen.findByText("Applications")).toBeInTheDocument();
  });
});

describe("a node error, wherever an error is shown", () => {
  it("names the node even when the request named it itself", async () => {
    // The fleet reads each node with an explicit proxy path, not the selected server.
    fakeBackend({
      "GET /api/nodes/db-1/api/system/machine": () =>
        problem(502, "node_unreachable", "Node db-1 did not answer through its tunnel.", { output: "ssh: connect to host 10.0.0.13 port 22: Connection refused" }),
    });
    const { api } = await import("../api/client");
    const error = await api("GET", "/api/nodes/db-1/api/system/machine").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).node).toBe("db-1");
    const Wrapper = onServer(null);
    const view = render(
      <Wrapper>
        <ErrorBlock error={error} title="Could not load db-1" />
      </Wrapper>,
    );
    expect(screen.getByText("db-1 is not answering")).toBeInTheDocument();
    expect(screen.getByText("This server could not reach db-1 through its tunnel.")).toBeInTheDocument();
    expect(screen.getByText("Node db-1 did not answer through its tunnel.")).toBeInTheDocument();
    expect(screen.getByText("ssh: connect to host 10.0.0.13 port 22: Connection refused")).toBeInTheDocument();
    await expectNoAxeViolations(view.container);
  });
});
