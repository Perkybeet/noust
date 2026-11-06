import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";

import { isApiError } from "../../api/client";
import { databaseOverviewQuery, enginesQuery } from "../../api/queries/databases";
import { DetailPage } from "../../components/page/DetailPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Notice } from "../../components/ui/Notice";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { DatabaseFrameProvider } from "./DatabasePage";
import { EngineStopped } from "./EngineStopped";
import { answers, useEngineOf } from "./engineOf";
import { engineName } from "./engines";
import { DatabaseJobProvider, DatabaseJobSlot, useHasDatabaseJob } from "./jobs";
import { useServerActions } from "./servers/useServerActions";

interface LayoutBodyProps {
  engine: string;
  name: string;
}

function LayoutBody({ engine, name }: LayoutBodyProps) {
  const t = useT();
  const where = useEngineOf(engine);
  const { server, life, reading } = where;
  const overview = useQuery({ ...databaseOverviewQuery(engine, name), retry: false, enabled: answers(where) });
  const engines = useQuery(enginesQuery());
  const hasJob = useHasDatabaseJob();
  const actions = useServerActions();
  const display = overview.data?.display_name ?? engineName(engine, engines.data?.engines);
  // What the engine can do is read from its own answer, and from the list of engines when it does not give one (it is stopped).
  const capabilities = overview.data?.capabilities ?? engines.data?.engines.find((item) => item.name === engine)?.capabilities;
  // A stopped engine fails the overview on purpose: it is not what the page ends in, the engine is what it says.
  const stopped = life === "stopped" || life === "changing";

  if (!stopped && overview.isError && isApiError(overview.error) && overview.error.status === 404) {
    return (
      <DetailPage header={{ title: name, mono: true }}>
        <EmptyState
          variant="firstUse"
          icon={<OBJECT_ICONS.database />}
          title={t("databases.layout.notFoundTitle")}
          description={t("databases.layout.notFoundDescription", { name, engine: display })}
          action={
            <Link to="/databases" className={buttonClassName("secondary")}>
              {t("databases.layout.allDatabases")}
            </Link>
          }
        />
      </DetailPage>
    );
  }

  let banner = null;
  if (stopped && server !== undefined) {
    banner = <EngineStopped server={server} actions={actions} changing={life === "changing"} />;
  } else if (!stopped && overview.isError) {
    banner = <ErrorBlock error={overview.error} title={t("databases.layout.loadError", { name })} onRetry={() => void overview.refetch()} retrying={overview.isRefetching} />;
  } else if (overview.data?.database.missing === true) {
    banner = (
      <Notice
        variant="banner"
        tone="error"
        title={t("databases.layout.missingTitle")}
        action={
          <Link to="/databases/$engine/$name/delete" params={{ engine, name }} className={buttonClassName("secondary", "sm")}>
            {t("databases.layout.forget")}
          </Link>
        }
      >
        {t("databases.layout.missingBody", { engine: display })}
      </Notice>
    );
  }

  return (
    // The job of an engine that was started from here (a container's start is one) is followed where a database's own is.
    <DatabaseFrameProvider value={{ engine, name, server, life, reading, capabilities, actions, banner, job: hasJob ? <DatabaseJobSlot /> : (actions.notice ?? null) }}>
      <Outlet />
      {actions.dialogs}
    </DatabaseFrameProvider>
  );
}

/**
 * One database: what every one of its sections shares. The job in hand (a dump, a restore, a rotation, a move) shows on
 * whichever section started it and on the others, the banner says once that the engine is stopped or the database gone,
 * and the section the column has open draws its own page. Dropping it follows the job here and, once it has gone,
 * goes back to the list.
 */
export function DatabaseLayout({ engine, name }: LayoutBodyProps) {
  const navigate = useNavigate();
  const dropped = useCallback(() => void navigate({ to: "/databases", replace: true }), [navigate]);
  return (
    <DatabaseJobProvider engine={engine} name={name} onDropped={dropped}>
      <LayoutBody engine={engine} name={name} />
    </DatabaseJobProvider>
  );
}
