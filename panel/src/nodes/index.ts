/**
 * The console's server plumbing, for features: which server is on screen, links between
 * servers, what a node offers. See app/nodeRoute.ts for the URL scheme and api/nodeScope.ts
 * for how requests, streams and the cache follow the selected server.
 */

export { NodeLink, NodeScope, ProvideNode, onServer, useNode, useNodeLink, useSwitchNode } from "./useNode";
export type { NodeLinkProps, NodeLinks, SelectedNode } from "./useNode";
export { NodeCapabilityGate, NotAvailableOnNode, compileOperations, nodeOperationsQuery, offers, useNodeCapability } from "./capability";
export type { NodeCapability, NodeOperations } from "./capability";
export { NodeNotice } from "./NodeNotice";
export { ServerSelector } from "./ServerSelector";
export { NODE_STATE, compareVersions, useServerList } from "./servers";
export { nodeErrorWords } from "./nodeErrors";
export { isCentralOnlyPath, serverPath, CENTRAL_ONLY_PATHS } from "../app/nodeRoute";
