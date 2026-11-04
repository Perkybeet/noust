import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CopyPlus, RefreshCcwDot, Upload } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { databaseKeys, databasesQuery } from "../../api/queries/databases";
import type { PageHeaderProps } from "../../app/PageHeader";
import { Button } from "../../components/ui/Button";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { VERB_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { CreateDatabaseDialog } from "./CreateDatabaseDialog";
import { ImportDumpDialog } from "./import/ImportDumpDialog";
import { isNotAdded } from "./DatabasesTable";
import type { ServerActions } from "./servers/useServerActions";

/**
 * What Databases and Database servers share: the header (its one primary action, and in its menu what is rarer:
 * a dump from elsewhere, adding what Noust does not hold yet, installing an engine) and the dialog the primary
 * action opens. The two sections are the column's, and a phone's section selector.
 */
export function useDatabasesHeader(page: "databases" | "servers", actions: ServerActions): { header: PageHeaderProps; dialogs: ReactNode; openCreate: () => void } {
  const t = useT();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const list = useQuery(databasesQuery());
  const untracked = (list.data?.databases ?? []).filter(isNotAdded).filter((database) => !database.missing).length;

  const adopt = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/adopt", { body: { engine: null } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
    },
    onError: (error) => reportActionError(t("databases.adopt.failed"), error),
  });

  const Add = ICONS.add;
  const other = (
    <Button icon={<CopyPlus aria-hidden="true" />} disabled={actions.busy} onClick={() => actions.other()} variant={page === "servers" ? "primary" : "secondary"}>
      {t("dbServers.other.open")}
    </Button>
  );
  const install = (
    <MenuItem icon={<VERB_ICONS.install />} disabled={actions.busy} onClick={actions.install}>
      {t("databases.engines.installEngine")}
    </MenuItem>
  );
  const header: PageHeaderProps =
    page === "databases"
      ? {
          title: t("databases.page.title"),
          secondaryActions: other,
          primaryAction: (
            <Button variant="primary" icon={<Add aria-hidden="true" />} onClick={() => setCreating(true)}>
              {t("databases.page.newDatabase")}
            </Button>
          ),
          overflow: (
            <>
              <MenuItem icon={<Upload />} onClick={() => setImporting(true)}>
                {t("dumpImport.action")}
              </MenuItem>
              <MenuItem icon={<RefreshCcwDot />} disabled={untracked === 0 || adopt.isPending} onClick={() => adopt.mutate()}>
                {untracked > 0 ? t("databases.adopt.action", { count: untracked }) : t("databases.adopt.nothing")}
              </MenuItem>
              {install}
            </>
          ),
        }
      : { title: t("dbServers.title"), primaryAction: other, overflow: install };

  return {
    header,
    dialogs: (
      <>
        <CreateDatabaseDialog open={creating} onOpenChange={setCreating} />
        {importing ? <ImportDumpDialog open onOpenChange={setImporting} /> : null}
        {actions.dialogs}
      </>
    ),
    openCreate: () => setCreating(true),
  };
}
