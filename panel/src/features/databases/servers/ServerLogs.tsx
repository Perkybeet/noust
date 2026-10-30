import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { engineLogsQuery } from "../../../api/queries/databases";
import type { DbServer } from "../../../api/queries/databases";
import { QueryState } from "../../../components/page/QueryState";
import { Drawer } from "../../../components/ui/Drawer";
import { LogViewer } from "../../../components/ui/LogViewer";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { serverTitle } from "../servers";

/** A database server's journal (or its container's log), verbatim, in a drawer: why it will not start is usually there. */
export function ServerLogs({ server, onClose }: { server: DbServer; onClose: () => void }) {
  const t = useT();
  const logs = useQuery(engineLogsQuery(server.key));
  const lines = useMemo(() => (logs.data?.logs ?? "").split("\n").map((text, id) => ({ id, text })), [logs.data]);
  const name = serverTitle(server);
  return (
    <Drawer
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="lg"
      title={t("databases.engines.logsTitle", { engine: name })}
      description={<Mono>{server.key}</Mono>}
    >
      <QueryState query={logs} label={t("databases.engines.logsNoun")} skeleton={<Skeleton className="h-80 w-full rounded-card" />}>
        {() => <LogViewer lines={lines} label={t("databases.engines.logsLabel", { engine: name })} filename={`${server.key}.log`} height={560} />}
      </QueryState>
    </Drawer>
  );
}
