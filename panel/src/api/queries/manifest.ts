/**
 * Previewing a manifest (`POST /api/manifest/plan`): what applying it would change on this server.
 * The preview changes nothing and carries no variable's value. Applying it
 * (`POST /api/manifest/apply`) queues a job that makes the calls of the person who asked, and
 * carries the digest of the preview that was read: the server refuses it if the plan has changed.
 */

import { request } from "../client";
import type { ResponseOf } from "../client";

export type ManifestPlan = ResponseOf<"/api/manifest/plan", "post">;
export type ManifestAction = ManifestPlan["actions"][number];

/** Plans a manifest, written as YAML or JSON, against this server. The server's refusal of the document is shown verbatim. */
export function planManifest(manifest: string) {
  return request("post", "/api/manifest/plan", { body: { manifest } });
}

export type ManifestApplied = ResponseOf<"/api/manifest/apply", "post">;

/** Applies a manifest as the signed-in person, exactly as previewed (`digest`); answers the job to follow. */
export function applyManifest(manifest: string, digest: string) {
  return request("post", "/api/manifest/apply", { body: { manifest, expect: digest } });
}
