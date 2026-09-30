/**
 * A database the New-application wizard was asked to create, waiting for the application to
 * exist. `POST /api/apps` takes no database: the application is created by its first deploy, and
 * only then can `POST /api/apps/{domain}/databases` create a database for it and write its
 * connection string. So the wizard hands the choice to a watcher that outlives the wizard's page
 * (the deploy hands over to its deployment page as soon as the build starts): it follows the
 * deploy job to its end and then queues the database, telling the operator in a toast either way.
 *
 * The choice is also kept in the tab's session storage, so the application's Database tab can
 * offer to finish it when the page was reloaded while the deploy ran.
 */

import { api } from "../../../api/client";
import { runOnNode } from "../../../api/nodeScope";
import type { Job } from "../../../api/queries/jobs";
import { getLocale } from "../../../app/locale";
import { toast } from "../../../components/ui/toast";
import { translate } from "../../../i18n";

export interface PendingDatabase {
  domain: string;
  engine: string;
  extraVars: boolean;
  /** The server it is for: null for this one. */
  node: string | null;
}

const STORAGE_KEY = "noust.pending-databases";
const POLL_MS = 4_000;
/** A first deploy that takes longer than this is not waited for; the Database tab offers to finish. */
const GIVE_UP_MS = 3 * 60 * 60 * 1000;

function read(): PendingDatabase[] {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as PendingDatabase[]).filter((item) => typeof item.domain === "string" && typeof item.engine === "string") : [];
  } catch {
    // Storage that cannot be read (disabled, or not JSON) holds nothing pending.
    return [];
  }
}

function write(items: readonly PendingDatabase[]): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // A tab that cannot store it still has the watcher; only a reload would forget it.
  }
}

export function pendingFor(domain: string, node: string | null): PendingDatabase | null {
  return read().find((item) => item.domain === domain && item.node === node) ?? null;
}

export function forgetPending(domain: string, node: string | null): void {
  write(read().filter((item) => !(item.domain === domain && item.node === node)));
}

function remember(pending: PendingDatabase): void {
  write([...read().filter((item) => !(item.domain === pending.domain && item.node === pending.node)), pending]);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export interface ProvisionAfterDeploy extends PendingDatabase {
  /** The deploy job `POST /api/apps` queued. */
  jobId: string;
  /** Opens the application's Database tab, for the toast's action. */
  openDatabaseTab: () => void;
}

/**
 * Waits for the first deploy to end, then creates and links the database. Every request goes to
 * the server it was started on (`runOnNode`), so switching servers meanwhile changes nothing.
 */
export async function provisionAfterDeploy({ jobId, domain, engine, extraVars, node, openDatabaseTab }: ProvisionAfterDeploy): Promise<void> {
  remember({ domain, engine, extraVars, node });
  const started = Date.now();
  const locale = getLocale();
  let job: Job | null = null;
  while (Date.now() - started < GIVE_UP_MS) {
    try {
      job = await runOnNode(node, () => api<Job>("GET", `/api/jobs/${encodeURIComponent(jobId)}`));
    } catch {
      // A missed poll (the server restarting, a dropped connection) is tried again.
      job = null;
    }
    if (job !== null && (job.status === "completed" || job.status === "failed" || job.status === "cancelled")) break;
    await delay(POLL_MS);
  }
  const action = { label: translate(locale, "databases.wizard.openTab"), onClick: openDatabaseTab };
  if (job?.status !== "completed") {
    if (job !== null && (job.status === "failed" || job.status === "cancelled")) {
      toast.warning(translate(locale, "databases.wizard.deployFailed", { domain }), { description: translate(locale, "databases.wizard.deployFailedHint"), action, timeout: 0 });
    }
    return;
  }
  try {
    await runOnNode(node, () =>
      api("POST", `/api/apps/${encodeURIComponent(domain)}/databases`, { engine, name: null, env_var: null, extra_vars: extraVars, restart: true }),
    );
    forgetPending(domain, node);
    toast.info(translate(locale, "databases.wizard.creating", { domain }), { description: translate(locale, "databases.wizard.creatingHint"), action });
  } catch (error: unknown) {
    const detail = typeof error === "object" && error !== null && "detail" in error ? String(error.detail) : String(error);
    toast.error(translate(locale, "databases.wizard.createFailed", { domain }), { detail, action });
  }
}
