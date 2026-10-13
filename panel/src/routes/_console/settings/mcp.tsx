import { createFileRoute } from "@tanstack/react-router";

import { McpSettings } from "../../../features/settings/mcp/McpSettings";

/** Settings > MCP: whether the endpoint for MCP clients answers, and which tools it offers. */
export const Route = createFileRoute("/_console/settings/mcp")({
  component: McpSettings,
});
