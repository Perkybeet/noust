/**
 * Downloading a dump: the browser saves it straight from the server, streamed to disk, never
 * held in the page's memory (a dump is the whole database). The endpoint needs sudo mode, and
 * a download the browser makes on its own cannot answer "Confirm it's you", so the session is
 * elevated first when its window has closed, then the browser is sent to the file.
 */

import { buildPath, request } from "../../../api/client";
import { activeNode, nodeApiPath } from "../../../api/nodeScope";
import { elevate } from "../../auth/elevation";

/** Seconds of sudo mode a download must start with, so the window cannot close under it. */
const MARGIN_MS = 30_000;

export async function downloadDump(engine: string, name: string): Promise<void> {
  const session = await request("get", "/api/auth/session");
  const until = session.elevated_until ? Date.parse(session.elevated_until) : 0;
  if (!Number.isFinite(until) || until - Date.now() < MARGIN_MS) await elevate();
  const link = document.createElement("a");
  link.href = nodeApiPath(activeNode(), buildPath("/api/databases/backups/{name}/download", { name }, { engine }));
  link.download = name;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}
