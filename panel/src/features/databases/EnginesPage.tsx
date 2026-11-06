import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";
import { enginesQuery } from "../../api/queries/databases";
import { ListPage } from "../../components/page/ListPage";
import { GuardedButton } from "../../components/page/permissions";
import { ErrorBlock } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { FirstUse } from "../../components/ui/FirstUse";
import { ICONS } from "../../components/ui/icons";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { ListingProblemsNotice } from "./EngineAccess";
import { EngineCards } from "./servers/EngineCards";
import { Moves } from "./servers/Moves";
import { useDatabaseServers } from "./servers/useDatabaseServers";
import { useServerActions } from "./servers/useServerActions";

export interface EnginesPageProps {
  /** The flavour the address narrows it to (`?engine=mariadb`), from an old link: only its engines are drawn. */
  engine?: string | undefined;
}

/**
 * The engines of this machine, by themselves: each with whether it runs, its port and how many databases it holds, where it runs
 * and where its version stands in its project's support, and its menu (make it the default, upgrade, its settings and logs,
 * start and stop, remove). "Add an engine" is the one way to get another. Below, the moves to newer versions that were made
 * and can be rolled back. A job in hand is followed in the notice slot, where it was started.
 */
export function EnginesPage({ engine }: EnginesPageProps) {
  const t = useT();
  const { node } = useNode();
  const actions = useServerActions();
  const serverList = useDatabaseServers();
  const engines = useQuery({ ...enginesQuery(), enabled: serverList.problems.length > 0 });
  const { data: hostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const machine = node ?? hostname ?? "";
  const servers = serverList.servers;
  const shown = servers === undefined ? undefined : servers.filter((server) => engine === undefined || server.flavour === engine || server.key === engine);
  const notice = actions.notice ?? (serverList.problems.length > 0 ? <ListingProblemsNotice problems={serverList.problems} engines={engines.data?.engines} /> : undefined);
  const Add = ICONS.add;
  return (
    <>
      <ListPage
        header={{
          title: t("dbServers.title"),
          description: t("dbServers.line"),
          primaryAction: (
            <GuardedButton permission="databases.manage" variant="primary" icon={<Add aria-hidden="true" />} disabled={actions.busy} onClick={actions.add}>
              {t("dbServers.add.open")}
            </GuardedButton>
          ),
        }}
        {...(notice !== undefined ? { notice } : {})}
      >
        {/* design-exception: hand-error-ternary the cards draw their own skeleton while the engines load */}
        {serverList.isError && servers === undefined ? (
          <ErrorBlock error={serverList.error} title={t("dbServers.loadFailed")} onRetry={serverList.refetch} retrying={serverList.isRefetching} />
        ) : (
          <Sections>
            <EngineCards
              servers={shown}
              actions={actions}
              machine={machine}
              empty={<FirstUse>{t("dbServers.empty.description")}</FirstUse>}
            />
            {serverList.older ? <p className="text-13 text-fg-muted">{t("dbServers.older")}</p> : <Moves actions={actions} />}
          </Sections>
        )}
      </ListPage>
      {actions.dialogs}
    </>
  );
}
