/**
 * Previewing a manifest (`POST /api/manifest/plan`): what applying it would change on this server.
 * The preview changes nothing and carries no variable's value; applying is the command line's,
 * from the repository the manifest lives in (`noust manifest apply`).
 */

import { request } from "../client";
import type { ResponseOf } from "../client";

export type ManifestPlan = ResponseOf<"/api/manifest/plan", "post">;
export type ManifestAction = ManifestPlan["actions"][number];

/** Plans a manifest, written as YAML or JSON, against this server. The server's refusal of the document is shown verbatim. */
export function planManifest(manifest: string) {
  return request("post", "/api/manifest/plan", { body: { manifest } });
}
