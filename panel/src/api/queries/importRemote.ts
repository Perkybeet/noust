/**
 * Reading another platform's server for an import (checklist N-13): `POST /api/import/applications`
 * lists what a Coolify or Dokploy server has, `POST /api/import/proposal` reads one application into
 * the export document the import review takes. Both ask for sudo mode, which the client handles, and
 * neither stores the token it is given.
 *
 * Typed by hand until the contract in `schema.gen.ts` carries these two routes; `ApiError` is what
 * a failure becomes, as for every call.
 */

import { api } from "../client";
import type { AppExportDocument } from "./appImport";

export type RemotePlatform = "coolify" | "dokploy";

/** The server to read and the read-only token to read it with. */
export interface RemoteServer {
  platform: RemotePlatform;
  url: string;
  token: string;
}

/** An application on the other server. */
export interface RemoteApplication {
  ref: string;
  name: string;
  build: string;
  source: string;
  domains: string[];
  project: string;
}

export interface RemoteApplications {
  platform: string;
  applications: RemoteApplication[];
}

/** What the other platform's configuration says, in Noust's terms: the part the review shows. */
export interface RemoteProposalSummary {
  name?: string | null;
  app_type?: string | null;
  source?: string | null;
  /** What has no equivalent here, and what to do instead. */
  warnings: string[];
  /** The `noust` command that creates what cannot be deployed from a proposal. */
  equivalent?: string | null;
}

export interface RemoteProposal {
  proposal: RemoteProposalSummary;
  /** The export document it becomes, which `POST /api/apps/import` takes after review. */
  document: AppExportDocument;
  /** False for an image, a Dockerfile or a Compose application: the proposal carries the command. */
  deployable: boolean;
}

export interface RemoteProposalRequest extends RemoteServer {
  ref: string;
  /** The domain to create it on here; the first one it answered on there when left out. */
  domain?: string;
}

export function listRemoteApplications(body: RemoteServer): Promise<RemoteApplications> {
  return api<RemoteApplications>("POST", "/api/import/applications", body);
}

export function readRemoteApplication(body: RemoteProposalRequest): Promise<RemoteProposal> {
  return api<RemoteProposal>("POST", "/api/import/proposal", body);
}
