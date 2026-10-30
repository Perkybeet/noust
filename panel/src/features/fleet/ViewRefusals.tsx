import type { ViewRefusal } from "../../api/queries/views";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { ServerText } from "../../components/ui/ServerText";
import { useT } from "../../i18n";

export interface ViewRefusalsProps {
  /** What the operator's role could not read on some servers, grouped by why (`refusals` of a view). */
  refusals: readonly ViewRefusal[];
  /** What the list is of, in the plural ("applications"): the sentence is "3 servers do not let you read applications". */
  what: string;
}

/**
 * The servers that did not let this account read a list, once: how many, which, why and how to give it,
 * in the console's language (`coded`, with the server's own English where the console has no text). It is
 * not a failure of the page: the rows of the servers that answered are all there, and this says whose are not.
 */
export function ViewRefusals({ refusals, what }: ViewRefusalsProps) {
  const t = useT();
  if (refusals.length === 0) return null;
  const count = refusals.reduce((sum, refusal) => sum + refusal.count, 0);
  return (
    <Notice tone="warning" title={t("fleet.view.refused", { count, what })}>
      <ul className="flex flex-col gap-2">
        {refusals.map((refusal) => (
          <li key={`${refusal.code}:${refusal.servers.join(",")}`} className="flex min-w-0 flex-col gap-0.5">
            <span className="flex flex-wrap items-baseline gap-x-2">
              {refusal.servers.map((server) => (
                <Mono key={server}>{server}</Mono>
              ))}
            </span>
            <span>
              <ServerText coded={refusal.coded} text={refusal.message} />
            </span>
            {refusal.hint !== null && refusal.hint !== undefined ? (
              <span className="text-fg-muted">
                <ServerText coded={refusal.coded} text={refusal.hint} part="hint" />
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </Notice>
  );
}
