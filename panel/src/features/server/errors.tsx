/**
 * The Server area's refusals, said the way the operator can act on them.
 *
 * Two answers need more than the console's usual error block:
 *
 * - **A node that does not let this console change how it is reached.** A central holds
 *   `server.host_access` on a node only when the node's own operator allowed it (`noust fleet
 *   access --host-access on`, run on the node). Without it the node answers 403
 *   `permission_denied` naming that permission; the console says which node refused, why, and
 *   the command to run there, instead of "ask a security officer".
 * - **A deliberate refusal with a list** (409 `preflight_failed`, `host_busy`,
 *   `confirmation_required`): the list travels beside the message (`blockers`, `holders`,
 *   `required`), and is what the operator has to read. It is added under the detail, verbatim.
 */

import { ApiError, isApiError } from "../../api/client";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import type { ErrorBlockProps } from "../../components/page/QueryState";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";

/** The permission a central needs on a node to change sshd, keys, the firewall or its power. */
export const HOST_ACCESS_PERMISSION = "server.host_access";

/** The command the node's operator runs to let centrals change how it is reached. */
export const HOST_ACCESS_COMMAND = "noust fleet access --level admin --host-access on";

/** True when a node refused this console an action on how it is reached (see the module doc). */
export function isHostAccessRefusal(error: unknown, node: string | null): boolean {
  return (
    node !== null &&
    isApiError(error) &&
    error.status === 403 &&
    error.error === "permission_denied" &&
    error.detail.includes(HOST_ACCESS_PERMISSION)
  );
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** The list a refusal carries (`blockers`, `holders`, `required.removals`), in its own words. */
export function refusalList(error: unknown): string[] {
  if (!isApiError(error)) return [];
  const required = error.extra["required"];
  const removals = typeof required === "object" && required !== null ? strings((required as Record<string, unknown>)["removals"]) : [];
  return [...strings(error.extra["blockers"]), ...strings(error.extra["holders"]), ...removals];
}

/**
 * The error as the operator should read it: a host-access refusal gets the node's command as
 * its fix; a refusal with a list gets the list under its detail. Anything else is returned as
 * it is. For ConfirmDialog and toasts, which show `hint` above and `detail` verbatim.
 */
export function explainServerError(t: T, error: unknown, node: string | null, withCommand = true): unknown {
  if (!isApiError(error)) return error;
  if (isHostAccessRefusal(error, node)) {
    return new ApiError(
      error.status,
      error.error,
      error.detail,
      withCommand ? t("server.hostAccess.hint", { node: node ?? "", command: HOST_ACCESS_COMMAND }) : t("server.hostAccess.explanation", { node: node ?? "" }),
      null,
      null,
      error.output,
      error.node,
    );
  }
  const list = refusalList(error);
  if (list.length === 0) return error;
  return new ApiError(
    error.status,
    error.error,
    `${error.detail}\n${list.map((item) => `- ${item}`).join("\n")}`,
    error.hint,
    error.fields,
    error.retryAfter,
    error.output,
    error.node,
    error.extra,
  );
}

/**
 * ErrorBlock for the Server area: a node that refused host access is named with the command
 * that allows it, in a terminal of that node; any other failure is the usual block.
 */
export function ServerErrorBlock({ error, title, ...rest }: ErrorBlockProps) {
  const t = useT();
  const { node } = useNode();
  if (isHostAccessRefusal(error, node)) {
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <ErrorBlock {...rest} error={explainServerError(t, error, node, false)} title={t("server.hostAccess.title", { node: node ?? "" })} />
        <CommandHint command={HOST_ACCESS_COMMAND} label={t("server.hostAccess.onNode", { node: node ?? "" })} />
      </div>
    );
  }
  return <ErrorBlock {...rest} error={explainServerError(t, error, node)} title={title} />;
}
