import { PartialNotice } from "../fleet/parts";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { ServerText } from "../../components/ui/ServerText";
import { useT } from "../../i18n";
import { refusedServers } from "./views";
import type { Refusal, ViewAnswer, ViewResource } from "./views";

/** What the operator was not let see, per view: the title counts the servers and names this. */
const TITLES = {
  activity: "operation.refused.title.activity",
  backups: "operation.refused.title.backups",
  alerts: "operation.refused.title.alerts",
  logs: "operation.refused.title.logs",
} as const satisfies Record<ViewResource, string>;

/**
 * One group of servers that refused alike: the sentence in the console's language, with the fix
 * under it. A refusal about a permission is a code (`fleet.refused.permission`), so it is worded
 * here and never shows the server's English or its body; any other refusal is the server's own
 * words, kept as it said them, because only it knows why.
 */
function Group({ group }: { group: Refusal }) {
  const t = useT();
  return (
    <li className="flex min-w-0 flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <p className="max-w-measure text-13 text-pretty text-fg">
        <ServerText coded={group.coded} text={group.message} />
      </p>
      {group.hint !== null && group.hint !== undefined && group.hint !== "" ? (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          <ServerText coded={group.coded} text={group.hint} part="hint" />
        </p>
      ) : null}
      <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-12 text-fg-muted">
        <span>{t("operation.refused.servers")}</span>
        {group.servers.map((name) => (
          <Mono key={name} tone="default">
            {name}
          </Mono>
        ))}
      </p>
    </li>
  );
}

/**
 * The servers that did not let the operator read a view, grouped by what they refused: "3 servers
 * do not let you see their activity", what is missing and how to give it, and which servers they
 * are. Said once, whatever the number of servers, and never as the raw body a node answered with.
 * Nothing is drawn when every server answered.
 */
export function Refusals({ view, resource }: { view: ViewAnswer | undefined; resource: ViewResource }) {
  const t = useT();
  const groups = view?.refusals ?? [];
  if (groups.length === 0) return null;
  const count = groups.reduce((sum, group) => sum + group.count, 0);
  return (
    <Notice title={t(TITLES[resource], { count })}>
      <ul aria-label={t("operation.refused.label")} className="flex flex-col divide-y divide-border">
        {groups.map((group) => (
          <Group key={`${group.code}:${group.permission ?? ""}:${group.servers.join(",")}`} group={group} />
        ))}
      </ul>
    </Notice>
  );
}

/**
 * What a view says about the servers that could not answer it fully: those that refused (grouped),
 * then those that are down, old or too old to answer, each with its words verbatim. A server that
 * refused is in the first and not the second, so nothing is said twice.
 */
export function ViewNotices({ view, resource }: { view: ViewAnswer | undefined; resource: ViewResource }) {
  return (
    <>
      <Refusals view={view} resource={resource} />
      <PartialNotice view={view} skip={refusedServers(view)} />
    </>
  );
}
