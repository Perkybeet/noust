import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";
import { enginesQuery } from "../../api/queries/databases";
import { CommandHint } from "../../components/page/CommandHint";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { ListingProblemsNotice } from "./EngineAccess";
import { useDatabasesHeader } from "./listHeader";
import { Moves } from "./servers/Moves";
import { ServerSlabs } from "./servers/ServerSlabs";
import { useDatabaseServers } from "./servers/useDatabaseServers";
import { useServerActions } from "./servers/useServerActions";

export interface ServersPageProps {
  /** The engine the side column chose (`?engine=mariadb`): only its servers are drawn. */
  engine?: string | undefined;
}

/**
 * The database servers of this machine, by themselves: each with where its version stands in its project's
 * support, its default, where it runs and how many databases it holds, and its menu (make it the default,
 * another version, upgrade, its settings and logs, start and stop, remove). Below, the moves to newer versions
 * that were made and can be rolled back. A job in hand is followed in the notice slot, where it was started.
 */
export function ServersPage({ engine }: ServersPageProps) {
  const t = useT();
  const { node } = useNode();
  const actions = useServerActions();
  const { header, dialogs } = useDatabasesHeader("servers", actions);
  const serverList = useDatabaseServers();
  const engines = useQuery({ ...enginesQuery(), enabled: serverList.problems.length > 0 });
  const { data: hostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const machine = node ?? hostname ?? "";
  const servers = serverList.servers;
  const shown = servers === undefined ? undefined : servers.filter((server) => engine === undefined || server.flavour === engine || server.key === engine);
  const notice = actions.notice ?? (serverList.problems.length > 0 ? <ListingProblemsNotice problems={serverList.problems} engines={engines.data?.engines} /> : undefined);
  return (
    <>
      <ListPage
        header={header}
        {...(notice !== undefined ? { notice } : {})}
        {...(servers === undefined ? {} : { footer: <CommandHint command="noust db servers" label={t("common.words.fromTerminal")} /> })}
      >
        {/* design-exception: hand-error-ternary the slabs draw their own skeleton while the servers load */}
        {serverList.isError && servers === undefined ? (
          <ErrorBlock error={serverList.error} title={t("dbServers.loadFailed")} onRetry={serverList.refetch} retrying={serverList.isRefetching} />
        ) : (
          <Sections>
            <ServerSlabs servers={shown} machine={machine} actions={actions} level={2} />
            {serverList.older ? <p className="text-13 text-fg-muted">{t("dbServers.older")}</p> : <Moves actions={actions} />}
          </Sections>
        )}
      </ListPage>
      {dialogs}
    </>
  );
}
