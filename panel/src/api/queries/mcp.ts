/**
 * The MCP endpoint's status and settings (`/api/mcp/status`, `/api/mcp/settings`): whether it
 * answers, which groups of tools it offers, and the tools themselves. The endpoint (`POST
 * /api/mcp`) is for MCP clients with an API token and is not called from the console; this is
 * only where an operator turns it on and chooses what it offers.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type McpStatus = ResponseOf<"/api/mcp/status", "get">;
export type McpTool = McpStatus["tools"][number];
export type McpSettingsBody = BodyOf<"/api/mcp/settings", "put">;

export const mcpKeys = {
  status: ["mcp", "status"] as const,
};

export const mcpStatusQuery = () =>
  queryOptions({
    queryKey: mcpKeys.status,
    queryFn: ({ signal }) => request("get", "/api/mcp/status", { signal }),
  });

/** Turns the endpoint on or off, or changes the groups of tools it offers. Needs "Confirm it's you". */
export function saveMcpSettings(body: McpSettingsBody) {
  return request("put", "/api/mcp/settings", { body });
}
